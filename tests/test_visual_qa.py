# tests/test_visual_qa.py
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from pipeline.visual_qa import visual_qa


def _make_fake_png(path: Path) -> Path:
    # Minimal valid 1x1 PNG
    path.write_bytes(
        b'\x89PNG\r\n\x1a\n'
        b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00:~\x9bU'
        b'\x00\x00\x00\nIDATx\x9cc`\x00\x00\x00\x02\x00\x01\xe2!\xbc3'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    return path


def _mock_response(passed: bool, issues: list) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({"passed": passed, "issues": issues}))]
    return msg


def test_visual_qa_returns_passed_when_no_issues(tmp_path):
    qa_dir = tmp_path / "qa_frames"
    qa_dir.mkdir()
    frame = _make_fake_png(qa_dir / "frame_00.png")

    with patch("pipeline.visual_qa._extract_frames") as mock_extract, \
         patch("pipeline.visual_qa.anthropic.Anthropic") as MockClient:
        mock_extract.return_value = [frame]
        MockClient.return_value.messages.create.return_value = _mock_response(True, [])
        result = visual_qa(tmp_path / "final.mp4", qa_dir)

    assert result["passed"] is True
    assert result["issues"] == []


def test_visual_qa_reports_issues_on_failure(tmp_path):
    qa_dir = tmp_path / "qa_frames"
    qa_dir.mkdir()
    frame = _make_fake_png(qa_dir / "frame_00.png")

    issues = [{"severity": "error", "description": "Text extends off-screen at right edge"}]

    with patch("pipeline.visual_qa._extract_frames") as mock_extract, \
         patch("pipeline.visual_qa.anthropic.Anthropic") as MockClient:
        mock_extract.return_value = [frame]
        MockClient.return_value.messages.create.return_value = _mock_response(False, issues)
        result = visual_qa(tmp_path / "final.mp4", qa_dir)

    assert result["passed"] is False
    assert result["issues"][0]["severity"] == "error"
    assert "off-screen" in result["issues"][0]["description"]


def test_visual_qa_sends_image_blocks_to_claude(tmp_path):
    qa_dir = tmp_path / "qa_frames"
    qa_dir.mkdir()
    frame = _make_fake_png(qa_dir / "frame_00.png")

    with patch("pipeline.visual_qa._extract_frames") as mock_extract, \
         patch("pipeline.visual_qa.anthropic.Anthropic") as MockClient:
        mock_extract.return_value = [frame]
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = _mock_response(True, [])
        visual_qa(tmp_path / "final.mp4", qa_dir)

    call_args = client_instance.messages.create.call_args
    content = call_args.kwargs["messages"][0]["content"]
    image_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/png"


def test_extract_frames_returns_frame_paths(tmp_path):
    qa_dir = tmp_path / "qa_frames"
    video_path = tmp_path / "final.mp4"

    ffprobe_result = MagicMock()
    ffprobe_result.stdout = "10.0\n"
    ffprobe_result.returncode = 0

    ffmpeg_result = MagicMock()
    ffmpeg_result.returncode = 0

    with patch("pipeline.visual_qa.subprocess.run") as mock_run:
        # ffprobe call returns duration, then ffmpeg calls return success
        mock_run.side_effect = [ffprobe_result] + [ffmpeg_result] * 5
        # Create fake frame files so the paths exist
        qa_dir.mkdir(parents=True, exist_ok=True)
        for i in range(5):
            (qa_dir / f"frame_{i:02d}.png").write_bytes(b"fake")

        from pipeline.visual_qa import _extract_frames
        paths = _extract_frames(video_path, qa_dir, n_frames=5)

    assert len(paths) == 5
    assert all(p.name.startswith("frame_") and p.name.endswith(".png") for p in paths)


def test_extract_frames_raises_on_zero_duration(tmp_path):
    qa_dir = tmp_path / "qa_frames"
    video_path = tmp_path / "final.mp4"

    ffprobe_result = MagicMock()
    ffprobe_result.stdout = "0.0\n"
    ffprobe_result.returncode = 0

    with patch("pipeline.visual_qa.subprocess.run") as mock_run:
        mock_run.return_value = ffprobe_result
        from pipeline.visual_qa import _extract_frames
        import pytest
        with pytest.raises(ValueError, match="no duration"):
            _extract_frames(video_path, qa_dir)


