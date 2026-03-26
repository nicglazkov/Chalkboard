import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from pipeline.graph import build_graph


def _make_script_response():
    import json
    return MagicMock(content=[MagicMock(text=json.dumps({
        "script": "B-trees are balanced trees.",
        "segments": [{"text": "B-trees are balanced.", "estimated_duration_sec": 2.0}],
        "needs_web_search": False,
    }))])


def _make_approved_response():
    import json
    return MagicMock(content=[MagicMock(text=json.dumps({
        "verdict": "approved", "feedback": "Looks good."
    }))])


def _make_manim_response():
    import json
    code = "from manim import *\nclass ChalkboardScene(Scene):\n    def construct(self):\n        self.wait(2.0)"
    return MagicMock(content=[MagicMock(text=json.dumps({"manim_code": code}))])


def test_graph_happy_path_reaches_approved(tmp_path):
    """Full pipeline run with all validators approving on first try."""
    async def mock_tts(segments, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as ScriptClaude, \
         patch("pipeline.agents.fact_validator.anthropic.Anthropic") as FactClaude, \
         patch("pipeline.agents.manim_agent.anthropic.Anthropic") as ManimClaude, \
         patch("pipeline.agents.code_validator.anthropic.Anthropic") as CodeClaude, \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        ScriptClaude.return_value.messages.create.return_value = _make_script_response()
        FactClaude.return_value.messages.create.return_value = _make_approved_response()
        ManimClaude.return_value.messages.create.return_value = _make_manim_response()
        CodeClaude.return_value.messages.create.return_value = _make_approved_response()

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
    import json

    fail_resp = MagicMock(content=[MagicMock(text=json.dumps({
        "verdict": "needs_revision", "feedback": "Claim X is wrong."
    }))])

    call_counts = {"fact": 0}
    def fact_side_effect(**kwargs):
        call_counts["fact"] += 1
        return fail_resp if call_counts["fact"] == 1 else _make_approved_response()

    async def mock_tts(segments, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as ScriptClaude, \
         patch("pipeline.agents.fact_validator.anthropic.Anthropic") as FactClaude, \
         patch("pipeline.agents.manim_agent.anthropic.Anthropic") as ManimClaude, \
         patch("pipeline.agents.code_validator.anthropic.Anthropic") as CodeClaude, \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        ScriptClaude.return_value.messages.create.return_value = _make_script_response()
        FactClaude.return_value.messages.create.side_effect = lambda **kw: fact_side_effect(**kw)
        ManimClaude.return_value.messages.create.return_value = _make_manim_response()
        CodeClaude.return_value.messages.create.return_value = _make_approved_response()

        graph = build_graph()

        config = {"configurable": {"thread_id": "test-retry-script"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "low"},
            config=config,
        ))

    assert result["status"] == "approved"
    assert call_counts["fact"] == 2
