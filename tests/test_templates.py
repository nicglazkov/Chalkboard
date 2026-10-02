# tests/test_templates.py
"""Tests for the --template flag and TEMPLATE_SPECS injection in manim_agent."""
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.agents.manim_agent import manim_agent, TEMPLATE_SPECS


DUMMY_CODE = "from manim import *\nclass ChalkboardScene(Scene):\n    def construct(self): pass"


def _mock_response():
    msg = MagicMock()
    msg.content = [MagicMock(
        type="text", text=json.dumps({"manim_code": DUMMY_CODE}),
    )]
    msg.stop_reason = "end_turn"
    msg.usage = MagicMock(input_tokens=200, output_tokens=80)
    return msg


def _run_agent(state):
    """manim_agent streams (`with client.messages.stream(...) as stream:
    stream.get_final_message()`). Wire the context-manager mock."""
    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        cm = instance.messages.stream.return_value
        cm.__enter__.return_value.get_final_message.return_value = _mock_response()
        cm.__exit__.return_value = False
        asyncio.run(manim_agent(state))
    return instance.messages.stream.call_args.kwargs["messages"][0]["content"]


# ── TEMPLATE_SPECS structure ─────────────────────────────────────────────────

def test_all_templates_defined():
    assert "algorithm" in TEMPLATE_SPECS
    assert "code" in TEMPLATE_SPECS
    assert "compare" in TEMPLATE_SPECS
    assert "howto" in TEMPLATE_SPECS
    assert "timeline" in TEMPLATE_SPECS
    assert "derivation" in TEMPLATE_SPECS


def test_template_specs_are_non_empty_strings():
    for name, spec in TEMPLATE_SPECS.items():
        assert isinstance(spec, str), f"{name} spec is not a string"
        assert len(spec) > 100, f"{name} spec is too short — probably missing content"


# ── Injection behaviour ───────────────────────────────────────────────────────

def test_no_template_excludes_template_block(base_state):
    base_state["script"] = "Hello."
    base_state["script_segments"] = [{"text": "Hello.", "estimated_duration_sec": 1.0}]
    # template=None by default in base_state
    content = _run_agent(base_state)
    # the shared spec header must not appear when no template is selected
    assert "TEMPLATE — " not in content


def test_algorithm_template_injected(base_state):
    base_state["script"] = "Quicksort partitions around a pivot."
    base_state["script_segments"] = [{"text": "Quicksort.", "estimated_duration_sec": 2.0}]
    base_state["template"] = "algorithm"
    content = _run_agent(base_state)
    assert "algorithm" in content.lower() or "step-through" in content.lower()
    assert "AlgorithmTemplate" in content
    assert "beats" in content


def test_code_template_injected(base_state):
    base_state["script"] = "Here is a binary search implementation."
    base_state["script_segments"] = [{"text": "Binary search.", "estimated_duration_sec": 2.0}]
    base_state["template"] = "code"
    content = _run_agent(base_state)
    assert "CodeTemplate" in content
    assert "line_indices" in content  # per-step highlight targets
    assert "code_string" in content   # correct Code constructor kwarg


def test_compare_template_injected(base_state):
    base_state["script"] = "SQL vs NoSQL trade-offs."
    base_state["script_segments"] = [{"text": "SQL vs NoSQL.", "estimated_duration_sec": 2.0}]
    base_state["template"] = "compare"
    content = _run_agent(base_state)
    assert "CompareTemplate" in content
    assert "divider" in content.lower() or "DashedLine" in content


def test_howto_template_injected(base_state):
    base_state["script"] = "How to set up a Python virtual environment."
    base_state["script_segments"] = [{"text": "Set up venv.", "estimated_duration_sec": 2.0}]
    base_state["template"] = "howto"
    content = _run_agent(base_state)
    assert "step" in content.lower() or "how-to" in content.lower()
    assert "highlight" in content.lower() or "accent" in content.lower()


def test_timeline_template_injected(base_state):
    base_state["script"] = "History of the internet from ARPANET to today."
    base_state["script_segments"] = [{"text": "ARPANET began.", "estimated_duration_sec": 2.0}]
    base_state["template"] = "timeline"
    content = _run_agent(base_state)
    assert "timeline" in content.lower()
    assert "Dot" in content or "marker" in content.lower()


def test_derivation_template_injected(base_state):
    base_state["script"] = "Deriving the power rule from the limit definition."
    base_state["script_segments"] = [{"text": "Start from the limit.", "estimated_duration_sec": 2.0}]
    base_state["template"] = "derivation"
    content = _run_agent(base_state)
    assert "DerivationTemplate" in content
    assert "render_all" in content
    assert "&=" in content           # aligned lines
    assert "\\dd" in content          # upright differential macro


def test_template_appended_after_theme(base_state):
    """Template spec should appear after the theme spec in the prompt."""
    base_state["script"] = "Test."
    base_state["script_segments"] = [{"text": "Test.", "estimated_duration_sec": 1.0}]
    base_state["theme"] = "chalkboard"
    base_state["template"] = "algorithm"
    content = _run_agent(base_state)
    theme_pos = content.find("#1C1C1C")       # chalkboard background marker
    template_pos = content.find("TEMPLATE — Algorithm")
    assert theme_pos != -1
    assert template_pos != -1
    assert template_pos > theme_pos


def test_unknown_template_value_ignored(base_state):
    """An unrecognised template value should not crash the agent."""
    base_state["script"] = "Test."
    base_state["script_segments"] = [{"text": "Test.", "estimated_duration_sec": 1.0}]
    base_state["template"] = "nonexistent"
    content = _run_agent(base_state)  # should not raise
    assert "TEMPLATE — " not in content


# ── State field propagation ───────────────────────────────────────────────────

def test_template_field_in_pipeline_state(base_state):
    from pipeline.state import PipelineState
    from typing import get_type_hints
    hints = get_type_hints(PipelineState)
    assert "template" in hints


def test_init_state_propagates_template():
    from pipeline.graph import _init_state
    result = _init_state({"topic": "t", "template": "compare"})
    assert result["template"] == "compare"


def test_init_state_template_defaults_to_none():
    from pipeline.graph import _init_state
    result = _init_state({"topic": "t"})
    assert result.get("template") is None
