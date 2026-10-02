# pipeline/render.py
"""Where and how Manim runs: natively ("local") or in the chalkboard-render image ("docker").

Both backends share one contract:
  * render:  output/<run_id>/scene.py  ->  output/<run_id>/<media_dir>/videos/scene/<subdir>/ChalkboardScene.mp4
  * check:   headless dry-run of scene.py that writes layout_report.json next to it

The local backend needs `manim` (pip, already in requirements via the venv) plus a
TeX distribution with `latex` and `dvisvgm` on PATH. It is several times faster than
Docker (no container start, no image build).
"""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

from config import RENDER_BACKEND

DOCKER_IMAGE = "chalkboard-render"
REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = REPO_ROOT / "docker"   # chalkboard_base, tokens, components, moves, templates

QUALITY_FLAG = {"low": "-ql", "medium": "-qm", "high": "-qh", "4k": "-qk"}
QUALITY_SUBDIR = {"low": "480p15", "medium": "720p30", "high": "1080p60", "4k": "2160p60"}


@functools.cache
def backend() -> str:
    """Resolve RENDER_BACKEND ("auto" picks local when manim + TeX are installed)."""
    choice = RENDER_BACKEND.lower()
    if choice in ("local", "docker"):
        return choice
    if _local_available():
        return "local"
    return "docker"


def _local_available() -> bool:
    try:
        import manim  # noqa: F401
    except ImportError:
        return False
    return all(shutil.which(t) for t in ("latex", "dvisvgm", "ffmpeg"))


def required_tools() -> list[str]:
    return ["ffmpeg"] if backend() == "local" else ["docker", "ffmpeg"]


def ensure_ready() -> None:
    """Build the Docker image on first use (docker backend only)."""
    if backend() != "docker":
        return
    result = subprocess.run(
        ["docker", "images", "-q", DOCKER_IMAGE], capture_output=True, text=True, timeout=30,
    )
    if not result.stdout.strip():
        print(f"\nDocker image '{DOCKER_IMAGE}' not found, building now (one-time setup)...")
        subprocess.run(
            ["docker", "build", "-f", "docker/Dockerfile", "-t", DOCKER_IMAGE, "."],
            check=True, timeout=900, cwd=REPO_ROOT,
        )


def local_env(run_dir: Path) -> dict:
    """Environment for running a scene natively: runtime modules importable,
    layout_report.json written next to scene.py."""
    env = dict(os.environ)
    env["CHALKBOARD_REPORT_DIR"] = str(run_dir)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(RUNTIME_DIR), env.get("PYTHONPATH")]))
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def video_path(run_dir: Path, quality: str, preview: bool = False,
               scene_class: str = "ChalkboardScene") -> Path:
    q = "low" if preview else quality
    media = "media_preview" if preview else "media"
    return run_dir / media / "videos" / "scene" / QUALITY_SUBDIR.get(q, "720p30") / f"{scene_class}.mp4"


def render_cmd(run_dir: Path, quality: str, preview: bool = False,
               scene_class: str = "ChalkboardScene") -> tuple[list[str], dict | None]:
    """Command (and env) that renders scene.py. Prints Manim's normal progress lines."""
    if backend() == "docker":
        cmd = ["docker", "run", "--rm", "-v", f"{run_dir.parent}:/output"]
        if preview:
            cmd += ["-e", "PREVIEW_MODE=1"]
        return cmd + [DOCKER_IMAGE, run_dir.name], None
    q = "low" if preview else quality
    media = run_dir / ("media_preview" if preview else "media")
    cmd = [sys.executable, "-m", "manim", "render", QUALITY_FLAG.get(q, "-qm"),
           "--media_dir", str(media), str(run_dir / "scene.py"), scene_class]
    return cmd, local_env(run_dir)


# Dry-run snippet: executes construct() at 1 fps with no frames written, so
# ChalkboardSceneBase can measure bounding boxes and timing per segment.
_CHECK_SNIPPET = r"""
import sys, importlib.util
run_dir = sys.argv[1]
sys.path.insert(0, run_dir)
from manim import config as _cfg
_cfg.dry_run = True
_cfg.frame_rate = 1
_cfg.verbosity = "ERROR"
import chalkboard_base
chalkboard_base.ChalkboardSceneBase._REPORT_DIR = run_dir
spec = importlib.util.spec_from_file_location("scene", run_dir + "/scene.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
if not hasattr(mod, "ChalkboardScene"):
    raise RuntimeError("scene.py does not define ChalkboardScene")
mod.ChalkboardScene().render()
"""


def check_cmd(run_dir: Path) -> tuple[list[str], dict | None]:
    """Command (and env) for the headless layout dry-run of run_dir/scene.py."""
    if backend() == "docker":
        return ["docker", "run", "--rm", "-v", f"{run_dir}:/output", DOCKER_IMAGE, "--check"], None
    return [sys.executable, "-c", _CHECK_SNIPPET, str(run_dir)], local_env(run_dir)
