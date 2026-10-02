# tests/test_manim_agent.py
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.agents.manim_agent import manim_agent


def _mock_response(code: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text=json.dumps({"manim_code": code}))]
    return msg


VALID_SCENE = '''
from manim import *
import json
from pathlib import Path

class ChalkboardScene(Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 1 - len(_d))
        title = Text("B-Trees")
        self.play(Write(title), run_time=1.0)
        self.wait(max(0.0, _d[0] - 1.0))
'''


def test_manim_agent_generates_chalkboard_scene(base_state):
    base_state["script"] = "B-trees are balanced search trees."
    base_state["script_segments"] = [{"text": "B-trees are balanced.", "estimated_duration_sec": 2.0}]
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = mock_resp
        result = asyncio.run(manim_agent(base_state))

    assert "ChalkboardScene" in result["manim_code"]
    assert result["status"] == "validating"


def test_manim_agent_includes_durations_in_prompt(base_state):
    base_state["script"] = "Hello world."
    base_state["script_segments"] = [
        {"text": "Hello.", "estimated_duration_sec": 1.5},
        {"text": "World.", "estimated_duration_sec": 2.3},
    ]
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = mock_resp
        asyncio.run(manim_agent(base_state))

    call_args = client_instance.messages.stream.call_args
    messages = call_args.kwargs["messages"]
    content = messages[0]["content"]
    assert "1.5" in content
    assert "2.3" in content


def test_manim_agent_includes_feedback_on_revision(base_state):
    base_state["script"] = "Hello world."
    base_state["script_segments"] = [{"text": "Hello.", "estimated_duration_sec": 1.0}]
    base_state["code_feedback"] = "Missing import for MathTex"
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = mock_resp
        asyncio.run(manim_agent(base_state))

    call_args = client_instance.messages.stream.call_args
    messages = call_args.kwargs["messages"]
    assert "Missing import for MathTex" in messages[0]["content"]


def test_manim_agent_includes_theme_colors_in_prompt(base_state):
    base_state["script"] = "B-trees are balanced search trees."
    base_state["script_segments"] = [{"text": "B-trees.", "estimated_duration_sec": 2.0}]
    base_state["theme"] = "light"
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = mock_resp
        asyncio.run(manim_agent(base_state))

    content = client_instance.messages.stream.call_args.kwargs["messages"][0]["content"]
    assert "#FAFAFA" in content  # light theme background


def test_manim_agent_colorful_theme_in_prompt(base_state):
    base_state["script"] = "B-trees are balanced search trees."
    base_state["script_segments"] = [{"text": "B-trees.", "estimated_duration_sec": 2.0}]
    base_state["theme"] = "colorful"
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = mock_resp
        asyncio.run(manim_agent(base_state))

    content = client_instance.messages.stream.call_args.kwargs["messages"][0]["content"]
    assert "#FBBF24" in content  # colorful focus_primary token, unique to that theme


def test_manim_agent_defaults_to_chalkboard_theme(base_state):
    base_state["script"] = "B-trees are balanced search trees."
    base_state["script_segments"] = [{"text": "B-trees.", "estimated_duration_sec": 2.0}]
    base_state.pop("theme", None)
    mock_resp = _mock_response(VALID_SCENE)

    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = mock_resp
        asyncio.run(manim_agent(base_state))

    content = client_instance.messages.stream.call_args.kwargs["messages"][0]["content"]
    assert "#1C1C1C" in content  # chalkboard theme background


def test_manim_agent_with_context_blocks_sends_list_content(base_state):
    base_state["script"] = "Script about trees."
    base_state["script_segments"] = [{"text": "Trees.", "estimated_duration_sec": 2.0}]
    context_blocks = [
        {"type": "text", "text": "--- file: diagram.py ---"},
        {"type": "text", "text": "class Tree: pass"},
    ]
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text='{"manim_code": "from manim import *"}')]

    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = mock_response
        from pipeline.agents.manim_agent import manim_agent
        asyncio.run(manim_agent(base_state, context_blocks=context_blocks))

    call_args = client_instance.messages.stream.call_args
    content = call_args.kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    assert any("source material" in b.get("text", "") for b in content)
    assert any("class Tree" in b.get("text", "") for b in content)


