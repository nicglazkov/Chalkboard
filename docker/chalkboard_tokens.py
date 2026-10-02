"""Semantic design tokens for Chalkboard scenes.

Single source of truth for every color, typography size, spacing unit,
stroke width, and motion specification used by generated Manim scenes.
SINGLE SOURCE: this file (docker/chalkboard_tokens.py) is the only copy.
It is imported two ways:

  • Render-side: generated scenes run with docker/ (or /render inside
    the container, where the Dockerfile COPYs this file) on PYTHONPATH
    and write `from chalkboard_tokens import T`.
  • Pipeline-side: `pipeline/design_tokens.py` is a thin shim that loads
    THIS file by path and re-exports it, so manim_agent can format the
    prompt's token block from the same values the renderer uses. Never
    copy the tables into pipeline/; edit them here.

Zero Manim imports at module level — components and moves resolve
motion `rate_func` strings to callables at the call site (`manim.utils.
rate_functions`). Keeping this module Manim-free means the pipeline can
introspect tokens without paying for the Manim import chain.

Design principles baked in:

  1. NEVER reference a raw color literal in scene code. Refer to a
     semantic role (`focus_primary`, `body`, `stroke_muted`, ...).
     The role names describe what the color MEANS, not how it looks —
     so a scene rendered in `chalkboard` vs `light` theme still tells
     the viewer the same story.

  2. Typography, spacing, stroke widths, and motion are theme-
     invariant — they're physical units of the canvas, not visual
     styling. Only color (`surface` + `role`) and the background of
     the canvas change with theme. This matches how real design
     systems separate "tokens" (the brand layer) from "spec" (the
     measurement layer).

  3. Motion vocabulary names ENCODE PEDAGOGICAL INTENT. `motion_snap`
     is for "the state just changed and you need to know"; `motion_
     emphasis` is for "look at this, it matters"; `motion_settle` is
     for "this is the final state, breathe"; `motion_grand` is for
     "chapter or topic boundary." A scene that uses raw `run_time=1.0`
     loses this signal — that's why no scene code should ever write a
     raw `run_time` literal.
"""
from __future__ import annotations

from typing import Literal, TypedDict


Theme = Literal["chalkboard", "light", "colorful"]
DEFAULT_THEME: Theme = "chalkboard"


class _Surface(TypedDict):
    bg: str          # canvas background — self.camera.background_color
    bg_subtle: str   # panel / sub-region background, slight contrast vs bg
    grid: str        # grid lines, faint guides, scaffold strokes


class _Role(TypedDict):
    focus_primary: str    # the primary attention magnet — pivot, active step, focal cell
    focus_secondary: str  # the tracked comparison element — never the magnet, always paired
    context_muted: str    # de-emphasized supporting text / past steps / dimmed states
    accent_warm: str      # error / heat / warning / outlier
    accent_cool: str      # data / signal / measured value
    accent_meta: str      # tertiary annotation — callouts that aren't the primary point
    body: str             # default text and shape color for the theme
    stroke: str           # default stroke for outlines (boxes, arrows, frames)
    stroke_muted: str     # de-emphasized stroke for dimmed elements


class _ThemeBlock(TypedDict):
    surface: _Surface
    role: _Role


# ── Theme-specific tokens ─────────────────────────────────────────────
#
# Each theme is a complete swap: changing `theme` from "chalkboard" to
# "light" should re-color every element of a scene without changing
# layout, typography, or motion. Roles map 1:1 across themes — the
# values differ but the meaning does not.
_THEMES: dict[Theme, _ThemeBlock] = {
    "chalkboard": {
        "surface": {
            "bg":        "#1C1C1C",
            "bg_subtle": "#252525",
            "grid":      "#2E2E2E",
        },
        "role": {
            "focus_primary":   "#E8D44D",  # chalk yellow — the magnet
            "focus_secondary": "#6EC6E8",  # sky blue — the paired element
            "context_muted":   "#7A756A",  # dimmed cream — past steps
            "accent_warm":     "#E07856",  # warm orange — error / heat
            "accent_cool":     "#7BD3F7",  # cool cyan — data signal (distinct from focus_secondary)
            "accent_meta":     "#B49FE3",  # lavender — meta annotations
            "body":            "#F5F0E8",  # cream — main text
            "stroke":          "#F5F0E8",  # cream — default outline
            "stroke_muted":    "#7A756A",  # dim cream — de-emphasized outline
        },
    },
    "light": {
        "surface": {
            "bg":        "#FAFAFA",
            "bg_subtle": "#F0F0F0",
            "grid":      "#E5E5E5",
        },
        "role": {
            "focus_primary":   "#DC2626",  # red 600 — strong attention on light bg
            "focus_secondary": "#2563EB",  # blue 600 — comparison
            "context_muted":   "#9CA3AF",  # gray 400 — past steps
            "accent_warm":     "#EA580C",  # orange 600
            "accent_cool":     "#0891B2",  # cyan 600
            "accent_meta":     "#7C3AED",  # violet 600
            "body":            "#1A1A1A",  # near-black
            "stroke":          "#1A1A1A",
            "stroke_muted":    "#9CA3AF",
        },
    },
    "colorful": {
        "surface": {
            "bg":        "#000000",
            "bg_subtle": "#1A1A1A",
            "grid":      "#2E2E2E",
        },
        "role": {
            "focus_primary":   "#FBBF24",  # amber 400 — bright on black
            "focus_secondary": "#34D399",  # emerald 400
            "context_muted":   "#6B7280",  # gray 500
            "accent_warm":     "#FB7185",  # rose 400
            "accent_cool":     "#38BDF8",  # sky 400
            "accent_meta":     "#C084FC",  # purple 400
            "body":            "#FFFFFF",
            "stroke":          "#FFFFFF",
            "stroke_muted":    "#6B7280",
        },
    },
}


