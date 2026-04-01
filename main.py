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
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from config import CHECKPOINT_DB, DEFAULT_AUDIENCE, DEFAULT_EFFORT, DEFAULT_THEME, DEFAULT_TONE, OUTPUT_DIR, MANIM_QUALITY
from pipeline.graph import build_graph
from pipeline.retry import TimeoutExhausted
from pipeline.context import collect_files, load_context_blocks, measure_context

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


class RenderFailed(Exception):
    """Raised when a render attempt fails (timeout or non-zero exit)."""


def _github_to_raw_url(repo: str) -> str:
    """Convert a GitHub repo identifier to its raw README URL.

    Accepts 'owner/repo', 'https://github.com/owner/repo', URLs with
    trailing paths/branches, and .git suffixes.
    """
    repo = repo.rstrip("/")
    if "github.com" in repo:
        m = re.search(r"github\.com/([^/]+/[^/]+)", repo)
        if not m:
            raise ValueError(f"Cannot parse GitHub URL: {repo!r}")
        slug = m.group(1).removesuffix(".git")
    elif re.match(r"^[^/]+/[^/]+$", repo):
        slug = repo
    else:
        raise ValueError(f"Expected 'owner/repo' or a GitHub URL, got: {repo!r}")
    return f"https://raw.githubusercontent.com/{slug}/HEAD/README.md"


def _check_tools() -> None:
    missing = [t for t in ("docker", "ffmpeg") if not shutil.which(t)]
    if missing:
        raise SystemExit(
            f"Missing required tools: {', '.join(missing)}\n"
            "Install Docker from https://docker.com and ffmpeg from https://ffmpeg.org"
        )


def _report_context(blocks: list[dict], _yes: bool = False) -> bool:
    """
    Print context token report. Returns True if pipeline should proceed, False to abort.
    Always prints the report. Prompts for confirmation only when tokens > 10k.
    """
    import anthropic as _anthropic
    try:
        client = _anthropic.Anthropic()
        token_count, context_window = measure_context(blocks, client)
        pct = int(token_count / context_window * 100)
        n_files = sum(
            1 for b in blocks
            if b.get("type") == "text" and b.get("text", "").startswith("--- file:")
        )
        n_urls = sum(
            1 for b in blocks
            if b.get("type") == "text" and b.get("text", "").startswith("--- url:")
        )
        parts = []
        if n_files:
            parts.append(f"{n_files} file{'s' if n_files != 1 else ''}")
        if n_urls:
            parts.append(f"{n_urls} URL{'s' if n_urls != 1 else ''}")
        sources = ", ".join(parts) if parts else "0 sources"
        print(
            f"\nContext: {sources}, ~{token_count // 1000}k tokens  "
            f"(model window: {context_window // 1000}k, ~{pct}% used by context)"
        )
        if pct >= 90:
            raise SystemExit(
                f"Error: context files use {pct}% of the model context window. "
                "Reduce files before proceeding."
            )
        if token_count > 10_000 and not _yes:
            answer = input("\nContext is large. Proceed? (y/n): ").strip().lower()
            return answer == "y"
    except SystemExit:
        raise
    except Exception as e:
        print(f"  Warning: could not measure context tokens ({e}) — proceeding without report.")
    return True


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
        capture_output=True, text=True, timeout=30,
    )
    if not result.stdout.strip():
        print(f"\nDocker image '{DOCKER_IMAGE}' not found — building now (one-time setup)...")
        subprocess.run(
            ["docker", "build", "-f", "docker/Dockerfile", "-t", DOCKER_IMAGE, "."],
            check=True, timeout=600,
        )


def _count_animations(scene_path: Path) -> int:
    """Estimate total animation count by counting self.play( calls in scene.py."""
    try:
        return len(re.findall(r'self\.play\(', scene_path.read_text()))
    except FileNotFoundError:
        return 0


