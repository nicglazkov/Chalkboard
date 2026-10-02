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
        interactive=True,
    )


@pytest.fixture
def mock_anthropic_client():
    client = MagicMock()
    client.messages = MagicMock()
    return client


@pytest.fixture(autouse=True)
def _fresh_llm_clients():
    """pipeline.llm caches clients; tests that patch anthropic.Anthropic need a fresh one."""
    from pipeline import llm
    from pipeline.tts import kokoro_tts
    llm._clients.clear()
    kokoro_tts._pipeline.cache_clear()
    yield
    llm._clients.clear()
    kokoro_tts._pipeline.cache_clear()


@pytest.fixture(autouse=True)
def _no_default_narrator(monkeypatch):
    """Tests must not inherit a NARRATOR from the developer's .env."""
    monkeypatch.setattr("pipeline.render_trigger.NARRATOR", "")
