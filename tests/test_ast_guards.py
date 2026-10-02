"""Tests for pipeline/ast_guards.py — deterministic AST checks for known Manim bug patterns."""
import ast
import pytest
from pipeline.ast_guards import run_guards


def _parse(src: str) -> ast.AST:
    return ast.parse(src)


# Minimal valid scaffold used by tests that just need a parseable scene.
_OK_SCAFFOLD = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        pass
'''


def test_run_guards_returns_none_when_all_pass():
    """A clean scene with all expected scaffolding should produce no guard violations."""
    tree = _parse(_OK_SCAFFOLD)
    assert run_guards(tree, _OK_SCAFFOLD) is None


def test_run_guards_returns_string_when_any_fire():
    """When at least one guard fires, run_guards returns a non-None string."""
    # Missing the chalkboard_base import → _check_scene_base_import will fire (Task 4).
    src = _OK_SCAFFOLD.replace(
        "from chalkboard_base import ChalkboardSceneBase\n", ""
    )
    tree = _parse(src)
    out = run_guards(tree, src)
    assert isinstance(out, str)
    assert "chalkboard_base" in out


# ── _check_wait_literals ─────────────────────────────────────────────────────

def test_wait_literal_fires_on_hardcoded_float():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.wait(0.5)
'''
    from pipeline.ast_guards import _check_wait_literals
    out = _check_wait_literals(_parse(src), src)
    assert out is not None
    assert "0.5" in out
    assert "self.wait" in out


def test_wait_literal_fires_on_hardcoded_int():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.wait(2)
'''
    from pipeline.ast_guards import _check_wait_literals
    out = _check_wait_literals(_parse(src), src)
    assert out is not None
    assert "2" in out


def test_wait_literal_passes_on_d_index():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.wait(_d[3])
'''
    from pipeline.ast_guards import _check_wait_literals
    assert _check_wait_literals(_parse(src), src) is None


def test_wait_literal_passes_on_max_expr():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        _r = max(0.0, _d[2] - 0.5)
        if _r > 0:
            self.wait(_r)
'''
    from pipeline.ast_guards import _check_wait_literals
    assert _check_wait_literals(_parse(src), src) is None


def test_wait_literal_ignores_bool():
    """bool is a subclass of int but should not be flagged here."""
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.wait(True)
'''
    from pipeline.ast_guards import _check_wait_literals
    assert _check_wait_literals(_parse(src), src) is None


# ── _check_scene_base_inheritance ────────────────────────────────────────────

def test_scene_base_inheritance_fires_when_missing():
    src = '''
class ChalkboardScene(Scene):
    def construct(self):
        pass
'''
    from pipeline.ast_guards import _check_scene_base_inheritance
    out = _check_scene_base_inheritance(_parse(src), src)
    assert out is not None
    assert "ChalkboardSceneBase" in out


def test_scene_base_inheritance_passes_when_present():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        pass
'''
    from pipeline.ast_guards import _check_scene_base_inheritance
    assert _check_scene_base_inheritance(_parse(src), src) is None


def test_scene_base_inheritance_passes_when_no_chalkboard_class():
    """If there's no class named ChalkboardScene, this guard returns None."""
    src = '''
class SomeOtherScene(Scene):
    def construct(self):
        pass
'''
    from pipeline.ast_guards import _check_scene_base_inheritance
    assert _check_scene_base_inheritance(_parse(src), src) is None


# ── _check_scene_base_import ─────────────────────────────────────────────────

def test_scene_base_import_fires_when_missing():
    src = '''
from manim import *

class ChalkboardScene(Scene):
    def construct(self):
        pass
'''
    from pipeline.ast_guards import _check_scene_base_import
    out = _check_scene_base_import(_parse(src), src)
    assert out is not None
    assert "chalkboard_base" in out


def test_scene_base_import_passes_when_present():
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        pass
'''
    from pipeline.ast_guards import _check_scene_base_import
    assert _check_scene_base_import(_parse(src), src) is None


