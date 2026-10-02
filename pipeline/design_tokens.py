"""Pipeline-side view of the Chalkboard design tokens.

The single source of truth is ``docker/chalkboard_tokens.py`` (the file the
renderer imports as ``chalkboard_tokens``). This module loads that file BY
PATH and re-exports it so pipeline code (manim_agent's prompt block, tests)
can write ``from pipeline.design_tokens import T, render_prompt_block``
without the token tables ever being duplicated.

Loading by path (rather than ``from docker.chalkboard_tokens import ...``)
avoids depending on ``docker`` resolving to this repo's folder instead of
the unrelated ``docker`` SDK package. The module is registered in
``sys.modules`` as ``chalkboard_tokens`` so the components/moves/templates
(which do ``from chalkboard_tokens import T``) share the exact same class
object whether they are imported render-side or pipeline-side.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "docker" / "chalkboard_tokens.py"


def _load():
    mod = sys.modules.get("chalkboard_tokens")
    if mod is not None and hasattr(mod, "T"):
        return mod
    spec = importlib.util.spec_from_file_location("chalkboard_tokens", _SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["chalkboard_tokens"] = mod
    spec.loader.exec_module(mod)
    return mod


_tokens = _load()

T = _tokens.T
TOKENS = _tokens.TOKENS
Theme = _tokens.Theme
DEFAULT_THEME = _tokens.DEFAULT_THEME
render_prompt_block = _tokens.render_prompt_block

__all__ = ["T", "TOKENS", "Theme", "DEFAULT_THEME", "render_prompt_block"]
