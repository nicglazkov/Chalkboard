# tests/test_cues.py
"""Word-level narration sync: cue markers, cue times, scene cue(), stubs."""
import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import numpy as np
import pytest

from pipeline.cues import (
    caption_lines, char_time, clean_segments, cue_times_from_alignment,
    cue_times_from_tokens, parse_cues, proportional_cue_times, strip_cues,
)


# ── Marker parsing / stripping ───────────────────────────────────────────────

def test_parse_cues_offsets_point_at_the_cued_word():
    clean, offs = parse_cues("Start with the [[1]] limit definition, then [[2]] expand it.")
    assert clean == "Start with the limit definition, then expand it."
    assert clean[offs[1]:].startswith("limit")
    assert clean[offs[2]:].startswith("expand")


def test_parse_cues_marker_after_punctuation_and_at_start():
    clean, offs = parse_cues("[[1]]Slope first.[[2]] Then the area.")
    assert clean == "Slope first. Then the area."
    assert clean[offs[1]:].startswith("Slope")
    assert clean[offs[2]:].startswith("Then")


def test_parse_cues_tolerates_spaces_and_no_markers():
    assert parse_cues("a [[ 3 ]] b") == ("a b", {3: 2})
    assert parse_cues("No markers here.") == ("No markers here.", {})
    assert strip_cues("") == ""


def test_strip_cues_leaves_no_double_spaces():
    assert strip_cues("Take [[1]] e to the [[2]] x.") == "Take e to the x."


def test_clean_segments_keeps_marked_text_in_cue_text():
    segs = clean_segments([
        {"text": "The [[1]] slope.", "estimated_duration_sec": 2.0},
        {"text": "Plain.", "estimated_duration_sec": 1.0},
    ])
    assert segs[0]["text"] == "The slope."
    assert segs[0]["cue_text"] == "The [[1]] slope."
    assert segs[0]["estimated_duration_sec"] == 2.0
    assert segs[1] == {"text": "Plain.", "estimated_duration_sec": 1.0}


# ── Cue time mapping ─────────────────────────────────────────────────────────

def test_proportional_cue_times():
    clean, offs = parse_cues("aaaa [[1]] bbbb [[2]] cccc!")   # 'b' at 5, 'c' at 10 of 15
    assert proportional_cue_times(clean, offs, 3.0) == [1.0, 2.0]


def test_proportional_cue_times_skipped_number_is_none():
    clean, offs = parse_cues("aaaa [[2]] bbbb")
    assert proportional_cue_times(clean, offs, 1.0)[0] is None


# ElevenLabs /with-timestamps alignment (shape verified live 2026-10-02).
_EL_TEXT = "Take e to the x, then 12 more."


def _el_alignment(text=_EL_TEXT):
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(0.1 * i, 3) for i in range(len(text))],
        "character_end_times_seconds": [round(0.1 * i + 0.1, 3) for i in range(len(text))],
    }


def test_alignment_exact_match():
    clean, offs = parse_cues("Take [[1]] e to the x, then [[2]] 12 more.")
    assert clean == _EL_TEXT
    a = _el_alignment()
    times = cue_times_from_alignment(clean, offs, a["characters"],
                                     a["character_start_times_seconds"], 3.0)
    assert times == [pytest.approx(0.5), pytest.approx(2.2)]


def test_alignment_with_differing_characters_still_maps():
    # Alignment of a slightly different text (one char dropped early on):
    # the sequential match keeps later cues on their words.
    clean, offs = parse_cues("Take e to the x, then [[1]] twelve more.")
    other = "Take e to the x then twelve more."           # comma missing
    a = _el_alignment(other)
    times = cue_times_from_alignment(clean, offs, a["characters"],
                                     a["character_start_times_seconds"], 3.3)
    assert times[0] == pytest.approx(0.1 * other.index("twelve"))


def test_alignment_missing_falls_back_to_proportional():
    clean, offs = parse_cues("aaaa [[1]] bbbb")
    assert cue_times_from_alignment(clean, offs, [], [], 2.0) == proportional_cue_times(clean, offs, 2.0)


def test_tokens_mapping_uses_first_token_at_or_after_the_cue():
    clean, offs = parse_cues("The [[1]] derivative of [[2]] e to the x.")
    tokens = [("The", 0.0), ("derivative", 0.3), ("of", 0.9), ("e", 1.2),
              ("to", 1.4), ("the", 1.6), ("x", 1.8), (".", None)]
    assert cue_times_from_tokens(clean, offs, tokens, 2.2) == [0.3, 1.2]


def test_tokens_mapping_without_tokens_is_proportional():
    clean, offs = parse_cues("aaaa [[1]] bbbb")
    assert cue_times_from_tokens(clean, offs, [], 2.0) == proportional_cue_times(clean, offs, 2.0)


