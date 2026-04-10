# docker/chalkboard_base.py
"""
ChalkboardSceneBase — layout validation mixin for generated Manim scenes.

Generated scenes inherit from this class (listed before Scene in MRO):
    class ChalkboardScene(ChalkboardSceneBase, Scene):

The class tracks per-segment animation timing and checks bounding boxes at
each segment boundary, writing layout_report.json to _REPORT_DIR on completion.
No Manim imports at module level — safe to import without Manim installed.
"""
import json
from pathlib import Path


# Canvas bounds (Manim CE default 16:9 scene)
_CANVAS_X_MIN = -7.11
_CANVAS_X_MAX =  7.11
_CANVAS_Y_MIN = -4.0
_CANVAS_Y_MAX =  4.0
_BOUND_TOL   = 0.1    # off-screen tolerance (Manim units)
_OVERLAP_TOL = 0.05   # overlap edge tolerance (avoids flagging exact touches)


def _classify_overlap(bb1, bb2, tol=_OVERLAP_TOL):
    """
    Classify spatial relationship between two bounding boxes.
    bb[0] = min corner (x_min, y_min, z), bb[2] = max corner (x_max, y_max, z).

    Returns:
        "none"      — no intersection
        "contained" — one box fully inside the other (intentional, e.g. text in box)
        "partial"   — boxes intersect but neither contains the other (collision)
    """
    x1_min, y1_min = bb1[0][0], bb1[0][1]
    x1_max, y1_max = bb1[2][0], bb1[2][1]
    x2_min, y2_min = bb2[0][0], bb2[0][1]
    x2_max, y2_max = bb2[2][0], bb2[2][1]

    x_overlaps = x1_max - tol > x2_min and x2_max - tol > x1_min
    y_overlaps = y1_max - tol > y2_min and y2_max - tol > y1_min
    if not (x_overlaps and y_overlaps):
        return "none"

    # m1 fully inside m2
    if (x1_min >= x2_min - tol and x1_max <= x2_max + tol and
            y1_min >= y2_min - tol and y1_max <= y2_max + tol):
        return "contained"

    # m2 fully inside m1
    if (x2_min >= x1_min - tol and x2_max <= x1_max + tol and
            y2_min >= y1_min - tol and y2_max <= y1_max + tol):
        return "contained"

    return "partial"


class ChalkboardSceneBase:
    """
    Validation mixin for ChalkboardScene. No Manim parent — use as:
        class ChalkboardScene(ChalkboardSceneBase, Scene):

    Public API (called by generated construct()):
        self.begin_segment(n, duration)   — start of each segment
        self.end_layout_check()           — BEFORE the final FadeOut
    """

    _REPORT_DIR: str = "/output"   # overridable in tests

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._lc_segment: int | None = None
        self._lc_run_time: float = 0.0
        self._lc_budget: float = 0.0
        self._lc_done: bool = False
        self._lc_violations: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def begin_segment(self, n: int, duration: float) -> None:
        """Call at the start of every segment block in construct()."""
        if self._lc_segment is not None:
            self._lc_check_segment()
        self._lc_segment = n
        self._lc_run_time = 0.0
        self._lc_budget = duration

    def end_layout_check(self) -> None:
        """Call BEFORE the final FadeOut at end of construct()."""
        if self._lc_segment is not None:
            self._lc_check_segment()
        self._lc_done = True
        self._lc_write_report()

    # ------------------------------------------------------------------
    # play() override — accumulate run_time
    # ------------------------------------------------------------------

    def play(self, *animations, run_time=None, **kwargs):
        if not self._lc_done and self._lc_segment is not None:
            self._lc_run_time += run_time if run_time is not None else 1.0
        if run_time is not None:
            kwargs["run_time"] = run_time
        return super().play(*animations, **kwargs)

    # ------------------------------------------------------------------
    # wait() override — accumulate duration
    # ------------------------------------------------------------------

    def wait(self, duration=1.0, **kwargs):
        if not self._lc_done and self._lc_segment is not None:
            self._lc_run_time += duration
        return super().wait(duration, **kwargs)

    # ------------------------------------------------------------------
    # Internal validation
    # ------------------------------------------------------------------

    def _lc_check_segment(self) -> None:
        n = self._lc_segment

        # 1. Timing overrun
        # Tolerance of 1.5s accounts for: (a) the 0.5s inter-segment FadeOut that
        # runs before begin_segment() and is charged to the previous segment, and
        # (b) the ~5-10% uncertainty between estimated and actual TTS durations.
        if self._lc_run_time > self._lc_budget + 1.5:
            self._lc_violations.append({
                "type": "timing_overrun",
                "segment": n,
                "budget_sec": round(self._lc_budget, 3),
                "actual_sec": round(self._lc_run_time, 3),
                "description": (
                    f"Segment {n} animations take {self._lc_run_time:.1f}s "
                    f"but audio budget is {self._lc_budget:.1f}s "
                    f"({self._lc_run_time - self._lc_budget:.1f}s over)"
                ),
            })

        mobjects = list(getattr(self, "mobjects", []))

        for i, m1 in enumerate(mobjects):
            try:
                bb1 = m1.get_bounding_box()
            except Exception:
                continue

            # 2. Off-screen check
            if (bb1[0][0] < _CANVAS_X_MIN - _BOUND_TOL or
                    bb1[2][0] > _CANVAS_X_MAX + _BOUND_TOL or
                    bb1[0][1] < _CANVAS_Y_MIN - _BOUND_TOL or
                    bb1[2][1] > _CANVAS_Y_MAX + _BOUND_TOL):
                self._lc_violations.append({
                    "type": "off_screen",
                    "segment": n,
                    "object": repr(m1)[:80],
                    "description": (
                        f"Segment {n}: {type(m1).__name__} extends outside canvas "
                        f"(x=[{bb1[0][0]:.2f},{bb1[2][0]:.2f}], "
                        f"y=[{bb1[0][1]:.2f},{bb1[2][1]:.2f}])"
                    ),
                })

            # 3. Overlap check against later mobjects
            for m2 in mobjects[i + 1:]:
                try:
                    bb2 = m2.get_bounding_box()
                except Exception:
                    continue

                if _classify_overlap(bb1, bb2) == "partial":
                    ox1 = max(bb1[0][0], bb2[0][0])
                    oy1 = max(bb1[0][1], bb2[0][1])
                    ox2 = min(bb1[2][0], bb2[2][0])
                    oy2 = min(bb1[2][1], bb2[2][1])
                    self._lc_violations.append({
                        "type": "overlap",
                        "segment": n,
                        "objects": [repr(m1)[:60], repr(m2)[:60]],
                        "overlap_region": {
                            "x": [round(ox1, 2), round(ox2, 2)],
                            "y": [round(oy1, 2), round(oy2, 2)],
                        },
                        "description": (
                            f"Segment {n}: {type(m1).__name__} and {type(m2).__name__} "
                            f"partially overlap at "
                            f"x=[{ox1:.2f},{ox2:.2f}] y=[{oy1:.2f},{oy2:.2f}]"
                        ),
                    })

    def _lc_write_report(self) -> None:
        report = {
            "passed": len(self._lc_violations) == 0,
            "violations": self._lc_violations,
        }
        report_path = Path(self._REPORT_DIR) / "layout_report.json"
        report_path.write_text(json.dumps(report, indent=2))
