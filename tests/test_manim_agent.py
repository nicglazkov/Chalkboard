# tests/test_manim_agent.py
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.agents.manim_agent import manim_agent


def _mock_response(code: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({"manim_code": code}))]
    return msg


VALID_SCENE = '''
from manim import *
import json
from pathlib import Path

class ChalkboardScene(Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 1 - len(_d))
        title = Text("B-Trees")
        self.play(Write(title), run_time=1.0)
        self.wait(max(0.0, _d[0] - 1.0))
'''


def test_manim_agent_generates_chalkboard_scene(base_state):
    base_state["script"] = "B-trees are balanced search trees."
    base_state["script_segments"] = [{"text": "B-trees are balanced.", "estimated_duration_sec": 2.0}]
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        result = asyncio.run(manim_agent(base_state))

    assert "ChalkboardScene" in result["manim_code"]
    assert result["status"] == "validating"


def test_manim_agent_includes_durations_in_prompt(base_state):
    base_state["script"] = "Hello world."
    base_state["script_segments"] = [
        {"text": "Hello.", "estimated_duration_sec": 1.5},
        {"text": "World.", "estimated_duration_sec": 2.3},
    ]
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_resp
        asyncio.run(manim_agent(base_state))

    call_args = client_instance.messages.create.call_args
    messages = call_args.kwargs["messages"]
    content = messages[0]["content"]
    assert "1.5" in content
    assert "2.3" in content


def test_manim_agent_includes_feedback_on_revision(base_state):
    base_state["script"] = "Hello world."
    base_state["script_segments"] = [{"text": "Hello.", "estimated_duration_sec": 1.0}]
    base_state["code_feedback"] = "Missing import for MathTex"
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_resp
        asyncio.run(manim_agent(base_state))

    call_args = client_instance.messages.create.call_args
    messages = call_args.kwargs["messages"]
    assert "Missing import for MathTex" in messages[0]["content"]


def test_manim_agent_includes_theme_colors_in_prompt(base_state):
    base_state["script"] = "B-trees are balanced search trees."
    base_state["script_segments"] = [{"text": "B-trees.", "estimated_duration_sec": 2.0}]
    base_state["theme"] = "light"
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_resp
        asyncio.run(manim_agent(base_state))

    content = client_instance.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "#FAFAFA" in content  # light theme background


def test_manim_agent_colorful_theme_in_prompt(base_state):
    base_state["script"] = "B-trees are balanced search trees."
    base_state["script_segments"] = [{"text": "B-trees.", "estimated_duration_sec": 2.0}]
    base_state["theme"] = "colorful"
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_resp
        asyncio.run(manim_agent(base_state))

    content = client_instance.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "vibrant" in content.lower()


def test_manim_agent_defaults_to_chalkboard_theme(base_state):
    base_state["script"] = "B-trees are balanced search trees."
    base_state["script_segments"] = [{"text": "B-trees.", "estimated_duration_sec": 2.0}]
    base_state.pop("theme", None)
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_resp
        asyncio.run(manim_agent(base_state))

    content = client_instance.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "#1C1C1C" in content  # chalkboard theme background


def test_manim_agent_with_context_blocks_sends_list_content(base_state):
    base_state["script"] = "Script about trees."
    base_state["script_segments"] = [{"text": "Trees.", "estimated_duration_sec": 2.0}]
    context_blocks = [
        {"type": "text", "text": "--- file: diagram.py ---"},
        {"type": "text", "text": "class Tree: pass"},
    ]
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"manim_code": "from manim import *"}')]

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.manim_agent import manim_agent
        asyncio.run(manim_agent(base_state, context_blocks=context_blocks))

    call_args = client_instance.messages.create.call_args
    content = call_args.kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    assert any("source material" in b.get("text", "") for b in content)
    assert any("class Tree" in b.get("text", "") for b in content)


def test_manim_agent_without_context_blocks_sends_string_content(base_state):
    base_state["script"] = "Script."
    base_state["script_segments"] = [{"text": "S.", "estimated_duration_sec": 1.0}]
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"manim_code": "from manim import *"}')]

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.manim_agent import manim_agent
        asyncio.run(manim_agent(base_state))

    call_args = client_instance.messages.create.call_args
    content = call_args.kwargs["messages"][0]["content"]
    assert isinstance(content, str)
