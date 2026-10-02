# docker/chalkboard_base.py
"""
ChalkboardSceneBase — layout validation mixin for generated Manim scenes.

Generated scenes inherit from this class (listed before Scene in MRO):
    class ChalkboardScene(ChalkboardSceneBase, Scene):

The class tracks per-segment animation timing and checks bounding boxes at
each segment boundary, writing layout_report.json to _REPORT_DIR on completion.
The report directory resolution order is: an explicit _REPORT_DIR override
(set on the class or instance, e.g. by tests), then the CHALKBOARD_REPORT_DIR
env var, then "/output" (the Docker render mount). The env var is read lazily
inside _lc_write_report so a renderer can set it per process (local renders
point it at the run's output directory) after this module is imported.
No direct Manim imports at module level — safe to import without Manim
installed. The house style (chalkboard_style: TeX preamble + fonts) is applied
on import when it is importable, i.e. whenever a scene renders with docker/
(or /render) on PYTHONPATH.
"""
import json
import os
import sys
from pathlib import Path

try:
    import chalkboard_style  # noqa: F401  (sets Manim-wide TeX/text defaults)
except ImportError:
    pass


# Canvas bounds (Manim CE default 16:9 scene)
_CANVAS_X_MIN = -7.11
_CANVAS_X_MAX =  7.11
_CANVAS_Y_MIN = -4.0
_CANVAS_Y_MAX =  4.0
_BOUND_TOL   = 0.1    # off-screen tolerance (Manim units)
_OVERLAP_TOL = 0.05   # overlap edge tolerance (avoids flagging exact touches)

# Zone boundaries. These match the LAYOUT RULES in
# manim_agent.py's system prompt: LEFT zone = x_center < -0.5, RIGHT zone
# = x_center > +0.5, CENTER zone = -0.5 ≤ x_center ≤ +0.5. The dual checks
# below catch two distinct failure modes that `partial overlap` does NOT:
#   1. zone_boundary_overlap — when one element fully contains another
#      (which `_classify_overlap` flags as `contained`, intentionally
#      ignored to allow text-in-box patterns) BUT the two are
#      structurally in different zones (cross-zone analogy boxes).
#   2. zone_collision — when a "left-zone" element's bounding box
#      crosses past x = -0.5 into the right zone while a right-zone
#      element is also present in the segment (the canonical horizontal-
#      array overflow case).
_ZONE_LEFT_MAX  = -0.5
_ZONE_RIGHT_MIN =  0.5


