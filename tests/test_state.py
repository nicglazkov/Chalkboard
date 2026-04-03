# tests/test_state.py
from pipeline.state import PipelineState, ValidationResult
from typing import get_type_hints


def test_pipeline_state_has_required_fields():
    hints = get_type_hints(PipelineState)
    required = [
        "topic", "run_id", "script", "script_segments", "manim_code",
        "script_attempts", "code_attempts", "fact_feedback", "code_feedback",
        "effort_level", "needs_web_search", "user_approved_search", "status",
    ]
    for field in required:
        assert field in hints, f"Missing field: {field}"


def test_validation_result_approved():
    result = ValidationResult(verdict="approved", feedback="Looks good")
    assert result.verdict == "approved"


def test_validation_result_needs_revision():
    result = ValidationResult(verdict="needs_revision", feedback="Fix claim X")
    assert result.verdict == "needs_revision"


def test_validation_result_rejects_bad_verdict():
    import pytest
    with pytest.raises(Exception):
        ValidationResult(verdict="unknown", feedback="")


def test_research_fields_in_pipeline_state():
    hints = get_type_hints(PipelineState)
    assert "research_brief" in hints
    assert "research_sources" in hints