# ── Theme-invariant tokens ────────────────────────────────────────────
#
# These don't change per-theme. Typography sizes are in Manim font_size
# units. Spacing is in Manim canvas units (the canvas is 14.22 × 8.0).
# Stroke widths are in Manim's stroke_width units.

# Typography scale — log-ish progression. Each role tells the agent
# WHEN to use a size, not just what size it is. The scale is named so
# the prompt can forbid raw numeric font sizes in Phase 5.
_TYPE_SIZE: dict[str, int] = {
    "display": 56,   # one-word topic punch — rare, only for opening titles
    "title":   42,   # scene title / chapter heading
    "heading": 32,   # section heading inside a multi-segment scene
    "body":    26,   # standard narration-adjacent text
    "caption": 20,   # secondary text under a focal element
    "code":    22,   # Code() paragraph_config font_size
    "micro":   16,   # axis labels, tick text, hover tags
    # Display math (MathTex/Tex). Sits off the text hierarchy on purpose:
    # TeX glyphs at a given font_size read smaller than Pango text at the
    # same number, and a derivation is usually the focal element, so it
    # gets its own slot rather than borrowing "heading". Inline math next
    # to body text should use "body" so baselines match.
    "math":    40,
}

# Spacing scale — for `.next_to(..., buff=T.space(...))`, padding inside
# containers, gaps between rows of cells.
_SPACE: dict[str, float] = {
    "xs": 0.12,   # tight pip / inline gap
    "sm": 0.25,   # default text-to-icon
    "md": 0.45,   # paragraph-style row spacing
    "lg": 0.85,   # column gap, callout-to-target
    "xl": 1.40,   # zone boundary, breathing space between groups
}

# Stroke widths — for `stroke_width=T.stroke_width(...)`. Manim's
# defaults are heavy by modern UI standards; these are calibrated.
_STROKE_WIDTH: dict[str, float] = {
    "hair":   1.5,  # grid lines, faint guides
    "normal": 3.0,  # default for boxes, arrows
    "bold":   5.0,  # focal element outline, emphasis
}

# Motion vocabulary — every animation must carry pedagogical intent.
# `run_time` is in seconds. `rate_func` is a STRING name that
# components/moves resolve at call site (avoids Manim import here).
#
# Pairing run_time with a rate_func gives motion a recognizable feel:
# snap is mechanical, emphasis is breathy, settle is decisive, grand
# is ceremonial. Mixing within a scene reads as careless; matching
# within a sequence reads as intentional.
class _MotionSpec(TypedDict):
    run_time: float
    rate_func: str


_MOTION: dict[str, _MotionSpec] = {
    "snap":     {"run_time": 0.40, "rate_func": "ease_out_cubic"},
    "emphasis": {"run_time": 0.90, "rate_func": "ease_in_out_cubic"},
    "settle":   {"run_time": 1.40, "rate_func": "ease_out_cubic"},
    "grand":    {"run_time": 2.00, "rate_func": "ease_in_out_quart"},
}

# Lag ratios for `LaggedStart(..., lag_ratio=T.lag(...))`.
# Cascade is rhythmic — viewer sees each item land. Quick is for
# tight groups that should feel like one object.
_LAG: dict[str, float] = {
    "cascade": 0.18,
    "quick":   0.05,
}


# ── Public namespace ──────────────────────────────────────────────────
#
# Scenes import `T` from this module and use it as the design-system
# namespace. The Dockerfile copies this file to /render/chalkboard_
# tokens.py so the in-container import is `from chalkboard_tokens
# import T`.
#
# Bound to a theme at construction:
#     t = T(theme="chalkboard")
#     self.camera.background_color = t.bg
#     title = Text("...", font_size=t.type("title"), color=t.role("body"))


