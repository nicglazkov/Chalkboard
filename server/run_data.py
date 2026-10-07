# server/run_data.py
"""Read-only views of a run directory for the UI: timeline, waveform, quality.

Every value is read or computed from files the pipeline wrote. Anything that
is missing or cannot be attributed with certainty is None.
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

WAVEFORM_BUCKETS = 400
WAVEFORM_CACHE = "waveform.json"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _label(text: str, limit: int = 80) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


# ── Timeline ──────────────────────────────────────────────────────────────────

def segments_timeline(segments: list[dict]) -> list[dict]:
    """Segments with measured start/duration. A segment without a measured
    duration (TTS has not run) has null duration, and so does every start
    after it."""
    out = []
    start: float | None = 0.0
    for i, seg in enumerate(segments):
        dur = seg.get("actual_duration_sec")
        dur = float(dur) if isinstance(dur, (int, float)) else None
        cues = seg.get("cues") if dur is not None else None
        speech_end = seg.get("speech_end_sec") if dur is not None else None
        pauses = seg.get("pauses") if dur is not None else None
        out.append({
            "index": i,
            "start_s": round(start, 3) if start is not None else None,
            "duration_s": round(dur, 3) if dur is not None else None,
            "label": _label(seg.get("text", "")),
            "cues": [c for c in cues] if isinstance(cues, list) else [],
            # Pacing (pipeline/pacing.py): the last word ends here (seconds from
            # the segment start; the rest is the silent hold), and the silences
            # inserted inside the narration. null / [] for runs before pacing.
            "speech_end_s": float(speech_end) if isinstance(speech_end, (int, float)) else None,
            "pauses": [{"at_s": p.get("at"), "sec": p.get("sec"), "kind": p.get("kind")}
                       for p in pauses if isinstance(p, dict)] if isinstance(pauses, list) else [],
        })
        start = start + dur if (start is not None and dur is not None) else None
    return out


def wav_duration(wav: Path) -> float | None:
    try:
        import soundfile as sf
        info = sf.info(str(wav))
        return round(info.frames / info.samplerate, 3)
    except Exception:
        return None


def waveform(run_dir: Path, buckets: int = WAVEFORM_BUCKETS) -> list[float] | None:
    """Peak envelope of voiceover.wav: max |sample| per bucket, divided by the
    loudest peak in the file (so 1.0 = the loudest moment). Cached next to the
    run, keyed on the wav's size and mtime."""
    wav = run_dir / "voiceover.wav"
    try:
        st = wav.stat()
    except OSError:
        return None
    key = {"size": st.st_size, "mtime": st.st_mtime, "buckets": buckets}
    cache = run_dir / WAVEFORM_CACHE
    cached = _read_json(cache)
    if isinstance(cached, dict) and cached.get("key") == key:
        return cached.get("peaks")
    try:
        import numpy as np
        import soundfile as sf
        data, _sr = sf.read(str(wav), dtype="float32", always_2d=True)
    except Exception:
        return None
    mono = np.abs(data).max(axis=1)
    if mono.size == 0:
        return None
    n = min(buckets, mono.size)
    edges = np.linspace(0, mono.size, n + 1).astype(int)
    peaks = np.array([mono[a:b].max() if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])])
    top = float(peaks.max())
    norm = (peaks / top) if top > 0 else peaks
    result = [round(float(v), 4) for v in norm]
    try:
        cache.write_text(json.dumps({"key": key, "peaks": result, "full_scale_peak": round(top, 5)}))
    except OSError:
        pass
    return result


def run_timeline(run_dir: Path) -> dict | None:
    segs = _read_json(run_dir / "segments.json")
    if not isinstance(segs, list):
        return None
    timeline = segments_timeline(segs)
    duration = wav_duration(run_dir / "voiceover.wav")
    if duration is None and timeline and all(s["duration_s"] is not None for s in timeline):
        duration = round(sum(s["duration_s"] for s in timeline), 3)
    return {
        "duration_s": duration,
        "segments": timeline,
        "waveform": waveform(run_dir),
        # final.mp4 exists only after the whole scene rendered and merged.
        "rendered_segments": len(timeline) if (run_dir / "final.mp4").exists() else None,
    }


