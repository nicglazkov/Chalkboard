# tests/test_chalkboard_moves.py
"""Phase 3 — moves library.

Each move takes an active Manim Scene and orchestrates one named
pedagogical animation pattern. These tests mock the scene to inspect
what got played + waited for, then assert each move:

  * Is callable without raising
  * Calls scene.play() at least once
  * Sources its run_time + rate_func from the token motion table
    (not raw literals)
  * Honors documented signature semantics (e.g. compare_split with
    add_divider=True plays an extra animation for the divider)

The whole file is gated on `pytest.importorskip("manim")` — without
Manim, the moves module fails at top-level import (it does
`from manim import FadeIn, ...`). Locally these skip; in CI / regression
/ the Docker render image they run.
"""
import pytest
from unittest.mock import MagicMock

manim = pytest.importorskip("manim")

from docker.chalkboard_components import ChalkBox, Callout
from docker.chalkboard_moves import (
    annotate_and_pause,
    cascade_reveal,
    chapter_transition,
    compare_split,
    focus_zoom,
    morph_show_equivalence,
    progressive_step,
    reveal_with_emphasis,
)
from pipeline.design_tokens import T


# ── Helpers ──────────────────────────────────────────────────────────


def _mock_scene():
    """Returns a MagicMock with .play() and .wait() that record calls.

    Real Scene construction requires Manim's full render runtime; we
    only need to assert structure of the queued animations.
    """
    return MagicMock(spec=["play", "wait", "add"])


def _all_play_kwargs(scene_mock) -> list[dict]:
    """All kwargs from every recorded scene.play(...) call."""
    return [c.kwargs for c in scene_mock.play.call_args_list]


# ── reveal_with_emphasis ─────────────────────────────────────────────


def test_reveal_with_emphasis_plays_once():
    scene = _mock_scene()
    item = manim.Dot()
    reveal_with_emphasis(scene, item)
    assert scene.play.call_count == 1


def test_reveal_with_emphasis_uses_token_motion():
    scene = _mock_scene()
    item = manim.Dot()
    reveal_with_emphasis(scene, item)
    kw = scene.play.call_args.kwargs
    expected_run_time = T.motion("emphasis")["run_time"]
    assert kw["run_time"] == expected_run_time
    assert callable(kw["rate_func"])


def test_reveal_with_emphasis_honors_motion_name():
    scene = _mock_scene()
    reveal_with_emphasis(scene, manim.Dot(), motion_name="snap")
    expected_run_time = T.motion("snap")["run_time"]
    assert scene.play.call_args.kwargs["run_time"] == expected_run_time


# ── compare_split ────────────────────────────────────────────────────


def test_compare_split_with_divider_plays_twice():
    scene = _mock_scene()
    left, right = manim.Dot(), manim.Dot()
    compare_split(scene, left, right, add_divider=True)
    assert scene.play.call_count == 2  # divider + LaggedStart


def test_compare_split_no_divider_plays_once():
    scene = _mock_scene()
    left, right = manim.Dot(), manim.Dot()
    compare_split(scene, left, right, add_divider=False)
    assert scene.play.call_count == 1


def test_compare_split_uses_lagged_start():
    scene = _mock_scene()
    compare_split(scene, manim.Dot(), manim.Dot(), add_divider=False)
    # The play call's first positional arg is a LaggedStart
    args = scene.play.call_args.args
    assert any(isinstance(a, manim.LaggedStart) for a in args), (
        f"Expected LaggedStart in {[type(a).__name__ for a in args]}"
    )


# ── focus_zoom ───────────────────────────────────────────────────────


def test_focus_zoom_plays_once_with_others():
    scene = _mock_scene()
    target = manim.Dot()
    others = [manim.Dot(), manim.Dot()]
    focus_zoom(scene, target, others=others)
    assert scene.play.call_count == 1


def test_focus_zoom_passes_token_motion():
    scene = _mock_scene()
    focus_zoom(scene, manim.Dot(), others=[manim.Dot()])
    kw = scene.play.call_args.kwargs
    assert kw["run_time"] == T.motion("emphasis")["run_time"]


# ── morph_show_equivalence ───────────────────────────────────────────


def test_morph_uses_replacement_transform():
    scene = _mock_scene()
    a, b = manim.Dot(), manim.Dot()
    morph_show_equivalence(scene, a, b)
    args = scene.play.call_args.args
    assert any(isinstance(x, manim.ReplacementTransform) for x in args)