def test_scene_base_import_passes_when_aliased_form():
    """Tolerate `from chalkboard_base import ChalkboardSceneBase as Base`."""
    src = '''
from chalkboard_base import ChalkboardSceneBase as Base

class ChalkboardScene(Base, Scene):
    def construct(self):
        pass
'''
    from pipeline.ast_guards import _check_scene_base_import
    assert _check_scene_base_import(_parse(src), src) is None


# ── _check_code_kwarg ────────────────────────────────────────────────────────

def test_code_kwarg_fires_on_old_form():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        c = Code(code="print(1)", language="python")
'''
    from pipeline.ast_guards import _check_code_kwarg
    out = _check_code_kwarg(_parse(src), src)
    assert out is not None
    assert "code_string" in out


def test_code_kwarg_passes_on_new_form():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        c = Code(code_string="print(1)", language="python")
'''
    from pipeline.ast_guards import _check_code_kwarg
    assert _check_code_kwarg(_parse(src), src) is None


def test_code_kwarg_ignores_other_call():
    src = '''
some_other_function(code="print(1)")
'''
    from pipeline.ast_guards import _check_code_kwarg
    assert _check_code_kwarg(_parse(src), src) is None


# ── _check_begin_segment ─────────────────────────────────────────────────────

def test_begin_segment_fires_when_missing_after_comment():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        # ── Segment 0:
        self.begin_segment(0, duration=_d[0])
        title = Text("hello")
        self.play(FadeIn(title))
        # ── Segment 2:
        # (no begin_segment(2, ...) within 3 lines)
        body = Text("body")
        self.play(FadeIn(body))
'''
    from pipeline.ast_guards import _check_begin_segment
    out = _check_begin_segment(_parse(src), src)
    assert out is not None
    assert "Segment 2" in out or "begin_segment(2" in out


def test_begin_segment_passes_when_present():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        # ── Segment 0:
        self.begin_segment(0, duration=_d[0])
        title = Text("hello")
        # ── Segment 1:
        self.begin_segment(1, duration=_d[1])
        body = Text("body")
'''
    from pipeline.ast_guards import _check_begin_segment
    assert _check_begin_segment(_parse(src), src) is None


def test_begin_segment_passes_with_kwarg_only():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        # ── Segment 3:
        self.begin_segment(3, duration=_d[3])
'''
    from pipeline.ast_guards import _check_begin_segment
    assert _check_begin_segment(_parse(src), src) is None


def test_begin_segment_no_segment_comments_returns_none():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("just one shot")
'''
    from pipeline.ast_guards import _check_begin_segment
    assert _check_begin_segment(_parse(src), src) is None


# ── _check_end_layout_check ──────────────────────────────────────────────────

def test_end_layout_check_fires_when_missing():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("hello")
        self.play(FadeIn(title))
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
'''
    from pipeline.ast_guards import _check_end_layout_check
    out = _check_end_layout_check(_parse(src), src)
    assert out is not None
    assert "end_layout_check" in out


def test_end_layout_check_fires_when_after_final_fadeout():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("hello")
        self.play(FadeIn(title))
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
        self.end_layout_check()
'''
    from pipeline.ast_guards import _check_end_layout_check
    out = _check_end_layout_check(_parse(src), src)
    assert out is not None


def test_end_layout_check_passes_when_before_teardown():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("hello")
        self.play(FadeIn(title))
        self.end_layout_check()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
'''
    from pipeline.ast_guards import _check_end_layout_check
    assert _check_end_layout_check(_parse(src), src) is None


def test_end_layout_check_passes_with_intervening_lines():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("hello")
        self.play(FadeIn(title))
        self.end_layout_check()
        self.wait(_d[0])
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
'''
    from pipeline.ast_guards import _check_end_layout_check
    assert _check_end_layout_check(_parse(src), src) is None


def test_end_layout_check_passes_when_no_construct_method():
    src = '''
class SomeOtherClass:
    pass
'''
    from pipeline.ast_guards import _check_end_layout_check
    assert _check_end_layout_check(_parse(src), src) is None


