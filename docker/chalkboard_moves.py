"""Chalkboard moves library.

A move is a NAMED pedagogical animation pattern. Each move takes a Manim
Scene as its first argument and orchestrates one specific kind of
choreography on top of it. Scenes are written by composing moves; raw
`self.play(FadeIn(...), run_time=1.0)` calls should be rare.

EVERY MOVE FOLLOWS THE SAME DISCIPLINE

  * First positional arg is `scene` (the active Manim Scene).
  * Every run_time and rate_func is sourced from `T.motion(name)` via
    `resolve_motion(...)`. No raw timings.
  * Docstrings cover BOTH "use when" AND "don't use when ..., use X
    instead". The inverse is what stops moves from being misapplied.

IMPORT CONTEXTS: same as chalkboard_components (render-side plain
module names, pipeline-side `docker.` package path).
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

try:
    from chalkboard_tokens import T  # type: ignore[import-not-found]
except ImportError:
    from pipeline.design_tokens import T  # noqa: F401

try:
    from chalkboard_components import (  # type: ignore[import-not-found]
        Callout as _Callout,
        EquationGroup as _EquationGroup,
        resolve_motion,
    )
except ImportError:
    from docker.chalkboard_components import (  # noqa: F401
        Callout as _Callout,
        EquationGroup as _EquationGroup,
        resolve_motion,
    )

from manim import (
    DOWN,
    Circumscribe,
    DashedLine,
    FadeIn,
    FadeOut,
    GrowFromCenter,
    LaggedStart,
    ReplacementTransform,
    Text,
    Transform,
    TransformMatchingShapes,
    TransformMatchingTex,
    UP,
    Write,
)
from manim.utils.color import ManimColor


# ── Helpers ───────────────────────────────────────────────────────────


def _motion(name: str) -> dict:
    """Shortcut for resolve_motion(T.motion(name))."""
    return resolve_motion(T.motion(name))


def _on_screen(scene, mob) -> bool:
    return mob in scene.get_mobject_family_members()


# ── Moves ─────────────────────────────────────────────────────────────


def reveal_with_emphasis(
    scene,
    item,
    *,
    motion_name: str = "emphasis",
) -> None:
    """Reveal one focal element with a breathy motion. The DEFAULT for
    "introduce one thing to the viewer."

    When NOT to use:
        - Several items revealing as a sequence → cascade_reveal.
        - A state change on an already-visible element → animate it
          directly (item.animate.set_color(...)).
        - Background scaffolding (axes, frames) → motion_name="snap" so
          it doesn't steal attention.
    """
    scene.play(FadeIn(item), **_motion(motion_name))


def compare_split(
    scene,
    left,
    right,
    *,
    add_divider: bool = True,
    motion_name: str = "emphasis",
    theme: str = "chalkboard",
) -> None:
    """Reveal two items side-by-side with an optional vertical divider.
    Position `left` at x < -0.5 and `right` at x > +0.5 before calling.

    When NOT to use:
        - More than two items → progressive_step or cascade_reveal.
        - Only one item is the focus, the other is context →
          reveal_with_emphasis on the focus and mute the context.
    """
    if add_divider:
        divider = DashedLine(
            start=UP * 3.0,
            end=DOWN * 3.0,
            stroke_color=ManimColor(T(theme=theme).role("stroke_muted")),
            stroke_width=T.stroke_width("hair"),
        )
        scene.play(GrowFromCenter(divider), **_motion("snap"))
    # Left lands a beat before right — viewers read left-to-right.
    scene.play(
        LaggedStart(FadeIn(left), FadeIn(right), lag_ratio=T.lag("cascade")),
        **_motion(motion_name),
    )


def focus_zoom(
    scene,
    target,
    *,
    others: Optional[Iterable] = None,
    scale: float = 1.15,
    motion_name: str = "emphasis",
) -> None:
    """Scale an ON-SCREEN target up while dimming the periphery.

    When NOT to use:
        - First-time reveal → reveal_with_emphasis.
        - The element is always the focal point → bake emphasis into
          its initial styling instead.
    """
    animations = [target.animate.scale(scale)]
    for m in others or []:
        animations.append(m.animate.set_opacity(0.4))
    scene.play(*animations, **_motion(motion_name))


def morph_show_equivalence(
    scene,
    from_form,
    to_form,
    *,
    motion_name: str = "settle",
) -> None:
    """Transform A into B in place — "these are the same thing in two
    forms." `to_form` must already be at its final position.

    When NOT to use:
        - The forms are similar but NOT equivalent → show B next to A.
        - A must stay visible → FadeIn(B) beside it.
        - Both are equations → transform_equation / derivation_step,
          which keep matching symbols in place.
    """
    scene.play(ReplacementTransform(from_form, to_form), **_motion(motion_name))


def cascade_reveal(
    scene,
    items: Sequence,
    *,
    lag_name: str = "cascade",
    motion_name: str = "emphasis",
) -> None:
    """Reveal items one-by-one with rhythm (list of properties, array
    cells, matched pairs).

    When NOT to use:
        - Single item → reveal_with_emphasis.
        - Items that should land as one visual group → lag_name="quick".
    """
    if not items:
        return
    scene.play(
        LaggedStart(*[FadeIn(m) for m in items], lag_ratio=T.lag(lag_name)),
        **_motion(motion_name),
    )


def progressive_step(
    scene,
    items: Sequence,
    current_idx: int,
    *,
    motion_name: str = "snap",
) -> None:
    """Walk an ordered set: highlight items[current_idx], mute the rest.
    Mechanical snap motion — algorithm transitions shouldn't breathe.

    When NOT to use:
        - Unordered sets (parallel branches) → cascade_reveal + selective
          highlights.
        - Recap where everything ends highlighted → LaggedStart of
          highlights instead.

    Args:
        items: components with .highlight() / .mute().
        current_idx: index of the focal item.
    """
    if not 0 <= current_idx < len(items):
        return
    # Build each item's end state on a copy and Transform into it, so the
    # change is actually animated (mutating in place first would make the
    # play a no-op jump).
    animations = []
    for i, m in enumerate(items):
        # Record as-built opacity BEFORE anything dims, so copies and the
        # post-play replay restore/dim from the true baseline.
        if hasattr(m, "_snapshot_opacity"):
            m._snapshot_opacity()
        target = m.copy()
        if i == current_idx:
            target.highlight("focus_primary")
        else:
            target.mute()
        animations.append(Transform(m, target))
    scene.play(*animations, **_motion(motion_name))
    # Transform interpolates points/colors but not Python attributes; apply
    # the same state change so later highlight()/mute() calls see the
    # right opacity snapshot (visually a no-op).
    for i, m in enumerate(items):
        if i == current_idx:
            m.highlight("focus_primary")
        else:
            m.mute()


def annotate_and_pause(
    scene,
    callout,
    *,
    hold_sec: float = 1.5,
    motion_name: str = "settle",
) -> None:
    """Add a Callout, hold it for reading, then dim it so it doesn't
    compete with the next move.

    When NOT to use:
        - The annotation should persist → reveal_with_emphasis alone.
        - The segment is short → drop the annotation.
    """
    scene.play(FadeIn(callout), **_motion(motion_name))
    if hold_sec > 0:
        scene.wait(hold_sec)
    if isinstance(callout, _Callout):
        scene.play(callout.animate.mute(), **_motion("snap"))
    else:
        scene.play(callout.animate.set_opacity(0.4), **_motion("snap"))


def chapter_transition(
    scene,
    from_items: Iterable,
    to_label: str,
    *,
    theme: str = "chalkboard",
    label_size: str = "title",
) -> Text:
    """Close the current section and open the next with a chapter title.
    Ceremonial (motion_grand) — at most once or twice per scene.

    When NOT to use:
        - Within-chapter transitions → cascade_reveal the next batch.
        - The label isn't a short (1–4 word) name.

    Returns the label so the caller can fade it out (or track it in
    seg_items) when the chapter's content arrives.
    """
    t = T(theme=theme)
    from_items = list(from_items or [])
    if from_items:
        scene.play(*[FadeOut(m) for m in from_items], **_motion("settle"))
    label = Text(
        to_label,
        font_size=t.type(label_size),
        color=ManimColor(t.role("focus_primary")),
    )
    label.set_color(ManimColor(t.role("focus_primary")))  # 0.21: sync .color
    scene.play(Write(label), **_motion("grand"))
    # Dim slightly so the chapter's content has visual priority.
    scene.play(label.animate.set_opacity(0.6), **_motion("snap"))
    return label


# ── Math moves ────────────────────────────────────────────────────────


def _transform_for(src, dst):
    """TransformMatchingTex when both expressions are split into several
    parts ({{ }} groups / multiple args, beyond the lhs/rhs split), else
    glyph-shape matching, which keeps every unchanged symbol in place
    without any manual splitting."""
    if len(getattr(src, "submobjects", [])) > 2 and len(getattr(dst, "submobjects", [])) > 2:
        return TransformMatchingTex(src, dst)
    return TransformMatchingShapes(src, dst)


def derivation_step(
    scene,
    eq,
    idx: int,
    *,
    from_idx: Optional[int] = None,
    dim_previous: bool = True,
    motion_name: str = "settle",
) -> None:
    """Reveal line `idx` of an EquationGroup by morphing a copy of the
    previous line into it: symbols that survive the step slide into
    place, new ones fade in, so the viewer SEES what changed. Earlier
    lines stay on screen, dimmed, with relations still aligned.

        eq = EquationGroup([...]); eq.move_to(ORIGIN)   # do NOT add it
        derivation_step(self, eq, 0)       # writes the first line
        derivation_step(self, eq, 1)       # line 0 -> line 1
        derivation_step(self, eq, 2)       # line 1 -> line 2

    When NOT to use:
        - The new line is not derived from the previous one (a separate
          fact) → reveal_with_emphasis(self, eq.lines[i]).
        - Replacing an expression in place, no history → transform_equation.

    Args:
        eq: the EquationGroup (lines already positioned, not yet added).
        idx: line to reveal. idx == 0 writes it.
        from_idx: source line (default idx - 1).
        dim_previous: dim the visible earlier lines to context_muted and
            bring the new line in at focus_primary (term colors kept).
    """
    is_group = isinstance(eq, _EquationGroup)
    line = eq.lines[idx]
    if dim_previous and is_group:
        eq._recolor_line(line, "focus_primary")
    if idx == 0 and from_idx is None:
        scene.play(Write(line), **_motion("emphasis"))
        return
    src_i = idx - 1 if from_idx is None else from_idx
    anims = [_transform_for(eq.lines[src_i].copy(), line)]
    if dim_previous and is_group:
        muted = ManimColor(eq._t.role("context_muted"))
        for j in range(len(eq.lines)):
            if j != idx and _on_screen(scene, eq.lines[j]):
                anims.append(eq.lines[j].animate.set_color(muted))
    scene.play(*anims, **_motion(motion_name))


def transform_equation(
    scene,
    from_eq,
    to_eq,
    *,
    motion_name: str = "settle",
) -> None:
    """Rewrite an expression in place (simplify, substitute, expand):
    matching symbols stay put, changed ones morph. Position `to_eq` where
    the result belongs first (often `to_eq.move_to(from_eq)`). Afterwards
    `to_eq` is on screen and `from_eq` is gone — track `to_eq` from then on.

    When NOT to use:
        - The history of steps matters → EquationGroup + derivation_step.
    """
    scene.play(_transform_for(from_eq, to_eq), **_motion(motion_name))


def emphasize_term(
    scene,
    expr,
    term: str,
    *,
    role: str = "focus_primary",
    theme: str = "chalkboard",
    motion_name: str = "emphasis",
) -> None:
    """Color one term of an on-screen MathTex by role and circle it, to
    point at it while narration explains it ("this 2x is the slope").
    The term must be isolated when the expression is built:
    math_tex(..., isolate=[term]) / EquationGroup(..., isolate=[term]) or
    colors={term: role}. Falls back to the whole expression otherwise.

    When NOT to use:
        - The whole expression is the point → reveal_with_emphasis or
          EquationGroup.focus().
    """
    color = ManimColor(T(theme=theme).role(role))
    # Recolor via a Transform of the WHOLE expression into a recolored copy.
    # Animating the term's own group (part.animate) would make Manim add that
    # group to the scene and split the expression out of its parent, which
    # breaks later dimming of the line.
    idx = [i for i, p in enumerate(getattr(expr, "submobjects", []))
           if getattr(p, "tex_string", None) == term]
    target = expr.copy()
    if idx:
        for i in idx:
            target.submobjects[i].set_color(color)
        ring = [expr.submobjects[i] for i in idx]
    else:
        part = target.get_part_by_tex(term) if hasattr(target, "get_part_by_tex") else None
        (part if part is not None else target).set_color(color)
        orig = expr.get_part_by_tex(term) if hasattr(expr, "get_part_by_tex") else None
        ring = [orig if orig is not None else expr]
    from manim import VGroup
    scene.play(
        Transform(expr, target),
        Circumscribe(VGroup(*ring).copy(), color=color, buff=T.space("xs"),
                     stroke_width=T.stroke_width("normal")),
        **_motion(motion_name),
    )


# ── Public exports ────────────────────────────────────────────────────

__all__ = [
    "reveal_with_emphasis",
    "compare_split",
    "focus_zoom",
    "morph_show_equivalence",
    "cascade_reveal",
    "progressive_step",
    "annotate_and_pause",
    "chapter_transition",
    "derivation_step",
    "transform_equation",
    "emphasize_term",
]