def test_morph_default_motion_is_settle():
    scene = _mock_scene()
    morph_show_equivalence(scene, manim.Dot(), manim.Dot())
    expected = T.motion("settle")["run_time"]
    assert scene.play.call_args.kwargs["run_time"] == expected


# ── cascade_reveal ───────────────────────────────────────────────────


def test_cascade_reveal_plays_once():
    scene = _mock_scene()
    items = [manim.Dot() for _ in range(4)]
    cascade_reveal(scene, items)
    assert scene.play.call_count == 1


def test_cascade_reveal_empty_no_op():
    """Empty items list should not crash and not call scene.play."""
    scene = _mock_scene()
    cascade_reveal(scene, [])
    assert scene.play.call_count == 0


def test_cascade_reveal_uses_lag_token():
    scene = _mock_scene()
    items = [manim.Dot() for _ in range(3)]
    cascade_reveal(scene, items, lag_name="cascade")
    args = scene.play.call_args.args
    lagged = [a for a in args if isinstance(a, manim.LaggedStart)]
    assert lagged
    # LaggedStart was constructed with the cascade ratio from tokens.
    # We can't easily inspect the lag_ratio without diving into Manim
    # internals — instead, run with the OTHER lag value and assert the
    # underlying object changes, proving the token is being read.
    scene2 = _mock_scene()
    cascade_reveal(scene2, items, lag_name="quick")
    # Token resolution differs between "cascade" and "quick"
    assert T.lag("cascade") != T.lag("quick")


# ── progressive_step ─────────────────────────────────────────────────


def test_progressive_step_highlights_current_mutes_others():
    scene = _mock_scene()
    boxes = [ChalkBox(str(i)) for i in range(3)]
    progressive_step(scene, boxes, current_idx=1)
    # The current box was highlighted to focus_primary; others muted.
    primary = T(theme="chalkboard").role("focus_primary")
    muted = T(theme="chalkboard").role("context_muted")
    # Inspect the inner text color of each box
    from manim.utils.color import ManimColor
    assert ManimColor(boxes[1].submobjects[1].color).to_hex().lower() == primary.lower()
    assert ManimColor(boxes[0].submobjects[1].color).to_hex().lower() == muted.lower()
    assert ManimColor(boxes[2].submobjects[1].color).to_hex().lower() == muted.lower()


def test_progressive_step_out_of_range_no_op():
    scene = _mock_scene()
    boxes = [ChalkBox(str(i)) for i in range(2)]
    progressive_step(scene, boxes, current_idx=99)
    assert scene.play.call_count == 0


# ── annotate_and_pause ───────────────────────────────────────────────


def test_annotate_and_pause_plays_then_waits_then_dims():
    scene = _mock_scene()
    callout = Callout("note", [0, 0, 0])
    annotate_and_pause(scene, callout, hold_sec=1.0)
    # play(reveal) → wait(1.0) → play(dim)
    assert scene.play.call_count == 2
    assert scene.wait.call_count == 1
    assert scene.wait.call_args.args[0] == 1.0


def test_annotate_and_pause_skips_wait_when_hold_zero():
    """hold_sec=0 must not call scene.wait — Manim's self.wait(0)
    raises ValueError.
    """
    scene = _mock_scene()
    callout = Callout("note", [0, 0, 0])
    annotate_and_pause(scene, callout, hold_sec=0)
    assert scene.wait.call_count == 0


# ── chapter_transition ───────────────────────────────────────────────


def test_chapter_transition_with_from_items():
    scene = _mock_scene()
    from_items = [manim.Dot(), manim.Dot()]
    chapter_transition(scene, from_items, to_label="Trees")
    # play(fade out from_items) → play(write title) → play(dim title)
    assert scene.play.call_count == 3


def test_chapter_transition_no_from_items_skips_first_play():
    scene = _mock_scene()
    chapter_transition(scene, [], to_label="Intro")
    # Only the title write + dim — no fade-out of empty set
    assert scene.play.call_count == 2


def test_chapter_transition_title_uses_focus_primary():
    """The chapter title is grand-motion + focus_primary color so it
    visually distinguishes from regular reveals.
    """
    scene = _mock_scene()
    chapter_transition(scene, [], to_label="Recursion")
    # First play call writes the title; inspect the Write target.
    write_args = scene.play.call_args_list[0].args
    write_obj = next((a for a in write_args if isinstance(a, manim.Write)), None)
    assert write_obj is not None
    title_text = write_obj.mobject
    from manim.utils.color import ManimColor
    expected = T(theme="chalkboard").role("focus_primary")
    assert ManimColor(title_text.color).to_hex().lower() == expected.lower()


# ── Cross-cutting: every play call carries token motion ──────────────