class T:
    """Token namespace bound to a theme.

    Theme defaults to chalkboard. Pass a different theme for light
    mode or the colorful variant. All theme-invariant tokens (type,
    space, stroke_width, motion, lag) are accessible regardless of
    theme — they're physical canvas units, not visual styling.
    """

    __slots__ = ("theme",)

    def __init__(self, theme: Theme = DEFAULT_THEME) -> None:
        if theme not in _THEMES:
            raise ValueError(
                f"Unknown theme {theme!r}. Valid: {sorted(_THEMES)}"
            )
        self.theme: Theme = theme

    # ── Per-theme accessors ──────────────────────────────────────────

    def surface(self, key: str) -> str:
        """Return the hex color for a surface key in the bound theme.

        Surface keys: bg, bg_subtle, grid.
        """
        block = _THEMES[self.theme]["surface"]
        if key not in block:
            raise KeyError(
                f"Unknown surface key {key!r}. Valid: {sorted(block)}"
            )
        return block[key]  # type: ignore[literal-required]

    def role(self, key: str) -> str:
        """Return the hex color for a semantic role in the bound theme.

        Role keys: focus_primary, focus_secondary, context_muted,
        accent_warm, accent_cool, accent_meta, body, stroke,
        stroke_muted.
        """
        block = _THEMES[self.theme]["role"]
        if key not in block:
            raise KeyError(
                f"Unknown role key {key!r}. Valid: {sorted(block)}"
            )
        return block[key]  # type: ignore[literal-required]

    @property
    def bg(self) -> str:
        """Shorthand for surface('bg') — the most-used token."""
        return self.surface("bg")

    @property
    def body(self) -> str:
        """Shorthand for role('body') — the most-used role."""
        return self.role("body")

    # ── Theme-invariant accessors ────────────────────────────────────

    @staticmethod
    def type(key: str) -> int:
        """Return the font_size for a typography role.

        Keys: display, title, heading, body, caption, code, micro, math.
        """
        if key not in _TYPE_SIZE:
            raise KeyError(
                f"Unknown type key {key!r}. Valid: {sorted(_TYPE_SIZE)}"
            )
        return _TYPE_SIZE[key]

    @staticmethod
    def space(key: str) -> float:
        """Return the spacing value for a buff/gap role.

        Keys: xs, sm, md, lg, xl.
        """
        if key not in _SPACE:
            raise KeyError(
                f"Unknown space key {key!r}. Valid: {sorted(_SPACE)}"
            )
        return _SPACE[key]

    @staticmethod
    def stroke_width(key: str) -> float:
        """Return the stroke width for a stroke role.

        Keys: hair, normal, bold.
        """
        if key not in _STROKE_WIDTH:
            raise KeyError(
                f"Unknown stroke_width key {key!r}. Valid: {sorted(_STROKE_WIDTH)}"
            )
        return _STROKE_WIDTH[key]

    @staticmethod
    def motion(key: str) -> _MotionSpec:
        """Return the motion spec dict for a named motion role.

        Keys: snap, emphasis, settle, grand. Returns a fresh dict per
        call so callers can mutate run_time without polluting the
        module-level table.

        The returned dict has STRING rate_func — resolution to a
        Manim callable is done by the components/moves layer at the
        call site (so this module stays Manim-free).
        """
        if key not in _MOTION:
            raise KeyError(
                f"Unknown motion key {key!r}. Valid: {sorted(_MOTION)}"
            )
        return dict(_MOTION[key])  # type: ignore[return-value]

    @staticmethod
    def lag(key: str) -> float:
        """Return the lag_ratio for a LaggedStart pattern.

        Keys: cascade, quick.
        """
        if key not in _LAG:
            raise KeyError(
                f"Unknown lag key {key!r}. Valid: {sorted(_LAG)}"
            )
        return _LAG[key]


# ── Snapshot accessors (used by manim_agent for prompt formatting) ────

TOKENS: dict = {
    "themes": _THEMES,
    "type":   _TYPE_SIZE,
    "space":  _SPACE,
    "stroke_width": _STROKE_WIDTH,
    "motion": _MOTION,
    "lag":    _LAG,
}


