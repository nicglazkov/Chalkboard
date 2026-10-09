# tests/test_research_agent.py
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.agents.research_agent import research_agent

DUMMY_BRIEF = "B-trees are self-balancing search trees used in databases."
DUMMY_SOURCES = ["https://en.wikipedia.org/wiki/B-tree"]


def _mock_response(brief=DUMMY_BRIEF, sources=None):
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text=json.dumps({
        "research_brief": brief,
        "sources": sources or DUMMY_SOURCES,
        "search_warning": None,
    }))]
    return msg


def test_research_agent_returns_brief(base_state):
    base_state["effort_level"] = "high"
    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = _mock_response()
        result = asyncio.run(research_agent(base_state))
    assert result["research_brief"] == DUMMY_BRIEF
    assert result["research_sources"] == DUMMY_SOURCES


def test_research_agent_uses_web_search_tool(base_state):
    base_state["effort_level"] = "high"
    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = _mock_response()
        asyncio.run(research_agent(base_state))
    call_kwargs = instance.messages.stream.call_args.kwargs
    tools = call_kwargs.get("tools", [])
    assert any(t.get("name") == "web_search" for t in tools)


def test_research_agent_includes_topic_in_message(base_state):
    base_state["topic"] = "explain quicksort"
    base_state["effort_level"] = "high"
    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = _mock_response()
        asyncio.run(research_agent(base_state))
    messages = instance.messages.stream.call_args.kwargs["messages"]
    assert "quicksort" in messages[0]["content"]


def test_research_agent_accepts_injected_client(base_state):
    """Allows callers to inject a mock client (same pattern as other agents)."""
    mock_client = MagicMock()
    mock_client.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = _mock_response()
    result = asyncio.run(research_agent(base_state, client=mock_client))
    assert result["research_brief"] == DUMMY_BRIEF
    mock_client.messages.stream.assert_called_once()
