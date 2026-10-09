# docker/chalkboard_style.py
"""House typography for every rendered scene: one LaTeX preamble, one text face.

Importing this module (chalkboard_base does it, so every generated scene gets it)
sets Manim-wide defaults, so even a bare ``MathTex(...)`` or ``Text(...)`` written
by the agent comes out in the house style:

  * Math: Latin Modern via a preamble with amsmath/mathtools/amssymb/bm/siunitx/
    cancel/mathrsfs/dsfont, so derivations, vectors, units, cancellations and
    script letters all typeset without the agent having to manage packages.
  * Text: CMU Serif (Computer Modern, matching the math) when installed, else
    Inter, else Pango's default sans. Code: JetBrains Mono NL (no ligatures), else DejaVu Sans Mono.

Swap faces with CHALKBOARD_TEXT_FONT / CHALKBOARD_CODE_FONT.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess

import manim
import manim.utils.color.core as _color_core
from manim import Code, ManimColor, MarkupText, MathTex, NumberLine, Paragraph, Tex, TexTemplate, Text
from manim.mobject.graphing.scale import LinearBase

PREAMBLE = r"""
\usepackage[english]{babel}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{lmodern}
\usepackage{microtype}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{mathtools}
\usepackage{bm}
\usepackage{mathrsfs}
\usepackage{dsfont}
\usepackage{cancel}
\usepackage{xcolor}
\usepackage{siunitx}
\sisetup{detect-all}
\DeclareMathOperator*{\argmax}{arg\,max}
\DeclareMathOperator*{\argmin}{arg\,min}
\DeclareMathOperator{\Var}{Var}
\DeclareMathOperator{\Cov}{Cov}
\DeclareMathOperator{\tr}{tr}
\DeclareMathOperator{\rank}{rank}
\newcommand{\R}{\mathbb{R}}
\newcommand{\N}{\mathbb{N}}
\newcommand{\Z}{\mathbb{Z}}
\newcommand{\Q}{\mathbb{Q}}
\newcommand{\C}{\mathbb{C}}
\newcommand{\E}{\mathbb{E}}
\newcommand{\dd}{\mathop{}\!\mathrm{d}}
"""

TEX_TEMPLATE = TexTemplate(documentclass=r"\documentclass[preview]{standalone}", preamble=PREAMBLE)


def _has_font(family: str) -> bool:
    if not shutil.which("fc-list"):
        return False
    try:
        out = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return False
    return any(family.lower() == f.strip().lower() for line in out.splitlines() for f in line.split(","))


def _pick(env: str, *candidates: str) -> str | None:
    if os.getenv(env):
        return os.environ[env]
    return next((c for c in candidates if _has_font(c)), None)


# Computer Modern for prose so Text, Tex and MathTex read as one typeface (the
# math is Latin Modern, CM's descendant). Inter is the fallback sans.
TEXT_FONT = _pick("CHALKBOARD_TEXT_FONT", "CMU Serif", "Inter", "DejaVu Sans")
# Ligature fonts (plain "JetBrains Mono", Fira Code) break Manim's per-glyph Code
# layout on "<=", "->", "==" ("rendered fewer glyphs"), so only ligature-free faces.
CODE_FONT = _pick("CHALKBOARD_CODE_FONT", "JetBrains Mono NL", "DejaVu Sans Mono")


_orig_interpolate_color = _color_core.interpolate_color


def _interpolate_color(color1, color2, alpha):
    """interpolate_color that also takes hex strings (design tokens are strings;
    Manim 0.21 only accepts ManimColor and crashes with "'str' object has no
    attribute 'interpolate'")."""
    return _orig_interpolate_color(ManimColor(color1), ManimColor(color2), alpha)


_MAX_TICK_DECIMALS = 3


def decimals_for(value) -> int:
    """Fewest decimal places that print `value` exactly: 2 -> 0, 2.0 -> 0,
    0.5 -> 1, 0.25 -> 2, 0.1 + 0.2 -> 1. Values that never terminate (1/3)
    get _MAX_TICK_DECIMALS."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(v):
        return 0
    for d in range(_MAX_TICK_DECIMALS + 1):
        if abs(v - round(v, d)) <= 1e-9 * max(1.0, abs(v)):
            return d
    return _MAX_TICK_DECIMALS


def tick_decimal_places(x_range) -> int | None:
    """Decimal places a number line's tick labels need so that every tick
    (x_min + k * step) prints distinctly: max of the start's and the step's
    decimals. A [0, 2.5, 0.5] axis needs 1 ("0.5, 1.0, ..."), never 0 (which
    printed "0, 1, 2, 2, 2" in run 3253240e). None when the range is unusable."""
    if x_range is None:
        return None
    try:
        vals = [float(v) for v in x_range]
    except (TypeError, ValueError):
        return None
    if len(vals) < 2:
        return None
    step = vals[2] if len(vals) > 2 else 1.0
    return max(decimals_for(vals[0]), decimals_for(step))


_orig_number_line_init = NumberLine.__init__


def _number_line_init(self, x_range=None, *args, **kwargs):
    """NumberLine.__init__ whose tick labels always show the decimals the step
    needs. Manim 0.21 infers decimals from str(step) only when no
    decimal_number_config is passed ("2.0" -> one place, "0.30000000000000004"
    -> 17); an explicit num_decimal_places is taken as is, so 0 on a 0.5 step
    rounds the labels to "0, 1, 2, 2, 2". Here a missing value is set to the
    step's decimals and a value too small for the step is raised to it; a
    larger explicit value is kept. Log-scaled axes are left alone (their
    labels are powers, not the range values)."""
    scaling = kwargs.get("scaling")
    need = tick_decimal_places(x_range)
    if need is not None and (scaling is None or isinstance(scaling, LinearBase)):
        cfg = dict(kwargs.get("decimal_number_config") or {})
        have = cfg.get("num_decimal_places")
        if not isinstance(have, int) or have < need:
            cfg["num_decimal_places"] = need
            kwargs["decimal_number_config"] = cfg
    _orig_number_line_init(self, x_range, *args, **kwargs)


_number_line_init._chalkboard_patch = True  # type: ignore[attr-defined]


def apply() -> None:
    """Install the house defaults on Manim's text and TeX classes (idempotent)."""
    # Scenes do `from manim import *` after importing chalkboard_base, so patching
    # the package attribute is enough for generated code.
    if manim.interpolate_color is not _interpolate_color:
        manim.interpolate_color = _interpolate_color
        _color_core.interpolate_color = _interpolate_color
    # Axes, NumberPlane and ChalkAxis/ChalkAxes all build NumberLines, so this
    # one patch makes every tick label follow its axis step.
    if not getattr(NumberLine.__init__, "_chalkboard_patch", False):
        NumberLine.__init__ = _number_line_init
    Tex.set_default(tex_template=TEX_TEMPLATE)
    MathTex.set_default(tex_template=TEX_TEMPLATE)
    if TEXT_FONT:
        for cls in (Text, MarkupText, Paragraph):
            cls.set_default(font=TEXT_FONT)
    if CODE_FONT:
        Code.set_default(paragraph_config={"font": CODE_FONT})


apply()
