"""Live telemetry: peeks from the real stream, usage events, render/TTS progress."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pipeline import llm, telemetry


def _usage(inp=120, out=80, searches=None):
    stu = SimpleNamespace(web_search_requests=searches) if searches is not None else None
    return SimpleNamespace(input_tokens=inp, output_tokens=out, server_tool_use=stu,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)


def _final(text, model="claude-opus-5-5", usage=None):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                           model=model, usage=usage or _usage())


class _FakeStream:
    """Mimics MessageStream: iterating yields events with type/snapshot."""
    def __init__(self, chunks, final):
        self.chunks, self.final = chunks, final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        snap = ""
        yield SimpleNamespace(type="content_block_start")
        for c in self.chunks:
            snap += c
            yield SimpleNamespace(type="text", text=c, snapshot=snap)

    def get_final_message(self):
        return self.final


def _collect():
    events = []
    return events, telemetry.Sink(events.append, peek=True)


def test_call_json_streams_peeks_with_growing_text(monkeypatch):
    monkeypatch.setattr(llm, "PEEK_INTERVAL", 0.0)
    doc = json.dumps({"title": "T", "script": "Hello \"world\" and more", "segments": [],
                      "needs_web_search": False})
    chunks = [doc[i:i + 7] for i in range(0, len(doc), 7)]
    client = MagicMock()
    client.messages.stream.return_value = _FakeStream(chunks, _final(doc))
    events, sink = _collect()
    tok = telemetry.set_sink(sink)
    try:
        data, _ = llm.call_json("script", content="x", schema={}, client=client)
    finally:
        telemetry.reset_sink(tok)
    assert data["script"] == 'Hello "world" and more'
    client.messages.create.assert_not_called()
    peeks = [e["updates"] for e in events if e["node"] == "peek"]
    assert len(peeks) >= 3
    texts = [p["text"] for p in peeks]
    assert all(b.startswith(a) for a, b in zip(texts, texts[1:]))   # grows monotonically
    assert peeks[-1] == {"stage": "script", "text": 'Hello "world" and more', "done": True}
    assert all(not p["done"] for p in peeks[:-1])
    usage = [e["updates"] for e in events if e["node"] == "usage"]
    assert usage == [{"agent": "script", "model": "claude-opus-5-5", "input_tokens": 120,
                      "output_tokens": 80, "cache_read_tokens": 0, "cache_write_tokens": 0, "web_searches": 0,
                      "cost_usd": pytest.approx(120 * 4 / 1e6 + 80 * 20 / 1e6),
                      "max_tokens": 16000, "effort": "high", "stop_reason": "end_turn"}]
    assert all("ts" in e for e in events)


def test_peek_throttled(monkeypatch):
    monkeypatch.setattr(llm, "PEEK_INTERVAL", 10.0)
    doc = json.dumps({"verdict": "approved", "feedback": "x" * 200})
    client = MagicMock()
    client.messages.stream.return_value = _FakeStream([doc[i:i + 3] for i in range(0, len(doc), 3)], _final(doc))
    events, sink = _collect()
    tok = telemetry.set_sink(sink)
    try:
        llm.call_json("fact", content="x", schema={}, client=client)
    finally:
        telemetry.reset_sink(tok)
    peeks = [e["updates"] for e in events if e["node"] == "peek"]
    assert len(peeks) == 2  # one live (then throttled) + the final
    assert peeks[-1]["done"] and peeks[-1]["stage"] == "fact_check"


def test_no_sink_keeps_plain_create_path():
    client = MagicMock()
    client.messages.create.return_value = _final('{"a": 1}')
    data, _ = llm.call_json("script", content="x", schema={}, client=client)
    assert data == {"a": 1}
    client.messages.stream.assert_not_called()


def test_record_only_sink_does_not_stream_but_reports_usage():
    client = MagicMock()
    client.messages.create.return_value = _final('{"a": 1}', usage=_usage(10, 5, searches=2))
    events = []
    tok = telemetry.set_sink(telemetry.Sink(events.append))
    try:
        llm.call_json("research", content="x", schema={}, client=client)
    finally:
        telemetry.reset_sink(tok)
    client.messages.stream.assert_not_called()
    (u,) = [e["updates"] for e in events]
    assert u["web_searches"] == 2
    assert u["cost_usd"] == pytest.approx(10 * 4 / 1e6 + 5 * 20 / 1e6 + 0.02)


def test_unknown_model_cost_is_null():
    client = MagicMock()
    client.messages.create.return_value = _final('{"a": 1}', model="claude-future-9")
    events = []
    tok = telemetry.set_sink(telemetry.Sink(events.append))
    try:
        llm.call_json("quiz", content="x", schema={}, client=client)
    finally:
        telemetry.reset_sink(tok)
    assert events[0]["updates"]["cost_usd"] is None


def test_sink_reaches_worker_threads():
    events = []

    async def main():
        tok = telemetry.set_sink(telemetry.Sink(events.append))
        try:
            await asyncio.to_thread(telemetry.emit, "tts", {"status": "running"})
        finally:
            telemetry.reset_sink(tok)

    asyncio.run(main())
    assert events and events[0]["node"] == "tts"


def test_emit_without_sink_is_noop():
    telemetry.emit("usage", {"x": 1})  # must not raise


# ── render progress ───────────────────────────────────────────────────────────

def test_segment_and_animation_line_parsing():
    from main import _parse_manim_line, _parse_segment_line
    assert _parse_segment_line("CB_SEGMENT 3") == 3
    assert _parse_segment_line("CB_SEGMENTS 3") is None
    assert _parse_segment_line("x CB_SEGMENT 3") is None
    assert _parse_manim_line("[10/02/26 15:53:10] INFO     Animation 7 : Partial movie file written") == 7
    assert _parse_manim_line("Animation 2: Create(Circle)") == 2


def test_render_progress_emits_events(monkeypatch):
    from main import _RenderProgress
    events = []
    tok = telemetry.set_sink(telemetry.Sink(events.append))
    try:
        p = _RenderProgress(4)
        p.update(segment=0)
        p.update(animation=1)
        p.update(segment=1)
        p.flush()
    finally:
        telemetry.reset_sink(tok)
    ups = [e["updates"] for e in events]
    assert ups[0] == {"status": "running", "segment": 0, "segments": 4, "animation": None, "animations": None}
    assert ups[-1]["segment"] == 1 and ups[-1]["animation"] == 1


def test_scene_prints_segment_marker_only_in_real_render(capsys, tmp_path):
    from tests.test_chalkboard_base import _FakeScene
    s = _FakeScene(tmp_path)
    s._real_render = lambda: True
    s.begin_segment(0, 2.0)
    assert "CB_SEGMENT 0" in capsys.readouterr().out
    s._real_render = lambda: False
    s.begin_segment(1, 2.0)
    assert "CB_SEGMENT" not in capsys.readouterr().out


def test_layout_report_records_mode(tmp_path):
    from tests.test_chalkboard_base import _FakeScene
    s = _FakeScene(tmp_path)
    s.begin_segment(0, 1.0)
    s.end_layout_check()
    assert json.loads((tmp_path / "layout_report.json").read_text())["mode"] == "dry_run"


# ── TTS progress ──────────────────────────────────────────────────────────────

def test_render_trigger_emits_tts_progress(tmp_path, monkeypatch):
    from pipeline import render_trigger as rt
    monkeypatch.setattr(rt, "OUTPUT_DIR", str(tmp_path))

    async def fake_backend(segments, path, speed=1.0, *, voice=None, model=None, on_segment=None):
        path.write_bytes(b"")
        for i, s in enumerate(segments):
            on_segment(i, len(s["text"]))
        return path, [1.0] * len(segments), [[] for _ in segments]

    monkeypatch.setattr(rt, "get_backend", lambda name: fake_backend)
    state = {"run_id": "r", "topic": "t", "script": "ab cde", "manim_code": "x",
             "script_segments": [{"text": "ab", "estimated_duration_sec": 1.0},
                                 {"text": "cde", "estimated_duration_sec": 1.0}],
             "narrator": "kokoro"}
    events = []
    tok = telemetry.set_sink(telemetry.Sink(events.append))
    try:
        asyncio.run(rt.render_trigger(state))
    finally:
        telemetry.reset_sink(tok)
    tts = [e["updates"] for e in events if e["node"] == "tts"]
    assert tts[0] == {"status": "running", "segments_done": 0, "segments": 2, "chars": 0}
    assert tts[1] == {"status": "running", "segments_done": 1, "segments": 2, "chars": 2}
    assert tts[-1] == {"status": "done", "segments_done": 2, "segments": 2, "chars": 5}


def test_tts_backend_without_callback_reports_given_text(tmp_path, monkeypatch):
    from pipeline import render_trigger as rt
    monkeypatch.setattr(rt, "OUTPUT_DIR", str(tmp_path))

    async def old_backend(segments, path, speed=1.0, *, voice=None, model=None):
        path.write_bytes(b"")
        return path, [1.0] * len(segments)

    monkeypatch.setattr(rt, "get_backend", lambda name: old_backend)
    state = {"run_id": "r", "topic": "t", "script": "abc", "manim_code": "x",
             "script_segments": [{"text": "abc", "estimated_duration_sec": 1.0}], "narrator": "kokoro"}
    events = []
    tok = telemetry.set_sink(telemetry.Sink(events.append))
    try:
        asyncio.run(rt.render_trigger(state))
    finally:
        telemetry.reset_sink(tok)
    assert [e["updates"] for e in events if e["node"] == "tts"][-1]["chars"] == 3


# ── QA persistence ────────────────────────────────────────────────────────────

def test_qa_report_saved_with_history(tmp_path):
    from main import _save_qa_report
    _save_qa_report(tmp_path, {"passed": False, "issues": [{"severity": "error", "description": "x"}]}, 0, "normal")
    _save_qa_report(tmp_path, {"passed": True, "issues": []}, 1, "normal")
    rep = json.loads((tmp_path / "qa_report.json").read_text())
    assert rep["passed"] is True and rep["attempt"] == 1 and rep["checked_at"]
    assert [h["passed"] for h in rep["history"]] == [False, True]