# ── Captions ─────────────────────────────────────────────────────────────────

def test_caption_lines_split_sentences():
    text = "First sentence here. Second one! Third?"
    spans = caption_lines(text)
    assert [text[a:b].strip() for a, b in spans] == ["First sentence here.", "Second one!", "Third?"]


def test_caption_lines_split_long_sentences_without_orphans():
    text = ("Here's a fact that feels almost magical: the derivative of e to the x "
            "is just e to the x.")
    spans = caption_lines(text)
    assert [text[a:b].strip() for a, b in spans] == [
        "Here's a fact that feels almost magical:",
        "the derivative of e to the x is just e to the x."]
    assert "".join(text[a:b] for a, b in spans) == text


def test_char_time_interpolates_through_cues():
    clean, offs = parse_cues("aaaaaaaaa [[1]] bbbbbbbbb")   # 'b' at 10 of 19
    # Cue spoken late (at 8s of 10s): characters before it stretch.
    assert char_time(offs, [8.0], len(clean), 10.0, 10) == pytest.approx(8.0)
    assert char_time(offs, [8.0], len(clean), 10.0, 5) == pytest.approx(4.0)


def test_caption_cues_sentence_level_and_anchored(tmp_path):
    from main import _caption_cues
    segs = [
        {"text": "One two three. Four five six.", "actual_duration_sec": 4.0,
         "cue_text": "One two three. [[1]] Four five six.", "cues": [3.0]},
        {"text": "Next.", "actual_duration_sec": 1.0, "cues": []},
    ]
    lines = _caption_cues(segs)
    assert lines[0] == (0.0, 3.0, "One two three.")
    assert lines[1] == (3.0, 4.0, "Four five six.")
    assert lines[2] == (4.0, 5.0, "Next.")


# ── Scene base: cue() ────────────────────────────────────────────────────────

from tests.test_chalkboard_base import _FakeScene  # noqa: E402


def _scene(tmp_path, cues):
    s = _FakeScene(tmp_path)
    s._cues = cues
    return s


def test_cue_waits_until_the_word(tmp_path):
    s = _scene(tmp_path, {0: [1.5, 4.0]})
    s.begin_segment(0, duration=6.0)
    s.play(run_time=0.5)
    assert s.cue(1) is True
    assert s._sync_tracked == pytest.approx(1.5)
    s.play(run_time=1.0)
    s.cue(2)
    assert s._sync_tracked == pytest.approx(4.0)
    assert not [v for v in s._lc_violations if v["type"] == "cue_late"]


def test_cue_is_relative_to_the_segment_narration_start(tmp_path):
    s = _scene(tmp_path, {0: [], 1: [2.0]})
    s.begin_segment(0, duration=3.0)
    s.play(run_time=1.0)
    s.next_segment(1, duration=5.0)       # holds until 3.0
    s.cue(1)
    assert s._sync_tracked == pytest.approx(5.0)


def test_cue_late_is_recorded_not_waited(tmp_path):
    s = _scene(tmp_path, {0: [1.0, 2.0]})
    s.begin_segment(0, duration=6.0)
    s.play(run_time=1.2)
    s.cue(1)                              # 0.2s late: tolerated
    s.play(run_time=1.8)                  # now at 3.0, cue 2 was at 2.0
    s.cue(2)
    assert s._sync_tracked == pytest.approx(3.0)
    late = [v for v in s._lc_violations if v["type"] == "cue_late"]
    assert len(late) == 1 and late[0]["cue"] == 2 and late[0]["lag_sec"] == pytest.approx(1.0)
    s.end_layout_check()
    report = json.loads((tmp_path / "layout_report.json").read_text())
    assert [e["lag"] for e in report["cue_log"]] == [pytest.approx(0.2), pytest.approx(1.0)]
    assert report["passed"] is False


def test_unknown_cue_never_crashes(tmp_path):
    s = _scene(tmp_path, {0: [1.0, None]})
    s.begin_segment(0, duration=3.0)
    assert s.cue(5) is False
    assert s.cue(2) is False              # skipped number
    assert s.cue("x") is False
    assert s._sync_tracked == 0.0
    assert s.has_cue(1) and not s.has_cue(2)


def test_cue_outside_a_segment_is_ignored(tmp_path):
    s = _scene(tmp_path, {0: [1.0]})
    assert s.cue(1) is False


def test_segment_with_cues_but_no_cue_call_is_flagged(tmp_path):
    s = _scene(tmp_path, {0: [1.0], 1: []})
    s.begin_segment(0, duration=2.0)
    s.play(run_time=1.0)
    s.begin_segment(1, duration=2.0)
    s.end_layout_check()
    types = [v["type"] for v in s._lc_violations]
    assert types == ["cue_unused"]