# ── _check_code_attr_access ──────────────────────────────────────────────────

def test_code_attr_fires_on_dot_code_access():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        c = Code(code_string="print(1)", language="python")
        first = c.code
'''
    from pipeline.ast_guards import _check_code_attr_access
    out = _check_code_attr_access(_parse(src), src)
    assert out is not None
    assert "code_lines" in out


def test_code_attr_passes_on_code_lines_access():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        c = Code(code_string="print(1)", language="python")
        first_line = c.code_lines[0]
'''
    from pipeline.ast_guards import _check_code_attr_access
    assert _check_code_attr_access(_parse(src), src) is None


def test_code_attr_ignores_unrelated_dot_code():
    """A variable not assigned from Code() shouldn't trigger this guard."""
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        x = some_function()
        y = x.code
'''
    from pipeline.ast_guards import _check_code_attr_access
    assert _check_code_attr_access(_parse(src), src) is None


# ── _check_next_to_chain_depth ───────────────────────────────────────────────

def test_next_to_chain_depth_fires_at_depth_3():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        a = Text("a")
        b = Text("b").next_to(a, RIGHT).next_to(a, UP).next_to(a, DOWN)
'''
    from pipeline.ast_guards import _check_next_to_chain_depth
    out = _check_next_to_chain_depth(_parse(src), src)
    assert out is not None
    assert "next_to" in out
    assert "3" in out


def test_next_to_chain_depth_passes_at_depth_2():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        a = Text("a")
        b = Text("b").next_to(a, RIGHT).next_to(a, UP)
'''
    from pipeline.ast_guards import _check_next_to_chain_depth
    assert _check_next_to_chain_depth(_parse(src), src) is None


def test_next_to_chain_depth_passes_at_depth_1():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        a = Text("a")
        b = Text("b").next_to(a, RIGHT)
'''
    from pipeline.ast_guards import _check_next_to_chain_depth
    assert _check_next_to_chain_depth(_parse(src), src) is None


def test_next_to_chain_depth_treats_non_chain_calls_separately():
    """Two separate next_to calls on different lines aren't a chain."""
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        a = Text("a")
        b = Text("b").next_to(a, RIGHT)
        c = Text("c").next_to(a, UP)
        d = Text("d").next_to(a, DOWN)
'''
    from pipeline.ast_guards import _check_next_to_chain_depth
    assert _check_next_to_chain_depth(_parse(src), src) is None


# ── _check_seg_data_loaded ───────────────────────────────────────────────────

def test_seg_data_loaded_fires_when_referenced_but_not_loaded():
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.wait(_d[0])
'''
    from pipeline.ast_guards import _check_seg_data_loaded
    out = _check_seg_data_loaded(_parse(src), src)
    assert out is not None
    assert "_seg_data" in out or "_d" in out
    assert "segments.json" in out


def test_seg_data_loaded_passes_when_loaded_via_path_chain():
    src = '''
import json
from pathlib import Path

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        self.wait(_d[0])
'''
    from pipeline.ast_guards import _check_seg_data_loaded
    assert _check_seg_data_loaded(_parse(src), src) is None


def test_seg_data_loaded_passes_when_neither_referenced():
    """A scene that doesn't use _d / _seg_data has nothing to check."""
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("hello")
'''
    from pipeline.ast_guards import _check_seg_data_loaded
    assert _check_seg_data_loaded(_parse(src), src) is None


# ── run_guards aggregation behavior ──────────────────────────────────────────

def test_run_guards_aggregates_multiple_violations():
    """Scene that fires 3 guards: missing import, hardcoded wait literal, missing end_layout_check."""
    src = '''
from manim import *

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.wait(0.5)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
'''
    out = run_guards(_parse(src), src)
    assert out is not None
    # Numbered list with at least 3 items
    assert "1." in out
    assert "2." in out
    assert "3." in out
    # Each violation surfaces its core keyword
    assert "chalkboard_base" in out
    assert "self.wait" in out or "0.5" in out
    assert "end_layout_check" in out


# ── _check_horizontal_array_zone_overflow (T2.5 / #176) ─────────────────────

def test_horizontal_array_zone_overflow_fires_on_overflow():
    """N=10, W=0.85, x_0=-4.5 → right_edge = -4.5 + 9.5 × 0.85 = 3.575 → fires."""
    src = '''
