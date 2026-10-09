# tests/test_chalkboard_components.py
"""Phase 2 — component library.

The components are thin VGroup subclasses that wrap Manim primitives with
design-token-driven defaults. These tests verify:

  * Each component is constructible without raising
  * Submobject structure matches the documented contract
  * Colors / sizes / strokes are token-derived (no raw hex literals)
  * highlight() / mute() correctly mutate the visual attributes
  * resolve_motion() round-trips a token motion spec to Manim kwargs

The whole file is gated on `pytest.importorskip("manim")` — without
Manim, the components module fails at top-level import (it does
`from manim import ...`). Locally these all skip; in CI / regression /
the Docker render image they run.
"""
import pytest

# Skip the whole file when Manim isn't installed. The components are
# meaningless without it.
manim = pytest.importorskip("manim")

from manim import RIGHT  # noqa: E402
from docker.chalkboard_components import (
    Callout,
    ChalkArrow,
    ChalkAxis,
    ChalkBadge,
    ChalkBox,
    ChalkCode,
    ChalkAxes,
    ChalkMatrix,
    ChalkPanel,
    EquationGroup,
    NetworkNode,
    StepCounter,
    resolve_motion,
    _RATE_FUNCS,
)
from pipeline.design_tokens import T


# ── resolve_motion ───────────────────────────────────────────────────


def test_resolve_motion_returns_callable_rate_func():
    kw = resolve_motion(T.motion("emphasis"))
    assert "run_time" in kw and "rate_func" in kw
    assert callable(kw["rate_func"])
    # Roundtrip: every named motion in tokens resolves to a real callable.
    for name in ("snap", "emphasis", "settle", "grand"):
        kw = resolve_motion(T.motion(name))
        assert callable(kw["rate_func"]), f"motion {name!r} did not resolve"


def test_resolve_motion_falls_back_for_unknown_func():
    """Unknown rate_func names degrade to `smooth` instead of crashing.

    A future tokens update may name a rate_func before this resolver
    table is taught about it. That should not kill the render pipeline.
    """
    spec = {"run_time": 1.0, "rate_func": "totally_made_up"}
    kw = resolve_motion(spec)
    assert kw["rate_func"] is _RATE_FUNCS["smooth"]


# ── ChalkBox ─────────────────────────────────────────────────────────


def test_chalkbox_constructs_with_default_role():
    box = ChalkBox("42")
    # VGroup with [RoundedRectangle, Text]
    assert len(box.submobjects) == 2
    inner_rect, inner_text = box.submobjects
    assert isinstance(inner_text, manim.Text)
    assert inner_text.original_text == "42"


def test_chalkbox_text_color_uses_token():
    box = ChalkBox("X", role="focus_primary")
    expected = T(theme="chalkboard").role("focus_primary")
    # Text color stored as r,g,b tuple — compare the hex round-trip
    label = box.submobjects[1]
    assert _to_hex(label.color).lower() == expected.lower()


def test_chalkbox_highlight_swaps_role():
    box = ChalkBox("X", role="body")
    box.highlight("focus_primary")
    expected = T(theme="chalkboard").role("focus_primary")
    label = box.submobjects[1]
    assert _to_hex(label.color).lower() == expected.lower()


def test_chalkbox_mute_recedes():
    box = ChalkBox("X", role="focus_primary")
    box.mute()
    expected = T(theme="chalkboard").role("context_muted")
    label = box.submobjects[1]
    assert _to_hex(label.color).lower() == expected.lower()
    # Opacity reduced
    assert box.get_fill_opacity() < 1.0 or box.fill_opacity < 1.0 if hasattr(box, "fill_opacity") else True


def test_chalkbox_theme_swap_changes_colors():
    box_dark = ChalkBox("X", role="focus_primary", theme="chalkboard")
    box_light = ChalkBox("X", role="focus_primary", theme="light")
    dark_color = _to_hex(box_dark.submobjects[1].color)
    light_color = _to_hex(box_light.submobjects[1].color)
    assert dark_color.lower() != light_color.lower(), (
        "ChalkBox in chalkboard vs light theme should pick different "
        "hex values for the same role"
    )


