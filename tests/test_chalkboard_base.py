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
        self._REPORT_DIR = str(report_dir)

    def play(self, *args, run_time=None, **kwargs):
        # Don't call super() — no real Manim scene in tests
        if not self._lc_done and self._lc_segment is not None:
            self._lc_run_time += run_time if run_time is not None else 1.0

    def wait(self, duration=1.0, **kwargs):
        # Don't call super() — no real Manim scene in tests
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