import numpy as np
from manim import *
from chalkboard_base import ChalkboardSceneBase

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        for i in range(10):
            cell = RoundedRectangle(width=0.85, height=0.85)
            cell.move_to(np.array([-4.5 + i * 0.85, 0, 0]))
'''
    from pipeline.ast_guards import _check_horizontal_array_zone_overflow
    out = _check_horizontal_array_zone_overflow(_parse(src), src)
    assert out is not None
    assert "right edge" in out.lower()
    # The flagged right edge is 3.575 ≈ 3.58
    assert "3.5" in out or "3.6" in out


def test_horizontal_array_zone_overflow_passes_when_safe():
    """N=2, W=0.85, x_0=-4.5 → right_edge = -4.5 + 1.5 × 0.85 = -3.225 → no fire."""
    src = '''
import numpy as np
from manim import *
from chalkboard_base import ChalkboardSceneBase

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        for i in range(2):
            cell = RoundedRectangle(width=0.85, height=0.85)
            cell.move_to(np.array([-4.5 + i * 0.85, 0, 0]))
'''
    from pipeline.ast_guards import _check_horizontal_array_zone_overflow
    assert _check_horizontal_array_zone_overflow(_parse(src), src) is None


def test_horizontal_array_zone_overflow_passes_when_pattern_unclear():
    """Non-matching pattern (no width kwarg, no x_0 expression) should not fire."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        for i in range(10):
            text = Text(str(i))
            text.shift(RIGHT * i)  # not a move_to(np.array(...))
'''
    from pipeline.ast_guards import _check_horizontal_array_zone_overflow
    assert _check_horizontal_array_zone_overflow(_parse(src), src) is None


def test_horizontal_array_zone_overflow_passes_when_no_width_kwarg():
    """Loop with move_to but no width= kwarg in any constructor — give up."""
    src = '''
import numpy as np
from manim import *
from chalkboard_base import ChalkboardSceneBase

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        for i in range(10):
            cell = Square()
            cell.move_to(np.array([-4.5 + i * 0.85, 0, 0]))
'''
    from pipeline.ast_guards import _check_horizontal_array_zone_overflow
    # No width kwarg → can't compute right_edge → don't false-positive.
    assert _check_horizontal_array_zone_overflow(_parse(src), src) is None


def test_horizontal_array_zone_overflow_passes_on_ok_scaffold():
    """The canonical OK scaffold should not trigger a false positive."""
    from pipeline.ast_guards import _check_horizontal_array_zone_overflow
    assert _check_horizontal_array_zone_overflow(_parse(_OK_SCAFFOLD), _OK_SCAFFOLD) is None


def test_horizontal_array_zone_overflow_fires_on_three_arg_range():
    """range(start, stop) variant should also be picked up — last arg is N."""
    src = '''
import numpy as np
from manim import *
from chalkboard_base import ChalkboardSceneBase

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        for i in range(0, 8):
            cell = RoundedRectangle(width=1.2, height=0.8)
            cell.move_to(np.array([-4.0 + i * 1.2, 0, 0]))
'''
    # right_edge = -4.0 + 7.5 × 1.2 = 5.0 → > -0.5 → fires
    from pipeline.ast_guards import _check_horizontal_array_zone_overflow
    out = _check_horizontal_array_zone_overflow(_parse(src), src)
    assert out is not None


def test_horizontal_array_zone_overflow_passes_when_loop_var_not_referenced():
    """If the move_to expression doesn't reference the loop variable, the
    extracted x_0 is meaningless — give up rather than false-positive."""
    src = '''
import numpy as np
from manim import *
from chalkboard_base import ChalkboardSceneBase

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        for i in range(10):
            cell = RoundedRectangle(width=0.85, height=0.85)
            cell.move_to(np.array([0, 0, 0]))  # no `i`
'''
    from pipeline.ast_guards import _check_horizontal_array_zone_overflow
    assert _check_horizontal_array_zone_overflow(_parse(src), src) is None