def test_template_driven_scene_is_exempt_from_cue_unused(tmp_path):
    s = _scene(tmp_path, {0: [1.0]})
    s._cues_template_driven = True
    s.begin_segment(0, duration=2.0)
    s.end_layout_check()
    assert s._lc_violations == []


def test_cues_load_from_segments_json_in_report_dir(tmp_path):
    (tmp_path / "segments.json").write_text(json.dumps([
        {"text": "a", "actual_duration_sec": 3.0, "cues": [0.75]},
    ]))
    s = _FakeScene(tmp_path)
    s.begin_segment(0, duration=3.0)
    s.cue(1)
    assert s._sync_tracked == pytest.approx(0.75)


def test_cues_accept_a_list_of_lists(tmp_path):
    s = _scene(tmp_path, [[0.5]])
    s.begin_segment(0, duration=1.0)
    s.cue(1)
    assert s._sync_tracked == pytest.approx(0.5)


# ── layout_checker stub ──────────────────────────────────────────────────────

def test_layout_checker_stub_estimates_cue_times(tmp_path):
    from pipeline.agents.layout_checker import layout_checker
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    state = {
        "run_id": "r1", "code_attempts": 0, "manim_code": "pass",
        "script_segments": [
            {"text": "aaaa bbbb cccc!", "cue_text": "aaaa [[1]] bbbb [[2]] cccc!",
             "estimated_duration_sec": 3.0},
            {"text": "Plain.", "estimated_duration_sec": 1.0},
        ],
    }

    async def _communicate():
        (run_dir / "layout_report.json").write_text(json.dumps({"passed": True, "violations": []}))
        return b"", b""

    proc = MagicMock()
    proc.communicate = AsyncMock(side_effect=_communicate)
    with patch("pipeline.agents.layout_checker.OUTPUT_DIR", str(tmp_path)), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        asyncio.run(layout_checker(state))
    stub = json.loads((run_dir / "segments.json").read_text())
    # Estimated at the default (relaxed) pace: lead-in, then proportional
    # through the speech at the delivery speed (no pause points in this text).
    from pipeline.pacing import PRESETS
    p = PRESETS["relaxed"]
    assert stub[0]["cues"] == pytest.approx([p.lead_in + 1.0 / p.speech_speed,
                                             p.lead_in + 2.0 / p.speech_speed], abs=0.002)
    assert stub[0]["actual_duration_sec"] == pytest.approx(p.lead_in + 3.0 / p.speech_speed + p.hold, abs=0.002)
    assert stub[0]["text"] == "aaaa bbbb cccc!"
    assert stub[1]["cues"] == []


def test_layout_checker_stub_keeps_measured_cues():
    from pipeline.agents.layout_checker import _stub_segment
    seg = {"text": "a b", "cue_text": "a [[1]] b", "actual_duration_sec": 2.0,
           "estimated_duration_sec": 2.0, "cues": [1.7]}
    assert _stub_segment(seg) == {"text": "a b", "actual_duration_sec": 2.0, "cues": [1.7]}


# ── TTS backends ─────────────────────────────────────────────────────────────

def test_elevenlabs_returns_cue_times_from_alignment(tmp_path, monkeypatch):
    from pipeline.tts import elevenlabs_tts
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        assert request.url.path.endswith("/with-timestamps")
        pcm = b"\x00\x00" * 72000                       # 3.0 s
        payload = {"audio_base64": base64.b64encode(pcm).decode(),
                   "alignment": _el_alignment(body["text"]),
                   "normalized_alignment": _el_alignment(body["text"])}
        return httpx.Response(200, json=payload, headers={"request-id": f"r{len(bodies)}"})

    real = httpx.Client
    with patch.object(elevenlabs_tts.httpx, "Client",
                      side_effect=lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)})):
        segs = [{"text": _EL_TEXT, "cue_text": "Take [[1]] e to the x, then [[2]] 12 more.",
                 "estimated_duration_sec": 3.0}]
        path, durs, cues = asyncio.run(elevenlabs_tts.generate_audio(segs, tmp_path / "v.wav",
                                                                     model="eleven_v4"))
    assert bodies[0]["text"] == _EL_TEXT                # markers never reach the voice
    assert durs == [3.0]
    assert cues == [[pytest.approx(0.5), pytest.approx(2.2)]]


class _Tok:
    def __init__(self, text, start_ts):
        self.text, self.start_ts = text, start_ts


class _Result:
    """Stand-in for KPipeline.Result: unpacks to (gs, ps, audio), has .tokens."""
    def __init__(self, text, tokens, seconds):
        self.graphemes, self.tokens = text, tokens
        self.audio = np.zeros(int(24000 * seconds), dtype=np.float32)

    def __iter__(self):
        return iter((self.graphemes, "", self.audio))


