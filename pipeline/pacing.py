# pipeline/pacing.py
"""Presentational pacing: scene holds, natural pauses and delivery speed.

A good presenter does not read a script back to back. They let a finished
picture sit for a moment before moving on, stop briefly after each sentence,
leave a real beat after a question or before a payoff, and speak a little
slower than conversation. This module bakes that into the voiceover itself:

* **Scene hold.** After the last word of every segment there is at least
  `hold` seconds of silence (also at the very end of the video). The scene's
  next segment starts only when that segment's audio starts, so the finished
  visual stays on screen, still, for the whole hold.
* **Lead-in.** Each segment starts with at least `lead_in` seconds of silence,
  so the clean-slate fade of the previous visuals happens before the voice
  starts talking about the new ones.
* **Natural pauses** inside a segment, at sentence ends (`sentence`), after a
  question (`question`), at a colon that introduces a result (`payoff`) and at
  explicit `[[beat]]` / `[[pause]]` markers written by the script agent or a
  paragraph break (`beat`).
* **Delivery speed** (`speech_speed`), applied through each TTS backend's
  native speed control.

Pause and hold lengths are TARGETS for the total silence at that point: the
voice already leaves a short natural gap at a full stop, so only the missing
part is inserted (a top-up). That keeps beats consistent across voices and
never doubles a pause the voice already took.

Silence is inserted into the audio after synthesis, at the exact positions the
backend reports (ElevenLabs per-character alignment, Kokoro token times; for
OpenAI, which has no timestamps, the proportional estimate snapped to the
nearest natural gap in the audio). The insertion point is snapped to the
quietest stretch around the word boundary, so it never cuts a word. Every cue
time and segment duration after an insertion moves with it, so segments.json,
the scene's segment clock, captions, chapters and the waveform all include the
pauses and visual sync stays exact.

ElevenLabs eleven_v4 has no deterministic pause control (SSML <break> is not
supported on v3/v4; audio tags such as [pause] are unmeasured and would put
extra characters into the alignment), so silence insertion is used for every
backend.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from pipeline.cues import parse_markers, segment_cue_text

DEFAULT_PACE = "relaxed"


@dataclass(frozen=True)
class Pace:
    name: str
    hold: float          # min silence after a segment's last word (and at the end)
    lead_in: float       # min silence before a segment's first word
    sentence: float      # min total silence at a sentence end inside a segment
    question: float      # ... after a question the narrator poses
    payoff: float        # ... at a colon that introduces a result
    beat: float          # ... at an explicit [[beat]] / [[pause]] or a paragraph break
    speech_speed: float  # delivery speed multiplier (native TTS speed control)

    def target(self, kind: str) -> float:
        return float(getattr(self, kind))

    def as_dict(self) -> dict:
        return asdict(self)


PRESETS: dict[str, Pace] = {
    "relaxed": Pace("relaxed", hold=2.0, lead_in=0.5, sentence=0.65, question=1.1,
                    payoff=0.9, beat=1.1, speech_speed=0.94),
    "normal": Pace("normal", hold=1.2, lead_in=0.35, sentence=0.45, question=0.75,
                   payoff=0.6, beat=0.75, speech_speed=1.0),
    "brisk": Pace("brisk", hold=0.6, lead_in=0.15, sentence=0.3, question=0.45,
                  payoff=0.4, beat=0.45, speech_speed=1.06),
}
PACE_CHOICES = tuple(PRESETS)

# The voice's own gap at a sentence end, assumed when estimating before TTS
# (measured: ElevenLabs ~0.1-0.2 s, Kokoro ~0.2-0.35 s).
_NATURAL_GAP_EST = 0.2
# Insertion search window around a boundary time from real timestamps, and
# around an estimated (proportional) one.
_WINDOW_ALIGNED = (0.35, 0.08)
_WINDOW_ESTIMATED = (1.0, 1.0)
_HOP = 0.01  # RMS frame (s)
_MIN_PAD = 0.02


def _env_float(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        v = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}") from None
    if v < 0:
        raise ValueError(f"{name} must be >= 0, got {raw!r}")
    return v


def resolve_pace(name: str | None = None) -> Pace:
    """The pace preset `name` (else PACE, else relaxed) with the explicit
    overrides SCENE_HOLD_S (hold seconds) and PACE_SPEECH_SPEED applied."""
    key = (name or os.getenv("PACE") or DEFAULT_PACE).strip().lower()
    if key not in PRESETS:
        raise ValueError(f"Unknown pace {key!r}. Choose: {', '.join(PACE_CHOICES)}")
    pace = PRESETS[key]
    hold = _env_float("SCENE_HOLD_S")
    if hold is not None:
        pace = replace(pace, hold=hold)
    sp = _env_float("PACE_SPEECH_SPEED")
    if sp is not None:
        if not 0.25 <= sp <= 4.0:
            raise ValueError(f"PACE_SPEECH_SPEED must be in [0.25, 4.0], got {sp}")
        pace = replace(pace, speech_speed=sp)
    return pace


def effective_speed(pace: Pace, speed: float | None) -> float:
    """Delivery speed sent to the voice: the preset's speech speed times the
    user's --speed multiplier."""
    return round(pace.speech_speed * float(speed or 1.0), 4)


