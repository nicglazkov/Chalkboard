# tests/test_chalkboard_base.py
import json
import numpy as np
import pytest
from pathlib import Path
from docker.chalkboard_base import ChalkboardSceneBase, _classify_overlap


# ── Mock helpers ──────────────────────────────────────────────────────────────

class MockMobject:
    """Minimal stand-in for a Manim mobject."""
    def __init__(self, x_min, y_min, x_max, y_max, name="Mock"):
        self._bb = np.array([
            [x_min, y_min, 0],
            [(x_min + x_max) / 2, (y_min + y_max) / 2, 0],
            [x_max, y_max, 0],
        ])
        self._name = name
    def get_bounding_box(self):
        return self._bb
    def __repr__(self):
        return f"Mock({self._name})"


class _FakeScene(ChalkboardSceneBase):
    """ChalkboardSceneBase without Manim Scene parent — for unit tests."""
    def __init__(self, report_dir):
        self.mobjects = []
        self._lc_segment = None
        self._lc_run_time = 0.0
        self._lc_budget = 0.0
        self._lc_done = False
        self._lc_violations = []
        self._sync_target = 0.0
        self._sync_tracked = 0.0
        self._REPORT_DIR = str(report_dir)

    def play(self, *args, run_time=None, **kwargs):
        # Don't call super() — no real Manim scene in tests
        self._sync_tracked += run_time if run_time is not None else 1.0
        if not self._lc_done and self._lc_segment is not None:
            self._lc_run_time += run_time if run_time is not None else 1.0

    def wait(self, duration=1.0, **kwargs):
        # Don't call super() — no real Manim scene in tests
        self._sync_tracked += duration
        if not self._lc_done and self._lc_segment is not None:
            self._lc_run_time += duration


# ── _classify_overlap ─────────────────────────────────────────────────────────

def _bb(x_min, y_min, x_max, y_max):
    return np.array([[x_min, y_min, 0], [0, 0, 0], [x_max, y_max, 0]])


def test_classify_overlap_none():
    assert _classify_overlap(_bb(0, 0, 1, 1), _bb(2, 2, 3, 3)) == "none"


def test_classify_overlap_partial():
    assert _classify_overlap(_bb(0, 0, 2, 2), _bb(1, 1, 3, 3)) == "partial"


def test_classify_overlap_contained_m1_inside_m2():
    assert _classify_overlap(_bb(1, 1, 2, 2), _bb(0, 0, 3, 3)) == "contained"


def test_classify_overlap_contained_m2_inside_m1():
    assert _classify_overlap(_bb(0, 0, 3, 3), _bb(1, 1, 2, 2)) == "contained"


def test_classify_overlap_touching_edges_not_partial():
    # Exactly touching edges — tolerance keeps this as "none"
    assert _classify_overlap(_bb(0, 0, 1, 1), _bb(1.0, 0, 2, 1)) == "none"


# ── Timing validation ─────────────────────────────────────────────────────────

def test_timing_overrun_detected(tmp_path):
    # Overrun must exceed 1.5s tolerance to be flagged (budget=3.0, actual=5.0 = 2.0s over)
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=3.0)
    scene.play(run_time=2.5)
    scene.play(run_time=2.5)  # total 5.0 > 3.0 + 1.5 tolerance
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    assert report["passed"] is False
    violations = [v for v in report["violations"] if v["type"] == "timing_overrun"]
    assert len(violations) == 1
    assert violations[0]["segment"] == 0
    assert violations[0]["actual_sec"] == pytest.approx(5.0)
    assert violations[0]["budget_sec"] == pytest.approx(3.0)


def test_timing_within_budget_not_flagged(tmp_path):
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=3.0)
    scene.play(run_time=2.5)
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    timing_violations = [v for v in report["violations"] if v["type"] == "timing_overrun"]
    assert timing_violations == []