def test_chalkbox_autosizes_to_long_text():
    """ChalkBox auto-sizes its outer box to fit the label's actual
    width. Pre-fix, every box was 1.4 units wide and long labels
    overflowed catastrophically (e.g. "Plants make their own food"
    in photosynthesis spilled ~5 units past the box edge).
    """
    short = ChalkBox("X")
    long_text = ChalkBox("Plants make their own food")
    # Long-text box must be visibly wider than short-text box.
    # The exact factor depends on font metrics, but ~3x or more is
    # a safe expectation for ~26 chars vs 1 char.
    assert long_text.box.width > short.box.width * 2, (
        f"long-text box width={long_text.box.width:.2f} is not "
        f"meaningfully larger than short-text width={short.box.width:.2f}; "
        f"auto-sizing is not engaged"
    )
    # Long-text box must actually CONTAIN the label, not just be wider.
    assert long_text.box.width > long_text.label.width, (
        f"box width={long_text.box.width:.2f} does not contain "
        f"label width={long_text.label.width:.2f} — label will overflow"
    )


def test_chalkbox_floor_for_single_chars():
    """Single-character / short labels are floored at the legacy
    default width so an array like [3, 7, 11, 14] still produces
    uniform-width cells (not a tiny "3" next to a wider "14").
    """
    cells = [ChalkBox(str(v)) for v in (3, 7, 11, 14)]
    widths = [c.box.width for c in cells]
    # All cells should be the floor width (within float tolerance) —
    # short labels don't exceed the floor.
    assert max(widths) - min(widths) < 0.01, (
        f"short-label cells have non-uniform widths {widths}; the "
        f"floor isn't preserving array rhythm"
    )


def test_chalkbox_explicit_width_is_a_floor_not_a_ceiling():
    """Explicit width= is a FLOOR (PR #341 follow-up): the agent
    says "I want at least this big," and we ensure the box still
    contains its label by taking max(explicit, auto). Previously
    explicit width WAS a ceiling, which let the agent accidentally
    produce overflow by passing a too-small explicit value.
    """
    # Long label + small explicit width → auto-size wins
    b1 = ChalkBox("Plants make their own food", width=1.4)
    assert b1.box.width > 1.4, (
        f"explicit width=1.4 should have been overridden by auto-size "
        f"to fit the long label; got {b1.box.width:.2f}"
    )
    assert b1.box.width > b1.label.width, (
        f"box must contain label; box={b1.box.width:.2f} "
        f"label={b1.label.width:.2f}"
    )
    # Short label + explicit width LARGER than auto-size → explicit wins
    # (uniform-array-cell use case: AlgorithmTemplate passes the same
    # explicit width to every cell; "3" and "311" both get that width).
    b2 = ChalkBox("3", width=2.0)
    assert abs(b2.box.width - 2.0) < 0.01, (
        f"explicit width=2.0 should win when label is small enough "
        f"to fit; got {b2.box.width:.2f}"
    )


def test_chalkbox_explicit_height_is_a_floor_not_a_ceiling():
    """Same as width — explicit height is a floor for multi-line labels."""
    b1 = ChalkBox("line1\nline2\nline3", height=0.5)
    assert b1.box.height > 0.5
    assert b1.box.height > b1.label.height


def test_chalkbox_multiline_grows_taller():
    """Multi-line labels need a taller box. Pre-fix, a ChalkBox with
    `"Wikipedia\\n2001"` (timeline-web segment 4) had the same height
    as a single-line box, so the second line spilled below the box.
    """
    single = ChalkBox("X")
    multi = ChalkBox("Wikipedia\n2001\nCERN")
    assert multi.box.height > single.box.height, (
        f"multi-line box height={multi.box.height:.2f} is not taller "
        f"than single-line height={single.box.height:.2f}"
    )