# ── where the pauses go ─────────────────────────────────────────────────

# Terminal punctuation (+ closing quotes/brackets), whitespace, then a word.
_BOUNDARY = re.compile(r"([.!?]+|:|…)[\"'”’)\]]*(\s+)(?=\S)")
_LOWER = re.compile(r"[a-z]")


def pause_points(clean: str, beats: list[int]) -> tuple[list[tuple[int, str]], bool]:
    """([(offset, kind), ...], end_beat) for a segment's clean text.

    offset = the first character of the word the pause comes before. One pause
    per offset (the longest kind wins when a beat marker sits on a sentence
    end). end_beat is True when a beat marker closes the segment: the hold then
    lasts at least a beat.
    """
    found: dict[int, str] = {}
    rank = {"sentence": 0, "payoff": 1, "question": 2, "beat": 3}
    end = len(clean.rstrip())

    def add(off: int, kind: str) -> None:
        if 0 < off < end and (off not in found or rank[kind] > rank[found[off]]):
            found[off] = kind

    for m in _BOUNDARY.finditer(clean):
        punct, gap = m.group(1), m.group(2)
        off = m.end()
        if "\n" in gap:
            add(off, "beat")
            continue
        if punct.endswith("?"):
            add(off, "question")
        elif punct == ":":
            add(off, "payoff")
        else:
            # "e.g. the" / "3. then": a lowercase next word is not a new sentence.
            if _LOWER.match(clean[off:off + 1]):
                continue
            add(off, "sentence")
    # Paragraph breaks without terminal punctuation.
    for m in re.finditer(r"\n\s*\n\s*(?=\S)", clean):
        add(m.end(), "beat")
    end_beat = False
    for b in beats:
        if b >= end:
            end_beat = True
        else:
            add(b, "beat")
    return sorted(found.items()), end_beat


def timing_text(clean: str, cue_offsets: dict[int, int], extra: list[int], first: int) -> str:
    """Marked text whose parse gives back `clean`, the same cue offsets, and
    markers [[first]], [[first+1]], ... at the `extra` offsets. Backends time
    every marker, so pause positions get real timestamps from the same
    alignment as the cues (no backend changes)."""
    marks = [(off, k) for k, off in cue_offsets.items()]
    marks += [(off, first + i) for i, off in enumerate(extra)]
    out = clean
    for off, k in sorted(marks, key=lambda x: (x[0], x[1]), reverse=True):
        out = out[:off] + f"[[{k}]]" + out[off:]
    return out