def _format_srt_time(seconds: float) -> str:
    """Format seconds as SRT timestamp: HH:MM:SS,mmm"""
    ms = int(seconds * 1000)
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _generate_caption_files(run_dir: Path) -> tuple[Path | None, Path | None]:
    """Write captions.srt and chapters.txt (FFMETADATA1) from segments.json.
    Prints YouTube-compatible chapter list to stdout.
    Returns (srt_path, chapters_path), or (None, None) if segments.json missing/empty.
    """
    segments_path = run_dir / "segments.json"
    if not segments_path.exists():
        return None, None
    segments = json.loads(segments_path.read_text())
    if not segments:
        return None, None

    # SRT file
    srt_path = run_dir / "captions.srt"
    srt_lines: list[str] = []
    t = 0.0
    for i, seg in enumerate(segments, 1):
        start = _format_srt_time(t)
        t += seg["actual_duration_sec"]
        end = _format_srt_time(t)
        srt_lines.append(f"{i}\n{start} --> {end}\n{seg['text']}\n")
    srt_path.write_text("\n".join(srt_lines), encoding="utf-8")

    # FFMETADATA1 chapter file
    chapters_path = run_dir / "chapters.txt"
    meta_lines = [";FFMETADATA1\n"]
    t_ms = 0
    for seg in segments:
        dur_ms = int(seg["actual_duration_sec"] * 1000)
        raw = seg["text"]
        title = (raw[:60].rstrip() + "...") if len(raw) > 60 else raw
        meta_lines.append(
            f"\n[CHAPTER]\nTIMEBASE=1/1000\n"
            f"START={t_ms}\nEND={t_ms + dur_ms}\ntitle={title}\n"
        )
        t_ms += dur_ms
    chapters_path.write_text("".join(meta_lines), encoding="utf-8")

    # Print YouTube-compatible chapter list
    print("\n  Chapters:")
    t = 0.0
    for seg in segments:
        m = int(t // 60)
        s = int(t % 60)
        raw = seg["text"]
        title = (raw[:60].rstrip() + "...") if len(raw) > 60 else raw
        print(f"    {m}:{s:02d}  {title}")
        t += seg["actual_duration_sec"]

    return srt_path, chapters_path


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


def _render_once(run_id: str, output_dir: Path, verbose: bool, timeout: float, burn_captions: bool = False) -> Path:
    """Single render attempt. Raises RenderFailed on timeout or non-zero exit."""
    docker_cmd = _docker_render_cmd(run_id, output_dir)

    if verbose:
        returncode, _, timed_out = subprocess_with_timeout(docker_cmd, timeout)
        if timed_out:
            raise RenderFailed(f"timed out after {timeout:.0f}s")
        if returncode != 0:
            raise RenderFailed(f"Docker exited with code {returncode}")
        video_path = None
    else:
        total_anims = _count_animations(output_dir / run_id / "scene.py")
        anim_count = 0
        video_path = None

        def on_line(line: str) -> None:
            nonlocal video_path, anim_count
            if line.startswith("RENDER_COMPLETE:"):
                container_path = line.split(":", 1)[1].strip()
                video_path = output_dir / Path(container_path).relative_to("/output")
            else:
                n = _parse_manim_line(line)
                if n is not None:
                    anim_count = n
                    suffix = f"/{total_anims}" if total_anims else ""
                    print(f"\r  [render] animation {anim_count}{suffix}...", end="", flush=True)

        returncode, lines_buffer, timed_out = subprocess_with_timeout(
            docker_cmd, timeout, on_line=on_line
        )
        if anim_count:
            print()
        if timed_out:
            raise RenderFailed(f"timed out after {timeout:.0f}s")
        if returncode != 0:
            print("\n".join(list(lines_buffer)[-20:]))
            raise RenderFailed(f"Docker exited with code {returncode}")

    if video_path is None or not video_path.exists():
        manifest = json.loads((output_dir / run_id / "manifest.json").read_text())
        quality = manifest.get("quality", "medium")
        subdir = QUALITY_SUBDIR.get(quality, "720p30")
        scene_class = manifest.get("scene_class_name", "ChalkboardScene")
        video_path = (
            output_dir / run_id / "media" / "videos" / "scene" / subdir / f"{scene_class}.mp4"
        )

    if not video_path.exists():
        raise RenderFailed(f"rendered video not found at {video_path}")

    run_dir = output_dir / run_id
    final_mp4 = run_dir / "final.mp4"
    wav_path = run_dir / "voiceover.wav"

    srt_path, chapters_path = _generate_caption_files(run_dir)

    extra: list[str] = []
    if chapters_path:
        extra = ["-f", "ffmetadata", "-i", str(chapters_path), "-map_metadata", "2"]

    if burn_captions and srt_path:
        srt_esc = str(srt_path).replace("\\", "/").replace(":", "\\:")
        cmd = ["ffmpeg", "-y",
               "-i", str(video_path), "-i", str(wav_path), *extra,
               "-vf", f"subtitles={srt_esc}",
               "-c:v", "libx264", "-preset", "fast",
               "-c:a", "aac", "-b:a", "128k", str(final_mp4)]
    else:
        cmd = ["ffmpeg", "-y",
               "-i", str(video_path), "-i", str(wav_path), *extra,
               "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", str(final_mp4)]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise RenderFailed("ffmpeg merge timed out after 120s")
    return final_mp4


def _render(run_id: str, verbose: bool = False, burn_captions: bool = False) -> Path:
    output_dir = Path(OUTPUT_DIR).resolve()
    final_mp4 = output_dir / run_id / "final.mp4"

    if final_mp4.exists():
        print(f"\n  [render] already done — {final_mp4}")
        return final_mp4

    _ensure_docker_image()
    timeout = _compute_render_timeout(run_id, output_dir)
    print(f"\n  [render] rendering animation (timeout: {timeout:.0f}s)...")

    for attempt in range(1, 4):
        try:
            return _render_once(run_id, output_dir, verbose, timeout, burn_captions=burn_captions)
        except RenderFailed as e:
            for d in ["media", "media_preview"]:
                shutil.rmtree(output_dir / run_id / d, ignore_errors=True)
            if attempt < 3:
                print(f"\n  [render] {e} — retrying (attempt {attempt + 1}/3)...")
            else:
                raise


def _run_visual_qa(run_id: str, final_mp4: Path, density: str = "normal") -> dict | None:
    """Run visual QA. Returns result dict, or None if skipped (density='zero' or error)."""
    if density == "zero":
        print("\n  [qa] skipped (--qa-density zero)")
        return None
    from pipeline.visual_qa import visual_qa  # lazy import keeps anthropic out of startup path
    output_dir = Path(OUTPUT_DIR).resolve()
    qa_dir = output_dir / run_id / "qa_frames"
    scene_py = output_dir / run_id / "scene.py"
    scene_code = scene_py.read_text() if scene_py.exists() else None
    print("\n  [qa] running visual quality check...")
    try:
        result = visual_qa(final_mp4, qa_dir, scene_code=scene_code, density=density)
    except Exception as e:
        print(f"  [qa] skipped — {e}")
        return None
    if result["passed"]:
        print("  [qa] passed")
    else:
        print("  [qa] issues found:")
        for issue in result["issues"]:
            print(f"        [{issue['severity']}] {issue['description']}")
    return result


async def _qa_regenerate_scene(
    run_id: str, qa_issues: str,
    theme: str, audience: str, tone: str, effort_level: str,
    context_blocks=None,
) -> None:
    """Re-invoke manim_agent with QA feedback, overwrite scene.py in place."""
    from pipeline.agents.manim_agent import manim_agent
    output_dir = Path(OUTPUT_DIR).resolve()
    run_dir = output_dir / run_id

    manifest = json.loads((run_dir / "manifest.json").read_text())
    topic = manifest["topic"]
    script = (run_dir / "script.txt").read_text()
    segments = json.loads((run_dir / "segments.json").read_text())
    current_code = (run_dir / "scene.py").read_text()

    state = {
        "run_id": run_id,
        "topic": topic,
        "script": script,
        "script_segments": segments,
        "manim_code": current_code,
        "code_feedback": (
            "Visual quality check found these issues that must be fixed:\n"
            f"{qa_issues}\n"
            "Fix the Manim code so there are no overlapping elements, truncated text, "
            "or elements extending off-screen."
        ),
        "code_attempts": 1,
        "theme": theme, "audience": audience, "tone": tone,
        "effort_level": effort_level,
        "fact_feedback": None, "script_attempts": 0,
        "needs_web_search": False, "user_approved_search": False,
        "status": "validating", "context_file_paths": [],
    }

    if context_blocks:
        result = await manim_agent(state, context_blocks=context_blocks)
    else:
        result = await manim_agent(state)

    (run_dir / "scene.py").write_text(result["manim_code"])


def _render_preview_once(run_id: str, output_dir: Path, preview_mp4: Path) -> Path:
    """Single preview render attempt. Raises RenderFailed on timeout or non-zero exit."""
    docker_cmd = _docker_render_cmd(run_id, output_dir, preview=True)

    video_path = None

    def on_line(line: str) -> None:
        nonlocal video_path
        if line.startswith("RENDER_COMPLETE:"):
            container_path = line.split(":", 1)[1].strip()
            video_path = output_dir / Path(container_path).relative_to("/output")

    returncode, lines_buffer, timed_out = subprocess_with_timeout(
        docker_cmd, RENDER_TIMEOUT_MIN, on_line=on_line
    )
    if timed_out:
        raise RenderFailed(f"timed out after {RENDER_TIMEOUT_MIN:.0f}s")
    if returncode != 0:
        print("\n".join(list(lines_buffer)[-20:]))
        raise RenderFailed(f"Docker exited with code {returncode}")

    if video_path is None or not video_path.exists():
        raise RenderFailed(f"preview video not found at {video_path}")

    wav_path = output_dir / run_id / "voiceover.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-i", str(wav_path),
             "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", str(preview_mp4)],
            check=True, capture_output=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise RenderFailed("ffmpeg merge timed out after 120s")
    return preview_mp4


