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
#      reaches past x = +0.5 into the right zone while a right-zone
#      element is also present in the segment (the canonical horizontal-
#      array overflow case).
_ZONE_LEFT_MAX  = -0.5
_ZONE_RIGHT_MIN =  0.5
# An element is side-zone content only when its center is clearly off to one
# side; wide centered equations and diagrams sit within +-1.5 of the middle.
_ZONE_SIDE_CENTER = 1.5
# Elements entirely above this y live in the title band (title_anchor =
# UP * 3.5, step counter in the top corner) and are not zone content.
_TITLE_BAND_MIN_Y = 2.9


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


def _flatten_plain_groups(mobjects: list) -> list:
    """Replace bare VGroup/Group containers with their members (recursively).

    Scenes often add a row of boxes as one VGroup; checking only top-level
    mobjects would never compare those boxes with each other, so two cards
    that collide inside the same group went unreported. Subclasses (design
    components, MathTex, Code, Axes) stay whole: their parts overlap on purpose.
    """
    try:
        from manim import Group, VGroup
    except ImportError:  # pure-python unit tests
        return mobjects
    out: list = []
    for m in mobjects:
        if type(m) in (VGroup, Group) and len(m.submobjects) > 0:
            out.extend(_flatten_plain_groups(list(m.submobjects)))
        else:
            out.append(m)
    return out


def _has_round_trip(animations) -> bool:
    """True if any animation must return to its start (its own rate_func matters)."""
    try:
        from manim import (ApplyWave, Circumscribe, Flash, FocusOn, Indicate,
                           ShowPassingFlash, Wiggle)
    except ImportError:
        return False
    kinds = (Indicate, Wiggle, Circumscribe, Flash, FocusOn, ApplyWave, ShowPassingFlash)
    return any(isinstance(a, kinds) for a in animations)


_MIN_OVERLAP = 0.1   # Manim units (~1.25% of frame height); smaller = grazing


def _measure(m):
    """Bounding box [min, center, max] of a mobject, or None if it is invisible
    or has no geometry. Manim 0.21's Cairo mobjects have no get_bounding_box(),
    so measure from the points (unit-test fakes still provide the method)."""
    # Check the class: Mobject.__getattr__ fabricates any get_* name, so
    # hasattr() is always True on real mobjects and the call then fails.
    if callable(getattr(type(m), "get_bounding_box", None)):
        try:
            return m.get_bounding_box()
        except Exception:
            return None
    try:
        import numpy as np
        if not _is_visible(m):
            return None
        pts = m.get_all_points()
        if len(pts) == 0:
            return None
        lo, hi = pts.min(axis=0), pts.max(axis=0)
        return np.array([lo, (lo + hi) / 2, hi])
    except Exception:
        return None


def _is_visible(m) -> bool:
    for sm in m.family_members_with_points():
        try:
            if sm.get_fill_opacity() > 0.05 or sm.get_stroke_opacity() > 0.05:
                return True
        except Exception:
            return True
    return False


def _is_connector(m) -> bool:
    """Things whose bounding box says nothing about what they cover: lines,
    arrows, braces and plotted curves (drawn to touch or pass through other
    elements), and unfilled outlines such as highlight rings around a term."""
    try:
        from manim import Brace, Line, ParametricFunction, TipableVMobject, VMobject
    except ImportError:
        return False
    kinds = (Line, TipableVMobject, Brace, ParametricFunction)
    if isinstance(m, kinds) or type(m).__name__ == "ChalkArrow":
        return True
    subs = getattr(m, "submobjects", [])
    if len(subs) == 1 and isinstance(subs[0], kinds):
        return True
    if isinstance(m, VMobject) and not _has_text(m):
        try:
            return all(sm.get_fill_opacity() <= 0.05 for sm in m.family_members_with_points())
        except Exception:
            return False
    return False


