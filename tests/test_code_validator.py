# tests/test_code_validator.py
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.agents.code_validator import code_validator


VALID_CODE = """
from manim import *
from chalkboard_base import ChalkboardSceneBase
from chalkboard_tokens import T
from chalkboard_components import ChalkBox, math_tex
from chalkboard_moves import reveal_with_emphasis
import json
from pathlib import Path

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 1 - len(_d))
        t = T(theme="chalkboard")
        self.camera.background_color = t.bg
        # ── Segment 0: Intro ──
        self.begin_segment(0, duration=_d[0])
        eq = math_tex(r"e^{i\\pi} + 1 = 0")
        reveal_with_emphasis(self, eq)
        _r = max(0.0, _d[0] - 0.9)
        if _r > 0:
            self.wait(_r)
        self.end_layout_check()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
"""

TEMPLATE_SCENE = """
from manim import *
from chalkboard_base import ChalkboardSceneBase
from chalkboard_tokens import T
from chalkboard_components import ChalkBox
from chalkboard_moves import reveal_with_emphasis
from chalkboard_templates import DerivationTemplate
import json
from pathlib import Path

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 2 - len(_d))
        t = T(theme="chalkboard")
        self.camera.background_color = t.bg
        DerivationTemplate(self, theme="chalkboard", beats={
            "title": r"Why $\\frac{\\dd}{\\dd x} x^2 = 2x$",
            "lines": [r"f'(x) &= 2x"],
        }).render_all(_d)
        self.end_layout_check()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
"""

INVALID_SYNTAX = "from manim import *\nclass Bad(\n    def broken"


def _mock_response(verdict: str, feedback: str) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(type="text", text=json.dumps({"verdict": verdict, "feedback": feedback}))]
    return msg