def report_dir_for(override: "str | None" = None) -> str:
    """Resolve where layout_report.json goes.

    Order: explicit override, then $CHALKBOARD_REPORT_DIR (read lazily so it
    can be set after import), then "/output" (the Docker render mount).
    """
    return override or os.environ.get("CHALKBOARD_REPORT_DIR") or "/output"


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

    # Explicit override (tests set this). None means: use the
    # CHALKBOARD_REPORT_DIR env var, falling back to "/output".
    _REPORT_DIR: str | None = None

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
        # Skip accumulation for Wait animations: wait() already counted the duration
        # directly, and Manim's wait() internally calls play(Wait(...)), which would
        # cause double-counting.
        from manim import Wait as _Wait
        is_internal_wait = (
            len(animations) == 1 and isinstance(animations[0], _Wait)
        )
        if not self._lc_done and self._lc_segment is not None and not is_internal_wait:
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

        # Cache per-mobject bounding boxes once; the zone checks below
        # iterate over them after the existing per-pair pass.
        bbox_cache: list[tuple] = []  # list of (mobj, bbox) for things we could measure
        for m in mobjects:
            try:
                bbox_cache.append((m, m.get_bounding_box()))
            except Exception:
                continue

        for i, (m1, bb1) in enumerate(bbox_cache):
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
            for j, (m2, bb2) in enumerate(bbox_cache[i + 1:], start=i + 1):
                rel = _classify_overlap(bb1, bb2)
                if rel == "partial":
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
                elif rel == "contained":
                    # Containment is intentional in-zone (text
                    # inside a labeled box, label inside a callout). But
                    # cross-zone containment — e.g. a right-zone analogy
                    # box covering left-zone array cells — is a layout
                    # error, not an intentional design pattern. Detect by
                    # comparing bounding-box x_centers: if one is left-zone
                    # (< -0.5) and the other is right-zone (> +0.5), flag.
                    x_center_1 = (bb1[0][0] + bb1[2][0]) / 2
                    x_center_2 = (bb2[0][0] + bb2[2][0]) / 2
                    crosses = (
                        (x_center_1 < _ZONE_LEFT_MAX and x_center_2 > _ZONE_RIGHT_MIN)
                        or (x_center_1 > _ZONE_RIGHT_MIN and x_center_2 < _ZONE_LEFT_MAX)
                    )
                    if crosses:
                        self._lc_violations.append({
                            "type": "zone_boundary_overlap",
                            "segment": n,
                            "objects": [repr(m1)[:60], repr(m2)[:60]],
                            "description": (
                                f"Segment {n}: {type(m1).__name__} (x_center={x_center_1:.2f}) "
                                f"and {type(m2).__name__} (x_center={x_center_2:.2f}) "
                                f"are in different zones (LEFT < {_ZONE_LEFT_MAX} / RIGHT > {_ZONE_RIGHT_MIN}) "
                                f"yet one contains the other. This indicates a multi-zone layout error — "
                                f"an element from one zone has grown to cover content in the opposite zone."
                            ),
                        })

        # 4. Zone collision. When LEFT and RIGHT zones BOTH
        # have ≥1 element AND any LEFT-zone element's bounding box extends
        # past x = _ZONE_LEFT_MAX into the right zone (or any RIGHT-zone
        # element extends back past x = _ZONE_RIGHT_MIN into the left
        # zone), the row of cells from one side has overflowed into the
        # other. Catches the binary-search horizontal-array case where
        # x_0 = -4.5 + N cells of width 0.85 lands the rightmost cell at
        # x ≈ +3.575, deep into the right zone, while a right-side
        # callout is also present in the same segment.
        left_elements: list[tuple] = []   # (mobj, bbox)
        right_elements: list[tuple] = []
        for m, bb in bbox_cache:
            x_center = (bb[0][0] + bb[2][0]) / 2
            if x_center < _ZONE_LEFT_MAX:
                left_elements.append((m, bb))
            elif x_center > _ZONE_RIGHT_MIN:
                right_elements.append((m, bb))
            # Center-zone elements (-0.5 ≤ x_center ≤ +0.5) don't
            # participate in this check.
        if left_elements and right_elements:
            for m, bb in left_elements:
                if bb[2][0] > _ZONE_LEFT_MAX:
                    self._lc_violations.append({
                        "type": "zone_collision",
                        "segment": n,
                        "object": repr(m)[:80],
                        "description": (
                            f"Segment {n}: {type(m).__name__} has its center in the LEFT zone "
                            f"(x_center={(bb[0][0]+bb[2][0])/2:.2f}) but its right edge "
                            f"({bb[2][0]:.2f}) crosses into the RIGHT zone (x > {_ZONE_LEFT_MAX}) "
                            f"while right-zone elements are also present in this segment. "
                            f"Horizontal arrays/rows must satisfy "
                            f"right_edge = x_0 + (N − 0.5) × W < {_ZONE_LEFT_MAX} for the LEFT zone."
                        ),
                    })
            for m, bb in right_elements:
                if bb[0][0] < _ZONE_RIGHT_MIN:
                    self._lc_violations.append({
                        "type": "zone_collision",
                        "segment": n,
                        "object": repr(m)[:80],
                        "description": (
                            f"Segment {n}: {type(m).__name__} has its center in the RIGHT zone "
                            f"(x_center={(bb[0][0]+bb[2][0])/2:.2f}) but its left edge "
                            f"({bb[0][0]:.2f}) crosses into the LEFT zone (x < {_ZONE_RIGHT_MIN}) "
                            f"while left-zone elements are also present in this segment."
                        ),
                    })

    def _lc_write_report(self) -> None:
        report = {
            "passed": len(self._lc_violations) == 0,
            "violations": self._lc_violations,
        }
        report_dir = report_dir_for(self._REPORT_DIR)
        report_path = Path(report_dir) / "layout_report.json"
        try:
            report_path.write_text(json.dumps(report, indent=2))
        except OSError as exc:
            # The report is advisory; never let it kill a real render.
            print(f"chalkboard_base: could not write {report_path}: {exc}", file=sys.stderr)
