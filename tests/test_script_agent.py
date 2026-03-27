# tests/test_script_agent.py
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.state import PipelineState
from tests.conftest import base_state


def _make_claude_response(script_text: str, segments: list[dict]) -> MagicMock:
    content = json.dumps({"script": script_text, "segments": segments, "needs_web_search": False})
    msg = MagicMock()
    msg.content = [MagicMock(text=content)]
    return msg


def test_script_agent_returns_script_and_segments(base_state):
    segments = [{"text": "B-trees are balanced.", "estimated_duration_sec": 1.2}]
    mock_response = _make_claude_response("B-trees are balanced.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        result = script_agent(base_state)

    assert result["script"] == "B-trees are balanced."
    assert len(result["script_segments"]) == 1
    assert result["status"] == "validating"


def test_script_agent_sets_needs_web_search_when_flagged(base_state):
    content = json.dumps({
        "script": "Quantum entanglement...",
        "segments": [{"text": "Quantum entanglement...", "estimated_duration_sec": 2.0}],
        "needs_web_search": True,
    })
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=content)]

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        result = script_agent(base_state)

    assert result["needs_web_search"] is True


def test_script_agent_includes_feedback_in_revision(base_state):
    base_state["fact_feedback"] = "Claim X is incorrect"
    base_state["script_attempts"] = 1
    segments = [{"text": "Revised script.", "estimated_duration_sec": 1.0}]
    mock_response = _make_claude_response("Revised script.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        script_agent(base_state)

    call_args = client_instance.messages.create.call_args
    messages = call_args.kwargs["messages"]
    user_content = messages[0]["content"]
    assert "Claim X is incorrect" in user_content


def test_script_agent_includes_audience_in_prompt(base_state):
    base_state["audience"] = "expert"
    segments = [{"text": "Expert content.", "estimated_duration_sec": 1.0}]
    mock_response = _make_claude_response("Expert content.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        script_agent(base_state)

    user_content = client_instance.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "expert" in user_content.lower()


def test_script_agent_uses_default_audience_when_not_set(base_state):
    base_state.pop("audience", None)
    segments = [{"text": "Default audience.", "estimated_duration_sec": 1.0}]
    mock_response = _make_claude_response("Default audience.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        script_agent(base_state)

    user_content = client_instance.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "intermediate" in user_content.lower()


def test_script_agent_includes_tone_in_prompt(base_state):
    base_state["tone"] = "socratic"
    segments = [{"text": "Socratic content.", "estimated_duration_sec": 1.0}]
    mock_response = _make_claude_response("Socratic content.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        script_agent(base_state)

    user_content = client_instance.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "question" in user_content.lower()


def test_script_agent_uses_default_tone_when_not_set(base_state):
    base_state.pop("tone", None)
    segments = [{"text": "Casual content.", "estimated_duration_sec": 1.0}]
    mock_response = _make_claude_response("Casual content.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        script_agent(base_state)

    user_content = client_instance.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "conversational" in user_content.lower()
