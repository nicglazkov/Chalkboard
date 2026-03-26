# tests/test_fact_validator.py
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.state import PipelineState
from pipeline.agents.fact_validator import fact_validator


def _mock_response(verdict: str, feedback: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({"verdict": verdict, "feedback": feedback}))]
    return msg


def test_fact_validator_approved(base_state):
    base_state["script"] = "B-trees are self-balancing trees."
    mock_resp = _mock_response("approved", "Accurate.")

    with patch("pipeline.agents.fact_validator.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        result = fact_validator(base_state)

    assert result["fact_feedback"] == "Accurate."
    assert result["script_attempts"] == 0  # not incremented on pass


def test_fact_validator_needs_revision_increments_attempts(base_state):
    base_state["script"] = "B-trees are hash maps."
    base_state["script_attempts"] = 1
    mock_resp = _mock_response("needs_revision", "B-trees are not hash maps.")

    with patch("pipeline.agents.fact_validator.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        result = fact_validator(base_state)

    assert result["script_attempts"] == 2
    assert "hash maps" in result["fact_feedback"]


def test_fact_validator_effort_low_uses_light_prompt(base_state):
    base_state["script"] = "Some script."
    base_state["effort_level"] = "low"
    mock_resp = _mock_response("approved", "OK")

    with patch("pipeline.agents.fact_validator.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_resp
        fact_validator(base_state)

    call_args = client_instance.messages.create.call_args
    messages = call_args.kwargs["messages"]
    assert "light" in messages[0]["content"].lower() or "obvious" in messages[0]["content"].lower()
