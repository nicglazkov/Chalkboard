# tests/test_chapters_captions.py
import json
import pytest
from pathlib import Path


def _write_segments(run_dir: Path, segments: list[dict]) -> None:
    (run_dir / "segments.json").write_text(json.dumps(segments))


def test_format_srt_time_zero():
    from main import _format_srt_time
    assert _format_srt_time(0.0) == "00:00:00,000"


def test_format_srt_time_sub_second():
    from main import _format_srt_time
    assert _format_srt_time(0.5) == "00:00:00,500"


def test_format_srt_time_minutes():
    from main import _format_srt_time
    assert _format_srt_time(90.0) == "00:01:30,000"


def test_format_srt_time_hours():
    from main import _format_srt_time
    assert _format_srt_time(3661.123) == "01:01:01,123"


def test_generate_caption_files_writes_srt(tmp_path):
    from main import _generate_caption_files
    _write_segments(tmp_path, [
        {"text": "Hello world.", "actual_duration_sec": 5.0},
        {"text": "Goodbye.", "actual_duration_sec": 3.0},
    ])
    srt_path, chapters_path = _generate_caption_files(tmp_path)
    assert srt_path is not None
    assert srt_path.exists()
    content = srt_path.read_text()
    assert "00:00:00,000 --> 00:00:05,000" in content
    assert "Hello world." in content
    assert "00:00:05,000 --> 00:00:08,000" in content
    assert "Goodbye." in content


def test_generate_caption_files_writes_ffmetadata(tmp_path):
    from main import _generate_caption_files
    _write_segments(tmp_path, [
        {"text": "Intro", "actual_duration_sec": 10.0},
        {"text": "Main", "actual_duration_sec": 20.0},
    ])
    _, chapters_path = _generate_caption_files(tmp_path)
    assert chapters_path is not None
    content = chapters_path.read_text()
    assert ";FFMETADATA1" in content
    assert "[CHAPTER]" in content
    assert "TIMEBASE=1/1000" in content
    assert "START=0" in content
    assert "END=10000" in content
    assert "START=10000" in content
    assert "END=30000" in content
    assert "title=Intro" in content
    assert "title=Main" in content


def test_generate_caption_files_prints_chapters(tmp_path, capsys):
    from main import _generate_caption_files
    _write_segments(tmp_path, [
        {"text": "First segment", "actual_duration_sec": 42.0},
        {"text": "Second segment", "actual_duration_sec": 30.0},
    ])
    _generate_caption_files(tmp_path)
    out = capsys.readouterr().out
    assert "0:00" in out
    assert "First segment" in out
    assert "0:42" in out
    assert "Second segment" in out


def test_generate_caption_files_returns_none_when_no_segments_json(tmp_path):
    from main import _generate_caption_files
    srt, chapters = _generate_caption_files(tmp_path)
    assert srt is None
    assert chapters is None


def test_generate_caption_files_long_title_truncated(tmp_path):
    from main import _generate_caption_files
    long_text = "A" * 100
    _write_segments(tmp_path, [{"text": long_text, "actual_duration_sec": 5.0}])
    _, chapters_path = _generate_caption_files(tmp_path)
    content = chapters_path.read_text()
    # Title line should be at most "title=" + 60 chars + "..." = 66 chars
    title_line = next(l for l in content.splitlines() if l.startswith("title="))
    assert len(title_line) <= len("title=") + 63


def test_srt_index_is_1_based(tmp_path):
    from main import _generate_caption_files
    _write_segments(tmp_path, [
        {"text": "A", "actual_duration_sec": 1.0},
        {"text": "B", "actual_duration_sec": 1.0},
    ])
    srt_path, _ = _generate_caption_files(tmp_path)
    lines = srt_path.read_text().splitlines()
    assert lines[0] == "1"