def test_timing_tolerance_1_5s(tmp_path):
    """Overrun within 1.5s tolerance should not be flagged."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=3.0)
    scene.play(run_time=4.4)  # 1.4s over — within 1.5s tolerance
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    timing_violations = [v for v in report["violations"] if v["type"] == "timing_overrun"]
    assert timing_violations == []


# ── Overlap validation ────────────────────────────────────────────────────────

def test_partial_overlap_detected(tmp_path):
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.mobjects = [
        MockMobject(-2, 1, 0, 3, "title"),
        MockMobject(-1, 2, 1, 4, "array"),  # partial overlap
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    overlap_violations = [v for v in report["violations"] if v["type"] == "overlap"]
    assert len(overlap_violations) == 1
    assert overlap_violations[0]["segment"] == 0


def test_contained_overlap_not_flagged(tmp_path):
    """Text fully inside a box should not be flagged."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.mobjects = [
        MockMobject(-3, -1, 3, 1, "box"),
        MockMobject(-1, -0.5, 1, 0.5, "label"),  # fully inside box
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    overlap_violations = [v for v in report["violations"] if v["type"] == "overlap"]
    assert overlap_violations == []


def test_text_overflow_from_box_detected(tmp_path):
    """Text that overflows its containing box is partial overlap — should be flagged."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.mobjects = [
        MockMobject(-2, -1, 2, 1, "box"),
        MockMobject(-1, -0.5, 3, 0.5, "label"),  # right side sticks out
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    overlap_violations = [v for v in report["violations"] if v["type"] == "overlap"]
    assert len(overlap_violations) == 1


# ── Off-screen validation ─────────────────────────────────────────────────────

def test_off_screen_right_detected(tmp_path):
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.mobjects = [MockMobject(6.0, -1, 8.5, 1, "wide")]  # right edge > 7.11
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    off_screen = [v for v in report["violations"] if v["type"] == "off_screen"]
    assert len(off_screen) == 1


def test_on_screen_not_flagged(tmp_path):
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.mobjects = [MockMobject(-6, -3, 6, 3, "normal")]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    off_screen = [v for v in report["violations"] if v["type"] == "off_screen"]
    assert off_screen == []


# ── Report output ─────────────────────────────────────────────────────────────

def test_passed_true_when_no_violations(tmp_path):
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    assert report["passed"] is True
    assert report["violations"] == []


def test_multiple_segments_all_checked(tmp_path):
    """Violations in non-final segments are caught when next begin_segment is called."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=2.0)
    scene.play(run_time=5.0)  # overrun in segment 0
    scene.begin_segment(1, duration=3.0)  # triggers check of segment 0
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    assert not report["passed"]
    assert report["violations"][0]["segment"] == 0


def test_end_layout_check_before_final_fadeout_does_not_count_it(tmp_path):
    """play() after end_layout_check() must not affect timing."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=3.0)
    scene.play(run_time=2.0)
    scene.end_layout_check()
    scene.play(run_time=10.0)  # final FadeOut — must not trigger overrun

    report = json.loads((tmp_path / "layout_report.json").read_text())
    timing_violations = [v for v in report["violations"] if v["type"] == "timing_overrun"]
    assert timing_violations == []


def test_wait_contributes_to_timing(tmp_path):
    """self.wait() calls must count toward segment timing budget."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=3.0)
    scene.play(run_time=1.0)
    scene.wait(4.0)  # total 5.0 > 3.0 + 1.5 tolerance — should trigger overrun
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    violations = [v for v in report["violations"] if v["type"] == "timing_overrun"]
    assert len(violations) == 1
    assert violations[0]["actual_sec"] == pytest.approx(5.0)