def test_kokoro_returns_cue_times_from_tokens(tmp_path):
    def pipeline(text, voice):
        assert "[[" not in text
        # Two chunks; token times are relative to each chunk.
        yield _Result("The derivative", [_Tok("The", 0.0), _Tok("derivative", 0.2)], 1.0)
        yield _Result("of e to the x.", [_Tok("of", 0.0), _Tok("e", 0.3), _Tok("x", 0.8)], 1.0)

    with patch("pipeline.tts.kokoro_tts.KPipeline") as KP:
        KP.return_value = pipeline
        from pipeline.tts.kokoro_tts import generate_audio
        segs = [{"text": "The derivative of e to the x.",
                 "cue_text": "The [[1]] derivative of [[2]] e to the x.",
                 "estimated_duration_sec": 2.0}]
        _, durs, cues = asyncio.run(generate_audio(segs, tmp_path / "v.wav"))
    assert durs == [pytest.approx(2.0)]
    assert cues == [[pytest.approx(0.2), pytest.approx(1.3)]]


# ── render_trigger ───────────────────────────────────────────────────────────

def _rt_state(run_id):
    return {
        "run_id": run_id, "topic": "t", "manim_code": "pass", "script": "Hello [[1]] there.",
        "script_segments": [{"text": "Hello there.", "cue_text": "Hello [[1]] there.",
                             "estimated_duration_sec": 1.0}],
    }


def test_render_trigger_two_tuple_backend_gets_proportional_cues(tmp_path):
    from pipeline.render_trigger import render_trigger

    async def gen(segments, output_path, speed=1.0, **kw):
        output_path.write_bytes(b"\x00")
        return output_path, [2.4]

    with patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)), \
         patch("pipeline.render_trigger.get_backend", return_value=gen):
        asyncio.run(render_trigger(_rt_state("a")))
    seg = json.loads((tmp_path / "a" / "segments.json").read_text())[0]
    assert seg["text"] == "Hello there."
    assert seg["cue_text"] == "Hello [[1]] there."
    assert seg["cues"] == [pytest.approx(2.4 * 6 / 12)]
    assert (tmp_path / "a" / "script.txt").read_text() == "Hello there."


def test_render_trigger_three_tuple_backend_cues_are_written(tmp_path):
    from pipeline.render_trigger import render_trigger

    async def gen(segments, output_path, speed=1.0, **kw):
        output_path.write_bytes(b"\x00")
        return output_path, [2.4], [[0.9]]

    with patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)), \
         patch("pipeline.render_trigger.get_backend", return_value=gen):
        asyncio.run(render_trigger(_rt_state("b")))
    seg = json.loads((tmp_path / "b" / "segments.json").read_text())[0]
    assert seg["cues"] == [0.9]


# ── agents ───────────────────────────────────────────────────────────────────

def test_script_agent_strips_markers_and_keeps_cue_text(base_state):
    segments = [{"text": "The [[1]] slope is [[2]] two.", "estimated_duration_sec": 2.0}]
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text=json.dumps(
        {"title": "T", "script": "The [[1]] slope is two.", "segments": segments,
         "needs_web_search": False}))]
    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = msg
        from pipeline.agents.script_agent import script_agent
        result = asyncio.run(script_agent(base_state))
    assert result["script"] == "The slope is two."
    seg = result["script_segments"][0]
    assert seg["text"] == "The slope is two."
    assert seg["cue_text"] == "The [[1]] slope is [[2]] two."


def test_fact_validator_sees_no_markers(base_state):
    base_state["script"] = "The [[1]] slope."
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text=json.dumps({"verdict": "approved", "feedback": ""}))]
    client = MagicMock()
    client.messages.create.return_value = msg
    from pipeline.agents.fact_validator import fact_validator
    asyncio.run(fact_validator(base_state, client=client))
    sent = json.dumps(client.messages.create.call_args.kwargs["messages"])
    assert "[[1]]" not in sent and "The slope." in sent


def test_manim_prompt_shows_markers_and_cue_times():
    from pipeline.agents.manim_agent import _format_segments, SYSTEM_PROMPT
    out = _format_segments([
        {"text": "aaaa bbbb cccc!", "cue_text": "aaaa [[1]] bbbb [[2]] cccc!",
         "estimated_duration_sec": 3.0},
        {"text": "Plain.", "estimated_duration_sec": 1.0},
    ])
    assert "aaaa [[1]] bbbb [[2]] cccc!" in out
    assert "[[1]]~1.6s, [[2]]~2.6s" in out   # relaxed: 0.5s lead-in, 0.94x delivery
    assert "then hold still" in out
    assert "self.cue(" in SYSTEM_PROMPT
