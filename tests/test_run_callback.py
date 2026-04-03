# tests/test_run_callback.py
import asyncio
import pytest
from unittest.mock import patch, MagicMock
from main import run


def _make_script_state():
    return {
        "script": "Test.",
        "script_segments": [{"text": "Test.", "estimated_duration_sec": 1.0}],
        "needs_web_search": False,
        "status": "validating",
    }


def test_on_progress_callback_receives_events(tmp_path):
    """on_progress must be called for each pipeline event instead of print."""
    events_received = []

    def capture_progress(event: dict):
        events_received.append(event)

    async def mock_script_agent(state, **kw):
        return _make_script_state()

    async def mock_fact_validator(state, **kw):
        return {"fact_feedback": None, "status": "validating"}

    async def mock_manim_agent(state, **kw):
        code = "from manim import *\nclass ChalkboardScene(Scene):\n    def construct(self): self.wait(1.0)"
        return {"manim_code": code, "status": "validating"}

    async def mock_code_validator(state, **kw):
        return {"code_feedback": None, "code_attempts": 0}

    async def mock_tts(segments, path, speed=1.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [1.0]

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        asyncio.run(run(
            topic="test",
            effort="low",
            thread_id="test-callback",
            on_progress=capture_progress,
        ))

    assert len(events_received) > 0
    # At minimum, script_agent and render_trigger events should be present
    node_names = [list(e.keys())[0] for e in events_received if e and "__end__" not in e]
    assert "script_agent" in node_names