def test_wait_after_end_layout_check_not_counted(tmp_path):
    """self.wait() after end_layout_check() must not affect timing."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=3.0)
    scene.play(run_time=1.0)
    scene.end_layout_check()
    scene.wait(10.0)  # after check — must not trigger overrun

    report = json.loads((tmp_path / "layout_report.json").read_text())
    violations = [v for v in report["violations"] if v["type"] == "timing_overrun"]
    assert violations == []


# ── Zone-boundary overlap ──────────────────────────────────────
# `contained` — when one mobject's bounding box fully encloses another — is
# usually intentional (e.g. text inside a labeled box, label inside a callout).
# But cross-zone containment indicates a layout regression: an element from
# the LEFT zone (x_center < -0.5) has grown to cover content nominally in the
# RIGHT zone (x_center > +0.5), or vice versa. Flag those as their own
# violation type so the regen prompt knows it's a multi-zone error and not
# a benign in-zone group.

def test_zone_boundary_overlap_left_contains_right(tmp_path):
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    # Outer "left zone" element actually spans the full canvas; its center
    # is in the left zone (x_center = -1.0). Inner element is right-zone.
    scene.mobjects = [
        MockMobject(-7, -3, 5, 3, "left_outer"),  # x_center = -1.0 (LEFT)
        MockMobject(2, 0, 4, 1, "right_inner"),   # x_center = 3.0 (RIGHT)
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    violations = [v for v in report["violations"] if v["type"] == "zone_boundary_overlap"]
    assert len(violations) == 1
    assert violations[0]["segment"] == 0
    assert "different zones" in violations[0]["description"]


def test_zone_boundary_overlap_right_contains_left(tmp_path):
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    # Reversed roles — outer element's x_center is in the right zone but
    # contains a left-zone inner element.
    scene.mobjects = [
        MockMobject(-5, -3, 7, 3, "right_outer"),  # x_center = 1.0 (RIGHT)
        MockMobject(-4, 0, -2, 1, "left_inner"),   # x_center = -3.0 (LEFT)
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    violations = [v for v in report["violations"] if v["type"] == "zone_boundary_overlap"]
    assert len(violations) == 1


def test_zone_boundary_overlap_same_zone_not_flagged(tmp_path):
    """Containment within the same zone (e.g. text in a left-zone box) is fine."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.mobjects = [
        MockMobject(-5, -1, -1, 1, "left_box"),     # x_center = -3.0 (LEFT)
        MockMobject(-4, -0.5, -2, 0.5, "left_text"),  # x_center = -3.0 (LEFT)
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    violations = [v for v in report["violations"] if v["type"] == "zone_boundary_overlap"]
    assert violations == []


def test_zone_boundary_overlap_center_zone_not_flagged(tmp_path):
    """Containment with a center-zone element should not flag (no cross-zone)."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.mobjects = [
        MockMobject(-2, -1, 2, 1, "center_box"),     # x_center = 0.0 (CENTER)
        MockMobject(-1, -0.5, 1, 0.5, "center_text"),  # x_center = 0.0 (CENTER)
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    violations = [v for v in report["violations"] if v["type"] == "zone_boundary_overlap"]
    assert violations == []


# ── Zone collision ──────────────────────────────────────────────
# When LEFT and RIGHT zones BOTH have ≥1 element AND any LEFT-zone element's
# bounding box extends past x = -0.5 into the right zone (or vice versa for
# right-zone elements), flag the collision. Catches the binary-search
# horizontal-array case where x_0 = -4.5 + 10 cells × 0.85 lands the rightmost
# cell at x ≈ +3.575, deep into the right zone, while a right-side callout is
# also present.

def test_zone_collision_left_array_overflows_right(tmp_path):
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    # Wide "left zone" array — center is in the left zone but right edge
    # extends past x = -0.5. Right-zone callout also present.
    scene.mobjects = [
        MockMobject(-4.5, -1, 3.5, 0, "wide_array"),   # x_center = -0.5 (boundary, but right edge crosses)
        MockMobject(2, 1, 4, 2, "right_callout"),       # x_center = 3.0 (RIGHT)
    ]
    # Force the left array's center into the LEFT zone explicitly.
    scene.mobjects[0] = MockMobject(-5.5, -1, 3.5, 0, "wide_array")  # x_center = -1.0
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    zone_violations = [v for v in report["violations"] if v["type"] == "zone_collision"]
    assert len(zone_violations) >= 1
    assert any("LEFT zone" in v["description"] for v in zone_violations)


def test_zone_collision_right_element_overflows_left(tmp_path):
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    # Right-zone element whose left edge extends past x = +0.5 into the
    # left zone; left-zone element also present.
    scene.mobjects = [
        MockMobject(-4, 0, -2, 1, "left_text"),         # x_center = -3.0 (LEFT)
        MockMobject(-1, -1, 4, 0, "wide_right"),         # x_center = 1.5 (RIGHT) — left edge -1 is in LEFT
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    zone_violations = [v for v in report["violations"] if v["type"] == "zone_collision"]
    assert len(zone_violations) >= 1


def test_zone_collision_only_one_zone_populated_not_flagged(tmp_path):
    """A wide left-zone element with no right-zone element present should not flag."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.mobjects = [
        MockMobject(-5.5, -1, 3.5, 0, "wide_array"),   # x_center = -1.0 (LEFT, but extends into right)
        # No right-zone mobjects — so no zone collision should fire.
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    zone_violations = [v for v in report["violations"] if v["type"] == "zone_collision"]
    assert zone_violations == []