def test_chalkbox_highlight_preserves_fill():
    """highlight() must NOT clobber fill_color. The pre-fix base-class
    `highlight()` set fill to the role color, which combined with the
    text also being the role color produced an invisible-text bug
    (timeline-web's highlighted NetworkNode for Tim Berners-Lee).

    After the fix, fill stays as bg_subtle so stroke + text retain
    contrast.
    """
    bg_subtle = T(theme="chalkboard").surface("bg_subtle")
    box = ChalkBox("Test", role="body")
    fill_before = _to_hex(box.box.get_fill_color())
    assert fill_before.lower() == bg_subtle.lower(), (
        f"initial fill={fill_before} should be bg_subtle={bg_subtle}"
    )
    box.highlight("focus_primary")
    fill_after = _to_hex(box.box.get_fill_color())
    assert fill_after.lower() == bg_subtle.lower(), (
        f"after highlight, fill={fill_after} must still be "
        f"bg_subtle={bg_subtle} (not the role color, otherwise text "
        f"is invisible on the same-color fill)"
    )
    # Stroke should HAVE changed to the role color
    expected_stroke = T(theme="chalkboard").role("focus_primary")
    actual_stroke = _to_hex(box.box.get_stroke_color())
    assert actual_stroke.lower() == expected_stroke.lower(), (
        f"highlight stroke={actual_stroke} should be role "
        f"focus_primary={expected_stroke}"
    )


# ── ChalkArrow ───────────────────────────────────────────────────────


def test_chalkarrow_straight_default():
    arrow = ChalkArrow([0, 0, 0], [1, 0, 0])
    assert len(arrow.submobjects) == 1
    assert isinstance(arrow.submobjects[0], manim.Arrow)


def test_chalkarrow_curve_picks_curvedarrow():
    arrow = ChalkArrow([0, 0, 0], [1, 0, 0], curve=True)
    assert isinstance(arrow.submobjects[0], manim.CurvedArrow)


# ── ChalkCode ────────────────────────────────────────────────────────


def test_chalkcode_uses_correct_kwarg():
    """The v0.20.1 Code constructor takes code_string=, NOT code=.

    Regression for the well-documented 0.20.1 pitfall — would raise
    TypeError at construction if we ever revert.
    """
    cc = ChalkCode("print('hi')\nprint('bye')")
    assert cc.code_obj is not None
    # code_lines accessor works
    assert len(cc.code_lines) >= 2


def test_chalkcode_lines_property_aliases_underlying():
    cc = ChalkCode("x = 1\ny = 2")
    assert cc.code_lines is cc.code_obj.code_lines


# ── Callout ──────────────────────────────────────────────────────────


def test_callout_has_connector_and_label():
    cb = Callout("Look here", [0, 0, 0])
    # connector (Line) + label (Text)
    types = [type(m).__name__ for m in cb.submobjects]
    assert "Line" in types
    assert "Text" in types


def test_callout_connector_endpoint_for_up_offset():
    """offset=UP * N → label is ABOVE anchor → connector exits the
    label's bottom edge and goes down to the anchor.
    """
    import numpy as np
    anchor = np.array([0.0, 0.0, 0.0])
    cb = Callout("note", anchor, offset=np.array([0.0, 1.5, 0.0]))
    # connector.get_start() should be near label.get_bottom()
    start = cb.connector.get_start()
    label_bottom = cb.label.get_bottom()
    assert abs(start[1] - label_bottom[1]) < 0.01, (
        f"with UP offset, connector start y={start[1]:.2f} should "
        f"match label bottom y={label_bottom[1]:.2f}"
    )


def test_callout_connector_endpoint_for_right_offset():
    """offset=RIGHT * N → label is to the RIGHT of anchor → connector
    exits the label's LEFT edge and goes left to the anchor.

    Regression for the pre-fix bug where the connector unconditionally
    chose top/bottom based on offset[1], so a horizontal offset
    produced a diagonal connector slashing through the canvas. The
    howto-cloudsql "Your future self will thank you" callout hit
    exactly this case.
    """
    import numpy as np
    anchor = np.array([0.0, 0.0, 0.0])
    cb = Callout("note", anchor, offset=np.array([1.5, 0.0, 0.0]))
    start = cb.connector.get_start()
    label_left = cb.label.get_left()
    assert abs(start[0] - label_left[0]) < 0.01, (
        f"with RIGHT offset, connector start x={start[0]:.2f} should "
        f"match label left x={label_left[0]:.2f}"
    )


def test_callout_connector_endpoint_for_left_offset():
    """offset=LEFT * N → label is to the LEFT of anchor → connector
    exits the label's RIGHT edge.
    """
    import numpy as np
    anchor = np.array([0.0, 0.0, 0.0])
    cb = Callout("note", anchor, offset=np.array([-1.5, 0.0, 0.0]))
    start = cb.connector.get_start()
    label_right = cb.label.get_right()
    assert abs(start[0] - label_right[0]) < 0.01, (
        f"with LEFT offset, connector start x={start[0]:.2f} should "
        f"match label right x={label_right[0]:.2f}"
    )


