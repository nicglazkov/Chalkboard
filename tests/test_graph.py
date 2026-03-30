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
    return {"fact_feedback": None, "status": "validating"}  # None = approved


def _make_manim_state():
    code = "from manim import *\nclass ChalkboardScene(Scene):\n    def construct(self):\n        self.wait(2.0)"
    return {"manim_code": code, "status": "validating"}


def _make_code_approved_state():
    return {"code_feedback": None, "code_attempts": 0}  # None = approved


def test_graph_happy_path_reaches_approved(tmp_path):
    """Full pipeline run with all validators approving on first try."""
    async def mock_script_agent(state, **kw):
        return _make_script_state()

    async def mock_fact_validator(state, **kw):
        return _make_approved_state()

    async def mock_manim_agent(state, **kw):
        return _make_manim_state()

    async def mock_code_validator(state, **kw):
        return _make_code_approved_state()

    async def mock_tts(segments, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
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

    async def mock_script_agent(state, **kw):
        return _make_script_state()

    async def mock_fact_validator(state, **kw):
        call_counts["fact"] += 1
        if call_counts["fact"] == 1:
            return {
                "fact_feedback": "Claim X is wrong.",
                "script_attempts": state.get("script_attempts", 0) + 1,
            }
        return {"fact_feedback": None, "status": "validating"}

    async def mock_manim_agent(state, **kw):
        return _make_manim_state()

    async def mock_code_validator(state, **kw):
        return _make_code_approved_state()

    async def mock_tts(segments, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
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


def test_graph_escalates_after_max_retries(tmp_path):
    """After 3 script failures, escalation is triggered and abort sets status=failed."""
    call_counts = {"fact": 0, "script": 0}

    async def mock_script_agent(state, **kw):
        call_counts["script"] += 1
        return _make_script_state()

    async def mock_fact_validator(state, **kw):
        call_counts["fact"] += 1
        attempts = state.get("script_attempts", 0) + 1
        return {
            "fact_feedback": "Wrong claim.",
            "script_attempts": attempts,
        }

    abort_payload = {"action": "abort", "guidance": ""}

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.agents.orchestrator.interrupt", return_value=abort_payload), \
         patch("pipeline.render_trigger.get_backend"), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-escalate-abort"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "low"},
            config=config,
        ))

    assert result["status"] == "failed"
    assert call_counts["fact"] == 3  # ran exactly 3 times before escalation
