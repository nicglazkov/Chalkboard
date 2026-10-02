"""TimelineTemplate — chronological events on a horizontal axis.

A horizontal axis spans the canvas. Each narration segment reveals one
event (dot on the axis + date + label + optional description),
alternating above/below. Past events recede; the current one is focus.
"""
from __future__ import annotations

from typing import Sequence

from ._base import _TemplateBase

try:
    from chalkboard_components import ChalkAxis  # type: ignore[import-not-found]
    from chalkboard_moves import reveal_with_emphasis
except ImportError:
    from docker.chalkboard_components import ChalkAxis  # noqa: F401
    from docker.chalkboard_moves import reveal_with_emphasis

from manim import DOWN, Dot, FadeIn, FadeOut, GrowFromCenter, LaggedStart, Line, UP, VGroup

_AXIS_Y = -0.2
_AXIS_LEN = 12.0
_CAPTION_Y = -3.35


class TimelineTemplate(_TemplateBase):
    """Chronological timeline.

    Beats schema:

        {
            "title": str,                  # persistent scene title ($..$ = math)
            "events": list[{               # one per segment after intro
                "date": str,               # short date label ("1995", "AD 800")
                "label": str,              # 1-3 word event name
                "description": str | None, # optional sub-label
                "callout": str | None,     # optional caption shown while current
            }],
        }

    Segment 0 establishes the axis; segment k reveals events[k-1].
    """

    NAME = "TimelineTemplate"
    REQUIRED_KEYS = ("title", "events")
    OPTIONAL_KEYS = ()

    def _validate_beats(self) -> None:
        self._require("title", str)
        events = self._require("events", list)
        for i, ev in enumerate(events):
            if not isinstance(ev, dict):
                raise ValueError(f"TimelineTemplate: beats['events'][{i}] must be dict")
            for key in ("date", "label"):
                if key not in ev or not isinstance(ev[key], str):
                    raise ValueError(
                        f"TimelineTemplate: beats['events'][{i}][{key!r}] must be str"
                    )

    def render_all(self, segment_durations: Sequence[float]) -> None:
        scene = self.scene
        t = self.t
        events = self.beats["events"]
        n = max(len(events), 1)

        # ── Segment 0: title + axis ──
        scene.begin_segment(0, duration=segment_durations[0])
        title = self._make_title(self.beats["title"])
        used = self._show_title(title)
        axis = ChalkAxis(x_range=[0, n + 1, 1], length=_AXIS_LEN,
                         include_numbers=False, theme=self.theme)
        axis.move_to(UP * _AXIS_Y)
        used += self._cue(1)
        reveal_with_emphasis(scene, axis, motion_name="snap")
        used += self._rt("snap")
        board = VGroup(axis)
        scene.remove(axis)
        scene.add(board)
        self._rest(segment_durations[0], used)

        # Events sit on the axis ticks 1..n. Same-side neighbours are two
        # ticks apart, which bounds how wide a label may be.
        step_x = _AXIS_LEN / (n + 1)
        max_w = min(3.6, 2 * step_x - t.space("sm"))

        # ── Segments 1..N ──
        prior: list = []
        prev_caption = None
        for seg_idx, ev in enumerate(events, start=1):
            if seg_idx >= len(segment_durations):
                break
            dur = segment_durations[seg_idx]
            scene.begin_segment(seg_idx, duration=dur)
            used = 0.0

            fades = []
            if prev_caption is not None:
                fades.append(FadeOut(prev_caption))
                prev_caption = None
            fades += [g.animate.set_color(t.role("context_muted")) for g in prior]
            if fades:
                scene.play(*fades, **self._motion("snap"))
                used += self._rt("snap")

            x = axis.number_line.n2p(seg_idx)[0]
            above = (seg_idx % 2) == 1
            sign = 1 if above else -1
            dot = Dot(point=[x, _AXIS_Y, 0], color=t.role("focus_primary"), radius=0.1)
            stem = Line([x, _AXIS_Y + sign * 0.15, 0], [x, _AXIS_Y + sign * 0.5, 0],
                        color=t.role("stroke_muted"), stroke_width=t.stroke_width("hair"))
            date = self._fit_text(ev["date"], size="caption", role="accent_cool", max_width=max_w)
            label = self._fit_text(ev["label"], size="body", role="focus_primary", max_width=max_w)
            stack = [date, label]
            if ev.get("description"):
                stack.append(self._fit_text(ev["description"], size="caption",
                                            role="context_muted", max_width=max_w))
            # Same top-down reading order on both sides (date, label,
            # description); the block hangs off the end of the stem.
            block = VGroup(*stack).arrange(DOWN, buff=t.space("xs"))
            block.set_x(x)
            anchor_y = _AXIS_Y + sign * 0.6
            block.align_to(UP * anchor_y, DOWN if above else UP)

            used += self._cue(1)
            scene.play(GrowFromCenter(dot), FadeIn(stem), **self._motion("snap"))
            scene.play(LaggedStart(*[FadeIn(m) for m in stack], lag_ratio=t.lag("cascade")),
                       **self._motion("emphasis"))
            used += self._rt("snap") + self._rt("emphasis")
            event = VGroup(dot, stem, *stack)
            prior.append(event)
            # Keep the whole timeline as ONE scene-level group: it spans the
            # canvas by design, so per-label zone classification would flag
            # every label near x = ±0.5. Rendering is unchanged.
            scene.remove(dot, stem, *stack)
            board.add(event)

            callout = ev.get("callout")
            if callout:
                used += self._cue(2)
                caption = self._fit_text(callout, size="body", role="accent_meta", max_width=11.0)
                caption.move_to(UP * _CAPTION_Y)
                scene.play(FadeIn(caption), **self._motion("settle"))
                used += self._rt("settle")
                prev_caption = caption

            self._rest(dur, used)