# ── StepCounter ──────────────────────────────────────────────────────


def test_stepcounter_initial_state():
    sc = StepCounter(total=5)
    assert sc.submobjects[0].original_text == "Step 0 / 5"


def test_stepcounter_advance_updates_text():
    sc = StepCounter(total=3)
    sc.advance()
    assert "Step 1" in sc.submobjects[0].original_text
    sc.advance()
    assert "Step 2" in sc.submobjects[0].original_text


def test_stepcounter_advance_clamped_to_total():
    sc = StepCounter(total=2)
    sc.advance(); sc.advance(); sc.advance(); sc.advance()
    assert "Step 2" in sc.submobjects[0].original_text
    assert "/ 2" in sc.submobjects[0].original_text


# ── ChalkAxis ────────────────────────────────────────────────────────


def test_chalkaxis_constructs():
    ax = ChalkAxis([0, 10, 1], length=4.0)
    assert ax.number_line is not None


def test_chalkaxis_stroke_uses_token():
    ax = ChalkAxis([0, 10, 1], role="stroke")
    expected = T(theme="chalkboard").role("stroke")
    assert _to_hex(ax.number_line.get_stroke_color()).lower() == expected.lower()


# ── ChalkPanel ───────────────────────────────────────────────────────


def test_chalkpanel_with_title():
    p = ChalkPanel(title="Definitions")
    assert p.frame is not None
    assert p.title_obj is not None
    assert p.title_obj.text == "Definitions"


def test_chalkpanel_no_title():
    p = ChalkPanel()
    assert p.frame is not None
    # `title_obj` always exists post-fix; None signals "no title set"
    # so body-zone accessors can branch on it without hasattr().
    assert p.title_obj is None


def test_chalkpanel_body_center_with_no_title():
    """When the panel has no title, the body zone IS the full frame,
    so body_center equals the frame's geometric center.
    """
    p = ChalkPanel(width=4.0, height=3.0)
    body = p.body_center
    frame_center = p.frame.get_center()
    assert abs(body[0] - frame_center[0]) < 0.01
    assert abs(body[1] - frame_center[1]) < 0.01


def test_chalkpanel_body_center_with_title_is_below_frame_center():
    """When titled, the body zone excludes the top strip occupied by
    the title. body_center should sit BELOW the frame center.

    Pre-fix bug: agent placed content at `panel.get_center()` and it
    collided with the title (timeline-web seg 1 Mosaic browser
    overlapping the "1993 — Mosaic" panel title).
    """
    p = ChalkPanel(title="A long title", width=5.0, height=4.0)
    body_y = p.body_center[1]
    frame_y = p.frame.get_center()[1]
    assert body_y < frame_y, (
        f"body_center y={body_y:.2f} should be below frame center "
        f"y={frame_y:.2f} when title is present"
    )


def test_chalkpanel_body_top_is_below_title():
    """body_top must sit below the title's bottom edge — with
    additional buff so content doesn't visually crowd the title.
    """
    p = ChalkPanel(title="X", width=4.0, height=3.0)
    body_top_y = p.body_top[1]
    title_bottom_y = p.title_obj.get_bottom()[1]
    assert body_top_y < title_bottom_y, (
        f"body_top y={body_top_y:.2f} should be strictly below "
        f"title bottom y={title_bottom_y:.2f}"
    )


def test_chalkpanel_body_bottom_equals_frame_bottom():
    p = ChalkPanel(title="X", width=4.0, height=3.0)
    body_bot_y = p.body_bottom[1]
    frame_bot_y = p.frame.get_bottom()[1]
    assert abs(body_bot_y - frame_bot_y) < 0.01


def test_chalkpanel_body_height_smaller_than_frame_with_title():
    p_with = ChalkPanel(title="A long title", width=5.0, height=4.0)
    p_without = ChalkPanel(width=5.0, height=4.0)
    assert p_with.body_height < p_without.body_height
    assert p_without.body_height == pytest.approx(p_without.frame.height, abs=0.05)


def test_chalkpanel_body_width_equals_frame_width():
    """Body zone has the same horizontal extent as the frame — only
    vertical area is reduced by the title.
    """
    p = ChalkPanel(title="X", width=5.0, height=4.0)
    assert abs(p.body_width - p.frame.width) < 0.01


