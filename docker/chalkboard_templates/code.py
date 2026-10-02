"""CodeTemplate — line-by-line walkthrough of a source-code block.

The whole block reveals at segment 0; each later segment highlights one
or more lines, optionally with a callout in the right column. Used for
function explanations, pseudo-code, config-file walk-throughs.
"""
from __future__ import annotations

from typing import Sequence

from ._base import _TemplateBase

try:
    from chalkboard_components import (  # type: ignore[import-not-found]
        ChalkCode, Callout,
    )
    from chalkboard_moves import (
        reveal_with_emphasis, annotate_and_pause,
    )
except ImportError:
    from docker.chalkboard_components import (  # noqa: F401
        ChalkCode, Callout,
    )
    from docker.chalkboard_moves import (
        reveal_with_emphasis, annotate_and_pause,
    )

from manim import DOWN, FadeOut, LaggedStart, LEFT, RIGHT, UP

# Code occupies the LEFT zone: its right edge must stay < -0.5 so callouts
# in the right column never collide with it.
_CODE_LEFT_X = -6.6
_CODE_RIGHT_X = -0.7
_CALLOUT_ANCHOR_X = 0.7


class CodeTemplate(_TemplateBase):
    """Code walkthrough.

    Beats schema:

        {
            "title": str,                # persistent scene title ($..$ = math)
            "code_string": str,          # the source code (multi-line)
            "language": str,             # Pygments lexer name (default "python")
            "steps": list[{              # one per narration segment after intro
                "line_indices": list[int],   # zero-indexed lines to highlight
                "callout": str | None,
            }],
        }
    """

    NAME = "CodeTemplate"
    REQUIRED_KEYS = ("title", "code_string", "steps")
    OPTIONAL_KEYS = ("language",)

    def _validate_beats(self) -> None:
        self._require("title", str)
        code = self._require("code_string", str)
        steps = self._require("steps", list)
        n_lines = max(1, code.count("\n") + 1)
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"CodeTemplate: beats['steps'][{i}] must be dict")
            lines = step.get("line_indices")
            if not isinstance(lines, list) or not lines:
                raise ValueError(
                    f"CodeTemplate: beats['steps'][{i}]['line_indices'] must be "
                    f"non-empty list[int]"
                )
            for j, idx in enumerate(lines):
                if not isinstance(idx, int) or not 0 <= idx < n_lines:
                    raise ValueError(
                        f"CodeTemplate: beats['steps'][{i}]['line_indices'][{j}]="
                        f"{idx!r} out of range [0, {n_lines})"
                    )

    def render_all(self, segment_durations: Sequence[float]) -> None:
        scene = self.scene
        t = self.t
        steps = self.beats["steps"]
        language = self.beats.get("language", "python")

        # ── Segment 0: title + whole code block ──
        scene.begin_segment(0, duration=segment_durations[0])
        title = self._make_title(self.beats["title"], size="heading")
        used = self._show_title(title)

        code = ChalkCode(self.beats["code_string"], language=language, theme=self.theme)
        max_w = _CODE_RIGHT_X - _CODE_LEFT_X
        max_h = title.get_bottom()[1] - t.space("md") - (-3.7)
        # Fill the left column: grow small snippets (up to 1.35x, so the font
        # never balloons) and shrink long ones to fit.
        code.scale(min(max_w / code.width, max_h / code.height, 1.35))
        code.move_to(UP * ((title.get_bottom()[1] - t.space("md") + -3.7) / 2))
        code.set_x(_CODE_LEFT_X + code.width / 2)
        reveal_with_emphasis(scene, code)
        used += self._rt("emphasis")
        self._rest(segment_durations[0], used)

        # ── Segments 1..N: one highlight step per narration segment ──
        primary = t.role("focus_primary")
        muted = t.role("context_muted")
        prev_note = None
        for seg_idx, step in enumerate(steps, start=1):
            if seg_idx >= len(segment_durations):
                break
            dur = segment_durations[seg_idx]
            scene.begin_segment(seg_idx, duration=dur)
            used = 0.0

            if prev_note is not None:
                scene.play(FadeOut(prev_note), **self._motion("snap"))
                used += self._rt("snap")
                prev_note = None

            active = set(step["line_indices"])
            highlights = [
                line.animate.set_color(primary if i in active else muted)
                for i, line in enumerate(code.code_lines)
            ]
            scene.play(
                LaggedStart(*highlights, lag_ratio=t.lag("quick")),
                **self._motion("emphasis"),
            )
            used += self._rt("emphasis")

            callout = step.get("callout")
            if callout:
                first = code.code_lines[min(active)]
                anchor = first.get_center().copy()
                anchor[0] = max(_CALLOUT_ANCHOR_X, first.get_right()[0] + t.space("sm"))
                note = Callout(
                    callout,
                    anchor,
                    role="accent_meta",
                    size="body",
                    offset=RIGHT * t.space("lg"),
                    theme=self.theme,
                )
                # Left-align the text just right of the anchor; shrink it if
                # it would run off the right edge.
                room = 6.6 - (anchor[0] + t.space("lg"))
                if note.label.width > room:
                    note.label.scale_to_fit_width(room)
                note.label.align_to(anchor + RIGHT * t.space("lg"), LEFT)
                note.connector.put_start_and_end_on(note.label.get_left() + LEFT * t.space("xs"), anchor)
                # Hold only what the narration leaves after the reveal + dim.
                hold = max(0.0, min(1.5, dur - used - self._rt("settle") - self._rt("snap")))
                annotate_and_pause(scene, note, hold_sec=hold)
                used += self._rt("settle") + hold + self._rt("snap")
                prev_note = note

            self._rest(dur, used)
