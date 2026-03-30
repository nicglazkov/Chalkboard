# main.py
from dotenv import load_dotenv
load_dotenv()

import asyncio
import argparse
import collections
import json
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from langgraph.types import Command
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from config import CHECKPOINT_DB, DEFAULT_AUDIENCE, DEFAULT_EFFORT, DEFAULT_THEME, DEFAULT_TONE, OUTPUT_DIR, MANIM_QUALITY
from pipeline.graph import build_graph

EFFORT_CHOICES = ["low", "medium", "high"]
AUDIENCE_CHOICES = ["beginner", "intermediate", "expert"]
TONE_CHOICES = ["casual", "formal", "socratic"]
THEME_CHOICES = ["chalkboard", "light", "colorful"]
DOCKER_IMAGE = "chalkboard-render"
QUALITY_SUBDIR = {"low": "480p15", "medium": "720p30", "high": "1080p60"}

# ---------------------------------------------------------------------------
# Render timeout constants
# ---------------------------------------------------------------------------
RENDER_TIMEOUT_BASE         = 60.0
RENDER_TIMEOUT_PER_ANIM     = 5.0
RENDER_TIMEOUT_AUDIO_RATIO  = 3.0
RENDER_TIMEOUT_QUALITY_MULT = {"low": 0.5, "medium": 1.0, "high": 2.0}
RENDER_TIMEOUT_MIN          = 90.0
RENDER_TIMEOUT_MAX          = 1200.0


def _check_tools() -> None:
    missing = [t for t in ("docker", "ffmpeg") if not shutil.which(t)]
    if missing:
        raise SystemExit(
            f"Missing required tools: {', '.join(missing)}\n"
            "Install Docker from https://docker.com and ffmpeg from https://ffmpeg.org"
        )


def subprocess_with_timeout(
    cmd: list[str], timeout: float, on_line=None
) -> tuple[int, collections.deque, bool]:
    """
    Run cmd as a subprocess. Kill it after `timeout` seconds.
    Calls on_line(line) for each line of stdout if provided.
    Returns (returncode, lines_buffer, timed_out).
    """
    timed_out = [False]
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )

    def _kill():
        timed_out[0] = True
        process.kill()

    timer = threading.Timer(timeout, _kill)
    timer.start()
    lines_buffer: collections.deque = collections.deque(maxlen=50)
    try:
        for line in process.stdout:
            line = line.rstrip()
            lines_buffer.append(line)
            if on_line:
                on_line(line)
    finally:
        timer.cancel()

    process.wait()
    return process.returncode, lines_buffer, timed_out[0]


def _compute_render_timeout(run_id: str, output_dir: Path) -> float:
    """
    Compute adaptive render timeout from segments.json (audio duration)
    and scene.py (animation count) and manifest.json (quality).
    """
    run_dir = output_dir / run_id

    try:
        segments = json.loads((run_dir / "segments.json").read_text())
        audio_duration = sum(s.get("actual_duration_sec", 0.0) for s in segments)
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        audio_duration = 30.0  # conservative fallback

    anim_count = _count_animations(run_dir / "scene.py")

    try:
        manifest = json.loads((run_dir / "manifest.json").read_text())
        quality = manifest.get("quality", "medium")
    except (FileNotFoundError, json.JSONDecodeError):
        quality = "medium"

    mult = RENDER_TIMEOUT_QUALITY_MULT.get(quality, 1.0)
    raw = (
        RENDER_TIMEOUT_BASE
        + anim_count * RENDER_TIMEOUT_PER_ANIM
        + audio_duration * RENDER_TIMEOUT_AUDIO_RATIO
    ) * mult
    return max(RENDER_TIMEOUT_MIN, min(RENDER_TIMEOUT_MAX, raw))


def _ensure_docker_image() -> None:
    result = subprocess.run(
        ["docker", "images", "-q", DOCKER_IMAGE],
        capture_output=True, text=True
    )
    if not result.stdout.strip():
        print(f"\nDocker image '{DOCKER_IMAGE}' not found — building now (one-time setup)...")
        subprocess.run(
            ["docker", "build", "-f", "docker/Dockerfile", "-t", DOCKER_IMAGE, "."],
            check=True
        )


def _count_animations(scene_path: Path) -> int:
    """Estimate total animation count by counting self.play( calls in scene.py."""
    try:
        return len(re.findall(r'self\.play\(', scene_path.read_text()))
    except FileNotFoundError:
        return 0


