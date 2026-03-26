# tests/test_graph.py
import json
import pytest
import asyncio
from unittest.mock import patch, MagicMock
from pipeline.graph import build_graph


def _make_script_state():
    return {
        "script": "B-trees are balanced trees.",
        "script_segments": [{"text": "B-trees are balanced.", "estimated_duration_sec": 2.0}],
        "needs_web_search": False,
        "status": "validating",
    }


def _make_approved_state():
    return {"fact_feedback": "Looks good.", "status": "validating"}


def _make_manim_state():
    code = "from manim import *\nclass ChalkboardScene(Scene):\n    def construct(self):\n        self.wait(2.0)"
    return {"manim_code": code, "status": "validating"}


def _make_code_approved_state():
    return {"code_feedback": "Looks good.", "code_attempts": 0}


def test_graph_happy_path_reaches_approved(tmp_path):
    """Full pipeline run with all validators approving on first try."""
    async def mock_tts(segments, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.script_agent", return_value=_make_script_state()) as MockScript, \
         patch("pipeline.graph.fact_validator", return_value=_make_approved_state()) as MockFact, \
         patch("pipeline.graph.manim_agent", return_value=_make_manim_state()) as MockManim, \
         patch("pipeline.graph.code_validator", return_value=_make_code_approved_state()) as MockCode, \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-happy-path"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "low"},
            config=config,
        ))

    assert result["status"] == "approved"
    assert (tmp_path / "test-happy-path" / "scene.py").exists()


def test_graph_retries_script_on_fact_failure(tmp_path):
    """Script validator fails once, then passes on second attempt."""
    call_counts = {"fact": 0}

    def fact_side_effect(state, **kw):
        call_counts["fact"] += 1
        if call_counts["fact"] == 1:
            return {
                "fact_feedback": "Claim X is wrong.",
                "script_attempts": state.get("script_attempts", 0) + 1,
            }
        return {"fact_feedback": None, "status": "validating"}

    async def mock_tts(segments, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.script_agent", return_value=_make_script_state()), \
         patch("pipeline.graph.fact_validator", side_effect=fact_side_effect), \
         patch("pipeline.graph.manim_agent", return_value=_make_manim_state()), \
         patch("pipeline.graph.code_validator", return_value=_make_code_approved_state()), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-retry-script"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "low"},
            config=config,
        ))

    assert result["status"] == "approved"
    assert call_counts["fact"] == 2
