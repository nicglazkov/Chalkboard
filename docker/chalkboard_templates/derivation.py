"""DerivationTemplate — a step-by-step math derivation.

The workhorse for calculus, algebra, linear algebra, probability and
physics topics: one aligned EquationGroup whose lines appear one per
narration segment, each morphing out of the previous line (unchanged
symbols stay put, new ones arrive), with an optional justification
("by the chain rule") and an optional term to point at.
"""
from __future__ import annotations

from typing import Sequence

from ._base import _TemplateBase

try:
    from chalkboard_components import EquationGroup  # type: ignore[import-not-found]
    from chalkboard_moves import _on_screen, derivation_step, emphasize_term
except ImportError:
    from docker.chalkboard_components import EquationGroup  # noqa: F401
    from docker.chalkboard_moves import _on_screen, derivation_step, emphasize_term

from manim import FadeIn, FadeOut, UP

_MAX_W = 12.4
_BOTTOM_Y = -2.7   # derivation stays above the caption band
_CAPTION_Y = -3.3
# Lines visible at once. Longer derivations scroll: older lines slide up and
# out, so a 7-step derivation stays as large as a 4-step one.
_WINDOW = 4
_MAX_UPSCALE = 1.3


class DerivationTemplate(_TemplateBase):
    """Step-by-step derivation.

    Beats schema:

        {
            "title": str,                 # persistent title ($..$ = math)
            "lines": list[str],           # LaTeX lines, align*-style: put & before the
                                          # relation to align on (else auto-aligns on the
                                          # first =, \\le, \\approx, ...). "&= ..." continues.
            "steps": list[{               # optional; steps[k] annotates lines[k]
                "note": str | None,       # justification caption ($..$ = math)
                "emphasize": str | None,  # a term of this line to circle (exact TeX substring)
                "role": str | None,       # role for the emphasized term (default focus_primary)
            }],
            "colors": dict[str, str],     # optional per-term role colors, e.g. {"x": "focus_primary"}
        }

    Segment k reveals lines[k] (segment 0 also shows the title). If there
    are more lines than segments, the remaining lines all land in the
    final segment; extra segments hold the finished derivation.
    """

    NAME = "DerivationTemplate"
    REQUIRED_KEYS = ("title", "lines")
    OPTIONAL_KEYS = ("steps", "colors")

    def _validate_beats(self) -> None:
        self._require("title", str)
        lines = self._require("lines", list, of_items=str)
        if not lines:
            raise ValueError("DerivationTemplate: beats['lines'] must be non-empty")
        steps = self.beats.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError("DerivationTemplate: beats['steps'] must be list[dict]")
        for i, st in enumerate(steps):
            if not isinstance(st, dict):
                raise ValueError(f"DerivationTemplate: beats['steps'][{i}] must be dict")
            for key in ("note", "emphasize", "role"):
                if st.get(key) is not None and not isinstance(st[key], str):
                    raise ValueError(
                        f"DerivationTemplate: beats['steps'][{i}][{key!r}] must be str"
                    )
            if st.get("role") is not None:
                self.t.role(st["role"])  # KeyError -> clear message on retry
        colors = self.beats.get("colors", {})
        if not isinstance(colors, dict):
            raise ValueError("DerivationTemplate: beats['colors'] must be dict[str, str]")
        for k, role in colors.items():
            self.t.role(role)

    def render_all(self, segment_durations: Sequence[float]) -> None:
        scene = self.scene
        t = self.t
        lines = self.beats["lines"]
        steps = self.beats.get("steps", [])
        colors = self.beats.get("colors") or None
        emph = [s.get("emphasize") for s in steps if s.get("emphasize")]
        n_seg = len(segment_durations)

        scene.begin_segment(0, duration=segment_durations[0])
        title = self._make_title(self.beats["title"])
        used0 = self._show_title(title)

        eq = EquationGroup(lines, colors=colors, isolate=emph or None, theme=self.theme)
        top = title.get_bottom()[1] - t.space("lg")
        max_h = top - _BOTTOM_Y
        # Fit the tallest run of _WINDOW consecutive lines (not the whole
        # derivation) into the space, and let short derivations grow a little.
        w = min(_WINDOW, len(lines))
        window_h = max(
            eq.lines[i].get_top()[1] - eq.lines[i + w - 1].get_bottom()[1]
            for i in range(len(lines) - w + 1)
        )
        eq.scale(min(_MAX_W / eq.width, max_h / window_h, _MAX_UPSCALE))
        # Center the block horizontally, hang it from just under the title.
        eq.set_x(0)
        eq.align_to(UP * top, UP)

        caption = None
        line_idx = 0
        for seg_idx in range(n_seg):
            dur = segment_durations[seg_idx]
            used = used0 if seg_idx == 0 else 0.0
            if seg_idx > 0:
                scene.begin_segment(seg_idx, duration=dur)
            if line_idx >= len(lines):
                self._rest(dur, used)
                continue
            if caption is not None:
                scene.play(FadeOut(caption), **self._motion("snap"))
                used += self._rt("snap")
                caption = None
            last = seg_idx == n_seg - 1
            batch = range(line_idx, len(lines)) if last else range(line_idx, line_idx + 1)
            for k in batch:
                used += self._scroll(eq, k, top)
                derivation_step(scene, eq, k)
                used += self._rt("emphasis") if k == 0 else self._rt("settle")
                st = steps[k] if k < len(steps) else {}
                if st.get("emphasize"):
                    emphasize_term(scene, eq.lines[k], st["emphasize"],
                                   role=st.get("role") or "focus_primary", theme=self.theme)
                    used += self._rt("emphasis")
                if st.get("note"):
                    if caption is not None:
                        scene.remove(caption)
                    caption = self._fit_text(st["note"], size="body",
                                             role="accent_meta", max_width=_MAX_W)
                    caption.move_to(UP * _CAPTION_Y)
                    scene.play(FadeIn(caption), **self._motion("emphasis"))
                    used += self._rt("emphasis")
            line_idx = batch[-1] + 1
            self._rest(dur, used)
        self.equation = eq

    def _scroll(self, eq, k: int, top: float) -> float:
        """Before revealing line k, slide the window so lines k-_WINDOW+1..k fit
        under `top`. Returns the time spent (0 when no scroll is needed)."""
        first = k - _WINDOW + 1
        if first <= 0:
            return 0.0
        dy = top - eq.lines[first].get_top()[1]
        if dy <= 0.01:
            return 0.0
        scene = self.scene
        leaving = [eq.lines[j] for j in range(first) if _on_screen(scene, eq.lines[j])]
        staying = [eq.lines[j] for j in range(first, k) if _on_screen(scene, eq.lines[j])]
        for j in range(k, len(eq.lines)):        # not on screen yet: move instantly
            eq.lines[j].shift(UP * dy)
        anims = [FadeOut(m, shift=UP * dy) for m in leaving] + [m.animate.shift(UP * dy) for m in staying]
        if not anims:
            return 0.0
        scene.play(*anims, **self._motion("snap"))
        return self._rt("snap")