def _parse_manim_line(line: str) -> int | None:
    """Return animation number if line is a Manim CE progress line, else None."""
    m = re.match(r'Animation (\d+) :', line)
    return int(m.group(1)) if m else None


def _docker_render_cmd(run_id: str, output_dir: Path, preview: bool = False) -> list[str]:
    cmd = ["docker", "run", "--rm", "-v", f"{output_dir}:/output"]
    if preview:
        cmd += ["-e", "PREVIEW_MODE=1"]
    cmd += [DOCKER_IMAGE, run_id]
    return cmd


def _render(run_id: str, verbose: bool = False) -> Path:
    output_dir = Path(OUTPUT_DIR).resolve()
    final_mp4 = output_dir / run_id / "final.mp4"

    if final_mp4.exists():
        print(f"\n  [render] already done — {final_mp4}")
        return final_mp4

    _ensure_docker_image()

    # Run Manim inside Docker
    print("\n  [render] rendering animation...")
    docker_cmd = _docker_render_cmd(run_id, output_dir)

    if verbose:
        # Stream Docker output directly to the terminal
        process = subprocess.Popen(docker_cmd)
        process.wait()
        if process.returncode != 0:
            raise SystemExit("Docker render failed.")
        video_path = None  # use manifest fallback below
    else:
        total_anims = _count_animations(output_dir / run_id / "scene.py")
        process = subprocess.Popen(
            docker_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        if process.stdout is None:
            process.wait()
            raise SystemExit("Docker render failed: could not capture output.")
        video_path = None
        anim_count = 0
        lines_buffer = collections.deque(maxlen=50)
        for line in process.stdout:
            line = line.rstrip()
            lines_buffer.append(line)
            if line.startswith("RENDER_COMPLETE:"):
                container_path = line.split(":", 1)[1].strip()
                video_path = output_dir / Path(container_path).relative_to("/output")
            else:
                anim_num = _parse_manim_line(line)
                if anim_num is not None:
                    anim_count = anim_num
                    suffix = f"/{total_anims}" if total_anims else ""
                    print(f"\r  [render] animation {anim_count}{suffix}...", end="", flush=True)
        process.wait()
        if anim_count:
            print()
        if process.returncode != 0:
            print("\n".join(lines_buffer[-20:]))
            raise SystemExit("Docker render failed.")

    if video_path is None or not video_path.exists():
        # Fallback: derive from manifest
        manifest = json.loads((output_dir / run_id / "manifest.json").read_text())
        quality = manifest.get("quality", "medium")
        subdir = QUALITY_SUBDIR.get(quality, "720p30")
        scene_class = manifest.get("scene_class_name", "ChalkboardScene")
        video_path = output_dir / run_id / "media" / "videos" / "scene" / subdir / f"{scene_class}.mp4"

    if not video_path.exists():
        raise SystemExit(f"Rendered video not found at {video_path}")

    # Merge voiceover with host ffmpeg (host AAC is browser/QuickTime compatible)
    print("  [render] merging voiceover...")
    wav_path = output_dir / run_id / "voiceover.wav"
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", str(video_path),
         "-i", str(wav_path),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
         str(final_mp4)],
        check=True,
        capture_output=True,
    )

    return final_mp4


def _run_visual_qa(run_id: str, final_mp4: Path) -> None:
    from pipeline.visual_qa import visual_qa  # lazy import keeps anthropic out of startup path
    output_dir = Path(OUTPUT_DIR).resolve()
    qa_dir = output_dir / run_id / "qa_frames"
    print("\n  [qa] running visual quality check...")
    try:
        result = visual_qa(final_mp4, qa_dir)
    except Exception as e:
        print(f"  [qa] skipped — {e}")
        return
    if result["passed"]:
        print("  [qa] passed")
    else:
        print("  [qa] issues found:")
        for issue in result["issues"]:
            print(f"        [{issue['severity']}] {issue['description']}")


