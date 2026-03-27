# tests/test_code_validator.py
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.agents.code_validator import code_validator


VALID_CODE = """
from manim import *
import json
from pathlib import Path

class ChalkboardScene(Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 1 - len(_d))
        self.play(Write(Text("Hello")))
        self.wait(_d[0])
"""

INVALID_SYNTAX = "from manim import *\nclass Bad(\n    def broken"


def _mock_response(verdict: str, feedback: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({"verdict": verdict, "feedback": feedback}))]
    return msg


def test_code_validator_passes_valid_code(base_state):
    base_state["manim_code"] = VALID_CODE
    base_state["script"] = "Hello world."
    mock_resp = _mock_response("approved", "Looks correct.")

    with patch("pipeline.agents.code_validator.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        result = code_validator(base_state)

    assert result["code_feedback"] is None  # cleared on approval
    assert result["code_attempts"] == 0  # not incremented on pass


def test_code_validator_fails_on_syntax_error_without_claude_call(base_state):
    base_state["manim_code"] = INVALID_SYNTAX
    base_state["code_attempts"] = 0

    with patch("pipeline.agents.code_validator.anthropic.Anthropic") as MockClient:
        result = code_validator(base_state)

    MockClient.assert_not_called()
    assert result["code_attempts"] == 1
    assert "syntax" in result["code_feedback"].lower()


def test_code_validator_increments_attempts_on_semantic_fail(base_state):
    base_state["manim_code"] = VALID_CODE
    base_state["script"] = "Explain hash tables."
    base_state["code_attempts"] = 1
    mock_resp = _mock_response("needs_revision", "Scene doesn't show hash tables.")

    with patch("pipeline.agents.code_validator.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        result = code_validator(base_state)

    assert result["code_attempts"] == 2
    assert "hash tables" in result["code_feedback"]


def test_code_validator_rejects_hardcoded_wait(base_state):
    BAD_CODE = """
from manim import *
class ChalkboardScene(Scene):
    def construct(self):
        t = Text("hello")
        self.play(Write(t), run_time=1.0)
        self.wait(2.5)
"""
    base_state["manim_code"] = BAD_CODE
    base_state["script"] = "Hello world."
    mock_resp = _mock_response("needs_revision", "self.wait uses hardcoded float")

    with patch("pipeline.agents.code_validator.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        result = code_validator(base_state)

    assert result["code_feedback"] == "self.wait uses hardcoded float"
    assert result["code_attempts"] == 1
