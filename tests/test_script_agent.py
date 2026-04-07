# tests/test_script_agent.py
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.state import PipelineState
from tests.conftest import base_state


def _make_claude_response(script_text: str, segments: list[dict]) -> MagicMock:
    content = json.dumps({"script": script_text, "segments": segments, "needs_web_search": False})
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text=content)]
    return msg


def test_script_agent_returns_script_and_segments(base_state):
    segments = [{"text": "B-trees are balanced.", "estimated_duration_sec": 1.2}]
    mock_response = _make_claude_response("B-trees are balanced.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        result = asyncio.run(script_agent(base_state))

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
    mock_response.content = [MagicMock(type="text", text=content)]

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        result = asyncio.run(script_agent(base_state))

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
        asyncio.run(script_agent(base_state))

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
        asyncio.run(script_agent(base_state))

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
        asyncio.run(script_agent(base_state))

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
        asyncio.run(script_agent(base_state))

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
        asyncio.run(script_agent(base_state))

    user_content = client_instance.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "conversational" in user_content.lower()


def test_script_agent_with_context_blocks_sends_list_content(base_state):
    context_blocks = [
        {"type": "text", "text": "--- file: notes.txt ---"},
        {"type": "text", "text": "Important source material"},
    ]
    segments = [{"text": "Script.", "estimated_duration_sec": 1.0}]
    mock_response = _make_claude_response("Script.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        asyncio.run(script_agent(base_state, context_blocks=context_blocks))

    call_args = client_instance.messages.create.call_args
    content = call_args.kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    assert any("source material" in b.get("text", "") for b in content)
    assert any("Important source material" in b.get("text", "") for b in content)


def test_script_agent_without_context_blocks_sends_string_content(base_state):
    segments = [{"text": "Script.", "estimated_duration_sec": 1.0}]
    mock_response = _make_claude_response("Script.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        asyncio.run(script_agent(base_state))

    call_args = client_instance.messages.create.call_args
    content = call_args.kwargs["messages"][0]["content"]
    assert isinstance(content, str)


def _mock_response():
    content = json.dumps({
        "script": "Test script.",
        "segments": [{"text": "Test script.", "estimated_duration_sec": 1.0}],
        "needs_web_search": False,
    })
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text=content)]
    return msg


def test_research_brief_injected_into_message(base_state):
    """When research_brief is set, it appears in the user message."""
    import anthropic as _anthropic
    base_state["research_brief"] = "B-trees store multiple keys per node."
    base_state["research_sources"] = ["https://example.com"]
    base_state["script"] = ""
    base_state["script_segments"] = []

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.create.return_value = _mock_response()
        from pipeline.agents.script_agent import script_agent
        asyncio.run(script_agent(base_state))

    messages = instance.messages.create.call_args.kwargs["messages"]
    content = messages[0]["content"]  # no context_blocks passed, so always a str
    assert "B-trees store multiple keys per node." in content


def test_web_search_disabled_when_brief_present(base_state):
    """When research_brief is set, script_agent must not enable web_search (research already done)."""
    import anthropic as _anthropic
    base_state["effort_level"] = "high"
    base_state["research_brief"] = "Some research."
    base_state["research_sources"] = []

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.create.return_value = _mock_response()
        from pipeline.agents.script_agent import script_agent
        asyncio.run(script_agent(base_state))

    call_kwargs = instance.messages.create.call_args.kwargs
    tools = call_kwargs.get("tools", _anthropic.NOT_GIVEN)
    if tools is not _anthropic.NOT_GIVEN:
        assert not any(t.get("type") == "web_search_20250305" for t in (tools or []))
