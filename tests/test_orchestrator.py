# tests/test_orchestrator.py
import asyncio
import pytest
from unittest.mock import patch
from pipeline.state import PipelineState
from pipeline.agents.orchestrator import escalate_to_user


def test_escalate_surfaces_script_feedback(base_state):
    base_state["fact_feedback"] = "Claim X is wrong."
    base_state["script_attempts"] = 3
    base_state["status"] = "validating"

    responses = iter(["retry_script", "Focus on CS accuracy."])

    async def mock_to_thread(fn, *args, **kwargs):
        return next(responses)

    with patch("asyncio.to_thread", new=mock_to_thread):
        result = asyncio.run(escalate_to_user(base_state))

    assert result["script_attempts"] == 0
    assert result["fact_feedback"] == "Focus on CS accuracy."
    assert result.get("code_feedback") is None
    assert result["status"] == "drafting"


def test_escalate_routes_abort(base_state):
    base_state["code_feedback"] = "Can't fix."
    base_state["code_attempts"] = 3

    async def mock_to_thread(fn, *args, **kwargs):
        return "abort"

    with patch("asyncio.to_thread", new=mock_to_thread):
        result = asyncio.run(escalate_to_user(base_state))

    assert result["status"] == "failed"


def test_escalate_retry_code_resets_code_attempts(base_state):
    base_state["code_feedback"] = "Wrong API."
    base_state["code_attempts"] = 3

    responses = iter(["retry_code", "Use MathTex for equations."])

    async def mock_to_thread(fn, *args, **kwargs):
        return next(responses)

    with patch("asyncio.to_thread", new=mock_to_thread):
        result = asyncio.run(escalate_to_user(base_state))

    assert result["code_attempts"] == 0
    assert result["code_feedback"] == "Use MathTex for equations."
    assert result.get("fact_feedback") is None
    assert result["status"] == "validating"