def _render_preview(run_id: str) -> Path:
    output_dir = Path(OUTPUT_DIR).resolve()
    preview_mp4 = output_dir / run_id / "preview.mp4"

    if preview_mp4.exists():
        print(f"\n  [preview] already done — {preview_mp4}")
        return preview_mp4

    _ensure_docker_image()
    print("\n  [preview] rendering preview at low quality (480p15)...")
    docker_cmd = _docker_render_cmd(run_id, output_dir, preview=True)

    process = subprocess.Popen(
        docker_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    if process.stdout is None:
        process.wait()
        raise SystemExit("Docker preview render failed: could not capture output.")
    video_path = None
    lines_buffer = collections.deque(maxlen=50)
    for line in process.stdout:
        line = line.rstrip()
        lines_buffer.append(line)
        if line.startswith("RENDER_COMPLETE:"):
            container_path = line.split(":", 1)[1].strip()
            video_path = output_dir / Path(container_path).relative_to("/output")
    process.wait()
    if process.returncode != 0:
        print("\n".join(lines_buffer[-20:]))
        raise SystemExit("Docker preview render failed.")

    if video_path is None or not video_path.exists():
        raise SystemExit(f"Preview video not found at {video_path}")

    wav_path = output_dir / run_id / "voiceover.wav"
    subprocess.run(
        ["ffmpeg", "-y",
         "-i", str(video_path),
         "-i", str(wav_path),
         "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest",
         str(preview_mp4)],
        check=True,
        capture_output=True,
    )
    return preview_mp4


def _print_progress(event: dict) -> None:
    for node_name, updates in event.items():
        if node_name == "__end__":
            continue
        status = updates.get("status", "")
        attempts_info = ""
        if "script_attempts" in updates:
            attempts_info = f" (script attempt {updates['script_attempts']})"
        elif "code_attempts" in updates:
            attempts_info = f" (code attempt {updates['code_attempts']})"
        print(f"  [{node_name}]{attempts_info} → {status or 'done'}")


def _handle_interrupt(interrupt_value: str) -> Command:
    print("\n" + interrupt_value)
    print("\nEnter action (retry_script / retry_code / abort):")
    action = input("  action: ").strip()
    guidance = ""
    if action in ("retry_script", "retry_code"):
        guidance = input("  guidance: ").strip()
    return Command(resume={"action": action, "guidance": guidance})


async def run(topic: str, effort: str, thread_id: str, audience: str = "intermediate", tone: str = "casual", theme: str = "chalkboard") -> None:
    print(f"\nChalkboard — topic: {topic!r} | effort: {effort} | run: {thread_id}\n")

    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        input_state = {"topic": topic, "effort_level": effort, "audience": audience, "tone": tone, "theme": theme}

        while True:
            async for event in graph.astream(input_state, config=config, stream_mode="updates"):
                _print_progress(event)

                if "__interrupt__" in event:
                    interrupt_value = event["__interrupt__"][0].value
                    resume_cmd = _handle_interrupt(interrupt_value)
                    input_state = resume_cmd
                    break
            else:
                break


def main():
    parser = argparse.ArgumentParser(description="Chalkboard — AI animation pipeline")
    parser.add_argument("--topic", required=True, help="Topic to explain")
    parser.add_argument("--effort", choices=EFFORT_CHOICES, default=DEFAULT_EFFORT)
    parser.add_argument("--audience", choices=AUDIENCE_CHOICES, default=DEFAULT_AUDIENCE,
                        help="Target audience level")
    parser.add_argument("--tone", choices=TONE_CHOICES, default=DEFAULT_TONE,
                        help="Narration tone")
    parser.add_argument("--theme", choices=THEME_CHOICES, default=DEFAULT_THEME,
                        help="Visual color theme for the animation")
    parser.add_argument("--run-id", default=None, help="Resume a previous run by ID")
    parser.add_argument("--no-render", action="store_true", help="Skip Docker render and ffmpeg merge")
    parser.add_argument("--verbose", action="store_true", help="Stream Docker render output to terminal")
    parser.add_argument("--preview", action="store_true", help="Render low-quality preview instead of full HD render")
    args = parser.parse_args()

    if args.verbose and args.preview:
        raise SystemExit("--verbose and --preview cannot be combined.")

    if not args.no_render:
        _check_tools()

    thread_id = args.run_id or str(uuid.uuid4())
    asyncio.run(run(args.topic, args.effort, thread_id, audience=args.audience, tone=args.tone, theme=args.theme))

    if not args.no_render:
        # Visual QA runs only on full renders — preview is low-quality by design
        if args.preview:
            preview = _render_preview(thread_id)
            print(f"\nPreview → {preview}")
            print(f"\nTo render the full video:")
            print(f"  python main.py --topic {args.topic!r} --run-id {thread_id}")
        else:
            final = _render(thread_id, verbose=args.verbose)
            print(f"\nDone → {final}")
            _run_visual_qa(thread_id, final)
    else:
        print(f"\nDone. Output files in output/{thread_id}/")


if __name__ == "__main__":
    main()