def plan_segments(segments: list[dict]) -> list[dict]:
    """Per segment: clean text, cue offsets, pause points and the marked text
    to synthesize (`timing_cue_text`). `n_cues` = highest real cue number."""
    plans = []
    for seg in segments:
        clean, cues, beats = parse_markers(segment_cue_text(seg))
        points, end_beat = pause_points(clean, beats)
        n_cues = max(cues) if cues else 0
        plans.append({
            "clean": clean, "cue_offsets": cues, "points": points, "end_beat": end_beat,
            "n_cues": n_cues,
            "timing_cue_text": timing_text(clean, cues, [o for o, _ in points], n_cues + 1),
        })
    return plans


# ── silence insertion ───────────────────────────────────────────────────

def _frames_rms(x, hop: int):
    import numpy as np
    n = len(x) // hop
    if n == 0:
        return np.zeros(0)
    f = x[: n * hop].reshape(n, hop)
    return np.sqrt((f.astype("float64") ** 2).mean(axis=1))


def _quiet_mask(rms):
    import numpy as np
    if len(rms) == 0:
        return np.zeros(0, dtype=bool)
    thr = max(0.003, 0.1 * float(np.percentile(rms, 95)))
    return rms < thr


def _runs(mask) -> list[tuple[int, int]]:
    """[(start_frame, end_frame_exclusive)] of True runs."""
    runs, start = [], None
    for i, q in enumerate(list(mask) + [False]):
        if q and start is None:
            start = i
        elif not q and start is not None:
            runs.append((start, i))
            start = None
    return runs


