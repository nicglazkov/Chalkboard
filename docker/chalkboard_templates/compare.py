"""CompareTemplate — side-by-side two-up comparison.

Two panels split the canvas LEFT vs RIGHT. Each narration segment
introduces one matched pair of points (left + right). Used for CPU vs
GPU, recursion vs iteration, mean vs median — any "two things weighed".
"""
from __future__ import annotations

from typing import Sequence

from ._base import _TemplateBase

try:
    from chalkboard_components import ChalkPanel  # type: ignore[import-not-found]
    from chalkboard_moves import compare_split, cascade_reveal
except ImportError:
    from docker.chalkboard_components import ChalkPanel  # noqa: F401
    from docker.chalkboard_moves import compare_split, cascade_reveal

from manim import DOWN, LEFT, RIGHT, UP


class CompareTemplate(_TemplateBase):
    """Two-up comparison.

    Beats schema:

        {
            "title": str | None,                       # optional title above the panels
            "left":  {"title": str, "points": list[str]},
            "right": {"title": str, "points": list[str]},
        }

    Points may contain `$...$` math. Segment 0 establishes the panels;
    segment k reveals pair k-1. If there are more pairs than segments,
    the remaining pairs all land in the final segment.
    """

    NAME = "CompareTemplate"
    REQUIRED_KEYS = ("left", "right")
    OPTIONAL_KEYS = ("title",)

    def _validate_beats(self) -> None:
        for side_key in ("left", "right"):
            side = self._require(side_key, dict)
            if "title" not in side or not isinstance(side["title"], str):
                raise ValueError(
                    f"CompareTemplate: beats[{side_key!r}]['title'] must be str"
                )
            if "points" not in side or not isinstance(side["points"], list):
                raise ValueError(
                    f"CompareTemplate: beats[{side_key!r}]['points'] must be list[str]"
                )
            for i, p in enumerate(side["points"]):
                if not isinstance(p, str):
                    raise ValueError(
                        f"CompareTemplate: beats[{side_key!r}]['points'][{i}] must be str"
                    )
        if len(self.beats["left"]["points"]) != len(self.beats["right"]["points"]):
            raise ValueError(
                "CompareTemplate: beats['left']['points'] and "
                "beats['right']['points'] must have the same length "
                "(matched pairs)."
            )
        if self.beats.get("title") is not None and not isinstance(self.beats["title"], str):
            raise ValueError("CompareTemplate: beats['title'] must be str when given")

    def render_all(self, segment_durations: Sequence[float]) -> None:
        scene = self.scene
        t = self.t
        n_seg = len(segment_durations)

        scene.begin_segment(0, duration=segment_durations[0])
        used = 0.0
        top = 3.6
        if self.beats.get("title"):
            title = self._make_title(self.beats["title"])
            used += self._show_title(title)
            top = title.get_bottom()[1] - t.space("md")
        panel_h = top - (-3.6)
        panel_w = 5.6  # at x=±3.5 the inner edges sit at ±0.7, clear of the zone line
        center_y = (top + -3.6) / 2

        left_panel = ChalkPanel(title=self.beats["left"]["title"], width=panel_w,
                                height=panel_h, theme=self.theme)
        left_panel.move_to(LEFT * 3.5 + UP * center_y)
        right_panel = ChalkPanel(title=self.beats["right"]["title"], width=panel_w,
                                 height=panel_h, theme=self.theme)
        right_panel.move_to(RIGHT * 3.5 + UP * center_y)

        # ── Segment 0: panels + divider ──
        compare_split(scene, left_panel, right_panel, add_divider=True, theme=self.theme)
        used += self._rt("snap") + self._rt("emphasis")
        self._rest(segment_durations[0], used)

        # ── Segments 1..N: matched pairs ──
        pairs = list(zip(self.beats["left"]["points"], self.beats["right"]["points"]))
        max_w = panel_w - 2 * t.space("md")
        cursors = {"l": left_panel.body_top + DOWN * t.space("sm"),
                   "r": right_panel.body_top + DOWN * t.space("sm")}
        shown: list = []

        def place(text: str, side: str, role: str):
            obj = self._fit_text(text, size="body", role=role, max_width=max_w)
            panel = left_panel if side == "l" else right_panel
            obj.move_to(cursors[side] + DOWN * obj.height / 2)
            obj.set_x(panel.get_center()[0])
            cursors[side] = obj.get_bottom() + DOWN * t.space("md")
            return obj

        seg_idx = 1
        i = 0
        while i < len(pairs) and seg_idx < n_seg:
            dur = segment_durations[seg_idx]
            scene.begin_segment(seg_idx, duration=dur)
            used = 0.0
            # Last segment takes every remaining pair.
            batch = pairs[i:] if seg_idx == n_seg - 1 else pairs[i:i + 1]
            if shown:
                scene.play(*[m.animate.set_color(t.role("body")) for m in shown],
                           **self._motion("snap"))
                used += self._rt("snap")
            new = []
            for l_text, r_text in batch:
                l_obj = place(l_text, "l", "focus_secondary")
                r_obj = place(r_text, "r", "focus_primary")
                new += [l_obj, r_obj]
            cascade_reveal(scene, new, lag_name="cascade", motion_name="emphasis")
            used += self._rt("emphasis")
            shown += new
            i += len(batch)
            self._rest(dur, used)
            seg_idx += 1