def _render_preview(run_id: str) -> Path:
    output_dir = Path(OUTPUT_DIR).resolve()
    preview_mp4 = output_dir / run_id / "preview.mp4"

    if preview_mp4.exists():
        print(f"\n  [preview] already done — {preview_mp4}")
        return preview_mp4

    _ensure_docker_image()
    print(f"\n  [preview] rendering preview at low quality (480p15, timeout: {RENDER_TIMEOUT_MIN:.0f}s)...")

    for attempt in range(1, 4):
        try:
            return _render_preview_once(run_id, output_dir, preview_mp4)
        except RenderFailed as e:
            for d in ["media", "media_preview"]:
                shutil.rmtree(output_dir / run_id / d, ignore_errors=True)
            if attempt < 3:
                print(f"\n  [preview] {e} — retrying (attempt {attempt + 1}/3)...")
            else:
                raise


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


async def run(topic: str, effort: str, thread_id: str, audience: str = "intermediate",
              tone: str = "casual", theme: str = "chalkboard",
              context_blocks=None, context_file_paths=None, speed: float = 1.0) -> None:
    print(f"\nChalkboard — topic: {topic!r} | effort: {effort} | run: {thread_id}\n")

    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        graph = build_graph(checkpointer=checkpointer, context_blocks=context_blocks)
        config = {"configurable": {"thread_id": thread_id}}
        input_state = {"topic": topic, "effort_level": effort, "audience": audience, "tone": tone, "theme": theme, "context_file_paths": context_file_paths or [], "speed": speed}

        while True:
            try:
                async for event in graph.astream(input_state, config=config, stream_mode="updates"):
                    _print_progress(event)
                break
            except TimeoutExhausted as e:
                print(f"\n  [pipeline] {e}")
                print("\nEnter action (retry / abort):")
                action = (await asyncio.to_thread(input, "  action: ")).strip()
                if action != "retry":
                    return
                input_state = None  # resume from last checkpoint