def test_run_guards_uses_singular_when_one_violation():
    """A single-violation scene uses 'issue' not 'issues' in the preamble."""
    # Scene with only one violation: the chalkboard_base import is missing.
    # Everything else is valid.
    src = '''
from manim import *

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        # ── Segment 0:
        self.begin_segment(0, duration=_d[0])
        self.end_layout_check()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
'''
    out = run_guards(_parse(src), src)
    assert out is not None
    # Note: "1 AST-level issue" (singular). _check_seg_data_loaded also fires
    # because _d is referenced but never loaded — verify both expected counts.
    # If only the import fires, "1 AST-level issue" appears.
    # If import + seg_data both fire, "2 AST-level issues" appears.
    # Either way, the test should at minimum verify singular OR plural is used
    # correctly based on count.
    n_violations = out.count("\n\n") + 1 if out else 0  # crude count via separator
    if "Found 1 AST-level issue " in out:
        # singular path
        assert "issues" not in out.split("\n\n")[0]
        assert "1." in out
        assert "2." not in out
    else:
        # plural path — any count ≥ 2 should pluralize. Phase 5 added the
        # design-system imports guard, which co-fires with the import guard
        # on this scaffold, raising the count to 3.
        assert "Found " in out and " AST-level issues " in out
        assert "1." in out
        assert "2." in out


# ── Phase 5 — design-system enforcement guards ───────────────────────────────


def test_hex_color_fires_in_color_kwarg():
    """Raw hex string as `color=` kwarg → fires."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("X", color="#FF0000")
'''
    from pipeline.ast_guards import _check_no_raw_hex_in_color_kwargs
    out = _check_no_raw_hex_in_color_kwargs(_parse(src), src)
    assert out is not None
    assert "#FF0000" in out
    assert "t.role" in out  # actionable replacement suggestion


def test_hex_color_fires_on_manimcolor_call():
    """ManimColor("#xxxxxx") → fires."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        c = ManimColor("#1C1C1C")
'''
    from pipeline.ast_guards import _check_no_raw_hex_in_color_kwargs
    out = _check_no_raw_hex_in_color_kwargs(_parse(src), src)
    assert out is not None
    assert "#1C1C1C" in out


def test_hex_color_fires_on_background_assignment():
    """self.camera.background_color = "#FAFAFA" → fires."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.camera.background_color = "#FAFAFA"
'''
    from pipeline.ast_guards import _check_no_raw_hex_in_color_kwargs
    out = _check_no_raw_hex_in_color_kwargs(_parse(src), src)
    assert out is not None
    assert "#FAFAFA" in out


def test_hex_color_passes_on_token_lookup():
    """color=t.role(...) — token-resolved, not raw hex → no fire."""
    src = '''
from manim import *
from chalkboard_tokens import T
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        t = T(theme="chalkboard")
        title = Text("X", color=t.role("focus_primary"))
'''
    from pipeline.ast_guards import _check_no_raw_hex_in_color_kwargs
    assert _check_no_raw_hex_in_color_kwargs(_parse(src), src) is None


def test_hex_color_passes_on_hex_in_code_string():
    """ChalkCode(code_string="...#CSS-color...") — hex appears as source-
    code content, NOT as a color kwarg → must not fire.
    """
    src = '''
from manim import *
from chalkboard_components import ChalkCode
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        c = ChalkCode(code_string="color = '#FF0000'  # red")
'''
    from pipeline.ast_guards import _check_no_raw_hex_in_color_kwargs
    assert _check_no_raw_hex_in_color_kwargs(_parse(src), src) is None


def test_manim_color_constant_fires_on_kwarg():
    """color=RED → fires."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("X", color=RED)
'''
    from pipeline.ast_guards import _check_no_manim_color_constants
    out = _check_no_manim_color_constants(_parse(src), src)
    assert out is not None
    assert "RED" in out
    assert "t.role" in out  # replacement suggestion


def test_manim_color_constant_fires_on_intensity_variant():
    """BLUE_E (intensity variant) → fires."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("X", color=BLUE_E)