# ── ChalkBadge ───────────────────────────────────────────────────────


def test_chalkbadge_pill_and_label():
    b = ChalkBadge("ACTIVE")
    assert b.pill is not None
    assert b.label is not None
    assert b.label.text == "ACTIVE"


def test_chalkbadge_highlight_swaps_pill_fill():
    b = ChalkBadge("X", role="accent_meta")
    before = _to_hex(b.pill.get_fill_color())
    b.highlight("focus_primary")
    after = _to_hex(b.pill.get_fill_color())
    assert before.lower() != after.lower()


# ── EquationGroup ────────────────────────────────────────────────────


def test_equationgroup_one_mobject_per_line():
    eg = EquationGroup(["a + b = c", "c - a = b"])
    assert len(eg.lines) == 2
    assert len(eg.submobjects) == 2


def test_equationgroup_focus_highlights_one():
    eg = EquationGroup(["a", "b", "c"])
    eg.focus(1)
    primary = T(theme="chalkboard").role("focus_primary")
    muted = T(theme="chalkboard").role("context_muted")
    assert _to_hex(eg.lines[0].color).lower() == muted.lower()
    assert _to_hex(eg.lines[1].color).lower() == primary.lower()
    assert _to_hex(eg.lines[2].color).lower() == muted.lower()


# ── NetworkNode ──────────────────────────────────────────────────────


def test_networknode_circle_and_text():
    n = NetworkNode("S")
    assert n.circle is not None
    assert n.label.text == "S"


def test_networknode_autosizes_to_long_label():
    """NetworkNode auto-sizes its circle radius to contain the label.

    Pre-fix bug: timeline-web's `NetworkNode("Tim\\nBerners-Lee",
    radius=0.85)` — the label was ~2x wider than the 0.85 radius
    allowed, so the text spilled out. After .highlight() filled the
    circle yellow, the visible text was reduced to only the parts
    that overflowed the now-opaque circle.
    """
    short = NetworkNode("S")
    long_node = NetworkNode("Tim Berners-Lee")
    assert long_node.circle.radius > short.circle.radius * 2, (
        f"long-label radius={long_node.circle.radius:.2f} is not "
        f"meaningfully larger than short-label radius="
        f"{short.circle.radius:.2f}"
    )
    # The circle must actually contain the label.
    assert long_node.circle.radius * 2 > long_node.label.width, (
        f"circle diameter={long_node.circle.radius * 2:.2f} does not "
        f"contain label width={long_node.label.width:.2f}"
    )


def test_networknode_floor_for_single_chars():
    """Short labels respect the 0.5 floor so a graph of nodes named
    "A", "B", "C", "D" still produces uniform-sized circles.
    """
    nodes = [NetworkNode(c) for c in ("A", "B", "C", "D")]
    radii = [n.circle.radius for n in nodes]
    assert max(radii) - min(radii) < 0.01


def test_networknode_explicit_radius_is_a_floor_not_a_ceiling():
    """Explicit radius= is a FLOOR (PR #341 follow-up). Pre-fix bug
    (timeline-web seg 3): the agent wrote `NetworkNode("platform",
    radius=0.6)`. Explicit 0.6 was honored as a ceiling, so the
    label spilled outside the circle. With max(explicit, auto),
    overflow is structurally impossible.
    """
    # Long label + small explicit radius → auto-size wins
    n1 = NetworkNode("platform", radius=0.6)
    assert n1.circle.radius > 0.6
    assert n1.circle.radius * 2 > n1.label.width
    # Short label + explicit radius LARGER than auto → explicit wins
    n2 = NetworkNode("A", radius=1.0)
    assert abs(n2.circle.radius - 1.0) < 0.01


def test_networknode_highlight_preserves_fill():
    """Same fill-preservation contract as ChalkBox. Pre-fix, calling
    .highlight() on a NetworkNode set the circle fill to the role
    color, making the same-color label inside invisible.
    """
    bg_subtle = T(theme="chalkboard").surface("bg_subtle")
    n = NetworkNode("X", role="body")
    fill_before = _to_hex(n.circle.get_fill_color())
    assert fill_before.lower() == bg_subtle.lower()
    n.highlight("focus_primary")
    fill_after = _to_hex(n.circle.get_fill_color())
    assert fill_after.lower() == bg_subtle.lower(), (
        f"after highlight, NetworkNode circle fill={fill_after} must "
        f"still be bg_subtle={bg_subtle} so the same-color text "
        f"inside stays readable"
    )