def _run_qa_loop(
    run_id: str, final_mp4: Path,
    theme: str, audience: str, tone: str, effort_level: str,
    context_blocks=None, verbose: bool = False,
    max_qa_attempts: int = 2, qa_density: str = "normal",
) -> None:
    """Run visual QA; if errors found, regenerate the Manim code and re-render (up to max_qa_attempts)."""
    output_dir = Path(OUTPUT_DIR).resolve()

    for qa_attempt in range(max_qa_attempts + 1):
        result = _run_visual_qa(run_id, final_mp4, density=qa_density)
        if result is None or result["passed"]:
            return

        errors = [i for i in result["issues"] if i["severity"] == "error"]
        if not errors or qa_attempt >= max_qa_attempts:
            return  # warnings only, or out of attempts

        issues_text = "\n".join(f"[{i['severity']}] {i['description']}" for i in result["issues"])
        print(f"\n  [qa] regenerating scene to fix errors (attempt {qa_attempt + 1}/{max_qa_attempts})...")
        asyncio.run(_qa_regenerate_scene(
            run_id, issues_text, theme, audience, tone, effort_level,
            context_blocks=context_blocks,
        ))
        # Clear old render artifacts so Docker re-renders the new scene.py
        run_dir = output_dir / run_id
        final_mp4.unlink(missing_ok=True)
        shutil.rmtree(run_dir / "media", ignore_errors=True)
        final_mp4 = _render(run_id, verbose=verbose)
        print(f"\n  [qa] re-rendered → {final_mp4}")