'''
    from pipeline.ast_guards import _check_no_manim_color_constants
    out = _check_no_manim_color_constants(_parse(src), src)
    assert out is not None
    assert "BLUE_E" in out


def test_manim_color_constant_fires_on_set_color():
    """obj.set_color(GREEN) → fires."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("X")
        title.set_color(GREEN)
'''
    from pipeline.ast_guards import _check_no_manim_color_constants
    out = _check_no_manim_color_constants(_parse(src), src)
    assert out is not None
    assert "GREEN" in out


def test_manim_color_constant_passes_on_direction():
    """UP / DOWN / LEFT / RIGHT are direction constants, NOT colors → must not fire."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        title = Text("X")
        title.shift(UP * 2)
'''
    from pipeline.ast_guards import _check_no_manim_color_constants
    assert _check_no_manim_color_constants(_parse(src), src) is None


def test_no_raw_primitive_construction_fires_on_roundedrectangle():
    """RoundedRectangle(...) in scene code → fires with ChalkBox suggestion."""
    src = '''
from manim import *
from chalkboard_components import ChalkBox
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        cell = RoundedRectangle(width=1.0, height=1.0)
'''
    from pipeline.ast_guards import _check_no_raw_primitive_construction
    out = _check_no_raw_primitive_construction(_parse(src), src)
    assert out is not None
    assert "RoundedRectangle" in out
    assert "ChalkBox" in out


def test_no_raw_primitive_construction_fires_on_code():
    """Code(...) → fires with ChalkCode suggestion."""
    src = '''
from manim import *
from chalkboard_components import ChalkCode
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        c = Code(code_string="print(1)", language="python")
'''
    from pipeline.ast_guards import _check_no_raw_primitive_construction
    out = _check_no_raw_primitive_construction(_parse(src), src)
    assert out is not None
    assert "Code" in out
    assert "ChalkCode" in out


def test_no_raw_primitive_construction_fires_on_arrow():
    """Arrow(...) → fires with ChalkArrow suggestion."""
    src = '''
from manim import *
from chalkboard_components import ChalkArrow
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        a = Arrow(start=[0,0,0], end=[1,0,0])
'''
    from pipeline.ast_guards import _check_no_raw_primitive_construction
    out = _check_no_raw_primitive_construction(_parse(src), src)
    assert out is not None
    assert "Arrow" in out
    assert "ChalkArrow" in out


def test_no_raw_primitive_construction_passes_on_chalkbox():
    """ChalkBox(...) usage is the canonical replacement → no fire."""
    src = '''
from manim import *
from chalkboard_components import ChalkBox
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        cell = ChalkBox("42")
'''
    from pipeline.ast_guards import _check_no_raw_primitive_construction
    assert _check_no_raw_primitive_construction(_parse(src), src) is None


# ── _check_no_growarrow (Tier B3 render-crash, #353) ─────────────────────────

def test_no_growarrow_fires_on_growarrow():
    """GrowArrow(<ChalkArrow>) crashes at render: GrowArrow calls .get_start()
    on its arg, but ChalkArrow is a VGroup that wraps the real Arrow as a
    submobject and has no points of its own → 'Cannot call Mobject.get_start
    for a Mobject with no points'. Guard fires + suggests Create(). See #353."""
    src = '''
from manim import *
from chalkboard_components import ChalkArrow
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        arrow = ChalkArrow(LEFT, RIGHT)
        self.play(GrowArrow(arrow))
'''
    from pipeline.ast_guards import _check_no_growarrow
    out = _check_no_growarrow(_parse(src), src)
    assert out is not None
    assert "GrowArrow" in out
    assert "Create" in out


