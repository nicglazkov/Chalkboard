"""AlgorithmTemplate — step-through over an array of cells.

The viewer sees an array; per narration segment one cell becomes the
focal point, the others recede, and an optional callout explains the
step. Used for binary search, sorting, DP traces — any deterministic
algorithm whose state advances cell by cell.
"""
from __future__ import annotations

from typing import Any, Sequence

from ._base import CANVAS_SAFE_W, _TemplateBase

try:
    from chalkboard_components import (  # type: ignore[import-not-found]
        ChalkBox, Callout, StepCounter,
    )
    from chalkboard_moves import (
        cascade_reveal, progressive_step, annotate_and_pause,
    )
except ImportError:
    from docker.chalkboard_components import (  # noqa: F401
        ChalkBox, Callout, StepCounter,
    )
    from docker.chalkboard_moves import (
        cascade_reveal, progressive_step, annotate_and_pause,
    )

from manim import FadeOut, RIGHT, Transform, UP, UR, VGroup


class AlgorithmTemplate(_TemplateBase):
    """Algorithm step-through.

    Beats schema:

        {
            "title": str,                          # persistent scene title ($..$ = math)
            "values": list[str | int],             # initial array contents
            "steps": list[{                        # one per narration segment after intro
                "active_idx": int,                 # cell to focus this step
                "callout": str | None,             # optional side-channel insight
                "value_change": dict | None,       # optional {"idx": int, "to": str|int}
            }],
            "math": bool,                          # optional: values are LaTeX (x_1, \\frac12)
        }

    Segment 0 is the intro (title + array reveal); segments 1..N map to
    steps[0..N-1].
    """

    NAME = "AlgorithmTemplate"
    REQUIRED_KEYS = ("title", "values", "steps")
    OPTIONAL_KEYS = ("math",)

    def _validate_beats(self) -> None:
        self._require("title", str)
        values = self._require("values", list)
        if not values:
            raise ValueError("AlgorithmTemplate: beats['values'] must be non-empty")
        steps = self._require("steps", list)
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(
                    f"AlgorithmTemplate: beats['steps'][{i}] must be dict"
                )
            if "active_idx" not in step or not isinstance(step["active_idx"], int):
                raise ValueError(
                    f"AlgorithmTemplate: beats['steps'][{i}]['active_idx'] "
                    f"must be int (0..len(values)-1)"
                )
            if not 0 <= step["active_idx"] < len(values):
                raise ValueError(
                    f"AlgorithmTemplate: beats['steps'][{i}]['active_idx']="
                    f"{step['active_idx']} out of range [0, {len(values)})"
                )

    def render_all(self, segment_durations: Sequence[float]) -> None:
        scene = self.scene
        t = self.t
        steps = self.beats["steps"]

        # ── Segment 0: title, array, step counter ──
        scene.begin_segment(0, duration=segment_durations[0])
        title = self._make_title(self.beats["title"])
        used = self._show_title(title)

        cells = self._build_cells(self.beats["values"])
        cascade_reveal(scene, cells, lag_name="quick", motion_name="emphasis")
        used += self._rt("emphasis")
        # Re-parent the individually revealed cells under one row group so
        # the layout check sees one centered array, not N zone-straddling
        # cells. Rendering is identical.
        row = VGroup(*cells)
        scene.remove(*cells)
        scene.add(row)

        counter = StepCounter(total=len(steps), theme=self.theme)
        counter.to_corner(UR)
        scene.add(counter)
        self._rest(segment_durations[0], used)

        # ── Segments 1..N: one step per narration segment ──
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

            vc = step.get("value_change")
            if isinstance(vc, dict) and "idx" in vc and "to" in vc and 0 <= vc["idx"] < len(cells):
                self._replace_cell_value(cells, vc["idx"], str(vc["to"]))
                used += self._rt("snap")

            progressive_step(scene, cells, current_idx=step["active_idx"])
            counter.advance()
            used += self._rt("snap")

            callout = step.get("callout")
            if callout:
                note = Callout(
                    callout,
                    cells[step["active_idx"]].get_top(),
                    role="accent_meta",
                    offset=UP * t.space("lg"),
                    theme=self.theme,
                )
                # Hold only what the narration leaves after the reveal + dim.
                hold = max(0.0, min(1.5, dur - used - self._rt("settle") - self._rt("snap")))
                annotate_and_pause(scene, note, hold_sec=hold)
                used += self._rt("settle") + hold + self._rt("snap")
                prev_note = note

            self._rest(dur, used)

    # ── helpers ────────────────────────────────────────────────────

    def _build_cells(self, values: Sequence[Any]) -> list:
        """A centered row of equal-width ChalkBoxes, scaled down only if it
        would not fit the canvas."""
        t = self.t
        math = bool(self.beats.get("math"))
        probe = [ChalkBox(str(v), role="body", math=math, theme=self.theme) for v in values]
        cell_w = max(c.width for c in probe)
        cells = [ChalkBox(str(v), role="body", width=cell_w, math=math, theme=self.theme)
                 for v in values]
        gap = t.space("sm")
        row = VGroup(*cells).arrange(RIGHT, buff=gap)
        if row.width > CANVAS_SAFE_W:
            row.scale_to_fit_width(CANVAS_SAFE_W)
        row.move_to(UP * 0.2)
        return cells

    def _replace_cell_value(self, cells: list, idx: int, new_value: str) -> None:
        """Morph cell `idx` into a fresh ChalkBox showing `new_value`, in place."""
        old = cells[idx]
        replacement = ChalkBox(
            new_value,
            role="body",
            width=old.box.width,
            height=old.box.height,
            math=bool(self.beats.get("math")),
            theme=self.theme,
        )
        replacement.move_to(old.get_center())
        if old._base_opacity is not None:
            replacement.mute()
        self.scene.play(Transform(old, replacement), **self._motion("snap"))