# ── Cross-cutting: no raw hex literals leaked ────────────────────────


def test_components_use_only_token_colors():
    """Every color set on a component's submobjects must be reachable
    by walking the token table — proves the design-system contract:
    no hardcoded hex inside component logic.
    """
    valid_hexes = _all_token_hexes("chalkboard")
    for component in (
        ChalkBox("X"),
        ChalkArrow([0, 0, 0], [1, 0, 0]),
        # ChalkCode omitted: Code draws its own window chrome (traffic-light dots).
        Callout("note", [0, 0, 0]),
        StepCounter(total=3),
        ChalkPanel(title="P"),
        ChalkBadge("B"),
        EquationGroup(["x"]),
        NetworkNode("N"),
        ChalkMatrix([[1, 2], [3, 4]]),
        ChalkAxes([0, 3, 1], [0, 3, 1]),
    ):
        for m in _walk_submobjects(component):
            for color_attr in ("color", "fill_color", "stroke_color"):
                v = getattr(m, color_attr, None)
                if v is None:
                    continue
                hexv = _to_hex(v).lower()
                # Black + white are Manim-internal defaults on some
                # primitives we don't override (Code background); skip.
                if hexv in ("#000000", "#ffffff"):
                    continue
                assert hexv in valid_hexes, (
                    f"{type(m).__name__}.{color_attr} = {hexv} is not a "
                    f"token value. Components must source colors via "
                    f"T.role(...) or T.surface(...)."
                )


# ── helpers ──────────────────────────────────────────────────────────


def _to_hex(color) -> str:
    """Normalize Manim's color types into a #RRGGBB string."""
    if isinstance(color, str):
        return color if color.startswith("#") else manim.ManimColor(color).to_hex()
    # ManimColor / numpy rgba / 3-tuple — use ManimColor as canonical
    mc = manim.ManimColor(color)
    return mc.to_hex()


def _walk_submobjects(mobj, depth: int = 5):
    """Yield every submobject recursively up to a depth cap."""
    yield mobj
    if depth <= 0:
        return
    for sub in getattr(mobj, "submobjects", []):
        yield from _walk_submobjects(sub, depth - 1)


def _all_token_hexes(theme: str) -> set[str]:
    """All hex values reachable through the token table for the given theme.

    Components are allowed to use any of these (and only these).
    """
    t = T(theme=theme)
    hexes: set[str] = set()
    for k in ("bg", "bg_subtle", "grid"):
        hexes.add(t.surface(k).lower())
    for k in (
        "focus_primary", "focus_secondary", "context_muted",
        "accent_warm", "accent_cool", "accent_meta",
        "body", "stroke", "stroke_muted",
    ):
        hexes.add(t.role(k).lower())
    return hexes


# ── Math: helpers, alignment, coloring, matrices, axes ───────────────

import docker.chalkboard_components as cc  # noqa: E402
from docker.chalkboard_components import _safe_split, _split_alignment, math_tex, tex  # noqa: E402


def test_tex_template_hook_defaults_to_house_style():
    """TEX_TEMPLATE is the single LaTeX hook; it defaults to the house
    template from chalkboard_style, and every helper uses it."""
    import docker.chalkboard_style as style
    assert cc.TEX_TEMPLATE is style.TEX_TEMPLATE
    m = math_tex(r"\dd x")  # \dd only exists in the house preamble
    assert m.tex_template is cc.TEX_TEMPLATE


def test_tex_template_hook_is_swappable(monkeypatch):
    custom = manim.TexTemplate()
    monkeypatch.setattr(cc, "TEX_TEMPLATE", custom)
    assert math_tex("x").tex_template is custom
    assert EquationGroup(["a = b"]).lines[0].tex_template is custom


def test_math_tex_uses_token_size_and_role():
    m = math_tex("x^2", size="math", role="focus_primary")
    assert m.font_size == pytest.approx(T.type("math"))
    primary = T(theme="chalkboard").role("focus_primary")
    assert _to_hex(m[0].get_fill_color()).lower() == primary.lower()