def test_zone_collision_disjoint_zones_not_flagged(tmp_path):
    """Properly-separated left + right elements with no overflow should not flag."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.mobjects = [
        MockMobject(-4, 0, -1, 1, "left_text"),         # x_center = -2.5 (LEFT) — right edge -1 < -0.5 ✓
        MockMobject(1, 0, 4, 1, "right_text"),          # x_center = 2.5 (RIGHT) — left edge 1 > 0.5 ✓
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    zone_violations = [v for v in report["violations"] if v["type"] == "zone_collision"]
    assert zone_violations == []


# ── Report directory resolution ───────────────────────────────────────────────

class _EnvScene(_FakeScene):
    """Like _FakeScene but without an explicit _REPORT_DIR override."""
    def __init__(self):
        super().__init__("unused")
        del self._REPORT_DIR  # fall back to the class attribute (None)


def test_report_dir_from_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("CHALKBOARD_REPORT_DIR", str(tmp_path))
    scene = _EnvScene()
    scene.begin_segment(0, duration=5.0)
    scene.end_layout_check()
    assert json.loads((tmp_path / "layout_report.json").read_text())["passed"] is True


def test_explicit_report_dir_beats_env_var(tmp_path, monkeypatch):
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    monkeypatch.setenv("CHALKBOARD_REPORT_DIR", str(env_dir))
    scene = _FakeScene(explicit)
    scene.begin_segment(0, duration=5.0)
    scene.end_layout_check()
    assert (explicit / "layout_report.json").exists()
    assert not (env_dir / "layout_report.json").exists()


def test_report_dir_defaults_to_output(monkeypatch):
    from docker.chalkboard_base import report_dir_for
    monkeypatch.delenv("CHALKBOARD_REPORT_DIR", raising=False)
    assert report_dir_for(None) == "/output"


def test_unwritable_report_dir_does_not_raise(tmp_path, capsys):
    """A missing report dir (e.g. /output outside Docker) must never kill a render."""
    scene = _FakeScene(tmp_path / "does" / "not" / "exist")
    scene.begin_segment(0, duration=5.0)
    scene.end_layout_check()  # must not raise
    assert "could not write" in capsys.readouterr().err


def test_zone_collision_ignores_title_band_chrome(tmp_path):
    """A step counter in the top-right corner is not right-zone content."""
    scene = _FakeScene(tmp_path)
    scene.begin_segment(0, duration=5.0)
    scene.mobjects = [
        MockMobject(-3.0, 0, 0.2, 1, "callout_over_cell"),  # LEFT, right edge crosses -0.5
        MockMobject(5.0, 3.3, 6.8, 3.8, "step_counter"),    # top-right corner, title band
    ]
    scene.end_layout_check()

    report = json.loads((tmp_path / "layout_report.json").read_text())
    assert [v for v in report["violations"] if v["type"] == "zone_collision"] == []



# ── narration sync ────────────────────────────────────────────────────────────

def test_short_segment_is_padded_to_its_narration(tmp_path):
    s = _FakeScene(tmp_path)
    s.begin_segment(0, duration=5.0)
    s.play(run_time=2.0)
    s.begin_segment(1, duration=3.0)          # pads 3.0s before starting segment 1
    assert s._sync_tracked == pytest.approx(5.0)
    s.play(run_time=1.0)
    s.end_layout_check()
    assert s._sync_tracked == pytest.approx(8.0)
    assert json.loads((tmp_path / "layout_report.json").read_text())["passed"]


def test_next_segment_holds_then_fades(tmp_path, monkeypatch):
    import manim
    monkeypatch.setattr(manim, "FadeOut", lambda m: m)   # FadeOut needs a real Mobject
    s = _FakeScene(tmp_path)
    s.begin_segment(0, duration=4.0)
    s.play(run_time=1.0)
    s.next_segment(1, duration=2.0, clear=[MockMobject(0, 0, 1, 1)], fade=0.5)
    # hold until 3.5s, 0.5s fade, so segment 1 starts exactly at 4.0s
    assert s._sync_tracked == pytest.approx(4.0)
    assert s._lc_segment == 1


def test_lagging_visuals_are_reported(tmp_path):
    s = _FakeScene(tmp_path)
    s.begin_segment(0, duration=2.0)
    s.play(run_time=5.0)
    s.begin_segment(1, duration=2.0)
    s.end_layout_check()
    report = json.loads((tmp_path / "layout_report.json").read_text())
    assert any(v["type"] == "sync_drift" for v in report["violations"])
