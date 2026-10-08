# tests/test_chapters.py
"""Step-level chapters: [[ch: Title]] markers -> chapters.txt, MP4 metadata, timeline."""
import asyncio
import json

import numpy as np
import pytest
import soundfile as sf

from pipeline import pacing
from pipeline.chapters import (
    MIN_GAP_S, build_chapters, ffmetadata, is_numbered, legacy_title, youtube_list,
)
from pipeline.cues import parse_chapter_markers, parse_cues, parse_markers, strip_cues

SR = 24000
WORDS = "one two three four five six seven eight nine ten".split()


def _tone(sec, amp=0.3):
    t = np.arange(int(round(sec * SR))) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype("float32")


def _sil(sec):
    return np.zeros(int(round(sec * SR)), dtype="float32")


# ── markers ────────────────────────────────────────────────────────────────

def test_chapter_markers_are_silent_and_located():
    raw = ("[[ch: Three steps]] Here are three steps. [[ch: 1. Mix]] [[1]] First, mix. "
           "[[2]] [[ch: 2. Bake]] Then bake. [[beat]] [[chapter: Recap]] Done.")
    clean, offsets, beats = parse_markers(raw)
    assert clean == "Here are three steps. First, mix. Then bake. Done."
    assert strip_cues(raw) == clean
    assert offsets == {1: clean.index("First"), 2: clean.index("Then")}
    assert beats == [clean.index("Done")]
    assert parse_chapter_markers(raw) == [
        (0, "Three steps", None),
        (clean.index("First"), "1. Mix", 1),      # cue right after the marker
        (clean.index("Then"), "2. Bake", 2),      # cue right before the marker
        (clean.index("Done"), "Recap", None),
    ]


def test_chapter_markers_never_reach_the_voice():
    seg = {"text": "x", "cue_text": "[[ch: Intro]] Hello. [[ch: 1. Mix]] [[1]] First, mix."}
    plan = pacing.plan_segments([seg])[0]
    assert "ch" not in plan["timing_cue_text"]
    assert parse_cues(plan["timing_cue_text"])[0] == "Hello. First, mix."


def test_numbered_titles():
    for t in ("3. Region test", "10) Total power", "Step 4: rinse", "#2 Noise margins", "Layer 3 corners"):
        assert is_numbered(t), t
    for t in ("Region test", "10 formulas overview", "1940s jazz", ""):
        assert not is_numbered(t), t


# ── build_chapters ─────────────────────────────────────────────────────────

def _ten_steps():
    """Three scenes that walk through ten numbered steps (3 + 4 + 3), the way
    a script with few segments packs an enumeration."""
    layout = [(26.0, [5.0, 12.0, 19.0]), (27.0, [0.6, 7.0, 13.5, 20.0]), (22.0, [0.6, 7.0, 14.0])]
    segs, n = [], 0
    for s, (dur, cue_times) in enumerate(layout):
        parts = ["[[ch: Ten steps]] Here are ten steps." if s == 0 else ""]
        for k, _ in enumerate(cue_times, start=1):
            n += 1
            parts.append(f"[[ch: {n}. Step {WORDS[n - 1]}]] [[{k}]] Step {WORDS[n - 1]} does a thing.")
        marked = " ".join(p for p in parts if p)
        segs.append({"text": strip_cues(marked), "cue_text": marked,
                     "actual_duration_sec": dur, "cues": cue_times})
    return segs, layout


def test_ten_steps_give_ten_chapters_at_their_cues():
    segs, layout = _ten_steps()
    ch = build_chapters(segs)
    assert [c["title"] for c in ch] == ["Ten steps"] + [f"{i}. Step {WORDS[i - 1]}" for i in range(1, 11)]
    expected, t0 = [0.0], 0.0
    for dur, cue_times in layout:
        expected += [t0 + c for c in cue_times]
        t0 += dur
    assert [c["start_s"] for c in ch] == pytest.approx(expected)
    assert all(c["numbered"] for c in ch[1:]) and all(c["source"] == "script" for c in ch)
    # Each chapter ends where the next starts; the last at the end of the video.
    assert [c["end_s"] for c in ch[:-1]] == [c["start_s"] for c in ch[1:]]
    assert ch[-1]["end_s"] == pytest.approx(75.0)
    assert [c["segment"] for c in ch] == [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2]