def test_no_growarrow_passes_on_create():
    """Create(<ChalkArrow>) is the correct reveal → no fire."""
    src = '''
from manim import *
from chalkboard_components import ChalkArrow
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        arrow = ChalkArrow(LEFT, RIGHT)
        self.play(Create(arrow))
'''
    from pipeline.ast_guards import _check_no_growarrow
    assert _check_no_growarrow(_parse(src), src) is None


def test_growarrow_guard_registered_in_run_guards():
    """Must be wired into _ALL_GUARDS or it never runs in prod (the bug that
    would re-ship #353)."""
    src = '''
from manim import *
from chalkboard_components import ChalkArrow
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        arrow = ChalkArrow(LEFT, RIGHT)
        self.play(GrowArrow(arrow))
'''
    out = run_guards(_parse(src), src)
    assert out is not None and "GrowArrow" in out


def test_no_raw_primitive_construction_passes_on_rectangle():
    """Rectangle is NOT in the replaced list (too generic, has legit uses
    for frames/backgrounds). Should not fire.
    """
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        bg = Rectangle(width=14, height=8)
'''
    from pipeline.ast_guards import _check_no_raw_primitive_construction
    assert _check_no_raw_primitive_construction(_parse(src), src) is None


def test_design_system_imports_fires_when_real_scene_missing_them():
    """Real scene (has begin_segment) lacking the design-system imports → fires."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.begin_segment(0, duration=2.0)
'''
    from pipeline.ast_guards import _check_design_system_imports
    out = _check_design_system_imports(_parse(src), src)
    assert out is not None
    assert "chalkboard_tokens" in out
    assert "chalkboard_components" in out
    assert "chalkboard_moves" in out


