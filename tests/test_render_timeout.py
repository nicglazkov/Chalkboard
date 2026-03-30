# tests/test_render_timeout.py
import json
import pytest
from pathlib import Path
from main import subprocess_with_timeout, _compute_render_timeout


# ---------------------------------------------------------------------------
# subprocess_with_timeout
# ---------------------------------------------------------------------------

def test_subprocess_with_timeout_success():
    returncode, lines_buffer, timed_out = subprocess_with_timeout(
        ["python3", "-c", "print('hello')"], timeout=10.0
    )
    assert returncode == 0
    assert not timed_out
    assert any("hello" in line for line in lines_buffer)


def test_subprocess_with_timeout_fires_on_slow_process():
    returncode, lines_buffer, timed_out = subprocess_with_timeout(
        ["python3", "-c", "import time; time.sleep(10)"], timeout=0.1
    )
    assert timed_out


def test_subprocess_with_timeout_on_line_callback():
    seen = []
    subprocess_with_timeout(
        ["python3", "-c", "print('hello')"], timeout=10.0, on_line=seen.append
    )
    assert any("hello" in line for line in seen)


def test_subprocess_with_timeout_nonzero_exit():
    returncode, lines_buffer, timed_out = subprocess_with_timeout(
        ["python3", "-c", "raise SystemExit(1)"], timeout=10.0
    )
    assert returncode != 0
    assert not timed_out


# ---------------------------------------------------------------------------
# _compute_render_timeout
# ---------------------------------------------------------------------------

def _write_run_dir(tmp_path, run_id, audio_duration, anim_count, quality):
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    segments = [{"text": "x", "actual_duration_sec": audio_duration}]
    (run_dir / "segments.json").write_text(json.dumps(segments))
    (run_dir / "scene.py").write_text("self.play()\n" * anim_count)
    (run_dir / "manifest.json").write_text(
        json.dumps({"quality": quality, "scene_class_name": "ChalkboardScene",
                    "run_id": run_id, "topic": "test", "timestamp": "now"})
    )


def test_compute_render_timeout_medium_quality(tmp_path):
    _write_run_dir(tmp_path, "run1", audio_duration=30.0, anim_count=10, quality="medium")
    # (60 + 10*5 + 30*3) * 1.0 = 200.0
    assert _compute_render_timeout("run1", tmp_path) == 200.0


def test_compute_render_timeout_floor(tmp_path):
    _write_run_dir(tmp_path, "run2", audio_duration=1.0, anim_count=1, quality="low")
    # (60 + 5 + 3) * 0.5 = 34.0 → clamped to MIN=90.0
    assert _compute_render_timeout("run2", tmp_path) == 90.0


def test_compute_render_timeout_ceiling(tmp_path):
    _write_run_dir(tmp_path, "run3", audio_duration=200.0, anim_count=100, quality="high")
    # would be huge → clamped to MAX=1200.0
    assert _compute_render_timeout("run3", tmp_path) == 1200.0


def test_compute_render_timeout_high_quality(tmp_path):
    _write_run_dir(tmp_path, "run4", audio_duration=60.0, anim_count=20, quality="high")
    # (60 + 100 + 180) * 2.0 = 680.0
    assert _compute_render_timeout("run4", tmp_path) == 680.0


def test_compute_render_timeout_fallback_when_files_missing(tmp_path):
    (tmp_path / "run5").mkdir()
    # No segments.json or manifest.json — should use fallbacks without crashing
    timeout = _compute_render_timeout("run5", tmp_path)
    assert timeout >= 90.0