def test_min_spacing_merges_but_keeps_numbered_steps():
    marked = ("[[ch: Intro]] Hi there. [[ch: Background]] [[1]] Some context. "
              "[[ch: 1. Alpha]] [[2]] Alpha. [[ch: 2. Beta]] [[3]] Beta. "
              "[[ch: Overview]] [[4]] Look. [[ch: 3. Gamma]] [[5]] Gamma. [[ch: 3. gamma]] [[6]] Again.")
    seg = {"text": strip_cues(marked), "cue_text": marked, "actual_duration_sec": 40.0,
           "cues": [2.0, 10.0, 11.5, 20.0, 22.0, 30.0]}
    ch = build_chapters([seg])
    # Background (2 s after Intro) merges away; 1 and 2 are 1.5 s apart but both
    # numbered, so both stay; Overview is replaced by step 3 two seconds later;
    # a repeated title is dropped.
    assert [(c["title"], c["start_s"]) for c in ch] == [
        ("Intro", 0.0), ("1. Alpha", 10.0), ("2. Beta", 11.5), ("3. Gamma", 22.0)]
    assert MIN_GAP_S == 4.0
    loose = build_chapters([seg], min_gap=0.5)
    assert [c["title"] for c in loose] == ["Intro", "Background", "1. Alpha", "2. Beta", "Overview", "3. Gamma"]


def test_chapter_without_cue_uses_anchors_and_first_chapter_starts_at_zero():
    marked = "Hello. [[ch: Opening]] So far so good. [[ch: The trick]] Here is the trick."
    clean = strip_cues(marked)
    seg = {"text": clean, "cue_text": marked, "actual_duration_sec": 20.0, "cues": [],
           "anchors": [[0, 0.5], [clean.index("So"), 2.0], [clean.index("Here"), 9.0], [len(clean), 14.0]]}
    ch = build_chapters([seg])
    assert [(c["title"], c["start_s"]) for c in ch] == [("Opening", 0.0), ("The trick", 9.0)]


def test_old_runs_keep_one_chapter_per_segment():
    long = "Here are the ten Quiz 2 formulas that are not on your equation sheet, ranked."
    segs = [{"text": long, "cue_text": "Here are the ten [[1]] Quiz 2 formulas that are not on your "
                                        "equation sheet, ranked.", "actual_duration_sec": 43.69, "cues": [3.0]},
            {"text": "Number three: which side is which.", "actual_duration_sec": 48.2}]
    ch = build_chapters(segs)
    assert [c["title"] for c in ch] == [legacy_title(long), "Number three: which side is which."]
    assert ch[0]["title"] == long[:60].rstrip() + "..."
    assert [c["start_s"] for c in ch] == [0.0, 43.69]
    assert all(c["source"] == "segments" for c in ch)
    meta = ffmetadata(ch)
    assert "START=0\nEND=43690\n" in meta and "START=43690\nEND=91890\n" in meta


def test_no_chapters_before_tts():
    assert build_chapters([{"text": "a", "cue_text": "[[ch: A]] a", "estimated_duration_sec": 3.0}]) == []
    assert build_chapters([]) == []


def test_ffmetadata_escapes_and_youtube_list():
    ch = [{"start_s": 0.0, "end_s": 5.0, "title": "a=b; #c \\ d"},
          {"start_s": 65.4, "end_s": 70.0, "title": "Next"}]
    meta = ffmetadata(ch)
    assert meta.startswith(";FFMETADATA1\n")
    assert "title=a\\=b\\; \\#c \\\\ d\n" in meta
    assert "START=65400\nEND=70000\n" in meta
    assert youtube_list(ch) == ["0:00  a=b; #c \\ d", "1:05  Next"]


