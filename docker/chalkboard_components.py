"""Chalkboard component library.

Reusable Mobject wrappers that consume design tokens by semantic name.
Every component:

  * Inherits from VGroup so `self.play(FadeIn(box))` just works in
    generated scenes (no `.mobj` indirection at the call site).
  * Accepts a `theme` keyword to bind to a token theme (defaults to
    chalkboard). NEVER references a raw hex color, font_size integer,
    spacing float, or stroke_width literal — every value is sourced
    from chalkboard_tokens.T.
  * Exposes `.highlight(role)` and `.mute()` methods so the moves
    library can reach for pedagogical emphasis without knowing each
    component's internal Mobject structure.
  * Avoids the Manim 0.20.1 pitfalls catalogued in manim_agent's KNOWN
    API PITFALLS block (Code kwargs, .arrange() chain, Mobject arithmetic).

MATH

Everything that typesets math goes through `math_tex()` / `tex()` (or a
component built on them: EquationGroup, ChalkMatrix, ChalkAxes labels,
ChalkBox(math=True)). Those helpers apply the token type scale, semantic
role colors (including per-term coloring via `colors={"x": "focus_primary"}`),
and the module-level TEX_TEMPLATE below.

TEX_TEMPLATE is the ONE hook for the LaTeX preamble. It defaults to
chalkboard_style.TEX_TEMPLATE; set `chalkboard_components.TEX_TEMPLATE =
my_template` (a manim TexTemplate) before building mobjects and every
MathTex/Tex this module creates uses it. None means manim's configured
default (config.tex_template).

IMPORT CONTEXTS

Render-side, docker/ (or /render in the container) is on PYTHONPATH and
scenes write `from chalkboard_components import ChalkBox`. Pipeline-side
tests import `docker.chalkboard_components`. The tokens import below
works in both: `chalkboard_tokens` is on the path render-side, and
pipeline-side `pipeline.design_tokens` loads the same file by path.
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Optional, Sequence

try:
    from chalkboard_tokens import T  # type: ignore[import-not-found]
except ImportError:  # pipeline-side / pytest path
    from pipeline.design_tokens import T  # noqa: F401

# Manim at module load. This module is meaningless without Manim; tests
# that exercise it use `pytest.importorskip("manim")`.
from manim import (
    Arrow,
    Axes,
    Circle,
    Code,
    CurvedArrow,
    DOWN,
    LEFT,
    Line,
    MathTex,
    Matrix,
    NumberLine,
    RIGHT,
    RoundedRectangle,
    SingleStringMathTex,
    Tex,
    Text,
    UP,
    VGroup,
)
from manim.utils.color import ManimColor
from manim.utils.rate_functions import (
    ease_in_cubic,
    ease_in_out_cubic,
    ease_in_out_quart,
    ease_in_out_sine,
    ease_out_cubic,
    ease_out_quart,
    ease_out_sine,
    linear,
    smooth,
)


# ── LaTeX hook ────────────────────────────────────────────────────────
#
# The single place the LaTeX preamble is chosen for every MathTex/Tex
# built by this module (and by moves/templates, which build math through
# the helpers below). Defaults to the house template from chalkboard_style
# (amsmath/mathtools/siunitx/... + \R \E \dd macros); None would mean
# manim's config.tex_template.
try:
    import chalkboard_style as _style  # type: ignore[import-not-found]
except ImportError:  # pipeline-side / pytest path
    try:
        from docker import chalkboard_style as _style  # type: ignore[no-redef]
    except ImportError:
        _style = None

TEX_TEMPLATE = getattr(_style, "TEX_TEMPLATE", None)
CODE_FONT = getattr(_style, "CODE_FONT", None)


# tex() prose (Latin Modern) is scaled up by this to match the optical size
# of Text in the UI face at the same token size.
TEX_PROSE_SCALE = 1.2


def _tex_template_kwargs() -> dict:
    return {} if TEX_TEMPLATE is None else {"tex_template": TEX_TEMPLATE}


# ── Motion-spec resolver ──────────────────────────────────────────────
#
# Tokens return motion specs with STRING rate_func names so the tokens
# module can stay Manim-free. Resolution happens here, at the first
# Manim-aware consumer.

_RATE_FUNCS: dict[str, callable] = {
    "smooth":             smooth,
    "linear":             linear,
    "ease_in_cubic":      ease_in_cubic,
    "ease_out_cubic":     ease_out_cubic,
    "ease_in_out_cubic":  ease_in_out_cubic,
    "ease_in_out_quart":  ease_in_out_quart,
    "ease_out_quart":     ease_out_quart,
    "ease_in_out_sine":   ease_in_out_sine,
    "ease_out_sine":      ease_out_sine,
}


def resolve_motion(spec: dict) -> dict:
    """Convert a T.motion(name) spec into Manim `self.play()` kwargs.

    Input shape: {"run_time": float, "rate_func": str}
    Output shape: {"run_time": float, "rate_func": callable}

    Unknown rate_func names fall back to `smooth` rather than raising, so a
    tokens update that names a new func degrades gracefully.
    """
    fn = _RATE_FUNCS.get(spec["rate_func"], smooth)
    return {"run_time": spec["run_time"], "rate_func": fn}


# ── Math helpers ──────────────────────────────────────────────────────


def _role_color_map(colors: Optional[Mapping[str, str]], t: T) -> dict:
    """{"x": "focus_primary"} -> {"x": ManimColor(<role hex>)}.

    Values may be role names or surface names; anything else raises the
    token KeyError so a typo surfaces immediately.
    """
    if not colors:
        return {}
    out = {}
    for tex_sub, role in colors.items():
        try:
            out[tex_sub] = ManimColor(t.role(role))
        except KeyError:
            out[tex_sub] = ManimColor(t.surface(role))
    return out


def _safe_split(s: str, keys: Sequence[str]) -> list[str]:
    """Split TeX source `s` so every occurrence of a key becomes its own
    piece — except occurrences inside a control word.

    Manim's substrings_to_isolate matches keys anywhere, so isolating "h"
    also hits the "h" in "\\right" and "\\hat", splicing markers into the
    command and breaking compilation. Here control sequences are consumed
    whole, so only real tokens are matched. Empty pieces are dropped.
    """
    if not keys:
        return [s] if s else []
    ordered = sorted({k for k in keys if k}, key=len, reverse=True)
    out: list[str] = []
    buf = ""
    i = 0
    n = len(s)
    while i < n:
        hit = None
        for k in ordered:
            if s.startswith(k, i):
                end = i + len(k)
                # A key ending in a control word must not stop half-way
                # through a longer one ("\\pi" vs "\\pix").
                if re.search(r"\\[A-Za-z]+$", k) and end < n and s[end].isalpha():
                    continue
                hit = k
                break
        if hit is not None:
            if buf:
                out.append(buf)
                buf = ""
            out.append(hit)
            i += len(hit)
            continue
        if s[i] == "\\":
            j = i + 1
            if j < n and s[j].isalpha():
                while j < n and s[j].isalpha():
                    j += 1
            else:
                j = min(j + 1, n)
            buf += s[i:j]
            i = j
            continue
        buf += s[i]
        i += 1
    if buf:
        out.append(buf)
    return out


def _count_parts(pieces: Sequence[str]) -> int:
    """How many MathTex submobjects these args become (Manim also splits
    each arg on `{{ ... }}` groups and drops empty strings)."""
    splitter = getattr(MathTex, "_split_double_braces", None)
    if splitter is None:
        return len([p for p in pieces if p])
    return sum(len([q for q in splitter(p) if q]) for p in pieces)


def _apply_colors(m, base_color, color_map: dict):
    """Paint the whole expression, then each colored term. Manim 0.21's
    MathTex/Tex constructor leaves glyphs white whatever color= says, so
    color is always applied explicitly here."""
    m.set_color(base_color)
    for part in m.submobjects:
        color = color_map.get(getattr(part, "tex_string", None))
        if color is not None:
            part.set_color(color)
    return m


def _build_math(pieces: list[str], fallback: Sequence[str], color_map: dict, base: dict) -> MathTex:
    try:
        m = MathTex(*pieces, **base)
    except Exception:
        # Should not happen with _safe_split, but never let a coloring
        # request crash a render: fall back to the uncolored source.
        m = MathTex(*fallback, **base)
    return _apply_colors(m, base["color"], color_map)


def math_tex(
    *parts: str,
    size: str = "math",
    role: str = "body",
    colors: Optional[Mapping[str, str]] = None,
    isolate: Optional[Sequence[str]] = None,
    theme: str = "chalkboard",
    **kwargs,
) -> MathTex:
    """Build a token-styled MathTex. The ONLY way scene code should
    typeset math.

    Args:
        *parts: LaTeX strings (no surrounding $). Several parts (or
            Manim's `{{ ... }}` double-brace groups) become separate
            submobjects, which is what TransformMatchingTex matches on.
        size: token type key ("math" for display, "body" inline).
        role: base color role for the whole expression.
        colors: per-term coloring by SEMANTIC ROLE, e.g.
            {"x": "focus_primary", "\\Delta t": "accent_cool"}. Every
            occurrence of a key outside a control word ("h" matches the
            variable, never the h in "\\right") becomes its own part and
            takes that role's color.
        isolate: extra substrings to split out (for get_part_by_tex /
            emphasize_term) without coloring them.
        theme: tokens theme name.
        **kwargs: forwarded to MathTex (e.g. tex_environment).
    """
    t = T(theme=theme)
    color_map = _role_color_map(colors, t)
    base = dict(
        font_size=t.type(size),
        color=ManimColor(t.role(role)),
        **_tex_template_kwargs(),
        **kwargs,
    )
    keys = list(color_map) + list(isolate or [])
    pieces = [p for part in parts for p in _safe_split(part, keys)]
    return _build_math(pieces, parts, color_map, base)


def tex(
    *parts: str,
    size: str = "body",
    role: str = "body",
    colors: Optional[Mapping[str, str]] = None,
    theme: str = "chalkboard",
    **kwargs,
) -> Tex:
    """Token-styled Tex (text mode, with $...$ for inline math). Use for
    prose that contains math, e.g. tex(r"slope $= \\frac{\\Delta y}{\\Delta x}$").
    Plain prose without math should stay Text.

    Sized TEX_PROSE_SCALE larger than the token: Latin Modern's x-height is
    much smaller than the UI text face's, so a Tex label at the same nominal
    size reads a step smaller than the Text beside it.
    """
    t = T(theme=theme)
    color_map = _role_color_map(colors, t)
    base = dict(
        font_size=t.type(size) * TEX_PROSE_SCALE,
        color=ManimColor(t.role(role)),
        **_tex_template_kwargs(),
        **kwargs,
    )
    if color_map:
        pieces = [p for part in parts for p in _safe_split(part, list(color_map))]
        try:
            return _apply_colors(Tex(*pieces, arg_separator="", **base), base["color"], color_map)
        except Exception:
            pass
    return _apply_colors(Tex(*parts, **base), base["color"], {})


def _balanced_wrap(text: str, n_lines: int) -> str:
    """Split `text` at word boundaries into ~n_lines lines of similar length."""
    words = text.split()
    n_lines = max(1, min(n_lines, len(words)))
    if n_lines == 2:
        # Two lines: break at the space that best balances the halves.
        best = min(range(1, len(words)),
                   key=lambda k: max(len(" ".join(words[:k])), len(" ".join(words[k:]))))
        return " ".join(words[:best]) + "\n" + " ".join(words[best:])
    target = len(text) / n_lines
    lines, cur = [], ""
    for w in words:
        cand = f"{cur} {w}".strip()
        if cur and len(cand) > target and len(lines) < n_lines - 1:
            lines.append(cur)
            cur = w
        else:
            cur = cand
    lines.append(cur)
    return "\n".join(lines)


# ── Shared base ───────────────────────────────────────────────────────


_TEXTUAL = (Text, SingleStringMathTex)  # SingleStringMathTex covers MathTex + Tex


def _text(s: str, *, font_size, color, **kwargs) -> Text:
    """Text with its color applied via set_color as well: in Manim 0.21 the
    constructor colors the glyphs but leaves the parent's `.color` at its
    default, which breaks anything that reads `.color` back (copies,
    .animate targets, tests)."""
    m = Text(s, font_size=font_size, color=color, **kwargs)
    m.set_color(color)
    return m


class _ChalkComponent(VGroup):
    """Shared base: holds the theme, exposes default highlight / mute.

    Subclasses override `_stroke_targets()` / `_text_targets()` if they
    need to color something other than the default (stroke on outline
    mobjects + color on text/TeX mobjects).
    """

    def __init__(self, *, theme: str = "chalkboard", **vgroup_kwargs):
        super().__init__(**vgroup_kwargs)
        self._theme = theme
        self._t = T(theme=theme)  # cached token namespace
        self._base_opacity = None  # snapshot taken on first mute()

    @property
    def theme(self) -> str:
        return self._theme

    # ── opacity bookkeeping ───────────────────────────────────────
    #
    # mute() dims the component; highlight() must undo that, otherwise a
    # cell muted in step 1 and focused in step 2 stays half-transparent.
    # We can't just set_opacity(1.0): that would turn transparent fills
    # (panel frames, fill_opacity=0) opaque. So each family member records
    # its own as-built fill/stroke opacity (an attribute, so it survives
    # copy() and Transform's family re-alignment) and we restore from that.

    _MUTE_FACTOR = 0.6  # muted = context_muted color at 60% opacity: recedes, still legible

    def _snapshot_opacity(self) -> None:
        for m in self.get_family():
            if not hasattr(m, "_cb_base_opacity"):
                m._cb_base_opacity = (m.get_fill_opacity(), m.get_stroke_opacity())
        self._base_opacity = True

    def _restore_opacity(self) -> None:
        if self._base_opacity is None:
            return
        for m in self.get_family():
            base = getattr(m, "_cb_base_opacity", None)
            if base is not None:
                m.set_fill(opacity=base[0], family=False)
                m.set_stroke(opacity=base[1], family=False)

    def _dim(self, factor: float) -> None:
        self._snapshot_opacity()
        for m in self.get_family():
            fo, so = getattr(m, "_cb_base_opacity", (m.get_fill_opacity(), m.get_stroke_opacity()))
            m.set_fill(opacity=fo * factor, family=False)
            m.set_stroke(opacity=so * factor, family=False)

    # ── public methods ────────────────────────────────────────────

    def highlight(self, role: str = "focus_primary") -> "_ChalkComponent":
        """Set stroke + text color to a semantic role + bolden stroke,
        restoring full opacity if the component was muted.

        Does NOT touch `fill_color`: ChalkBox and NetworkNode keep their
        `surface("bg_subtle")` fill so a same-color stroke + text never
        collapses onto an opaque fill of the same color.

        Returns self so callers can chain or feed into `.animate`.
        """
        self._restore_opacity()
        color = ManimColor(self._t.role(role))
        for m in self._stroke_targets():
            m.set_stroke(color=color, width=self._t.stroke_width("bold"))
        for m in self._text_targets():
            m.set_color(color)
        return self

    def mute(self) -> "_ChalkComponent":
        """Recede visually: stroke + text become context_muted, opacity
        halves. Fill colors are preserved.
        """
        color = ManimColor(self._t.role("context_muted"))
        for m in self._stroke_targets():
            m.set_stroke(color=color, width=self._t.stroke_width("normal"))
        for m in self._text_targets():
            m.set_color(color)
        self._dim(self._MUTE_FACTOR)
        return self

    # ── overridable in subclasses ─────────────────────────────────

    def _stroke_targets(self) -> Iterable:
        """Mobjects whose stroke is meaningful (outlines, arrows). Text and
        TeX glyphs are excluded: stroking glyphs makes them look bold/blurry.
        """
        return [m for m in self.submobjects if not isinstance(m, _TEXTUAL)]

    def _text_targets(self) -> Iterable:
        """Mobjects whose color setter affects glyphs (Text, MathTex, Tex)."""
        return [m for m in self.submobjects if isinstance(m, _TEXTUAL)]


# ── Components ────────────────────────────────────────────────────────


class ChalkBox(_ChalkComponent):
    """A discrete-value container — array cell, definition box, metric tile.

    One ChalkBox holds one thing the viewer should focus on. A row of
    ChalkBoxes is an array; an isolated ChalkBox is an emphasized fact.
    Do not use ChalkBox for layout framing — that's ChalkPanel.

    Args:
        value: the displayed string (Text, or LaTeX when math=True).
        role: token role for the box stroke + text color.
        size: token type-scale key for the text size.
        width / height: FLOORS in canvas units; the box always grows to
            contain its label.
        math: typeset `value` as MathTex (e.g. r"\\frac{1}{2}", "x_3").
        theme: tokens theme name.
    """

    def __init__(
        self,
        value: str,
        *,
        role: str = "body",
        size: str = "body",
        width: Optional[float] = None,
        height: Optional[float] = None,
        math: bool = False,
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        if math:
            label = math_tex(str(value), size=size, role=role, theme=theme)
        else:
            label = _text(
                str(value),
                font_size=t.type(size),
                color=ManimColor(t.role(role)),
            )
        h_pad = t.space("md")
        v_pad = t.space("sm")
        auto_w = label.width + h_pad * 2
        auto_h = label.height + v_pad * 2
        # Floor keeps a row of single-character cells uniform.
        floor_w = t.space("xl")
        floor_h = t.space("xl") * 0.85
        w = max(width, auto_w) if width is not None else max(auto_w, floor_w)
        h = max(height, auto_h) if height is not None else max(auto_h, floor_h)
        box = RoundedRectangle(
            width=w,
            height=h,
            corner_radius=t.space("xs"),
            stroke_color=ManimColor(t.role(role)) if role != "body" else ManimColor(t.role("stroke")),
            stroke_width=t.stroke_width("normal"),
            fill_color=ManimColor(t.surface("bg_subtle")),
            fill_opacity=1.0,
        )
        label.move_to(box.get_center())
        self.add(box, label)
        self.box = box
        self.label = label


class ChalkArrow(_ChalkComponent):
    """A directional arrow — causal direction, value transit, transition.

    Arrows are never decorative: each represents causality, transit, or
    temporal transition. Reveal with Create(arrow) or FadeIn(arrow), never
    GrowArrow (a ChalkArrow is a VGroup with no points of its own).

    Args:
        start, end: 3-vectors in Manim canvas units.
        role: token role for the arrow color + tip.
        curve: when True, uses CurvedArrow (set `angle` for control).
        angle: curvature angle (radians) when curve=True.
        theme: tokens theme name.
    """

    def __init__(
        self,
        start,
        end,
        *,
        role: str = "focus_primary",
        curve: bool = False,
        angle: float = 0.4,
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        color = ManimColor(t.role(role))
        if curve:
            arrow = CurvedArrow(
                start_point=start,
                end_point=end,
                angle=angle,
                color=color,
                stroke_width=t.stroke_width("normal"),
            )
        else:
            arrow = Arrow(
                start=start,
                end=end,
                color=color,
                stroke_width=t.stroke_width("normal"),
                buff=t.space("xs"),
            )
        self.add(arrow)
        self.arrow = arrow


class ChalkCode(_ChalkComponent):
    """Syntax-highlighted source code. Use `.code_lines[i]` to reveal /
    highlight one line at a time. Wraps the v0.20.1 Code API correctly
    (code_string=, paragraph_config font size).

    Args:
        code_string: source text (multi-line OK).
        language: Pygments lexer name (python, c, cpp, js, ...).
        size: token type-scale key for the code font.
        theme: tokens theme name.
    """

    def __init__(
        self,
        code_string: str,
        *,
        language: str = "python",
        size: str = "code",
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        # An explicit paragraph_config replaces Code's class default (which
        # chalkboard_style uses to set the code font), so carry the font over.
        # Fonts with OpenType programming ligatures (JetBrains Mono, Fira
        # Code: "<=", "->", "==") make Manim's Text raise "rendered fewer
        # glyph(s) than its non-space characters", so fall back to a
        # ligature-free mono face rather than crash the render.
        fonts = [CODE_FONT] if CODE_FONT else []
        fonts += ["DejaVu Sans Mono", None]
        last_exc = None
        for font in fonts:
            paragraph_config = {"font_size": t.type(size)}
            if font:
                paragraph_config["font"] = font
            try:
                self.code_obj = Code(
                    code_string=code_string,
                    language=language,
                    background="window",
                    # Token surface instead of the style's pure-black window.
                    background_config={
                        "fill_color": ManimColor(t.surface("bg_subtle")),
                        "stroke_color": ManimColor(t.role("stroke_muted")),
                        "stroke_width": t.stroke_width("hair"),
                    },
                    paragraph_config=paragraph_config,
                )
                break
            except ValueError as exc:
                last_exc = exc
        else:
            raise last_exc
        self.add(self.code_obj)

    @property
    def code_lines(self):
        """Individual code lines (VGroups). `.code` does not exist in 0.20.1."""
        return self.code_obj.code_lines


class Callout(_ChalkComponent):
    """Side-channel annotation tied to a tracked element. Says ONE thing
    about one element; multi-sentence reasoning belongs in narration.

    Args:
        text: the annotation copy (1–2 lines max).
        anchor_point: 3-vector where the connector terminates.
        role: token role for connector + text color.
        size: type-scale key for the text.
        offset: displacement of the label from anchor_point
            (default UP * space.lg).
        math: typeset `text` as MathTex instead of Text.
        max_width: wrap (then shrink) the label to this width; None = off.
        theme: tokens theme name.
    """

    def __init__(
        self,
        text: str,
        anchor_point,
        *,
        role: str = "accent_meta",
        size: str = "caption",
        offset=None,
        math: bool = False,
        max_width: Optional[float] = 4.5,
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        color = ManimColor(t.role(role))
        if offset is None:
            offset = UP * t.space("lg")
        label_pos = anchor_point + offset
        # A side callout must fit between the anchor and the canvas edge;
        # tighten max_width to that room so it wraps instead of being shoved
        # back over its own anchor by the on-canvas clamp below.
        _ox = float(offset[0]) if len(offset) > 0 else 0.0
        _oy = float(offset[1]) if len(offset) > 1 else 0.0
        if max_width and abs(_ox) > abs(_oy):
            _lim = 7.11 - t.space("md")
            _ax = float(anchor_point[0])
            room = (_lim - (_ax + _ox)) if _ox > 0 else ((_ax + _ox) + _lim)
            if room >= 1.2:
                max_width = min(max_width, room)
        if math:
            label = math_tex(text, size=size, role=role, theme=theme)
        else:
            label = _text(text, font_size=t.type(size), color=color)
            if max_width and label.width > max_width and " " in text and "\n" not in text:
                label = _text(
                    _balanced_wrap(text, int(label.width // max_width) + 1),
                    font_size=t.type(size), color=color, line_spacing=0.8,
                )
        if max_width and label.width > max_width:
            label.scale_to_fit_width(max_width)
        label.move_to(label_pos)
        # `offset` is the GAP between the anchor and the label's facing
        # edge (not its center), so a long label never covers its anchor.
        ox = float(offset[0]) if len(offset) > 0 else 0.0
        oy = float(offset[1]) if len(offset) > 1 else 0.0
        if abs(ox) > abs(oy):
            label.shift(RIGHT * (label.width / 2) * (1 if ox > 0 else -1))
        elif oy != 0:
            label.shift(UP * (label.height / 2) * (1 if oy > 0 else -1))
        # Keep the label on canvas (anchors near an edge would otherwise push
        # it off-frame); the connector is computed after, so it still lands
        # on the anchor.
        margin = t.space("md")
        x_lim, y_lim = 7.11 - margin, 4.0 - margin
        if label.get_right()[0] > x_lim:
            label.shift(LEFT * (label.get_right()[0] - x_lim))
        if label.get_left()[0] < -x_lim:
            label.shift(RIGHT * (-x_lim - label.get_left()[0]))
        if label.get_top()[1] > y_lim:
            label.shift(DOWN * (label.get_top()[1] - y_lim))
        if label.get_bottom()[1] < -y_lim:
            label.shift(UP * (-y_lim - label.get_bottom()[1]))
        # Connector leaves the label edge that faces the anchor: bottom/top
        # for vertical-dominant offsets, left/right for horizontal ones.
        dx = offset[0] if len(offset) > 0 else 0.0
        dy = offset[1] if len(offset) > 1 else 0.0
        if abs(dy) >= abs(dx):
            connector_start = label.get_bottom() if dy > 0 else label.get_top()
        else:
            connector_start = label.get_left() if dx > 0 else label.get_right()
        connector = Line(
            start=connector_start,
            end=anchor_point,
            stroke_color=color,
            stroke_width=t.stroke_width("hair"),
        )
        self.add(connector, label)
        self.connector = connector
        self.label = label


class StepCounter(_ChalkComponent):
    """Persistent "Step N / M" indicator (typically top-right via
    `.to_corner(UR)`). Advance with `self.play(counter.animate.advance())`.
    """

    def __init__(
        self,
        total: int,
        *,
        size: str = "caption",
        role: str = "context_muted",
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        self._total = total
        self._current = 0
        self._size_key = size
        self._role_key = role
        label = _text(
            self._format(),
            font_size=self._t.type(size),
            color=ManimColor(self._t.role(role)),
        )
        self.add(label)

    def _format(self) -> str:
        return f"Step {self._current} / {self._total}"

    def advance(self) -> "StepCounter":
        """Increment and rewrite the inner Text. Returns self."""
        self._current = min(self._current + 1, self._total)
        new_label = _text(
            self._format(),
            font_size=self._t.type(self._size_key),
            color=ManimColor(self._t.role(self._role_key)),
        )
        # Keep the right edge fixed so a corner-anchored counter doesn't
        # drift as the digit count changes.
        new_label.move_to(self.submobjects[0].get_right(), aligned_edge=RIGHT)
        self.submobjects[0].become(new_label)
        # become() copies geometry/colors only; keep the string attributes true.
        self.submobjects[0].original_text = new_label.original_text
        self.submobjects[0].text = new_label.text
        return self


class ChalkAxis(_ChalkComponent):
    """A token-styled 1-D NumberLine (numeric scales, timelines).
    For function plots use ChalkAxes.
    """

    def __init__(
        self,
        x_range: Sequence[float],
        *,
        length: float = 6.0,
        include_numbers: bool = True,
        role: str = "stroke_muted",
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        color = ManimColor(t.role(role))
        self.number_line = NumberLine(
            x_range=list(x_range),
            length=length,
            include_numbers=include_numbers,
            stroke_color=color,
            stroke_width=t.stroke_width("hair"),
            font_size=t.type("micro"),
        )
        self.add(self.number_line)


class ChalkAxes(_ChalkComponent):
    """Token-styled 2-D axes for plotting functions (calculus, physics,
    probability densities).

        ax = ChalkAxes(x_range=[-1, 4, 1], y_range=[0, 9, 3],
                       x_label="x", y_label="f(x)")
        curve = ax.plot(lambda x: x**2, role="focus_primary", x_range=[0, 3])
        area  = ax.area(curve, x_range=[1, 2], role="accent_cool")
        tangent = ax.tangent(lambda x: x**2, x=1.5, role="accent_warm")

    Axis labels and tick numbers are typeset as math with TEX_TEMPLATE.
    Curves/areas are returned (not added) so the scene controls reveal
    order; place them with the axes (they already live in axes coords).

    Args:
        x_range, y_range: [min, max, step].
        x_length, y_length: canvas size of each axis.
        x_label, y_label: LaTeX strings ("" for none).
        include_numbers: tick labels on both axes.
        role: token role for axis lines.
        theme: tokens theme name.
    """

    def __init__(
        self,
        x_range: Sequence[float],
        y_range: Sequence[float],
        *,
        x_length: float = 6.0,
        y_length: float = 4.0,
        x_label: str = "x",
        y_label: str = "y",
        include_numbers: bool = True,
        role: str = "stroke_muted",
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        color = ManimColor(t.role(role))
        num_color = ManimColor(t.role("context_muted"))
        self.axes = Axes(
            x_range=list(x_range),
            y_range=list(y_range),
            x_length=x_length,
            y_length=y_length,
            tips=False,
            axis_config={
                "stroke_color": color,
                "stroke_width": t.stroke_width("normal"),
                "include_numbers": include_numbers,
                "font_size": t.type("caption"),
                "decimal_number_config": {"color": num_color, "num_decimal_places": 0},
                "tick_size": t.space("xs") * 0.6,
            },
        )
        self.add(self.axes)
        self.x_label = None
        self.y_label = None
        if x_label:
            self.x_label = math_tex(x_label, size="body", role="body", theme=theme)
            self.x_label.next_to(self.axes.x_axis.get_end(), RIGHT, buff=t.space("sm"))
            self.add(self.x_label)
        if y_label:
            self.y_label = math_tex(y_label, size="body", role="body", theme=theme)
            self.y_label.next_to(self.axes.y_axis.get_end(), UP, buff=t.space("sm"))
            self.add(self.y_label)

    def _text_targets(self):
        return [m for m in (self.x_label, self.y_label) if m is not None]

    def _stroke_targets(self):
        return [self.axes]

    # ── plotting helpers (return mobjects; caller reveals them) ─────

    def c2p(self, x: float, y: float):
        """Axes coordinates -> canvas point."""
        return self.axes.c2p(x, y)

    def plot(self, fn, *, role: str = "focus_primary", x_range=None, **kwargs):
        return self.axes.plot(
            fn,
            x_range=x_range,
            color=ManimColor(self._t.role(role)),
            stroke_width=self._t.stroke_width("bold"),
            **kwargs,
        )

    def area(self, graph, *, x_range=None, role: str = "accent_cool", opacity: float = 0.35, bounded_graph=None):
        return self.axes.get_area(
            graph,
            x_range=x_range,
            color=ManimColor(self._t.role(role)),
            opacity=opacity,
            bounded_graph=bounded_graph,
        )

    def tangent(self, fn, *, x: float, length: float = 3.0, role: str = "accent_warm", dx: float = 1e-4):
        """Tangent segment to y=fn(x) at x, centered on the touch point."""
        import numpy as np

        slope = (fn(x + dx) - fn(x - dx)) / (2 * dx)
        p = np.array(self.axes.c2p(x, fn(x)))
        q = np.array(self.axes.c2p(x + 1.0, fn(x) + slope))
        direction = (q - p) / max(np.linalg.norm(q - p), 1e-9)
        return Line(
            p - direction * length / 2,
            p + direction * length / 2,
            color=ManimColor(self._t.role(role)),
            stroke_width=self._t.stroke_width("normal"),
        )

    def dot_at(self, x: float, y: float, *, role: str = "focus_primary"):
        from manim import Dot

        return Dot(self.axes.c2p(x, y), radius=0.07, color=ManimColor(self._t.role(role)))


class ChalkPanel(_ChalkComponent):
    """A framed section — divider in compare layouts or a section frame.

    Content goes BELOW the title: use `panel.body_center`, `body_top`,
    `body_bottom`, `body_width`, `body_height` (never `get_center()`,
    which would overlap the title).

    Args:
        title: optional panel title rendered at the top edge.
        width, height: canvas-unit dimensions.
        role: token role for the frame.
        theme: tokens theme name.
    """

    def __init__(
        self,
        title: Optional[str] = None,
        *,
        width: float = 5.5,
        height: float = 4.5,
        role: str = "stroke_muted",
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        frame = RoundedRectangle(
            width=width,
            height=height,
            corner_radius=t.space("sm"),
            stroke_color=ManimColor(t.role(role)),
            stroke_width=t.stroke_width("hair"),
            fill_opacity=0.0,
        )
        self.add(frame)
        self.frame = frame
        self.title_obj = None
        if title:
            title_obj = _text(
                title,
                font_size=t.type("heading"),
                color=ManimColor(t.role("body")),
            )
            # Never let a long title spill past the frame.
            max_w = width - 2 * t.space("md")
            if title_obj.width > max_w:
                title_obj.scale_to_fit_width(max_w)
            title_obj.next_to(frame.get_top(), DOWN, buff=t.space("sm"))
            self.add(title_obj)
            self.title_obj = title_obj

    @property
    def body_top(self):
        if self.title_obj is None:
            return self.frame.get_top()
        return self.title_obj.get_bottom() + DOWN * self._t.space("sm")

    @property
    def body_bottom(self):
        return self.frame.get_bottom()

    @property
    def body_center(self):
        return (self.body_top + self.body_bottom) / 2

    @property
    def body_height(self) -> float:
        return float((self.body_top - self.body_bottom)[1])

    @property
    def body_width(self) -> float:
        return float(self.frame.width)


class ChalkBadge(_ChalkComponent):
    """A small pill — status tag, role label, step number. One or two words.

    Args:
        text: badge copy (single line, ≤ 16 chars recommended).
        role: token role for the pill fill.
        theme: tokens theme name.
    """

    def __init__(
        self,
        text: str,
        *,
        role: str = "accent_meta",
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        color = ManimColor(t.role(role))
        label = _text(
            text,
            font_size=t.type("micro"),
            color=ManimColor(t.surface("bg")),
        )
        pill_h = label.height + t.space("xs") * 2
        pill = RoundedRectangle(
            # Never narrower than tall, so single digits read as a round pip.
            width=max(label.width + t.space("md"), pill_h),
            height=pill_h,
            corner_radius=pill_h / 2,
            fill_color=color,
            fill_opacity=1.0,
            stroke_width=0,
        )
        pill.move_to(label.get_center())
        self.add(pill, label)
        self.pill = pill
        self.label = label

    def _text_targets(self):
        # Badge text is the contrast color set by the pill; never recolor it.
        return []

    def _stroke_targets(self):
        return [self.pill]

    def highlight(self, role: str = "focus_primary") -> "ChalkBadge":
        self._restore_opacity()
        self.pill.set_fill(color=ManimColor(self._t.role(role)), opacity=1.0)
        return self

    def mute(self) -> "ChalkBadge":
        self._snapshot_opacity()
        self.pill.set_fill(color=ManimColor(self._t.role("context_muted")))
        self._dim(self._MUTE_FACTOR)
        return self


# ── Equations ─────────────────────────────────────────────────────────

# Relations EquationGroup auto-aligns on when a line has no explicit '&'.
_RELATION_RE = re.compile(
    r"(\\(?:leq?|geq?|neq|approx|equiv|sim(?:eq)?|propto|implies|iff|Rightarrow|"
    r"Longrightarrow|to|mapsto|coloneqq|triangleq)(?![A-Za-z])|<|>|=)"
)


def _split_on_relation(line: str) -> tuple[str, str] | None:
    """Split `line` before its first relation at brace depth 0.

    Returns (lhs, rhs) with rhs starting at the relation, or None when the
    line has no top-level relation.
    """
    depth = 0
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] in "{}":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            m = _RELATION_RE.match(line, i)
            if m and i > 0:
                return line[:i], line[i:]
            if ch == "\\":
                # skip the rest of a control word so "\left" etc. aren't scanned
                j = i + 1
                while j < len(line) and line[j].isalpha():
                    j += 1
                i = max(j, i + 1)
                continue
        i += 1
    return None


def _split_alignment(line: str) -> tuple[str, str] | None:
    """Split at the first top-level align '&' (not inside braces or a
    \\begin..\\end environment such as pmatrix, where & separates cells)."""
    depth = 0
    env = 0
    i = 0
    while i < len(line):
        if line.startswith("\\begin", i):
            env += 1
        elif line.startswith("\\end", i):
            env -= 1
        ch = line[i]
        if ch == "\\":
            i += 2  # skip escaped chars like \& \{ and the first letter of commands
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "&" and depth == 0 and env == 0:
            return line[:i], line[i + 1:]
        i += 1
    return None


class EquationGroup(_ChalkComponent):
    """A multi-line derivation with the relation signs lined up.

    Write lines like an align* block: put `&` right before the relation
    to align on. Without any `&`, each line auto-aligns on its first
    top-level relation (=, \\le, \\approx, \\implies, ...). A line that
    starts with the relation ("&= 2x + 1") continues the previous one.

        eq = EquationGroup([
            r"f(x) &= x^2 + 3x",
            r"f'(x) &= \\lim_{h \\to 0} \\frac{f(x+h) - f(x)}{h}",
            r"&= 2x + 3",
        ], colors={"x": "focus_primary"})

    Each line is its own MathTex at `eq.lines[i]` (so TransformMatchingTex
    and reveal-one-line-at-a-time work); `eq.lhs[i]` / `eq.rhs[i]` are the
    two sides (rhs is None for a line with no relation). Use `{{ ... }}`
    groups inside a line to give TransformMatchingTex finer parts.

    Args:
        lines: LaTeX strings (NO surrounding $).
        role: base color role.
        size: type-scale key ("math" by default).
        buff: vertical gap between lines (token space key).
        colors: per-term role coloring, e.g. {"x": "focus_primary"}.
        isolate: extra substrings to isolate for emphasize_term().
        align: line up relations (True) or center every line (False).
        theme: tokens theme name.
    """

    def __init__(
        self,
        lines: Sequence[str],
        *,
        role: str = "body",
        size: str = "math",
        buff: str = "md",
        colors: Optional[Mapping[str, str]] = None,
        isolate: Optional[Sequence[str]] = None,
        align: bool = True,
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        self._role = role
        self._colors = dict(colors or {})
        self.lines: list = []
        self.lhs: list = []
        self.rhs: list = []
        anchors: list = []  # x of each line's alignment point (None = center)

        color_map = _role_color_map(colors, t)
        keys = list(color_map) + list(isolate or [])
        base = dict(
            font_size=t.type(size),
            color=ManimColor(t.role(role)),
            **_tex_template_kwargs(),
        )
        for raw in lines:
            split = _split_alignment(raw)
            if split is None:
                split = _split_on_relation(raw) if align else None
            lhs_s, rhs_s = split if split else (raw, "")
            lhs_s, rhs_s = lhs_s.strip(), rhs_s.strip()
            lhs_pieces = _safe_split(lhs_s, keys)
            rhs_pieces = _safe_split(rhs_s, keys)
            line = _build_math(lhs_pieces + rhs_pieces,
                               [p for p in (lhs_s, rhs_s) if p], color_map, base)
            n_lhs = _count_parts(lhs_pieces)
            if rhs_s and len(line.submobjects) > n_lhs:
                lhs_part = VGroup(*line.submobjects[:n_lhs]) if n_lhs else None
                rhs_part = VGroup(*line.submobjects[n_lhs:])
                anchor = rhs_part.get_left()[0] if align else None
            else:
                lhs_part, rhs_part, anchor = VGroup(*line.submobjects), None, None
            self.lines.append(line)
            self.lhs.append(lhs_part)
            self.rhs.append(rhs_part)
            anchors.append(anchor)

        # Vertical stacking: fixed baseline-ish rhythm, bbox-based so tall
        # lines (fractions, sums) never collide with their neighbours.
        gap = t.space(buff)
        for i, line in enumerate(self.lines):
            if i:
                line.next_to(self.lines[i - 1], DOWN, buff=gap)
        # Horizontal alignment: shift every aligned line so its relation
        # sits on x = 0; unaligned lines are centered on that column.
        for line, anchor in zip(self.lines, anchors):
            if anchor is not None:
                line.shift(RIGHT * (0 - anchor))
            else:
                line.set_x(0)
        self.add(*self.lines)
        self._anchors_x = 0.0

    @property
    def align_x(self) -> float:
        """Current canvas x of the shared relation column."""
        for line, rhs in zip(self.lines, self.rhs):
            if rhs is not None:
                return float(rhs.get_left()[0])
        return float(self.get_center()[0])

    def _recolor_line(self, line, role: str) -> None:
        _apply_colors(line, ManimColor(self._t.role(role)), _role_color_map(self._colors, self._t))

    def focus(self, i: int, role: str = "focus_primary") -> "EquationGroup":
        """Line `i` -> `role` (term colors kept); every other line -> context_muted."""
        for j, line in enumerate(self.lines):
            if j == i:
                self._recolor_line(line, role)
            else:
                line.set_color(ManimColor(self._t.role("context_muted")))
        return self

    def restore(self) -> "EquationGroup":
        """Back to the as-built colors (base role + term colors) on every line."""
        for line in self.lines:
            self._recolor_line(line, self._role)
        return self

    def highlight(self, role: str = "focus_primary") -> "EquationGroup":
        self._restore_opacity()
        for line in self.lines:
            self._recolor_line(line, role)
        return self

    def _stroke_targets(self):
        return []

    def _text_targets(self):
        return list(self.lines)


class ChalkMatrix(_ChalkComponent):
    """A bracketed matrix / vector for linear algebra, entries typeset as
    math with token size + color.

        A = ChalkMatrix([[2, -1], [1, 3]])
        v = ChalkMatrix([["x"], ["y"]], role="focus_secondary")
        A.highlight_row(0); A.highlight_column(1, role="accent_cool")
        A.highlight_entry(1, 1, role="focus_primary")

    Args:
        rows: list of rows; entries are numbers or LaTeX strings.
        role: base role for entries and brackets.
        size: type-scale key for the entries.
        theme: tokens theme name.
    """

    def __init__(
        self,
        rows: Sequence[Sequence],
        *,
        role: str = "body",
        size: str = "math",
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        self._role = role
        color = ManimColor(t.role(role))
        fs = t.type(size)
        # Spacing scales with the font so small matrices aren't sparse and
        # big ones aren't cramped (Manim's defaults assume font_size 48).
        scale = fs / 48.0
        self.matrix = Matrix(
            [[str(e) for e in row] for row in rows],
            v_buff=0.8 * scale,
            h_buff=1.3 * scale,
            element_to_mobject_config={"font_size": fs, "color": color, **_tex_template_kwargs()},
            bracket_config={"color": color},
        )
        self.matrix.set_color(color)
        self.add(self.matrix)
        self.n_rows = len(rows)
        self.n_cols = len(rows[0]) if rows else 0

    def entry(self, i: int, j: int):
        return self.matrix.get_entries()[i * self.n_cols + j]

    def row(self, i: int) -> VGroup:
        return self.matrix.get_rows()[i]

    def column(self, j: int) -> VGroup:
        return self.matrix.get_columns()[j]

    def brackets(self) -> VGroup:
        return self.matrix.get_brackets()

    def highlight_entry(self, i: int, j: int, role: str = "focus_primary") -> "ChalkMatrix":
        self.entry(i, j).set_color(ManimColor(self._t.role(role)))
        return self

    def highlight_row(self, i: int, role: str = "focus_primary") -> "ChalkMatrix":
        self.row(i).set_color(ManimColor(self._t.role(role)))
        return self

    def highlight_column(self, j: int, role: str = "focus_primary") -> "ChalkMatrix":
        self.column(j).set_color(ManimColor(self._t.role(role)))
        return self

    def highlight(self, role: str = "focus_primary") -> "ChalkMatrix":
        self._restore_opacity()
        self.matrix.get_entries().set_color(ManimColor(self._t.role(role)))
        return self

    def mute(self) -> "ChalkMatrix":
        self.matrix.set_color(ManimColor(self._t.role("context_muted")))
        self._dim(self._MUTE_FACTOR)
        return self


class NetworkNode(_ChalkComponent):
    """A graph / state-machine node — Circle with centered label. Edges
    between nodes are ChalkArrows. Explicit radius is a FLOOR.

    Args:
        label: node text (typically 1–4 chars).
        radius: minimum radius in canvas units.
        role: token role for the circle stroke + label text.
        math: typeset the label as MathTex (e.g. "v_1").
        theme: tokens theme name.
    """

    def __init__(
        self,
        label: str,
        *,
        radius: Optional[float] = None,
        role: str = "body",
        math: bool = False,
        theme: str = "chalkboard",
    ):
        super().__init__(theme=theme)
        t = self._t
        color = ManimColor(t.role(role))
        if math:
            text = math_tex(label, size="body", role=role, theme=theme)
        else:
            text = _text(label, font_size=t.type("body"), color=color)
        auto_r = max(text.width, text.height) / 2 + t.space("sm")
        final_r = max(radius, auto_r) if radius is not None else max(auto_r, 0.5)
        circle = Circle(
            radius=final_r,
            stroke_color=color,
            stroke_width=t.stroke_width("normal"),
            fill_color=ManimColor(t.surface("bg_subtle")),
            fill_opacity=1.0,
        )
        text.move_to(circle.get_center())
        self.add(circle, text)
        self.circle = circle
        self.label = text


# ── Public exports ────────────────────────────────────────────────────

__all__ = [
    "ChalkBox",
    "ChalkArrow",
    "ChalkCode",
    "Callout",
    "StepCounter",
    "ChalkAxis",
    "ChalkAxes",
    "ChalkPanel",
    "ChalkBadge",
    "EquationGroup",
    "ChalkMatrix",
    "NetworkNode",
    "math_tex",
    "tex",
    "resolve_motion",
    "TEX_TEMPLATE",
]
