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

import os
import shutil
import subprocess

import manim
import manim.utils.color.core as _color_core
from manim import Code, ManimColor, MarkupText, MathTex, Paragraph, Tex, TexTemplate, Text

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


def apply() -> None:
    """Install the house defaults on Manim's text and TeX classes (idempotent)."""
    # Scenes do `from manim import *` after importing chalkboard_base, so patching
    # the package attribute is enough for generated code.
    if manim.interpolate_color is not _interpolate_color:
        manim.interpolate_color = _interpolate_color
        _color_core.interpolate_color = _interpolate_color
    Tex.set_default(tex_template=TEX_TEMPLATE)
    MathTex.set_default(tex_template=TEX_TEMPLATE)
    if TEXT_FONT:
        for cls in (Text, MarkupText, Paragraph):
            cls.set_default(font=TEXT_FONT)
    if CODE_FONT:
        Code.set_default(paragraph_config={"font": CODE_FONT})


apply()