# ── on the paced timeline (render_trigger -> segments.json -> chapters.txt) ──

def test_chapters_follow_paced_cue_times(base_state, tmp_path, monkeypatch):
    from main import _generate_caption_files
    from pipeline.render_trigger import render_trigger
    for k in ("PACE", "SCENE_HOLD_S", "PACE_SPEECH_SPEED"):
        monkeypatch.delenv(k, raising=False)
    seg1 = "[[ch: Why it works]] Why? [[ch: 1. The answer]] [[1]] Because."
    base_state.update({
        "manim_code": "x = 1", "script": "Hello there. Why? Because.", "run_id": "ch-run",
        "script_segments": [
            {"text": "Hello there.", "cue_text": "[[ch: Intro]] Hello there.", "estimated_duration_sec": 1.0},
            {"text": "Why? Because.", "cue_text": seg1, "estimated_duration_sec": 2.0},
        ],
        "pace": "relaxed",
    })
    seen = []

    async def backend(segments, output_path, speed=1.0):
        seen.extend(s["cue_text"] for s in segments)
        a0 = np.concatenate([_sil(0.05), _tone(1.0), _sil(0.05)])
        a1 = np.concatenate([_sil(0.05), _tone(0.6), _sil(0.1), _tone(0.8), _sil(0.05)])
        sf.write(str(output_path), np.concatenate([a0, a1]), SR, subtype="PCM_16")
        # Segment 1: cue [[1]] and the question pause both start "Because" at 0.75 s.
        return output_path, [len(a0) / SR, len(a1) / SR], [[], [0.75, 0.75]]

    monkeypatch.setattr("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("pipeline.render_trigger.get_backend", lambda name: backend)
    asyncio.run(render_trigger(base_state))

    assert all("ch" not in t for t in seen)           # the voice never sees chapter markers
    run = tmp_path / "ch-run"
    segs = json.loads((run / "segments.json").read_text())
    assert segs[1]["cue_text"] == seg1                # kept for chapters
    shifted = segs[1]["cues"][0]
    assert shifted > 0.75 + 0.4                       # lead-in + question pause moved the cue
    ch = build_chapters(segs)
    # "Why it works" opens the scene ~2 s before step 1 lands, so the numbered
    # step replaces it (minimum spacing).
    assert [c["title"] for c in ch] == ["Intro", "1. The answer"]
    assert ch[1]["start_s"] == pytest.approx(segs[0]["actual_duration_sec"] + shifted, abs=0.001)

    _, chapters_path = _generate_caption_files(run)
    meta = chapters_path.read_text()
    assert f"START={int(round(ch[1]['start_s'] * 1000))}\n" in meta
    assert "title=1. The answer" in meta
    captions = (run / "captions.srt").read_text()
    assert "ch:" not in captions and "Why it works" not in captions


def test_timeline_api_returns_chapters(tmp_path):
    from server.run_data import job_timeline, run_timeline
    segs, _ = _ten_steps()
    (tmp_path / "segments.json").write_text(json.dumps(segs))
    tl = run_timeline(tmp_path)
    assert len(tl["chapters"]) == 11 and len(tl["segments"]) == 3
    assert tl["chapters"][4]["title"] == "4. Step four" and tl["chapters"][4]["start_s"] == pytest.approx(26.6)
    old = tmp_path / "old"
    old.mkdir()
    (old / "segments.json").write_text(json.dumps([{"text": "Only scene", "actual_duration_sec": 5.0}]))
    assert run_timeline(old)["chapters"] == [{
        "index": 0, "start_s": 0.0, "end_s": 5.0, "title": "Only scene", "segment": 0, "cue": None,
        "numbered": False, "source": "segments"}]
    pending = tmp_path / "pending"
    pending.mkdir()
    assert job_timeline(pending, [])["chapters"] == []