def test_manim_agent_without_context_blocks_sends_string_content(base_state):
    base_state["script"] = "Script."
    base_state["script_segments"] = [{"text": "S.", "estimated_duration_sec": 1.0}]
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text='{"manim_code": "from manim import *"}')]

    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = mock_response
        from pipeline.agents.manim_agent import manim_agent
        asyncio.run(manim_agent(base_state))

    call_args = client_instance.messages.stream.call_args
    content = call_args.kwargs["messages"][0]["content"]
    assert isinstance(content, str)


def test_manim_agent_output_includes_chalkboard_base_import(base_state):
    """Generated code must import ChalkboardSceneBase and inherit from it."""
    code = """
from chalkboard_base import ChalkboardSceneBase
from manim import *
import json
from pathlib import Path

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 1 - len(_d))
        # ── Segment 0 ──
        self.begin_segment(0, duration=_d[0])
        seg_items = []
        t = Text("Hello")
        self.play(Write(t), run_time=1.0)
        seg_items.append(t)
        self.end_layout_check()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
"""
    mock_resp = MagicMock()
    mock_resp.content = [MagicMock(type="text", text=json.dumps({"manim_code": code}))]

    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = mock_resp
        result = asyncio.run(manim_agent(base_state))

    assert "ChalkboardSceneBase" in result["manim_code"]
    assert "from chalkboard_base import ChalkboardSceneBase" in result["manim_code"]
    assert "begin_segment" in result["manim_code"]
    assert "end_layout_check" in result["manim_code"]


# ── Revision rounds: surgical edits on the prior code ────────────────────────

def _capture_user_msg(base_state):
    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.stream.return_value.__enter__.return_value.get_final_message.return_value = _mock_response(VALID_SCENE)
        asyncio.run(manim_agent(base_state))
    return client_instance.messages.stream.call_args.kwargs["messages"][0]["content"]


def test_manim_agent_uses_surgical_edit_when_prior_code_present(base_state):
    marker = "UNIQUE_MARKER_FOR_TEST_qwert12345"
    base_state["script"] = "s"
    base_state["script_segments"] = [{"text": "a", "estimated_duration_sec": 2.0}]
    base_state["manim_code"] = f"# {marker}\nfrom manim import *\n"
    base_state["code_feedback"] = "line 6: self.wait(0.5) uses a hardcoded literal."
    msg = _capture_user_msg(base_state)
    assert "MAKE MINIMAL TARGETED CHANGES" in msg
    assert marker in msg
    assert "self.wait(0.5)" in msg
    assert "```python" in msg


def test_manim_agent_falls_back_when_no_prior_code(base_state):
    base_state["script"] = "s"
    base_state["script_segments"] = [{"text": "a", "estimated_duration_sec": 2.0}]
    base_state["manim_code"] = ""
    base_state["code_feedback"] = "Some issue."
    msg = _capture_user_msg(base_state)
    assert "MAKE MINIMAL TARGETED CHANGES" not in msg
    assert "```python" not in msg
    assert "Issues to address:" in msg and "Some issue." in msg


def test_manim_agent_first_attempt_has_no_revision_instructions(base_state):
    base_state["script"] = "s"
    base_state["script_segments"] = [{"text": "a", "estimated_duration_sec": 2.0}]
    msg = _capture_user_msg(base_state)
    assert "MAKE MINIMAL TARGETED CHANGES" not in msg
    assert "Previous attempt" not in msg


# ── Design-system vocabulary in the system prompt ────────────────────────────

from pipeline.agents.manim_agent import SYSTEM_PROMPT  # noqa: E402


def test_system_prompt_targets_manim_0_21():
    assert "v0.21.0" in SYSTEM_PROMPT
    assert "v0.20.1" not in SYSTEM_PROMPT