def job_timeline(run_dir: Path, events: list[dict]) -> dict:
    """Timeline for a job that may still be running."""
    if (run_dir / "segments.json").exists():
        tl = run_timeline(run_dir)
        if tl is not None:
            if tl["rendered_segments"] is None:
                tl["rendered_segments"] = _rendered_from_events(events)
            return tl
    # Before TTS: the script's segments, with no durations (they are not measured yet).
    segs = None
    for e in reversed(events):
        if e.get("node") == "script_agent":
            segs = (e.get("updates") or {}).get("script_segments")
            if segs:
                break
    timeline = []
    for i, s in enumerate(segs or []):
        timeline.append({"index": i, "start_s": None, "duration_s": None,
                         "label": _label(s.get("text", "")), "cues": []})
    return {"duration_s": None, "segments": timeline, "waveform": None,
            "rendered_segments": _rendered_from_events(events)}


def _rendered_from_events(events: list[dict]) -> int | None:
    """Segments fully rendered so far, from live render events: when the
    renderer starts segment n (0-based), segments 0..n-1 are done."""
    started = None   # 0-based index of the segment being rendered
    total = None
    finished = False
    for e in events:
        if e.get("node") != "render":
            continue
        u = e.get("updates") or {}
        status = u.get("status")
        if status == "running":
            finished = False
            if isinstance(u.get("segment"), int):
                started = u["segment"]
            if isinstance(u.get("segments"), int):
                total = u["segments"]
        elif status == "done":
            finished = True
        elif status == "failed":
            started, finished = None, False
    if finished:
        return total
    return started


# ── Quality ───────────────────────────────────────────────────────────────────

def layout_report_source(run_dir: Path, report: dict) -> tuple[str | None, str]:
    """Who wrote layout_report.json: ("render", why) / ("dry_run", why) / (None, why).

    New reports say so in `mode`. Older ones are attributed from file times:
    the dry-run runs before render_trigger writes manifest.json, and the real
    render writes the report before the merge writes final.mp4. A report newer
    than final.mp4 came from a later dry-run (QA regeneration)."""
    mode = report.get("mode")
    if mode in ("render", "dry_run"):
        return mode, f"layout_report.json mode={mode!r}"
    try:
        rep = (run_dir / "layout_report.json").stat().st_mtime
        man = (run_dir / "manifest.json").stat().st_mtime
        fin = (run_dir / "final.mp4").stat().st_mtime
    except OSError:
        return None, "no mode field and file times unavailable"
    if man < rep <= fin:
        return "render", "no mode field; written after manifest.json and before final.mp4 (file times)"
    return None, "no mode field; file times do not show which run wrote it"


def quality(run_dir: Path) -> dict:
    report = _read_json(run_dir / "layout_report.json")
    sync = None
    layout = None
    if isinstance(report, dict):
        source, why = layout_report_source(run_dir, report)
        layout = {"passed": report.get("passed"), "violations": report.get("violations", []),
                  "source": source, "evidence": why}
        cue_log = report.get("cue_log")
        if source == "render" and isinstance(cue_log, list) and cue_log:
            cues = [{"segment": c.get("segment"), "cue": c.get("cue"), "lag_s": c.get("lag"),
                     "spoken_at_s": c.get("spoken_at"), "visual_at_s": c.get("visual_at")}
                    for c in cue_log if isinstance(c.get("lag"), (int, float))]
            lags = [c["lag_s"] for c in cues]
            if lags:
                sync = {
                    "cues": cues,
                    "median_lag_s": round(statistics.median(lags), 3),
                    "worst_lag_s": round(max(lags), 3),
                    "source": "render",
                    # What the number is: the scene clock (frames written) when the
                    # cued animation started vs when the word is spoken in the TTS.
                    "evidence": why + "; lag = renderer time at self.cue(k) minus the word's TTS time",
                }
    qa = _read_json(run_dir / "qa_report.json")
    visual_qa = None
    if isinstance(qa, dict) and "passed" in qa:
        visual_qa = {"passed": qa.get("passed"), "issues": qa.get("issues", []),
                     "checked_at": qa.get("checked_at"), "attempts": len(qa.get("history") or []) or None}
    return {"sync": sync, "layout": layout, "visual_qa": visual_qa}
