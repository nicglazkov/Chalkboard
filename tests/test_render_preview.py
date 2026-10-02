from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline import render


@pytest.fixture
def docker_backend():
    with patch.object(render, "backend", return_value="docker"):
        yield


@pytest.fixture
def local_backend():
    with patch.object(render, "backend", return_value="local"):
        yield


def test_docker_render_cmd_standard(docker_backend):
    cmd, env = render.render_cmd(Path("/out/abc123"), "medium")
    assert cmd == ["docker", "run", "--rm", "-v", "/out:/output", render.DOCKER_IMAGE, "abc123"]
    assert env is None


def test_docker_render_cmd_preview_adds_env_var(docker_backend):
    cmd, _ = render.render_cmd(Path("/out/abc123"), "medium", preview=True)
    assert "-e" in cmd
    assert "PREVIEW_MODE=1" in cmd


def test_local_render_cmd_uses_quality_and_media_dir(local_backend):
    cmd, env = render.render_cmd(Path("/out/abc123"), "high")
    assert "-qh" in cmd
    assert cmd[cmd.index("--media_dir") + 1] == "/out/abc123/media"
    assert cmd[-2:] == ["/out/abc123/scene.py", "ChalkboardScene"]
    assert str(render.RUNTIME_DIR) in env["PYTHONPATH"]


def test_local_preview_is_low_quality_in_separate_dir(local_backend):
    cmd, _ = render.render_cmd(Path("/out/abc123"), "high", preview=True)
    assert "-ql" in cmd
    assert cmd[cmd.index("--media_dir") + 1] == "/out/abc123/media_preview"


def test_video_path_matches_manim_layout():
    run_dir = Path("/out/r")
    assert render.video_path(run_dir, "high") == run_dir / "media/videos/scene/1080p60/ChalkboardScene.mp4"
    assert render.video_path(run_dir, "high", preview=True) == (
        run_dir / "media_preview/videos/scene/480p15/ChalkboardScene.mp4"
    )


def test_local_check_cmd_points_at_run_dir(local_backend):
    cmd, env = render.check_cmd(Path("/out/r"))
    assert cmd[-1] == "/out/r"
    assert env is not None
