"""HowtoTemplate — numbered step list with progressive highlight.

All steps appear at segment 0, dimmed. Each later segment promotes one
step to focus and marks the previous one done. Used for procedures,
setup guides, multi-stage recipes and proofs-by-steps.
"""
from __future__ import annotations

from typing import Sequence

from ._base import _TemplateBase

try:
    from chalkboard_components import ChalkBadge, Callout  # type: ignore[import-not-found]
    from chalkboard_moves import cascade_reveal, annotate_and_pause
except ImportError:
    from docker.chalkboard_components import ChalkBadge, Callout  # noqa: F401
    from docker.chalkboard_moves import cascade_reveal, annotate_and_pause

from manim import DOWN, FadeOut, LEFT, RIGHT, Transform, UP, VGroup

_ROW_LEFT_X = -6.3
_ROW_RIGHT_X = -0.8          # rows stay in the LEFT zone
_CALLOUT_ANCHOR_X = 0.7      # callouts live in the right column


class _StepRow(VGroup):
    """Badge + label + description with three visual states."""

    def __init__(self, badge, label, description, t):
        super().__init__(badge, label, description)
        self.badge, self.label, self.description, self._t = badge, label, description, t

    def set_state(self, state: str) -> "_StepRow":
        t = self._t
        if state == "active":
            self.badge.highlight("focus_primary")
            self.label.set_color(t.role("focus_primary")).set_opacity(1.0)
            self.description.set_color(t.role("body")).set_opacity(1.0)
        elif state == "done":
            self.badge.highlight("focus_secondary")
            self.label.set_color(t.role("body")).set_opacity(0.85)
            self.description.set_color(t.role("context_muted")).set_opacity(0.85)
        else:  # pending
            self.badge.mute()
            self.label.set_color(t.role("context_muted")).set_opacity(0.6)
            self.description.set_color(t.role("context_muted")).set_opacity(0.6)
        return self


class HowtoTemplate(_TemplateBase):
    """Numbered step list.

    Beats schema:

        {
            "title": str,                # persistent scene title ($..$ = math)
            "steps": list[{              # one entry per step
                "label": str,            # short step name (≤ 4 words)
                "description": str,      # one-line description ($..$ = math)
                "callout": str | None,   # optional side note while active
            }],
        }

    Segment 0 introduces all steps dimmed; segment k activates step k-1
    and marks earlier steps done.
    """

    NAME = "HowtoTemplate"
    REQUIRED_KEYS = ("title", "steps")
    OPTIONAL_KEYS = ()

    def _validate_beats(self) -> None:
        self._require("title", str)
        steps = self._require("steps", list)
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                raise ValueError(f"HowtoTemplate: beats['steps'][{i}] must be dict")
            for key in ("label", "description"):
                if key not in step or not isinstance(step[key], str):
                    raise ValueError(
                        f"HowtoTemplate: beats['steps'][{i}][{key!r}] must be str"
                    )

    def render_all(self, segment_durations: Sequence[float]) -> None:
        scene = self.scene
        t = self.t
        steps = self.beats["steps"]

        # ── Segment 0: title + whole list, dimmed ──
        scene.begin_segment(0, duration=segment_durations[0])
        title = self._make_title(self.beats["title"])
        used = self._show_title(title)
        rows = self._build_rows(steps, top=title.get_bottom()[1] - t.space("lg"))
        for row in rows:
            row.set_state("pending")
        used += self._cue(1)
        cascade_reveal(scene, rows, lag_name="cascade", motion_name="emphasis")
        used += self._rt("emphasis")
        self._rest(segment_durations[0], used)

        # ── Segments 1..N: activate one step at a time ──
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

            cur = rows[seg_idx - 1]
            anims = [Transform(cur, cur.copy().set_state("active"))]
            if seg_idx >= 2:
                prev = rows[seg_idx - 2]
                anims.append(Transform(prev, prev.copy().set_state("done")))
            used += self._cue(1)
            scene.play(*anims, **self._motion("snap"))
            used += self._rt("snap")

            callout = step.get("callout")
            if callout:
                used += self._cue(2)
                anchor = cur.label.get_center().copy()
                anchor[0] = _CALLOUT_ANCHOR_X
                note = Callout(callout, anchor, role="accent_meta", size="body",
                               offset=RIGHT * t.space("lg"), theme=self.theme)
                room = 6.6 - (anchor[0] + t.space("lg"))
                if note.label.width > room:
                    note.label.scale_to_fit_width(room)
                note.label.align_to(anchor + RIGHT * t.space("lg"), LEFT)
                note.connector.put_start_and_end_on(
                    note.label.get_left() + LEFT * t.space("xs"), anchor)
                # Hold only what the narration leaves after the reveal + dim.
                hold = max(0.0, min(1.5, dur - used - self._rt("settle") - self._rt("snap")))
                annotate_and_pause(scene, note, hold_sec=hold)
                used += self._rt("settle") + hold + self._rt("snap")
                prev_note = note

            self._rest(dur, used)

    def _build_rows(self, steps, *, top: float) -> list:
        """Rows left-aligned in the LEFT zone, evenly spaced, shrunk to fit."""
        t = self.t
        rows = []
        text_max_w = _ROW_RIGHT_X - _ROW_LEFT_X
        for i, step in enumerate(steps):
            badge = ChalkBadge(str(i + 1), role="focus_secondary", theme=self.theme)
            avail = text_max_w - badge.width - t.space("sm")
            label = self._fit_text(step["label"], size="heading", role="body", max_width=avail)
            description = self._fit_text(step["description"], size="caption",
                                         role="context_muted", max_width=avail)
            label.next_to(badge, RIGHT, buff=t.space("sm"))
            description.next_to(label, DOWN, buff=t.space("xs"), aligned_edge=LEFT)
            rows.append(_StepRow(badge, label, description, t))

        bottom = -3.6
        gap = t.space("md")
        stack = VGroup(*rows).arrange(DOWN, buff=gap, aligned_edge=LEFT)
        avail_h = top - bottom
        if stack.height > avail_h:
            stack.scale_to_fit_height(avail_h)
        stack.set_x(_ROW_LEFT_X + stack.width / 2)
        stack.align_to(UP * top, UP)
        return rows
