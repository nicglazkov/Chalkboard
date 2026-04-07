# tests/test_thumbnail.py
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from main import _extract_thumbnail


def test_extract_thumbnail_runs_ffmpeg(tmp_path):
    """_extract_thumbnail calls ffmpeg with correct seek time and output path."""
    run_dir = tmp_path / "run-abc"
    run_dir.mkdir()
    segments = [
        {"text": "Hello.", "actual_duration_sec": 30.0},
        {"text": "World.", "actual_duration_sec": 70.0},
    ]
    (run_dir / "segments.json").write_text(json.dumps(segments))
    (run_dir / "final.mp4").write_bytes(b"fake")

    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("main.subprocess.run", return_value=mock_result) as mock_run:
        # Create the thumb.jpg so the function returns it
        def side_effect(cmd, **kwargs):
            # Simulate ffmpeg creating the output file
            thumb = run_dir / "thumb.jpg"
            thumb.write_bytes(b"fake-jpeg")
            return mock_result
        mock_run.side_effect = side_effect
        result = _extract_thumbnail(run_dir)

    assert result == run_dir / "thumb.jpg"
    cmd = mock_run.call_args[0][0]
    # seek time should be 10% of 100s = 10.0
    assert "-ss" in cmd
    ss_idx = cmd.index("-ss")
    assert float(cmd[ss_idx + 1]) == pytest.approx(10.0, abs=0.1)
    assert str(run_dir / "thumb.jpg") in cmd


def test_extract_thumbnail_returns_none_on_ffmpeg_failure(tmp_path):
    run_dir = tmp_path / "run-fail"
    run_dir.mkdir()
    (run_dir / "segments.json").write_text(json.dumps([{"text": "Hi", "actual_duration_sec": 60.0}]))
    (run_dir / "final.mp4").write_bytes(b"fake")

    with patch("main.subprocess.run", side_effect=Exception("ffmpeg not found")):
        result = _extract_thumbnail(run_dir)

    assert result is None


def test_extract_thumbnail_returns_none_when_segments_missing(tmp_path):
    run_dir = tmp_path / "run-noseg"
    run_dir.mkdir()
    result = _extract_thumbnail(run_dir)
    assert result is None


def test_extract_thumbnail_returns_none_when_duration_zero(tmp_path):
    run_dir = tmp_path / "run-zero"
    run_dir.mkdir()
    (run_dir / "segments.json").write_text(json.dumps([{"text": "Hi", "actual_duration_sec": 0.0}]))
    (run_dir / "final.mp4").write_bytes(b"fake")
    with patch("main.subprocess.run"):
        result = _extract_thumbnail(run_dir)
    assert result is None
