# tests/conftest.py
import pytest
from unittest.mock import MagicMock, AsyncMock
from pipeline.state import PipelineState


@pytest.fixture
def base_state() -> PipelineState:
    return PipelineState(
        topic="explain how B-trees work",
        run_id="test-run-001",
        script="",
        script_segments=[],
        manim_code="",
        script_attempts=0,
        code_attempts=0,
        fact_feedback=None,
        code_feedback=None,
        effort_level="medium",
        audience="intermediate",
        tone="casual",
        theme="chalkboard",
        needs_web_search=False,
        user_approved_search=False,
        status="drafting",
        context_file_paths=[],
        speed=1.0,
        template=None,
        research_brief=None,
        research_sources=[],
    )


@pytest.fixture
def mock_anthropic_client():
    client = MagicMock()
    client.messages = MagicMock()
    return client