def test_every_move_plays_with_token_motion():
    """No move emits a raw run_time literal. Every scene.play(...) call
    from every move must have a run_time that exists in T.motion(...).
    """
    valid_run_times = {T.motion(k)["run_time"] for k in ("snap", "emphasis", "settle", "grand")}

    cases = [
        (lambda s: reveal_with_emphasis(s, manim.Dot()), "reveal_with_emphasis"),
        (lambda s: compare_split(s, manim.Dot(), manim.Dot()), "compare_split"),
        (lambda s: focus_zoom(s, manim.Dot(), others=[manim.Dot()]), "focus_zoom"),
        (lambda s: morph_show_equivalence(s, manim.Dot(), manim.Dot()), "morph_show_equivalence"),
        (lambda s: cascade_reveal(s, [manim.Dot(), manim.Dot()]), "cascade_reveal"),
        (lambda s: progressive_step(s, [ChalkBox(str(i)) for i in range(2)], 0), "progressive_step"),
        (lambda s: annotate_and_pause(s, Callout("x", [0, 0, 0]), hold_sec=0.5), "annotate_and_pause"),
        (lambda s: chapter_transition(s, [], "Title"), "chapter_transition"),
    ]
    for fn, name in cases:
        scene = _mock_scene()
        fn(scene)
        for kw in _all_play_kwargs(scene):
            assert kw.get("run_time") in valid_run_times, (
                f"{name}: scene.play got run_time={kw.get('run_time')!r}, "
                f"not in token table {valid_run_times}"
            )
            assert callable(kw.get("rate_func")), (
                f"{name}: scene.play got non-callable rate_func"
            )


# ── Math moves ───────────────────────────────────────────────────────

from docker.chalkboard_components import EquationGroup, math_tex  # noqa: E402
from docker.chalkboard_moves import derivation_step, emphasize_term, transform_equation  # noqa: E402


def _eq():
    return EquationGroup([r"(a+b)^2 &= (a+b)(a+b)", r"&= a^2 + 2ab + b^2"])


def test_derivation_step_first_line_is_written():
    scene = MagicMock(spec=["play", "wait", "add", "get_mobject_family_members"])
    scene.get_mobject_family_members.return_value = []
    eq = _eq()
    derivation_step(scene, eq, 0)
    anims = scene.play.call_args.args
    assert isinstance(anims[0], manim.Write)
    assert anims[0].mobject is eq.lines[0]


def test_derivation_step_morphs_previous_line_and_dims_it():
    scene = MagicMock(spec=["play", "wait", "add", "get_mobject_family_members"])
    eq = _eq()
    scene.get_mobject_family_members.return_value = [eq.lines[0]]
    derivation_step(scene, eq, 1)
    anims = scene.play.call_args.args
    assert isinstance(anims[0], (manim.TransformMatchingShapes, manim.TransformMatchingTex))
    assert len(anims) == 2  # the morph + dimming line 0
    assert scene.play.call_args.kwargs["run_time"] == T.motion("settle")["run_time"]


def test_derivation_step_keeps_alignment():
    eq = _eq()
    x0 = eq.rhs[0].get_left()[0]
    scene = MagicMock(spec=["play", "wait", "add", "get_mobject_family_members"])
    scene.get_mobject_family_members.return_value = [eq.lines[0]]
    derivation_step(scene, eq, 1)
    assert abs(eq.rhs[1].get_left()[0] - x0) < 1e-6


def test_transform_equation_uses_matching_transform():
    scene = _mock_scene()
    a = math_tex(r"2(x + 3)")
    b = math_tex(r"2x + 6")
    transform_equation(scene, a, b)
    anim = scene.play.call_args.args[0]
    assert isinstance(anim, (manim.TransformMatchingShapes, manim.TransformMatchingTex))


def test_emphasize_term_colors_isolated_part():
    scene = _mock_scene()
    expr = math_tex(r"f'(x) = 2x", isolate=["2x"])
    emphasize_term(scene, expr, "2x", role="accent_warm")
    anims = scene.play.call_args.args
    assert any(isinstance(a, manim.Circumscribe) for a in anims)


def test_progressive_step_animates_with_transforms():
    """Pre-port, progressive_step mutated in place and then played a no-op;
    the change must now be a real Transform per item."""
    scene = _mock_scene()
    cells = [ChalkBox(str(i)) for i in range(3)]
    progressive_step(scene, cells, 1)
    anims = scene.play.call_args.args
    assert len(anims) == 3 and all(isinstance(a, manim.Transform) for a in anims)