def test_safe_split_never_matches_inside_commands():
    assert _safe_split(r"\left( 2x + h \right)", ["h"]) == [r"\left( 2x + ", "h", r" \right)"]
    assert _safe_split(r"\hat{x} + h", ["h"]) == [r"\hat{x} + ", "h"]
    assert _safe_split(r"\pi r^2", [r"\pi"]) == [r"\pi", " r^2"]
    assert _safe_split(r"\pix", [r"\pi"]) == [r"\pix"]


def test_math_tex_colors_terms_by_role_even_near_commands():
    """colors={"h": ...} must not break "\\right" (Manim's own isolation would)."""
    m = math_tex(r"\left( 2x + h \right)", colors={"h": "accent_cool"})
    cool = T(theme="chalkboard").role("accent_cool")
    parts = {p.tex_string: p for p in m.submobjects}
    assert "h" in parts
    assert _to_hex(parts["h"].get_fill_color()).lower() == cool.lower()


def test_tex_helper_typesets_inline_math():
    m = tex(r"slope $= \frac{\Delta y}{\Delta x}$")
    assert isinstance(m, manim.Tex)
    assert m.font_size == pytest.approx(T.type("body") * cc.TEX_PROSE_SCALE)


def test_split_alignment_ignores_matrix_ampersands():
    line = r"A &= \begin{pmatrix} 1 & 2 \\ 3 & 4 \end{pmatrix}"
    lhs, rhs = _split_alignment(line)
    assert lhs.strip() == "A"
    assert rhs.count("&") == 2


def test_equationgroup_aligns_relations():
    """Every relation sign sits on the same x — the align* look."""
    eq = EquationGroup([
        r"f(x) &= x^2 + 3x",
        r"f'(x) &= \lim_{h \to 0} \frac{f(x+h) - f(x)}{h}",
        r"&= 2x + 3",
    ])
    xs = [rhs.get_left()[0] for rhs in eq.rhs]
    assert max(xs) - min(xs) < 1e-6


def test_equationgroup_auto_aligns_without_ampersand():
    eq = EquationGroup([r"y = mx + b", r"\frac{\dd y}{\dd x} = m"])
    xs = [rhs.get_left()[0] for rhs in eq.rhs]
    assert all(r is not None for r in eq.rhs)
    assert max(xs) - min(xs) < 1e-6


def test_equationgroup_lines_do_not_overlap_vertically():
    eq = EquationGroup([r"\sum_{i=1}^{n} i &= \frac{n(n+1)}{2}", r"&= \frac{n^2 + n}{2}"])
    assert eq.lines[0].get_bottom()[1] > eq.lines[1].get_top()[1]


def test_equationgroup_colors_terms_on_every_line():
    eq = EquationGroup([r"F &= m a", r"a &= \frac{F}{m}"], colors={"m": "accent_cool"})
    cool = T(theme="chalkboard").role("accent_cool").lower()
    for line in eq.lines:
        m_parts = [p for p in line.submobjects if p.tex_string == "m"]
        assert m_parts and all(_to_hex(p.get_fill_color()).lower() == cool for p in m_parts)


def test_equationgroup_focus_keeps_term_colors():
    eq = EquationGroup([r"F &= m a", r"a &= \frac{F}{m}"], colors={"m": "accent_cool"})
    eq.focus(1)
    cool = T(theme="chalkboard").role("accent_cool").lower()
    m_part = [p for p in eq.lines[1].submobjects if p.tex_string == "m"][0]
    assert _to_hex(m_part.get_fill_color()).lower() == cool


def test_equationgroup_highlight_does_not_stroke_glyphs():
    """Stroking TeX glyphs makes them look bold and blurry."""
    eq = EquationGroup(["a = b"])
    eq.highlight("focus_primary")
    assert all(m.get_stroke_width() == 0 for m in eq.lines[0].get_family() if m.has_points())


def test_chalkmatrix_entries_rows_columns():
    A = ChalkMatrix([[2, -1], [1, 3]])
    assert A.n_rows == 2 and A.n_cols == 2
    A.highlight_row(0)
    A.highlight_column(1, role="accent_cool")
    primary = T(theme="chalkboard").role("focus_primary").lower()
    cool = T(theme="chalkboard").role("accent_cool").lower()
    assert _to_hex(A.entry(0, 0).get_fill_color()).lower() == primary
    assert _to_hex(A.entry(1, 1).get_fill_color()).lower() == cool


