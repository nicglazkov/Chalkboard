"""Chalkboard template library.

Scene skeletons that the manim_agent INSTANTIATES instead of hand-rolling
choreography from primitives:

    AlgorithmTemplate   — step-through over an array of cells
    CodeTemplate        — line-by-line walkthrough of a code block
    CompareTemplate     — side-by-side two-up comparison
    DerivationTemplate  — aligned step-by-step math derivation
    HowtoTemplate       — numbered step list with progressive highlight
    TimelineTemplate    — chronological events on a horizontal axis

Each template:

  * Takes (scene, beats, theme="chalkboard") in __init__.
  * Exposes render_all(segment_durations) that emits the FULL animation
    sequence — begin_segment calls, animations, timing — using the
    components and moves under the hood.
  * Validates `beats` strictly at construction time and raises ValueError
    with a schema-mismatch message, so the problem surfaces in the
    layout dry-run and flows back to the agent instead of mid-render.

The structure is fixed by Python code; the agent fills in the data (the
`beats` dict), so the same kind of topic looks the same shape across runs.

IMPORT CONTEXTS: render-side `from chalkboard_templates import ...` with
docker/ (or /render) on PYTHONPATH; pipeline-side tests import
`docker.chalkboard_templates`. Relative imports work in both.
"""
from __future__ import annotations

from .algorithm import AlgorithmTemplate
from .code import CodeTemplate
from .compare import CompareTemplate
from .derivation import DerivationTemplate
from .howto import HowtoTemplate
from .timeline import TimelineTemplate

__all__ = [
    "AlgorithmTemplate",
    "CodeTemplate",
    "CompareTemplate",
    "DerivationTemplate",
    "HowtoTemplate",
    "TimelineTemplate",
]