def pace_segment(audio, sr: int, pace: Pace, points: list[tuple[int, str, float | None]],
                 *, end_beat: bool = False, estimated: bool = False) -> dict:
    """Insert silence into one segment's audio (1-D float array).

    points: [(char_offset, kind, time_sec)] in text order; time = when the
    next word starts in the ORIGINAL audio (None = unknown, skipped).
    Returns {"audio", "lead_pad", "pads": [(char_offset, pad_sec, gap_sec,
    kind, insert_time_original)], "speech_start", "speech_end"} where
    speech_* are in the NEW timeline.
    """
    import numpy as np
    hop = max(1, int(round(sr * _HOP)))
    rms = _frames_rms(audio, hop)
    quiet = _quiet_mask(rms)
    nf = len(quiet)
    loud = np.flatnonzero(~quiet) if nf else np.array([], dtype=int)
    if len(loud) == 0:
        # Silent (or empty) segment: just make sure it holds.
        need = max(0, int(round((pace.hold + pace.lead_in) * sr)) - len(audio))
        out = np.concatenate([audio, np.zeros(need, dtype=audio.dtype)])
        return {"audio": out, "lead_pad": 0.0, "pads": [], "speech_start": 0.0, "speech_end": 0.0}
    first, last = int(loud[0]), int(loud[-1])
    lead_existing = first * hop / sr
    trail_existing = (len(audio) - (last + 1) * hop) / sr
    lead_pad = max(0.0, pace.lead_in - lead_existing)
    hold_target = max(pace.hold, pace.beat) if end_beat else pace.hold
    trail_pad = max(0.0, hold_target - trail_existing)

    runs = _runs(quiet)
    before, after = _WINDOW_ESTIMATED if estimated else _WINDOW_ALIGNED
    inserts: list[tuple[int, int, float, float, int, str]] = []  # (sample, pad_samples, gap, t, off, kind)
    floor = first + 1  # never insert before the speech starts, nor before the last insert
    for off, kind, t in points:
        if t is None:
            continue
        lo = max(floor, int((t - before) / _HOP))
        hi = min(last - 1, int((t + after) / _HOP))
        if hi <= lo:
            continue
        cands = [(a, b) for a, b in runs if b > lo and a < hi and a > first and b <= last]
        if cands:
            if estimated:
                tf = t / _HOP
                a, b = min(cands, key=lambda r: (abs((r[0] + r[1]) / 2 - tf), -(r[1] - r[0])))
            else:
                a, b = max(cands, key=lambda r: (r[1] - r[0], -abs((r[0] + r[1]) / 2 - t / _HOP)))
            at_frame = max(lo, min(hi, (a + b) // 2))
            existing = (b - a) * hop / sr
        else:
            at_frame = lo + int(np.argmin(rms[lo:hi]))
            existing = 0.0
        pad = max(0.0, pace.target(kind) - existing)
        floor = at_frame + 1
        if pad < _MIN_PAD:
            continue
        inserts.append((at_frame * hop, int(round(pad * sr)), existing + pad, at_frame * hop / sr, off, kind))

    pieces = [np.zeros(int(round(lead_pad * sr)), dtype=audio.dtype)]
    cursor = 0
    for sample, n, _, _, _, _ in inserts:
        pieces.append(audio[cursor:sample])
        pieces.append(np.zeros(n, dtype=audio.dtype))
        cursor = sample
    pieces.append(audio[cursor:])
    pieces.append(np.zeros(int(round(trail_pad * sr)), dtype=audio.dtype))
    out = np.concatenate(pieces)
    lead_s = len(pieces[0]) / sr
    inserted = sum(n for _, n, _, _, _, _ in inserts) / sr
    speech_start = lead_s + lead_existing
    speech_end = lead_s + inserted + (last + 1) * hop / sr
    pads = [(off, n / sr, gap, kind, t) for _, n, gap, t, off, kind in inserts]
    return {"audio": out, "lead_pad": lead_s, "pads": pads,
            "speech_start": speech_start, "speech_end": min(speech_end, len(out) / sr)}


def shift_time(t: float, offset: int, lead_pad: float, pads: list[tuple]) -> float:
    """New time of a word at char `offset` that started at `t` originally:
    later by the lead-in pad and every pause inserted before that word."""
    return t + lead_pad + sum(p[1] for p in pads if p[0] <= offset)


def apply_pacing(wav_path: Path, durations: list[float], cue_times: list[list[float | None]],
                 point_times: list[list[float | None]], plans: list[dict], pace: Pace,
                 *, estimated: bool) -> tuple[list[float], list[list[float | None]], list[dict]]:
    """Rewrite wav_path with holds and pauses. Returns (durations, cue_times,
    meta) where meta[i] has speech_start_sec, speech_end_sec, pauses and
    anchors for segments.json."""
    import numpy as np
    import soundfile as sf

    info = sf.info(str(wav_path))
    audio, sr = sf.read(str(wav_path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    bounds, acc = [0], 0.0
    for d in durations:
        acc += d
        bounds.append(min(len(audio), int(round(acc * sr))))
    bounds[-1] = len(audio)

    out_pieces, new_durs, new_cues, metas = [], [], [], []
    for i, plan in enumerate(plans):
        seg = audio[bounds[i]:bounds[i + 1]]
        times = point_times[i] if i < len(point_times) else []
        pts = [(off, kind, times[j] if j < len(times) else None)
               for j, (off, kind) in enumerate(plan["points"])]
        r = pace_segment(seg, sr, pace, pts, end_beat=plan["end_beat"], estimated=estimated)
        out_pieces.append(r["audio"])
        new_durs.append(len(r["audio"]) / sr)
        cues = cue_times[i] if i < len(cue_times) else []
        offs = plan["cue_offsets"]
        shifted = []
        for k, t in enumerate(cues, start=1):
            if t is None or k not in offs:
                shifted.append(None if t is None else round(t + r["lead_pad"], 3))
            else:
                shifted.append(round(shift_time(t, offs[k], r["lead_pad"], r["pads"]), 3))
        new_cues.append(shifted)
        pauses, anchors = [], [[0, round(r["speech_start"], 3)]]
        for off, pad, gap, kind, t_ins in r["pads"]:
            # Silence starts at the insertion point, shifted by earlier pads.
            at = t_ins + r["lead_pad"] + sum(p[1] for p in r["pads"] if p[0] < off)
            pauses.append({"at": round(at, 3), "sec": round(pad, 3), "gap": round(gap, 3), "kind": kind})
            anchors.append([max(0, off - 1), round(at, 3)])
            j = [o for o, _ in plan["points"]].index(off)
            if times and j < len(times) and times[j] is not None:
                anchors.append([off, round(shift_time(times[j], off, r["lead_pad"], r["pads"]), 3)])
        for k, off in offs.items():
            if k - 1 < len(shifted) and shifted[k - 1] is not None:
                anchors.append([off, shifted[k - 1]])
        anchors.append([len(plan["clean"]), round(r["speech_end"], 3)])
        anchors = _monotonic(anchors)
        metas.append({
            "speech_start_sec": round(r["speech_start"], 3),
            "speech_end_sec": round(r["speech_end"], 3),
            "hold_sec": round(len(r["audio"]) / sr - r["speech_end"], 3),
            "pauses": pauses,
            "anchors": anchors,
        })
    full = np.concatenate(out_pieces) if out_pieces else np.zeros(0, dtype="float32")
    sf.write(str(wav_path), full, sr, subtype=info.subtype if info.subtype in ("PCM_16", "PCM_24", "FLOAT") else "PCM_16")
    return new_durs, new_cues, metas


def _monotonic(anchors: list[list]) -> list[list]:
    """Sorted by offset, times never decreasing (drops inconsistent points)."""
    out: list[list] = []
    for off, t in sorted(anchors, key=lambda a: (a[0], a[1])):
        if out and (t < out[-1][1] or off == out[-1][0]):
            if off == out[-1][0] and t >= out[-1][1]:
                out[-1] = [off, t]
            continue
        out.append([off, t])
    return out


# ── estimates before the voice exists (layout dry-run, manim prompt) ───

def estimate_segment(seg: dict, pace: Pace, speed: float = 1.0) -> dict:
    """segments.json-shaped estimate for one segment before TTS: the script's
    estimated speech time at the delivery speed, plus lead-in, pauses and
    hold. Segments that already carry measured values are returned as they
    are (QA regeneration)."""
    clean, cues, beats = parse_markers(segment_cue_text(seg))
    if isinstance(seg.get("cues"), list) and "actual_duration_sec" in seg:
        out = {"text": clean, "actual_duration_sec": seg["actual_duration_sec"], "cues": seg["cues"]}
        for k in ("speech_start_sec", "speech_end_sec", "hold_sec", "pauses", "anchors"):
            if k in seg:
                out[k] = seg[k]
        return out
    base = seg.get("actual_duration_sec", seg.get("estimated_duration_sec", 2.0))
    speech = float(base) / effective_speed(pace, speed)
    points, end_beat = pause_points(clean, beats)
    n = max(1, len(clean))
    pads = [(off, max(0.0, pace.target(kind) - _NATURAL_GAP_EST)) for off, kind in points]
    lead = pace.lead_in

    def t_of(off: int) -> float:
        return lead + speech * off / n + sum(p for o, p in pads if o <= off)

    hold = max(pace.hold, pace.beat) if end_beat else pace.hold
    speech_end = lead + speech + sum(p for _, p in pads)
    cue_list = []
    if cues:
        for k in range(1, max(cues) + 1):
            cue_list.append(round(t_of(cues[k]), 3) if k in cues else None)
    return {
        "text": clean,
        "actual_duration_sec": round(speech_end + hold, 3),
        "cues": cue_list,
        "speech_start_sec": round(lead, 3),
        "speech_end_sec": round(speech_end, 3),
        "hold_sec": round(hold, 3),
        "pauses": [{"at": round(t_of(o) - p, 3), "sec": round(p, 3), "kind": k}
                   for (o, p), (_, k) in zip(pads, points) if p > 0],
    }