def _has_text(m) -> bool:
    try:
        from manim import MathTex, Paragraph, Tex, Text, MarkupText
    except ImportError:
        return False
    return any(isinstance(sm, (Text, MarkupText, Tex, MathTex, Paragraph)) for sm in m.get_family())


def _grazing(bb1, bb2) -> bool:
    ow = min(bb1[2][0], bb2[2][0]) - max(bb1[0][0], bb2[0][0])
    oh = min(bb1[2][1], bb2[2][1]) - max(bb1[0][1], bb2[0][1])
    return ow < _MIN_OVERLAP or oh < _MIN_OVERLAP


class ChalkboardSceneBase:
    """
    Validation mixin for ChalkboardScene. No Manim parent — use as:
        class ChalkboardScene(ChalkboardSceneBase, Scene):

    Public API (called by generated construct()):
        self.begin_segment(n, duration)   — start of each segment
        self.next_segment(n, duration, clear=items)
                                          — hold, clear, then begin segment n
        self.end_layout_check()           — BEFORE the final FadeOut

    Narration sync: segment n always starts at (or after) the sum of the
    budgets of segments 0..n-1 on the scene clock. If a segment's animations
    run short, the scene waits out the gap, so visuals can never drift ahead
    of the voiceover; next_segment() does that wait while the old content is
    still on screen. Running long is reported as a timing violation.
    """

    # Allowed lag of the visuals behind narration before it is a violation.
    _SYNC_DRIFT_TOL = 2.0

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
        self._sync_target: float = 0.0     # scene time at which the current segment should end
        self._sync_tracked: float = 0.0    # fallback clock when there is no renderer (tests)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def begin_segment(self, n: int, duration: float) -> None:
        """Call at the start of every segment block in construct()."""
        if self._lc_segment is not None:
            self._sync_to(self._sync_target)
            self._lc_check_segment()
        self._lc_segment = n
        self._lc_run_time = 0.0
        self._lc_budget = duration
        self._sync_target = max(self._sync_target, 0.0) + duration

    def next_segment(self, n: int, duration: float, clear=(), fade: float = 0.5) -> None:
        """Finish the current segment and start segment n, in sync with narration.

        Holds the current frame until `fade` seconds before the current
        segment's narration ends, fades out `clear`, then begin_segment(n).
        Use this instead of a manual remainder wait + FadeOut + begin_segment.
        """
        if self._lc_segment is not None:
            self._sync_to(self._sync_target - (fade if clear else 0.0))
        items = list(clear)
        if items:
            from manim import FadeOut
            self.play(*[FadeOut(m) for m in items], run_time=fade)
        self.begin_segment(n, duration)

    def segment_time_left(self) -> float:
        """Seconds of narration left in the current segment (never negative)."""
        return max(0.0, self._sync_target - self._scene_time())

    def _scene_time(self) -> float:
        # Our own clock, not renderer.time: the layout dry-run renders at 1 fps
        # and Manim rounds every animation up to whole frames there (a 0.4s
        # play advances renderer.time by 1s), which would fake huge drift.
        return self._sync_tracked

    def _sync_to(self, target: float) -> None:
        """Wait until the scene clock reaches `target`; flag excessive lag."""
        now = self._scene_time()
        gap = target - now
        if gap > 0.02:
            self.wait(gap)
        elif -gap > self._SYNC_DRIFT_TOL and self._lc_segment is not None and not self._lc_done:
            self._lc_violations.append({
                "type": "sync_drift",
                "segment": self._lc_segment,
                "description": (
                    f"Segment {self._lc_segment}: visuals are {-gap:.1f}s behind the narration "
                    f"at the segment boundary. Shorten this segment's animations."
                ),
            })

    def end_layout_check(self) -> None:
        """Call BEFORE the final FadeOut at end of construct()."""
        if self._lc_segment is not None:
            self._sync_to(self._sync_target)
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
        if run_time is not None:
            kwargs["run_time"] = run_time
        if "rate_func" in kwargs and _has_round_trip(animations):
            # Indicate/Wiggle/Circumscribe/Flash... rely on their own
            # there-and-back rate_func; a scene-wide ease (resolve_motion)
            # would freeze the object in its highlighted state.
            kwargs.pop("rate_func")
        result = super().play(*animations, **kwargs)
        if not is_internal_wait:
            # Manim records the real run time of the last play() in self.duration
            # (it honours run_time set on the animations themselves, e.g. a
            # LaggedStart(run_time=3)); fall back to the kwarg, else Manim's 1s default.
            actual = getattr(self, "duration", None)
            if not isinstance(actual, (int, float)):
                actual = run_time if run_time is not None else 1.0
            self._sync_tracked += actual
            if not self._lc_done and self._lc_segment is not None:
                self._lc_run_time += actual
        return result

    # ------------------------------------------------------------------
    # wait() override — accumulate duration
    # ------------------------------------------------------------------

    def wait(self, duration=1.0, **kwargs):
        self._sync_tracked += duration
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

        mobjects = _flatten_plain_groups(list(getattr(self, "mobjects", [])))

        # Cache per-mobject bounding boxes once; the zone checks below
        # iterate over them after the existing per-pair pass.
        bbox_cache: list[tuple] = []  # list of (mobj, bbox) for things we could measure
        for m in mobjects:
            bb = _measure(m)
            if bb is not None:
                bbox_cache.append((m, bb))

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
                if rel == "partial" and (_is_connector(m1) or _is_connector(m2)
                                         or _grazing(bb1, bb2)):
                    rel = "none"   # arrows/lines touch on purpose; slivers are noise
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
            if bb[0][1] > _TITLE_BAND_MIN_Y:
                # Title-band chrome (persistent title, step counter in the
                # top corner) sits above the zones; it is not "right-zone
                # content" a left-zone row could collide with.
                continue
            x_center = (bb[0][0] + bb[2][0]) / 2
            if x_center < -_ZONE_SIDE_CENTER:
                left_elements.append((m, bb))
            elif x_center > _ZONE_SIDE_CENTER:
                right_elements.append((m, bb))
            # Centered content (|x_center| <= _ZONE_SIDE_CENTER, e.g. a wide
            # equation aligned on its "=") doesn't
            # participate in this check.
        if left_elements and right_elements:
            for m, bb in left_elements:
                # Crossing the center strip is fine (centered diagrams put
                # nodes at x = +-0.7); reaching into the far zone is not.
                if bb[2][0] > _ZONE_RIGHT_MIN:
                    self._lc_violations.append({
                        "type": "zone_collision",
                        "segment": n,
                        "object": repr(m)[:80],
                        "description": (
                            f"Segment {n}: {type(m).__name__} has its center in the LEFT zone "
                            f"(x_center={(bb[0][0]+bb[2][0])/2:.2f}) but its right edge "
                            f"({bb[2][0]:.2f}) reaches into the RIGHT zone (x > {_ZONE_RIGHT_MIN}) "
                            f"while right-zone elements are also present in this segment. "
                            f"Horizontal arrays/rows must satisfy "
                            f"right_edge = x_0 + (N − 0.5) × W < {_ZONE_LEFT_MAX} for the LEFT zone."
                        ),
                    })
            for m, bb in right_elements:
                if bb[0][0] < _ZONE_LEFT_MAX:
                    self._lc_violations.append({
                        "type": "zone_collision",
                        "segment": n,
                        "object": repr(m)[:80],
                        "description": (
                            f"Segment {n}: {type(m).__name__} has its center in the RIGHT zone "
                            f"(x_center={(bb[0][0]+bb[2][0])/2:.2f}) but its left edge "
                            f"({bb[0][0]:.2f}) reaches into the LEFT zone (x < {_ZONE_LEFT_MAX}) "
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