def test_visual_qa_density_normal_uses_30s_interval(tmp_path):
    qa_dir = tmp_path / "qa_frames"
    qa_dir.mkdir()
    frame = _make_fake_png(qa_dir / "frame_00.png")

    with patch("pipeline.visual_qa._extract_frames") as mock_extract, \
         patch("pipeline.visual_qa.anthropic.Anthropic") as MockClient:
        mock_extract.return_value = [frame]
        MockClient.return_value.messages.create.return_value = _mock_response(True, [])
        visual_qa(tmp_path / "final.mp4", qa_dir, density="normal")

    _, kwargs = mock_extract.call_args
    assert kwargs["seconds_per_frame"] == 30
    assert kwargs["max_frames"] == 10


def test_visual_qa_density_high_uses_15s_interval(tmp_path):
    qa_dir = tmp_path / "qa_frames"
    qa_dir.mkdir()
    frame = _make_fake_png(qa_dir / "frame_00.png")

    with patch("pipeline.visual_qa._extract_frames") as mock_extract, \
         patch("pipeline.visual_qa.anthropic.Anthropic") as MockClient:
        mock_extract.return_value = [frame]
        MockClient.return_value.messages.create.return_value = _mock_response(True, [])
        visual_qa(tmp_path / "final.mp4", qa_dir, density="high")

    _, kwargs = mock_extract.call_args
    assert kwargs["seconds_per_frame"] == 15
    assert kwargs["max_frames"] == 20


def test_segment_boundary_timestamps_basic():
    from pipeline.visual_qa import _segment_boundary_timestamps
    segments = [
        {"actual_duration_sec": 3.0},
        {"actual_duration_sec": 4.0},
        {"actual_duration_sec": 2.0},
    ]
    timestamps = _segment_boundary_timestamps(segments, max_frames=20)
    # Should include t=0.5, end of each segment, midpoint of 4s segment
    ts_values = [t for t, _, _ in timestamps]
    assert 0.5 in ts_values          # intro sample
    assert 3.0 in ts_values          # end of segment 0
    assert 7.0 in ts_values          # end of segment 1
    assert 9.0 in ts_values          # end of segment 2


def test_segment_boundary_timestamps_midpoint_for_long_segment():
    from pipeline.visual_qa import _segment_boundary_timestamps
    segments = [{"actual_duration_sec": 10.0}]
    timestamps = _segment_boundary_timestamps(segments, max_frames=20)
    ts_values = [t for t, _, _ in timestamps]
    # Midpoint at 5.0 should be included (segment > 4s)
    assert 5.0 in ts_values


def test_segment_boundary_timestamps_respects_max_frames():
    from pipeline.visual_qa import _segment_boundary_timestamps
    segments = [{"actual_duration_sec": 2.0}] * 20  # 20 segments
    timestamps = _segment_boundary_timestamps(segments, max_frames=10)
    assert len(timestamps) <= 10


def test_visual_qa_uses_segment_timestamps_when_provided(tmp_path):
    qa_dir = tmp_path / "qa"
    qa_dir.mkdir()
    frame = _make_fake_png(qa_dir / "frame_00.png")

    segments = [{"actual_duration_sec": 3.0, "text": "Hello world"}]

    with patch("pipeline.visual_qa._extract_frames_at_timestamps") as mock_extract, \
         patch("pipeline.visual_qa.anthropic.Anthropic") as MockClient:
        mock_extract.return_value = [(frame, 3.0, 0, "Hello world")]
        MockClient.return_value.messages.create.return_value = _mock_response(True, [])
        from pipeline.visual_qa import visual_qa
        visual_qa(tmp_path / "final.mp4", qa_dir, segments=segments)

    mock_extract.assert_called_once()


def test_visual_qa_frame_label_includes_segment_context(tmp_path):
    """Frame labels sent to Claude must include segment number and script text."""
    qa_dir = tmp_path / "qa"
    qa_dir.mkdir()
    frame = _make_fake_png(qa_dir / "frame_00.png")

    segments = [{"actual_duration_sec": 3.0, "text": "Bubble sort compares adjacent elements"}]

    with patch("pipeline.visual_qa._extract_frames_at_timestamps") as mock_extract, \
         patch("pipeline.visual_qa.anthropic.Anthropic") as MockClient:
        mock_extract.return_value = [(frame, 3.0, 0, "Bubble sort compares adjacent elements")]
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = _mock_response(True, [])
        from pipeline.visual_qa import visual_qa
        visual_qa(tmp_path / "final.mp4", qa_dir, segments=segments)

    call_args = client_instance.messages.create.call_args
    content = call_args.kwargs["messages"][0]["content"]
    text_blocks = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
    label_texts = " ".join(text_blocks)
    assert "Segment 0" in label_texts
    assert "Bubble sort" in label_texts