def test_chalkaxes_plot_area_tangent():
    ax = ChalkAxes([0, 3, 1], [0, 9, 3], x_label="x", y_label="f(x)")
    curve = ax.plot(lambda x: x ** 2, x_range=[0, 3])
    area = ax.area(curve, x_range=[0, 2])
    tangent = ax.tangent(lambda x: x ** 2, x=1.5)
    assert curve.has_points() and area.has_points()
    # tangent passes through (1.5, 2.25)
    p = ax.c2p(1.5, 2.25)
    assert abs(((tangent.get_start() + tangent.get_end()) / 2 - p)).max() < 1e-6


def test_chalkbox_math_label():
    b = ChalkBox(r"\frac{1}{2}", math=True)
    assert isinstance(b.label, manim.MathTex)


def test_highlight_after_mute_restores_opacity():
    """A cell muted in one step and focused in the next must be fully opaque."""
    b = ChalkBox("7")
    before = [(m.get_fill_opacity(), m.get_stroke_opacity()) for m in b.get_family()]
    b.mute()
    b.highlight("focus_primary")
    after = [(m.get_fill_opacity(), m.get_stroke_opacity()) for m in b.get_family()]
    assert before == after


def test_panel_highlight_after_mute_keeps_frame_transparent():
    p = ChalkPanel(title="P")
    p.mute()
    p.highlight("focus_primary")
    assert p.frame.get_fill_opacity() == 0


def test_chalkcode_survives_ligature_operators():
    """'<=' / '->' break fonts with programming ligatures; ChalkCode must
    fall back to a ligature-free face instead of crashing."""
    c = ChalkCode("def f(n):\n    if n <= 1:\n        return n\n    return f(n - 1)")
    assert len(c.code_lines) == 4


def test_callout_wraps_long_text_and_stays_on_canvas():
    c = Callout("A rather long annotation that would run off the right edge", [6.5, 0, 0],
                offset=RIGHT * 1.0)
    assert c.label.width <= 4.5 + 1e-6
    assert c.label.get_right()[0] <= 7.11


# ── Axis tick labels follow the step (run 3253240e: "0, 1, 2, 2, 2") ──

def _tick_labels(number_line) -> list[str]:
    d = number_line.decimal_number_config["num_decimal_places"]
    return [f"{float(n.number):.{d}f}" for n in number_line.numbers]


def test_chalkaxes_half_step_ticks_show_one_decimal():
    ax = ChalkAxes([0, 6, 1], [0, 2.5, 0.5])
    assert _tick_labels(ax.axes.y_axis) == ["0.5", "1.0", "1.5", "2.0", "2.5"]
    assert _tick_labels(ax.axes.x_axis) == ["1", "2", "3", "4", "5", "6"]


def test_chalkaxes_keeps_token_number_color_with_decimals():
    ax = ChalkAxes([0, 1, 0.25], [0, 3, 1])
    cfg = ax.axes.x_axis.decimal_number_config
    assert cfg["num_decimal_places"] == 2 and "color" in cfg
    assert _tick_labels(ax.axes.x_axis) == ["0.25", "0.50", "0.75", "1.00"]


def test_chalkaxis_quarter_steps():
    ax = ChalkAxis([0, 1, 0.25], length=4.0)
    assert _tick_labels(ax.number_line) == ["0.00", "0.25", "0.50", "0.75", "1.00"]


def test_chalkaxes_hline_label_sits_outside_the_plot():
    ax = ChalkAxes([0, 6, 1], [0, 2.5, 0.5], x_length=5.2, y_length=4.4)
    ref = ax.hline(1.9, label=r"V_{in} = 1.9\,\mathrm{V}")
    assert ref.label is not None and ref.line in ref.submobjects
    assert ref.label.get_left()[0] >= ax.c2p(6, 0)[0]          # right of the plot
    assert abs(ref.label.get_center()[1] - ax.c2p(0, 1.9)[1]) < 1e-6
    v = ax.vline(1.3, label=r"\tau")
    assert v.label.get_bottom()[1] >= ax.c2p(0, 2.5)[1]         # above the plot
    assert ax.hline(1.0).label is None