def _generate_quiz(run_id: str) -> Path | None:
    """Generate MCQ comprehension questions for a completed run.

    Reads script.txt from the run directory, calls Claude, and writes
    quiz.json alongside the other output files. Returns the quiz path.
    """
    import anthropic as _anthropic
    from config import CLAUDE_MODEL

    run_dir = Path(OUTPUT_DIR) / run_id
    script_path = run_dir / "script.txt"
    if not script_path.exists():
        print("  [quiz] script.txt not found — skipping.")
        return None

    script = script_path.read_text()
    client = _anthropic.Anthropic()

    print("\n  [quiz] generating comprehension questions...")
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system="You generate educational multiple-choice comprehension questions for explainer videos.",
        messages=[{
            "role": "user",
            "content": (
                "Generate 4–6 multiple-choice comprehension questions for this educational script.\n\n"
                f"{script}\n\n"
                "For each question provide the question text, exactly 4 answer options (labelled A–D), "
                "the correct answer letter, and a one-sentence explanation of why it is correct."
            ),
        }],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "question":    {"type": "string"},
                                    "options":     {"type": "array", "items": {"type": "string"}},
                                    "answer":      {"type": "string"},
                                    "explanation": {"type": "string"},
                                },
                                "required": ["question", "options", "answer", "explanation"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["questions"],
                    "additionalProperties": False,
                },
            }
        },
    )

    data = json.loads(response.content[0].text)
    questions = data["questions"]

    quiz_path = run_dir / "quiz.json"
    quiz_path.write_text(json.dumps(questions, indent=2))

    print(f"\n  Quiz ({len(questions)} questions) → {quiz_path}")
    for i, q in enumerate(questions, 1):
        print(f"    Q{i}: {q['question']}")

    return quiz_path


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
    parser.add_argument(
        "--context", action="append", dest="context", default=[], metavar="PATH",
        help="File or directory to include as context. Repeatable.",
    )
    parser.add_argument(
        "--context-ignore", action="append", dest="context_ignore", default=[], metavar="PATTERN",
        help="Glob pattern to exclude from context directories. Repeatable.",
    )
    parser.add_argument(
        "--url", action="append", dest="urls", default=[], metavar="URL",
        help="URL to fetch as source context (HTML stripped to text). Repeatable.",
    )
    parser.add_argument(
        "--github", action="append", dest="github", default=[], metavar="REPO",
        help="GitHub repo (owner/repo or URL) — fetches README as context. Repeatable.",
    )
    parser.add_argument(
        "--quiz", action="store_true",
        help="Generate comprehension questions (quiz.json) after the pipeline.",
    )
    parser.add_argument(
        "--qa-density", choices=["zero", "normal", "high"], default="normal",
        help="Visual QA frame sampling density: zero=skip, normal=1/30s (default), high=1/15s",
    )
    parser.add_argument(
        "--burn-captions", action="store_true",
        help="Burn subtitles into video (re-encodes video; captions.srt is always written)",
    )
    parser.add_argument(
        "--speed", type=float, default=1.0,
        help="Narration speed multiplier (e.g. 1.25). OpenAI: 0.25-4.0 natively; others use ffmpeg atempo.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip confirmation prompts (e.g. large-context warning).",
    )
    args = parser.parse_args()

    if args.verbose and args.preview:
        raise SystemExit("--verbose and --preview cannot be combined.")

    if not args.no_render:
        _check_tools()

    thread_id = args.run_id or str(uuid.uuid4())

    context_blocks = None
    context_file_paths: list[str] = []
    if args.context:
        files = collect_files(args.context, ignore_patterns=args.context_ignore or None)
        context_blocks = load_context_blocks(files)
        context_file_paths = [str(f) for f in files]

    if args.urls:
        from pipeline.context import fetch_url_blocks
        for url in args.urls:
            print(f"\n  Fetching {url}...")
            try:
                url_blocks = fetch_url_blocks(url)
            except Exception as e:
                raise SystemExit(f"Failed to fetch {url}: {e}")
            context_blocks = (context_blocks or []) + url_blocks

    if args.github:
        from pipeline.context import fetch_url_blocks
        for repo in args.github:
            try:
                raw_url = _github_to_raw_url(repo)
            except ValueError as e:
                raise SystemExit(str(e))
            print(f"\n  Fetching GitHub README: {raw_url}...")
            try:
                url_blocks = fetch_url_blocks(raw_url)
            except Exception as e:
                raise SystemExit(f"Failed to fetch README for {repo!r}: {e}")
            context_blocks = (context_blocks or []) + url_blocks

    if context_blocks:
        if not _report_context(context_blocks, _yes=args.yes):
            raise SystemExit("Aborted.")
    elif args.run_id:
        print("Note: resuming without context files. Pass --context or --url to include source material.")

    asyncio.run(run(
        args.topic, args.effort, thread_id,
        audience=args.audience, tone=args.tone, theme=args.theme,
        context_blocks=context_blocks, context_file_paths=context_file_paths,
        speed=args.speed,
    ))

    if not args.no_render:
        if args.preview:
            while True:
                try:
                    preview = _render_preview(thread_id)
                    print(f"\nPreview → {preview}")
                    print(f"\nTo render the full video:")
                    print(f"  python main.py --topic {args.topic!r} --run-id {thread_id}")
                    break
                except RenderFailed as e:
                    print(f"\n  [render] all 3 attempts failed: {e}")
                    print("\nEnter action (retry_render / abort):")
                    action = input("  action: ").strip()
                    if action == "retry_render":
                        continue
                    raise SystemExit("Aborted.")
        else:
            while True:
                try:
                    final = _render(thread_id, verbose=args.verbose, burn_captions=args.burn_captions)
                    print(f"\nDone → {final}")
                    _run_qa_loop(
                        thread_id, final,
                        theme=args.theme, audience=args.audience,
                        tone=args.tone, effort_level=args.effort,
                        context_blocks=context_blocks,
                        verbose=args.verbose,
                        qa_density=args.qa_density,
                    )
                    break
                except RenderFailed as e:
                    print(f"\n  [render] all 3 attempts failed: {e}")
                    print("\nEnter action (retry_render / abort):")
                    action = input("  action: ").strip()
                    if action == "retry_render":
                        continue
                    raise SystemExit("Aborted.")
    else:
        print(f"\nDone. Output files in output/{thread_id}/")

    if args.quiz:
        _generate_quiz(thread_id)


if __name__ == "__main__":
    main()