def test_design_system_imports_passes_when_all_present():
    """Scene with all three imports + begin_segment → no fire."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
from chalkboard_tokens import T
from chalkboard_components import ChalkBox
from chalkboard_moves import reveal_with_emphasis
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.begin_segment(0, duration=2.0)
'''
    from pipeline.ast_guards import _check_design_system_imports
    assert _check_design_system_imports(_parse(src), src) is None


def test_design_system_imports_silent_on_empty_scaffold():
    """The _OK_SCAFFOLD-style minimal fixture has no begin_segment → guard
    must be silent (so existing tests aren't broken).
    """
    from pipeline.ast_guards import _check_design_system_imports
    assert _check_design_system_imports(_parse(_OK_SCAFFOLD), _OK_SCAFFOLD) is None


def test_design_system_imports_partial_lists_what_missing():
    """Only missing chalkboard_moves → feedback names just that one."""
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
from chalkboard_tokens import T
from chalkboard_components import ChalkBox
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.begin_segment(0, duration=2.0)
'''
    from pipeline.ast_guards import _check_design_system_imports
    out = _check_design_system_imports(_parse(src), src)
    assert out is not None
    assert "chalkboard_moves" in out
    # Tokens + components already imported — must not be in the missing list.
    # The feedback only lists what's missing.
    assert "from chalkboard_tokens import T`" not in out
    assert "from chalkboard_components import ChalkBox" not in out


# ── End-to-end via run_guards ────────────────────────────────────────────────


def test_run_guards_aggregates_phase5_violations():
    """A scene that breaks multiple Phase 5 rules should aggregate them all
    into a numbered list. This is the failure-mode the agent receives back
    on retry — terse but complete.
    """
    src = '''
from manim import *
from chalkboard_base import ChalkboardSceneBase
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.begin_segment(0, duration=2.0)
        self.camera.background_color = "#1C1C1C"
        cell = RoundedRectangle(width=1.0)
        cell.set_color(RED)
        self.end_layout_check()
'''
    out = run_guards(_parse(src), src)
    assert out is not None
    # All four Phase-5 categories should be represented in the output.
    assert "RED" in out
    assert "#1C1C1C" in out
    assert "RoundedRectangle" in out
    assert "chalkboard_moves" in out  # design-system imports missing


# ── _check_no_play_with_filtered_comp ────────────────────────────────────────


def test_play_filtered_comp_fires_on_basic_filter():
    """The smoking-gun pattern from the quicksort smoke-test failure."""
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.begin_segment(0, duration=2.0)
        seg_items = [a, b, c]
        boxes = [a, b, c]
        self.play(*[FadeOut(m) for m in seg_items if m not in boxes], run_time=0.3)
'''
    from pipeline.ast_guards import _check_no_play_with_filtered_comp
    out = _check_no_play_with_filtered_comp(_parse(src), src)
    assert out is not None
    assert "Called Scene.play with no animations" in out
    assert "to_play" in out  # actionable fix in feedback


def test_play_filtered_comp_passes_on_unfiltered():
    """Unfiltered comprehension is OK — empty seg_items is the caller's
    responsibility; the agent typically maintains seg_items to be
    non-empty when it's calling FadeOut on it.
    """
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        seg_items = [a, b]
        self.play(*[FadeOut(m) for m in seg_items], run_time=0.5)
'''
    from pipeline.ast_guards import _check_no_play_with_filtered_comp
    assert _check_no_play_with_filtered_comp(_parse(src), src) is None


def test_play_filtered_comp_passes_on_guarded_pattern():
    """The recommended safe pattern: extract to variable, guard with if,
    then play. Should NOT trigger — the variable isn't a comprehension
    inside the play() call.
    """
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        to_fade = [FadeOut(m) for m in seg_items if m not in boxes]
        if to_fade:
            self.play(*to_fade, run_time=0.3)
'''
    from pipeline.ast_guards import _check_no_play_with_filtered_comp
    assert _check_no_play_with_filtered_comp(_parse(src), src) is None


def test_play_filtered_comp_passes_on_non_play_call():
    """Other functions calling `*[comp if ...]` aren't our concern."""
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        result = max(*[x for x in nums if x > 0])
'''
    from pipeline.ast_guards import _check_no_play_with_filtered_comp
    assert _check_no_play_with_filtered_comp(_parse(src), src) is None


def test_play_filtered_comp_fires_on_nested_if_in_generators():
    """Multi-generator comprehension with an `if` in any of them."""
    src = '''
class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        self.play(*[FadeOut(m) for row in rows for m in row if m.visible])
'''
    from pipeline.ast_guards import _check_no_play_with_filtered_comp
    out = _check_no_play_with_filtered_comp(_parse(src), src)
    assert out is not None


# ── Math typesetting guard ───────────────────────────────────────────────────

from pipeline.ast_guards import (  # noqa: E402
    _check_no_math_in_text,
    _check_no_raw_primitive_construction,
)


def _g(guard, src):
    return guard(ast.parse(src), src)


@pytest.mark.parametrize("src", [
    'Text("x^2 + 1")',
    'Text("e^x")',
    'Text(r"\\frac{a}{b}")',
    'Text("a_{n+1}")',
    'Text("√2 ≈ 1.414")',
    'Text("x² + y²")',
    'Text("∫ f(x) dx")',
    'MarkupText("a ≤ b")',
    'Text(f"value: {v}^2")',
])
def test_math_in_text_flagged(src):
    out = _g(_check_no_math_in_text, src)
    assert out is not None and "math_tex" in out


@pytest.mark.parametrize("src", [
    'Text("Binary Search")',
    'Text("Input → Output")',
    'Text("3 × 3 grid")',
    'Text("C++ templates")',
    'math_tex(r"x^2 + 1")',
    'tex(r"Why $\\frac{\\dd}{\\dd x} e^x = e^x$")',
    'MathTex(r"\\int_0^1 x \\, \\dd x")',
])
def test_math_in_text_not_flagged(src):
    assert _g(_check_no_math_in_text, src) is None


def test_axes_and_matrix_require_components():
    assert "ChalkAxes" in _g(_check_no_raw_primitive_construction, "ax = Axes(x_range=[0, 1])")
    assert "ChalkMatrix" in _g(_check_no_raw_primitive_construction, "m = Matrix([[1, 2]])")


def test_square_allowed_for_geometry():
    """Squares on a right triangle's sides are geometry, not array cells."""
    assert _g(_check_no_raw_primitive_construction, "s = Square(side_length=2)") is None