def _run(base_state, code, verdict="approved", feedback="ok"):
    """Run the validator with Claude mocked; returns (result, MockClient)."""
    base_state["manim_code"] = code
    base_state["script"] = base_state.get("script") or "Hello world."
    with patch("pipeline.llm.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = _mock_response(verdict, feedback)
        result = asyncio.run(code_validator(base_state))
    return result, MockClient


def test_code_validator_passes_valid_code(base_state):
    result, MockClient = _run(base_state, VALID_CODE, "approved", "Looks correct.")
    MockClient.return_value.messages.create.assert_called_once()
    assert result["code_feedback"] is None  # cleared on approval
    assert result["code_attempts"] == 0  # not incremented on pass


def test_code_validator_passes_template_driven_scene(base_state):
    """A template scene has no '# ── Segment N:' blocks of its own; the guards
    must not demand them."""
    result, MockClient = _run(base_state, TEMPLATE_SCENE)
    MockClient.return_value.messages.create.assert_called_once()
    assert result["code_feedback"] is None


def test_code_validator_fails_on_syntax_error_without_claude_call(base_state):
    base_state["code_attempts"] = 0
    result, MockClient = _run(base_state, INVALID_SYNTAX)
    MockClient.assert_not_called()
    assert result["code_attempts"] == 1
    assert "syntax" in result["code_feedback"].lower()


def test_semantic_fail_is_advisory_and_spares_hard_budget(base_state):
    base_state["script"] = "Explain hash tables."
    base_state["code_attempts"] = 1
    base_state["claude_review_failures"] = 1
    result, _ = _run(base_state, VALID_CODE, "needs_revision", "Scene doesn't show hash tables.")
    assert "code_attempts" not in result          # hard retry budget untouched
    assert result["claude_review_failures"] == 2
    assert result["code_feedback_advisory"] is True
    assert "hash tables" in result["code_feedback"]


def test_review_prompt_lists_design_system_apis(base_state):
    """The Claude reviewer must be told the design-system names are real,
    or it flags ChalkBox / DerivationTemplate as undefined."""
    _, MockClient = _run(base_state, VALID_CODE)
    content = MockClient.return_value.messages.create.call_args.kwargs["messages"][0]["content"]
    for name in ("ChalkBox", "EquationGroup", "derivation_step", "DerivationTemplate", "math_tex"):
        assert name in content


# ── Deterministic AST guards short-circuit before Claude ─────────────────────

def test_code_validator_rejects_hardcoded_wait(base_state):
    BAD_CODE = """
from manim import *
class ChalkboardScene(Scene):
    def construct(self):
        t = Text("hello")
        self.play(Write(t), run_time=1.0)
        self.wait(2.5)
"""
    result, MockClient = _run(base_state, BAD_CODE)
    MockClient.assert_not_called()
    assert "self.wait" in result["code_feedback"]
    assert "hardcoded" in result["code_feedback"]
    assert result["code_attempts"] == 1


def test_code_validator_rejects_missing_base_class(base_state):
    """Scene must inherit ChalkboardSceneBase, not bare Scene."""
    BAD_CODE = """
from manim import *
import json
from pathlib import Path

class ChalkboardScene(Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 1 - len(_d))
        self.begin_segment(0, duration=_d[0])
        self.play(Write(Text("Hello")), run_time=1.0)
        self.end_layout_check()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
"""
    result, MockClient = _run(base_state, BAD_CODE)
    MockClient.assert_not_called()
    assert "ChalkboardSceneBase" in result["code_feedback"]


def test_code_validator_rejects_missing_begin_segment(base_state):
    """Every segment block must call self.begin_segment(N, duration=_d[N])."""
    BAD_CODE = """
from chalkboard_base import ChalkboardSceneBase
from manim import *
import json
from pathlib import Path

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 1 - len(_d))
        # ── Segment 0: Intro ──
        seg_items = []
        t = Text("Hello")
        self.play(Write(t), run_time=1.0)
        seg_items.append(t)
        self.end_layout_check()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
"""
    result, MockClient = _run(base_state, BAD_CODE)
    MockClient.assert_not_called()
    assert "begin_segment" in result["code_feedback"]


def test_code_validator_rejects_missing_end_layout_check(base_state):
    """construct() must call self.end_layout_check() before final FadeOut."""
    BAD_CODE = """
from chalkboard_base import ChalkboardSceneBase
from manim import *
import json
from pathlib import Path

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 1 - len(_d))
        # ── Segment 0: Intro ──
        self.begin_segment(0, duration=_d[0])
        seg_items = []
        t = Text("Hello")
        self.play(Write(t), run_time=1.0)
        seg_items.append(t)
        self.play(*[FadeOut(m) for m in seg_items], run_time=0.5)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
"""
    result, MockClient = _run(base_state, BAD_CODE)
    MockClient.assert_not_called()
    assert "end_layout_check" in result["code_feedback"]


def test_code_validator_rejects_math_in_text(base_state):
    BAD_CODE = VALID_CODE.replace('math_tex(r"e^{i\\pi} + 1 = 0")', 'Text("x^2 + 1")')
    assert 'Text("x^2 + 1")' in BAD_CODE
    result, MockClient = _run(base_state, BAD_CODE)
    MockClient.assert_not_called()
    assert "math_tex" in result["code_feedback"]


def test_code_validator_rejects_raw_hex_color(base_state):
    BAD_CODE = VALID_CODE.replace("self.camera.background_color = t.bg",
                                  'self.camera.background_color = "#1C1C1C"')
    result, MockClient = _run(base_state, BAD_CODE)
    MockClient.assert_not_called()
    assert "hex" in result["code_feedback"]


# ── Mobject arithmetic guard ─────────────────────────────────────────────────
# `Text(...).move_to(...) + 0` is valid Python that raises NotImplementedError
# at render time. These pin the fail-fast AST check.

def test_code_validator_rejects_mobject_plus_int_literal(base_state):
    BAD_CODE = """
from chalkboard_base import ChalkboardSceneBase
from manim import *

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        q = Text("Search: 32").move_to([0, 3.0, 0]) + 0
"""
    base_state["code_attempts"] = 0
    result, MockClient = _run(base_state, BAD_CODE)
    MockClient.assert_not_called()
    assert result["code_attempts"] == 1
    assert "Mobject" in result["code_feedback"]
    assert "NotImplementedError" in result["code_feedback"]


def test_code_validator_rejects_mobject_plus_mobject(base_state):
    BAD_CODE = """
from chalkboard_base import ChalkboardSceneBase
from manim import *

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        combined = Text("a") + Text("b")
"""
    result, MockClient = _run(base_state, BAD_CODE)
    MockClient.assert_not_called()
    assert "VGroup" in result["code_feedback"]


def test_code_validator_rejects_mobject_minus_literal(base_state):
    BAD_CODE = """
from chalkboard_base import ChalkboardSceneBase
from manim import *

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        x = Circle().scale(2) - 1
"""
    result, MockClient = _run(base_state, BAD_CODE)
    MockClient.assert_not_called()
    assert "Mobject" in result["code_feedback"]


def test_code_validator_catches_arithmetic_on_design_system_calls(base_state):
    BAD_CODE = """
from chalkboard_base import ChalkboardSceneBase
from manim import *

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        bad = math_tex("x^2").scale(0.8).next_to(ORIGIN, UP) + 0
"""
    result, MockClient = _run(base_state, BAD_CODE)
    MockClient.assert_not_called()
    assert "Mobject" in result["code_feedback"]


def test_code_validator_allows_legitimate_arithmetic_on_non_mobjects(base_state):
    OK_CODE = VALID_CODE.replace(
        "        eq = math_tex(",
        "        n = len(_d) + 1\n"
        "        target = UP * 2 + LEFT\n"
        "        items = [1, 2] + [3, 4]\n"
        "        eq = math_tex(",
    )
    result, _ = _run(base_state, OK_CODE)
    assert result["code_feedback"] is None
    assert result["code_attempts"] == 0


def test_review_outage_does_not_fail_the_run(base_state):
    """A 529/overload that outlasts retries skips the advisory review."""
    import asyncio
    from unittest.mock import patch
    from pipeline.retry import TimeoutExhausted
    from pipeline.agents.code_validator import code_validator
    base_state["manim_code"] = VALID_CODE
    base_state["code_attempts"] = 1

    async def boom(*a, **k):
        raise TimeoutExhausted("code_validator failed after 3 attempts: 529 overloaded")

    with patch("pipeline.agents.code_validator.call_json_budgeted", new=boom):
        result = asyncio.run(code_validator(base_state))
    assert result["code_feedback"] is None
    assert result["code_attempts"] == 1