def render_prompt_block(theme: Theme = DEFAULT_THEME) -> str:
    """Format the token table as a prose block for injection into the
    manim_agent system/user prompt. Centralized so the prompt and the
    runtime tokens never drift — touching this module is the only way
    to change either.
    """
    th = _THEMES[theme]
    role_lines = "\n".join(
        f"    {k:<18s} {v}  — {_ROLE_PURPOSE[k]}" for k, v in th["role"].items()
    )
    surface_lines = "\n".join(
        f"    {k:<12s} {v}  — {_SURFACE_PURPOSE[k]}" for k, v in th["surface"].items()
    )
    type_lines = ", ".join(f"{k}={v}" for k, v in _TYPE_SIZE.items())
    space_lines = ", ".join(f"{k}={v}" for k, v in _SPACE.items())
    stroke_lines = ", ".join(f"{k}={v}" for k, v in _STROKE_WIDTH.items())
    motion_lines = "\n".join(
        f"    motion('{k}')  → run_time={v['run_time']}, "
        f"rate_func={v['rate_func']}  — {_MOTION_PURPOSE[k]}"
        for k, v in _MOTION.items()
    )

    return f"""DESIGN TOKENS — {theme} theme.

Generated scenes MUST reference design tokens by semantic name. NEVER
write a raw hex color, a raw font_size integer, a raw buff float, a
raw stroke_width, or a raw run_time/rate_func literal in scene code.

Import the token namespace at the top of the scene:

  from chalkboard_tokens import T
  t = T(theme="{theme}")

Then use:

Surface (canvas backgrounds):
{surface_lines}

  Usage:  self.camera.background_color = t.bg
          panel = Rectangle(fill_color=t.surface("bg_subtle"), ...)

Semantic role colors — choose by what the element MEANS, not what it looks like:
{role_lines}

  Usage:  Text("...", color=t.role("focus_primary"))
          arrow = Arrow(..., color=t.role("focus_secondary"))

Typography (font_size, Manim units): {type_lines}

  Usage:  title = Text("Topic", font_size=t.type("title"))
          caption = Text("...", font_size=t.type("caption"))
          eq = math_tex(r"e^{{i\\pi}} + 1 = 0")   # size "math", house TeX template

Spacing (buff / gap, Manim units): {space_lines}

  Usage:  desc.next_to(arr, DOWN, buff=t.space("lg"))
          row = VGroup(*cells).arrange(RIGHT, buff=t.space("sm"))

Stroke widths: {stroke_lines}

  Usage:  Square(stroke_width=t.stroke_width("bold"), ...)
          grid_line = Line(..., stroke_width=t.stroke_width("hair"))

Motion vocabulary — every self.play() picks a named motion. Each name
encodes pedagogical intent; mixing within a sequence reads as
intentional, raw run_time=1.0 reads as careless:
{motion_lines}

  Usage:  self.play(FadeIn(title), run_time=t.motion("emphasis")["run_time"],
                    rate_func=ease_in_out_cubic)
          # OR (preferred):
          self.play(FadeIn(title), **resolve_motion(t.motion("emphasis")))

  resolve_motion is imported from chalkboard_components.

LaggedStart lag ratios — for sequences that should feel rhythmic:
    lag('cascade')  → {_LAG['cascade']}  (one-by-one reveal, visible beats)
    lag('quick')    → {_LAG['quick']}  (group-like, near-simultaneous)

  Usage:  self.play(LaggedStart(*reveals, lag_ratio=t.lag("cascade")))

Raw color literals (ManimColor("#xxxxxx"), "#FFFFFF", RED, BLUE, etc.)
are NOT permitted in scene code. Resolve every color through
t.surface(...) or t.role(...). When you
need to interpolate between two colors, interpolate between two role
values: interpolate_color(t.role("focus_primary"), t.role("context_muted"), 0.5)."""


# ── Internal: prose for the prompt-injection block ────────────────────
# Kept beside the token tables so the prose and the values never drift.

_SURFACE_PURPOSE: dict[str, str] = {
    "bg":        "canvas background; set on self.camera.background_color",
    "bg_subtle": "slight-contrast panel/section background",
    "grid":      "scaffold strokes (grid lines, faint guides)",
}

_ROLE_PURPOSE: dict[str, str] = {
    "focus_primary":   "PRIMARY attention magnet (one per segment ideally)",
    "focus_secondary": "the paired/tracked element; never the magnet",
    "context_muted":   "supporting/past/dimmed states; recedes visually",
    "accent_warm":     "error, heat, warning, outlier",
    "accent_cool":     "data, signal, measured value",
    "accent_meta":     "tertiary annotations / callouts that aren't the point",
    "body":            "default text and shape color",
    "stroke":          "default outline color for boxes/arrows/frames",
    "stroke_muted":    "de-emphasized outline (paired with context_muted)",
}

_MOTION_PURPOSE: dict[str, str] = {
    "snap":     "state-change tick; mechanical, no breath",
    "emphasis": "focal reveal; the viewer should LOOK",
    "settle":   "final state of a sequence; let it land",
    "grand":    "chapter/topic boundary; ceremonial",
}
