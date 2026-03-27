# tests/test_render_preview.py
from pathlib import Path
from main import _docker_render_cmd, DOCKER_IMAGE


def test_docker_render_cmd_standard():
    cmd = _docker_render_cmd("abc123", Path("/output"))
    assert cmd == ["docker", "run", "--rm", "-v", "/output:/output", DOCKER_IMAGE, "abc123"]


def test_docker_render_cmd_preview_adds_env_var():
    cmd = _docker_render_cmd("abc123", Path("/output"), preview=True)
    assert "-e" in cmd
    assert "PREVIEW_MODE=1" in cmd
    assert cmd[-1] == "abc123"


def test_docker_render_cmd_preview_does_not_affect_standard():
    cmd = _docker_render_cmd("abc123", Path("/output"))
    assert "-e" not in cmd
    assert "PREVIEW_MODE=1" not in cmd