def test_system_prompt_lists_all_components():
    for name in (
        "ChalkBox", "ChalkArrow", "ChalkCode", "Callout", "StepCounter",
        "ChalkAxis", "ChalkAxes", "ChalkPanel", "ChalkBadge", "EquationGroup",
        "ChalkMatrix", "NetworkNode", "math_tex", "tex(",
    ):
        assert name in SYSTEM_PROMPT, f"component {name!r} missing from SYSTEM_PROMPT"


def test_system_prompt_lists_all_moves():
    for name in (
        "reveal_with_emphasis", "compare_split", "focus_zoom",
        "morph_show_equivalence", "cascade_reveal", "progressive_step",
        "annotate_and_pause", "chapter_transition",
        "derivation_step", "transform_equation", "emphasize_term",
    ):
        assert name in SYSTEM_PROMPT, f"move {name!r} missing from SYSTEM_PROMPT"


def test_system_prompt_lists_all_templates():
    for name in ("AlgorithmTemplate", "CodeTemplate", "CompareTemplate",
                 "DerivationTemplate", "HowtoTemplate", "TimelineTemplate"):
        assert name in SYSTEM_PROMPT


def test_system_prompt_has_forbidden_patterns_section():
    assert "FORBIDDEN" in SYSTEM_PROMPT
    for marker in ("Raw hex colors", "Raw font_size", "Raw buff", "Raw stroke_width",
                   "Raw run_time", "Math in Text"):
        assert marker in SYSTEM_PROMPT, f"FORBIDDEN list missing {marker!r}"


def test_system_prompt_has_annotated_exemplars():
    exemplars = SYSTEM_PROMPT.split("EXEMPLAR ")
    assert len(exemplars) >= 5  # preamble + 4 exemplars
    for i in (1, 2, 3, 4):
        body = exemplars[i]
        assert any(f"{c}(" in body for c in (
            "ChalkBox", "ChalkArrow", "ChalkCode", "Callout", "ChalkPanel",
            "ChalkAxis", "ChalkAxes", "ChalkBadge", "EquationGroup",
        )), f"EXEMPLAR {i} does not construct a component"
        assert any(f"{m}(" in body for m in (
            "reveal_with_emphasis", "compare_split", "focus_zoom", "cascade_reveal",
            "progressive_step", "annotate_and_pause", "derivation_step",
        )), f"EXEMPLAR {i} does not call a move"


def test_system_prompt_explains_role_and_motion_semantics():
    assert "ROLE SEMANTICS" in SYSTEM_PROMPT
    assert "magnet" in SYSTEM_PROMPT.lower()
    assert "MOTION SEMANTICS" in SYSTEM_PROMPT
    for name in ("motion_snap", "motion_emphasis", "motion_settle", "motion_grand"):
        assert name in SYSTEM_PROMPT


def test_required_scaffold_imports_design_system():
    scaffold = SYSTEM_PROMPT.split("REQUIRED SCAFFOLD —")[1]
    for line in ("from chalkboard_base import ChalkboardSceneBase",
                 "from chalkboard_tokens import T",
                 "from chalkboard_components import",
                 "from chalkboard_moves import",
                 "from chalkboard_templates import",
                 "t = T(theme=",
                 "self.camera.background_color = t.bg"):
        assert line in scaffold


def test_system_prompt_math_guidance():
    """Math must be typeset with LaTeX through the design system, with the
    house macros, upright differentials and aligned derivations."""
    assert "MATH" in SYSTEM_PROMPT
    assert "NEVER put math in Text" in SYSTEM_PROMPT
    for needle in (r"\frac", r"\cdot", r"\left(", r"\dd", r"\frac{\dd y}{\dd x}",
                   r"\R", r"\E", r"\Var", "siunitx", "colors=", "isolate=",
                   "&=", 'size="math"'):
        assert needle in SYSTEM_PROMPT, f"math guidance missing {needle!r}"
    # A title with math must be typeset, never Text with a caret.
    assert "Why the derivative of e^x is e^x" in SYSTEM_PROMPT


def test_system_prompt_pitfalls_match_manim_0_21():
    """VGroup.arrange returns the group in CE; the old 'returns None' advice is wrong."""
    assert "arrange() returns None" not in SYSTEM_PROMPT
    assert "arrange() returns the group" in SYSTEM_PROMPT
