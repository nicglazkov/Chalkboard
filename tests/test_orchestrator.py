# tests/test_orchestrator.py
import pytest
from unittest.mock import patch
from pipeline.state import PipelineState
from pipeline.agents.orchestrator import escalate_to_user


def test_escalate_surfaces_script_feedback(base_state):
    base_state["fact_feedback"] = "Claim X is wrong."
    base_state["script_attempts"] = 3
    base_state["status"] = "validating"

    resume_payload = {"action": "retry_script", "guidance": "Focus on CS accuracy."}

    with patch("pipeline.agents.orchestrator.interrupt", return_value=resume_payload):
        result = escalate_to_user(base_state)

    assert result["script_attempts"] == 0
    assert result["fact_feedback"] == "Focus on CS accuracy."
    assert result["status"] == "drafting"


def test_escalate_routes_abort(base_state):
    base_state["code_feedback"] = "Can't fix."
    base_state["code_attempts"] = 3

    resume_payload = {"action": "abort", "guidance": ""}

    with patch("pipeline.agents.orchestrator.interrupt", return_value=resume_payload):
        result = escalate_to_user(base_state)

    assert result["status"] == "failed"


def test_escalate_retry_code_resets_code_attempts(base_state):
    base_state["code_feedback"] = "Wrong API."
    base_state["code_attempts"] = 3

    resume_payload = {"action": "retry_code", "guidance": "Use MathTex for equations."}

    with patch("pipeline.agents.orchestrator.interrupt", return_value=resume_payload):
        result = escalate_to_user(base_state)

    assert result["code_attempts"] == 0
    assert result["code_feedback"] == "Use MathTex for equations."
    assert result["status"] == "validating"
