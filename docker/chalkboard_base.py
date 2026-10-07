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
                                          — hold, begin segment n, clear
        self.segment_time_left()          — seconds until the segment ends
        self.speech_time_left()           — seconds until its last word ends
        self.end_layout_check()           — BEFORE the final FadeOut
        self.cue(k)                       — wait for cue marker [[k]] of the
                                            current segment's narration, so the
                                            next play() starts on that word

    Narration sync: segment n always starts at (or after) the sum of the
    budgets of segments 0..n-1 on the scene clock. If a segment's animations
    run short, the scene waits out the gap, so visuals can never drift ahead
    of the voiceover; next_segment() does that wait while the old content is
    still on screen. Running long is reported as a timing violation.

    Pacing (pipeline/pacing.py): each segment's audio ends with a silent hold
    (>= SCENE_HOLD_S after the last word, `speech_end_sec` in segments.json)
    and starts with a short silent lead-in. The finished visual stays still
    through the hold; the clean-slate fade runs at the start of the next
    segment, inside its lead-in, never during the hold. An animation still
    running more than _HOLD_BUSY_TOL into the hold is a `hold_busy` violation.
    """

    # Allowed lag of the visuals behind narration before it is a violation.
    _SYNC_DRIFT_TOL = 2.0
    # A cued animation starting later than this after its word is a violation.
    _CUE_LATE_TOL = 0.6
    # An animation may finish this far into the silent hold after the last
    # word (a reveal on the final word); later than that is `hold_busy`.
    _HOLD_BUSY_TOL = 0.5

    # Word-level sync state. Class-level defaults so subclasses/test doubles
    # that skip __init__ still work. _cues: {segment: [t1, t2, ...]} seconds
    # from the segment's narration start (None = loaded lazily from
    # segments.json next to the scene file; tests may assign it directly).
    _cues = None
    _speech_end = None   # {segment: seconds from segment start}; lazily loaded
    _seg_audio_start: float = 0.0
    _seg_hold_flagged: bool = False
    _seg_cue_calls: int = 0
    # Set by templates: they cue what they can, so unused cues are not an error.
    _cues_template_driven: bool = False

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
        if self._real_render():
            # Live progress for the host: main._render_once parses this line.
            print(f"CB_SEGMENT {n}", flush=True)
        self._lc_run_time = 0.0
        self._lc_budget = duration
        # Narration of segment n starts at the sum of the earlier budgets
        # (the voiceover is the segments' audio back to back).
        self._seg_audio_start = max(self._sync_target, 0.0)
        self._seg_cue_calls = 0
        self._seg_hold_flagged = False
        self._sync_target = max(self._sync_target, 0.0) + duration

    def next_segment(self, n: int, duration: float, clear=(), fade: float = 0.5) -> None:
        """Finish the current segment and start segment n, in sync with narration.

        Holds the current frame until the current segment's audio ends (its
        silent hold included), starts segment n, then fades out `clear`. The
        fade runs in segment n's silent lead-in, so the hold after the last
        word is never cut short. Use this instead of a manual remainder wait +
        FadeOut + begin_segment.
        """
        self.begin_segment(n, duration)
        items = list(clear)
        if items:
            from manim import FadeOut
            self.play(*[FadeOut(m) for m in items], run_time=fade)

    def segment_time_left(self) -> float:
        """Seconds left in the current segment's audio, hold included (never negative)."""
        return max(0.0, self._sync_target - self._scene_time())

    def speech_time_left(self) -> float:
        """Seconds until the current segment's last word ends (never negative).
        Equals segment_time_left() when the segment has no measured hold."""
        end = self._segment_speech_end()
        if end is None:
            return self.segment_time_left()
        return max(0.0, getattr(self, "_seg_audio_start", 0.0) + end - self._scene_time())

    def _segment_speech_end(self):
        seg = self._lc_segment
        if seg is None:
            return None
        if self._speech_end is None:
            self._speech_end = {i: s.get("speech_end_sec") for i, s in enumerate(self._load_segments())}
        v = self._speech_end.get(seg)
        return float(v) if isinstance(v, (int, float)) else None

    # ------------------------------------------------------------------
    # Word-level sync: cue markers
    # ------------------------------------------------------------------

    def cue(self, k: int) -> bool:
        """Hold until cue marker [[k]] of the current segment is spoken.

        The next self.play(...) then starts on that word. If the scene is
        already past the cue, nothing waits and the lag is recorded (more
        than _CUE_LATE_TOL is a `cue_late` violation). Unknown cue numbers
        never crash a render: they warn and return False without waiting.
        """
        seg = self._lc_segment
        if seg is None or self._lc_done:
            return False
        try:
            k = int(k)
            cues = self._cue_table().get(seg) or []
            t = cues[k - 1] if 1 <= k <= len(cues) else None
        except Exception:
            t = None
        if t is None:
            print(f"chalkboard_base: segment {seg} has no cue {k!r}; not waiting", file=sys.stderr)
            return False
        self._seg_cue_calls = getattr(self, "_seg_cue_calls", 0) + 1
        target = getattr(self, "_seg_audio_start", 0.0) + float(t)
        now = self._scene_time()
        gap = target - now
        if gap > 0.02:
            self.wait(gap)
        lag = max(0.0, -gap)
        log = self.__dict__.setdefault("_cue_log", [])
        log.append({"segment": seg, "cue": k, "spoken_at": round(target, 3),
                    "visual_at": round(self._scene_time(), 3), "lag": round(lag, 3)})
        if lag > self._CUE_LATE_TOL:
            self._lc_violations.append({
                "type": "cue_late",
                "segment": seg,
                "cue": k,
                "lag_sec": round(lag, 2),
                "description": (
                    f"Segment {seg}: the animation for cue [[{k}]] starts {lag:.1f}s after the "
                    f"word is spoken. Shorten or move the animations before self.cue({k}) "
                    f"so they finish before that word."
                ),
            })
        return True

    def has_cue(self, k: int) -> bool:
        """True if the current segment has a time for cue marker [[k]]."""
        if self._lc_segment is None:
            return False
        cues = self._cue_table().get(self._lc_segment) or []
        return 1 <= int(k) <= len(cues) and cues[int(k) - 1] is not None

    def _cue_table(self) -> dict:
        if self._cues is None:
            self._cues = self._load_cues()
        elif isinstance(self._cues, list):
            self._cues = dict(enumerate(self._cues))
        return self._cues

    def _load_cues(self) -> dict:
        """Per-segment `cues` from segments.json."""
        return {i: (s.get("cues") or []) for i, s in enumerate(self._load_segments())}

    def _load_segments(self) -> list:
        """segments.json next to the scene file (falling back to the report
        directory, where renders keep it); [] when there is none."""
        candidates = []
        mod = sys.modules.get(type(self).__module__)
        f = getattr(mod, "__file__", None)
        if f:
            candidates.append(Path(f).resolve().parent / "segments.json")
        candidates.append(Path(report_dir_for(self._REPORT_DIR)) / "segments.json")
        for p in candidates:
            try:
                data = json.loads(p.read_text())
            except Exception:
                continue
            if isinstance(data, list):
                return [s if isinstance(s, dict) else {} for s in data]
        return []

    def _scene_time(self) -> float:
        # In a real render, the renderer's clock counts the frames actually
        # written: Manim rounds every play up to whole frames and static waits
        # down, so summing requested run_times drifts from the video. The
        # layout dry-run renders at 1 fps (a 0.4s play would advance the
        # renderer by 1s), so there, and in tests, use our own clock.
        if self._real_render():
            return float(self.renderer.time)
        return self._sync_tracked

    def _real_render(self) -> bool:
        r = getattr(self, "__dict__", {}).get("renderer")
        if r is None or not isinstance(getattr(r, "time", None), (int, float)):
            return False
        try:
            from manim import config
            return not config.dry_run and config.frame_rate >= 10
        except Exception:
            return False

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
                self._lc_check_hold()
        return result

    def _lc_check_hold(self) -> None:
        """Flag an animation that runs into the silent hold after the last word."""
        if getattr(self, "_seg_hold_flagged", False):
            return
        end = self._segment_speech_end()
        if end is None:
            return
        into = self._scene_time() - (getattr(self, "_seg_audio_start", 0.0) + end)
        if into > self._HOLD_BUSY_TOL:
            self._seg_hold_flagged = True
            self._lc_violations.append({
                "type": "hold_busy",
                "segment": self._lc_segment,
                "into_hold_sec": round(into, 2),
                "description": (
                    f"Segment {self._lc_segment}: an animation is still running {into:.1f}s after "
                    f"the narration's last word, inside the silent hold that lets the viewer take "
                    f"in the finished picture. Finish this segment's animations by its last word "
                    f"(shorten them, or move them before the last cue) and let the hold stay still."
                ),
            })

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

        # 0. Word-level sync: a segment whose narration has cue markers must
        # reveal its visuals on them (templates cue what they can).
        cues = [c for c in (self._cue_table().get(n) or []) if c is not None]
        if cues and not getattr(self, "_seg_cue_calls", 0) and not self._cues_template_driven:
            self._lc_violations.append({
                "type": "cue_unused",
                "segment": n,
                "description": (
                    f"Segment {n}: the narration has {len(cues)} cue marker(s) but the scene never "
                    f"calls self.cue(k). Put self.cue(k) right before the animation that shows "
                    f"what the narration introduces at marker [[k]]."
                ),
            })

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
            # Every self.cue(k): when the word is spoken vs when the cued
            # animation starts (scene clock = video time in a real render).
            "cue_log": self.__dict__.get("_cue_log", []),
            # Which run wrote this file: "render" (a real render, scene clock =
            # frames written) or "dry_run" (layout check / tests, tracked clock).
            "mode": "render" if self._real_render() else "dry_run",
        }
        report_dir = report_dir_for(self._REPORT_DIR)
        report_path = Path(report_dir) / "layout_report.json"
        try:
            report_path.write_text(json.dumps(report, indent=2))
        except OSError as exc:
            # The report is advisory; never let it kill a real render.
            print(f"chalkboard_base: could not write {report_path}: {exc}", file=sys.stderr)
