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


async def _mock_layout_checker_pass(state, **kw):
    return {"code_feedback": None}  # always passes


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

    async def mock_tts(segments, path, speed=1.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.graph.layout_checker", new=_mock_layout_checker_pass), \
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

    async def mock_tts(segments, path, speed=1.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.graph.layout_checker", new=_mock_layout_checker_pass), \
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

    async def mock_to_thread(fn, *args, **kwargs):
        return "abort"

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("asyncio.to_thread", new=mock_to_thread), \
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


def test_high_effort_routes_through_research_agent(tmp_path):
    """With effort_level=high, graph must pass through research_agent before script_agent."""
    visited = []

    async def mock_research_agent(state, **kw):
        visited.append("research_agent")
        return {"research_brief": "Facts about B-trees.", "research_sources": []}

    async def mock_script_agent(state, **kw):
        visited.append("script_agent")
        return _make_script_state()

    async def mock_fact_validator(state, **kw):
        return _make_approved_state()

    async def mock_manim_agent(state, **kw):
        return _make_manim_state()

    async def mock_code_validator(state, **kw):
        return _make_code_approved_state()

    async def mock_tts(segments, path, speed=1.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.research_agent", new=mock_research_agent), \
         patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.graph.layout_checker", new=_mock_layout_checker_pass), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-research-routing"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "high"},
            config=config,
        ))

    assert result["status"] == "approved"
    assert visited.index("research_agent") < visited.index("script_agent")


def test_medium_effort_skips_research_agent(tmp_path):
    """With effort_level=medium, graph goes directly to script_agent."""
    visited = []

    async def mock_research_agent(state, **kw):
        visited.append("research_agent")
        return {"research_brief": "should not be called", "research_sources": []}

    async def mock_script_agent(state, **kw):
        visited.append("script_agent")
        return _make_script_state()

    async def mock_fact_validator(state, **kw):
        return _make_approved_state()

    async def mock_manim_agent(state, **kw):
        return _make_manim_state()

    async def mock_code_validator(state, **kw):
        return _make_code_approved_state()

    async def mock_tts(segments, path, speed=1.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.research_agent", new=mock_research_agent), \
         patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.graph.layout_checker", new=_mock_layout_checker_pass), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-skip-research"}}
        asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "medium"},
            config=config,
        ))

    assert "research_agent" not in visited
    assert "script_agent" in visited


def test_non_interactive_escalates_to_failed(tmp_path):
    """With interactive=False, escalation auto-aborts instead of prompting stdin."""
    call_counts = {"fact": 0, "script": 0}

    async def mock_script_agent(state, **kw):
        call_counts["script"] += 1
        return _make_script_state()

    async def mock_fact_validator(state, **kw):
        call_counts["fact"] += 1
        attempts = state.get("script_attempts", 0) + 1
        return {"fact_feedback": "Wrong claim.", "script_attempts": attempts}

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.render_trigger.get_backend"), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-non-interactive-escalate"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "low", "interactive": False},
            config=config,
        ))

    assert result["status"] == "failed"
    assert call_counts["fact"] == 3  # ran 3 times before escalation


def test_graph_layout_checker_passes_reaches_render_trigger(tmp_path):
    """layout_checker passes → pipeline proceeds to render_trigger."""
    async def mock_script_agent(state, **kw):
        return {
            "script": "Test.", "script_segments": [{"text": "Test.", "estimated_duration_sec": 2.0}],
            "needs_web_search": False, "status": "validating",
        }
    async def mock_fact_validator(state, **kw):
        return {"fact_feedback": None, "status": "validating"}
    async def mock_manim_agent(state, **kw):
        return {"manim_code": "from manim import *\nclass ChalkboardScene(ChalkboardSceneBase, Scene): pass", "status": "validating"}
    async def mock_code_validator(state, **kw):
        return {"code_feedback": None, "code_attempts": 0}
    async def mock_layout_checker(state, **kw):
        return {"code_feedback": None}  # passed
    async def mock_tts(segments, path, speed=1.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.graph.layout_checker", new=mock_layout_checker), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-lc-pass"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "test", "effort_level": "low"}, config=config,
        ))

    assert result["status"] == "approved"


def test_graph_layout_checker_failure_retries_manim_agent(tmp_path):
    """layout_checker fails → manim_agent is retried with code_feedback."""
    call_counts = {"layout": 0, "manim": 0}

    async def mock_script_agent(state, **kw):
        return {
            "script": "Test.", "script_segments": [{"text": "Test.", "estimated_duration_sec": 2.0}],
            "needs_web_search": False, "status": "validating",
        }
    async def mock_fact_validator(state, **kw):
        return {"fact_feedback": None, "status": "validating"}
    async def mock_manim_agent(state, **kw):
        call_counts["manim"] += 1
        return {"manim_code": "from manim import *\nclass ChalkboardScene(ChalkboardSceneBase, Scene): pass", "status": "validating"}
    async def mock_code_validator(state, **kw):
        return {"code_feedback": None, "code_attempts": state.get("code_attempts", 0)}
    async def mock_layout_checker(state, **kw):
        call_counts["layout"] += 1
        if call_counts["layout"] == 1:
            return {"code_feedback": "Overlap in segment 0", "code_attempts": state.get("code_attempts", 0) + 1}
        return {"code_feedback": None}
    async def mock_tts(segments, path, speed=1.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.graph.layout_checker", new=mock_layout_checker), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-lc-retry"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "test", "effort_level": "low"}, config=config,
        ))

    assert call_counts["manim"] == 2   # retried once
    assert call_counts["layout"] == 2  # checked twice
    assert result["status"] == "approved"


def test_graph_layout_checker_escalates_at_max_attempts(tmp_path):
    """layout_checker fails 3 times → escalate_to_user with status=failed."""
    async def mock_script_agent(state, **kw):
        return {
            "script": "Test.", "script_segments": [{"text": "Test.", "estimated_duration_sec": 2.0}],
            "needs_web_search": False, "status": "validating",
        }
    async def mock_fact_validator(state, **kw):
        return {"fact_feedback": None, "status": "validating"}
    async def mock_manim_agent(state, **kw):
        return {"manim_code": "from manim import *\nclass ChalkboardScene(ChalkboardSceneBase, Scene): pass", "status": "validating"}
    async def mock_code_validator(state, **kw):
        return {"code_feedback": None, "code_attempts": state.get("code_attempts", 0)}
    async def mock_layout_checker(state, **kw):
        attempts = state.get("code_attempts", 0)
        return {"code_feedback": "Overlap in segment 0", "code_attempts": attempts + 1}

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.graph.layout_checker", new=mock_layout_checker):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-lc-escalate"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "test", "effort_level": "low", "interactive": False},
            config=config,
        ))

    assert result["status"] == "failed"
