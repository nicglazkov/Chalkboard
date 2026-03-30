# Timeout & Retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate all indefinite hang points across the Chalkboard pipeline by adding adaptive timeouts, 3-attempt retry loops with user notification, and clean escalation when all attempts fail.

**Architecture:** `pipeline/retry.py` provides `TimeoutExhausted` and `api_call_with_retry` (async, wraps any sync callable). `main.py` gets `subprocess_with_timeout` (threading.Timer kill) and `_compute_render_timeout` (reads segments.json + counts animations). Agents become `async def` to await the retry wrapper. TTS backends use the same wrapper. Other subprocess calls (`ffmpeg`, `docker build`, `ffprobe`) get `timeout=N` added to existing `subprocess.run()` calls.

**Tech Stack:** Python stdlib only — `asyncio`, `threading`, `subprocess`, `pathlib`. No new dependencies.

---

## File Structure

| File | Change |
|------|--------|
| `pipeline/retry.py` | **New** — `TimeoutExhausted`, `api_call_with_retry`, all timeout constants |
| `tests/test_retry.py` | **New** — tests for retry wrapper |
| `main.py` | `import threading`; `RenderFailed`; `subprocess_with_timeout`; timeout constants; `_compute_render_timeout`; `_render_once`; retry loop in `_render`; `_render_preview_once`; retry loop in `_render_preview`; ffmpeg+docker build timeouts; render escalation in `main()`; `TimeoutExhausted` catch in `run()` |
| `tests/test_render_timeout.py` | **New** — tests for `subprocess_with_timeout` and `_compute_render_timeout` |
| `pipeline/agents/script_agent.py` | `async def script_agent`; wrap `messages.create` with `api_call_with_retry` |
| `pipeline/agents/fact_validator.py` | Same pattern |
| `pipeline/agents/manim_agent.py` | Same pattern |
| `pipeline/agents/code_validator.py` | Same pattern |
| `tests/test_script_agent.py` | Update all calls from `script_agent(state)` → `asyncio.run(script_agent(state))` |
| `tests/test_fact_validator.py` | Same pattern |
| `tests/test_manim_agent.py` | Same pattern |
| `tests/test_code_validator.py` | Same pattern |
| `pipeline/tts/openai_tts.py` | Per-segment `api_call_with_retry`; remove `_generate_sync` |
| `pipeline/tts/elevenlabs_tts.py` | Per-segment `api_call_with_retry`; remove `_generate_sync` |
| `pipeline/tts/kokoro_tts.py` | Whole-call `api_call_with_retry` |
| `pipeline/visual_qa.py` | `timeout=60` on ffprobe; `timeout=30` on ffmpeg frame extractions; `api_call_with_retry` on Claude call |

---

## Task 1: `pipeline/retry.py` — the foundation

**Files:**
- Create: `pipeline/retry.py`
- Create: `tests/test_retry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_retry.py`:

```python
# tests/test_retry.py
import asyncio
import pytest
from pipeline.retry import api_call_with_retry, TimeoutExhausted


def test_api_call_with_retry_returns_value():
    result = asyncio.run(
        api_call_with_retry(lambda: 42, timeout=5.0, label="test")
    )
    assert result == 42


def test_api_call_with_retry_retries_on_exception_and_succeeds():
    call_count = [0]

    def flaky():
        call_count[0] += 1
        if call_count[0] < 3:
            raise ConnectionError("network error")
        return "ok"

    result = asyncio.run(
        api_call_with_retry(flaky, timeout=5.0, max_attempts=3, label="test")
    )
    assert result == "ok"
    assert call_count[0] == 3


def test_api_call_with_retry_raises_timeout_exhausted_after_max_attempts():
    call_count = [0]

    def always_fails():
        call_count[0] += 1
        raise ValueError("bad")

    with pytest.raises(TimeoutExhausted) as exc_info:
        asyncio.run(
            api_call_with_retry(always_fails, timeout=5.0, max_attempts=3, label="myagent")
        )
    assert call_count[0] == 3
    assert "myagent" in str(exc_info.value)
    assert "3 attempts" in str(exc_info.value)


def test_api_call_with_retry_timeout_fires_and_exhausts():
    import time
    call_count = [0]

    def slow():
        call_count[0] += 1
        time.sleep(10)

    with pytest.raises(TimeoutExhausted):
        asyncio.run(
            api_call_with_retry(slow, timeout=0.05, max_attempts=2, label="test")
        )
    assert call_count[0] == 2


def test_api_call_with_retry_prints_notification(capsys):
    call_count = [0]

    def failing():
        call_count[0] += 1
        raise RuntimeError("oops")

    with pytest.raises(TimeoutExhausted):
        asyncio.run(
            api_call_with_retry(failing, timeout=5.0, max_attempts=2, label="mylabel")
        )

    captured = capsys.readouterr()
    assert "[mylabel]" in captured.out
    assert "attempt 2/2" in captured.out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_retry.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.retry'`

- [ ] **Step 3: Create `pipeline/retry.py`**

```python
# pipeline/retry.py
import asyncio

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class TimeoutExhausted(Exception):
    """Raised when api_call_with_retry exhausts all attempts."""


# ---------------------------------------------------------------------------
# Timeout constants (seconds) — tune these as needed
# ---------------------------------------------------------------------------

TIMEOUT_SCRIPT_AGENT   = 120.0   # script_agent (may use web search tool)
TIMEOUT_FACT_VALIDATOR =  60.0   # fact_validator
TIMEOUT_MANIM_AGENT    = 180.0   # manim_agent (max_tokens=16384)
TIMEOUT_CODE_VALIDATOR =  60.0   # code_validator
TIMEOUT_VISUAL_QA      =  90.0   # visual_qa (5 base64 images)
TIMEOUT_TTS_SEGMENT    =  30.0   # OpenAI + ElevenLabs per-segment
TIMEOUT_TTS_KOKORO     = 120.0   # Kokoro full call (includes model load)


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

async def api_call_with_retry(fn, timeout, max_attempts=3, label="API call"):
    """
    Run sync callable `fn` in a thread with a timeout.
    Retry up to `max_attempts` times on any exception or timeout.
    Raises TimeoutExhausted when all attempts are exhausted.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        except (asyncio.TimeoutError, Exception) as e:
            if attempt == max_attempts:
                raise TimeoutExhausted(
                    f"{label} failed after {max_attempts} attempts: {e}"
                )
            print(
                f"  [{label}] failed ({type(e).__name__}) — "
                f"retrying (attempt {attempt + 1}/{max_attempts})..."
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_retry.py -v
```

Expected: 5 PASS

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest -q
```

Expected: all 50 tests pass

- [ ] **Step 6: Commit**

```bash
git add pipeline/retry.py tests/test_retry.py
git commit -m "feat: add pipeline/retry.py with TimeoutExhausted and api_call_with_retry"
```

---

## Task 2: `subprocess_with_timeout` + `_compute_render_timeout` in `main.py`

**Files:**
- Modify: `main.py`
- Create: `tests/test_render_timeout.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_render_timeout.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_render_timeout.py -v
```

Expected: FAIL — `ImportError: cannot import name 'subprocess_with_timeout' from 'main'`

- [ ] **Step 3: Add `import threading` and the new code to `main.py`**

At the top of `main.py`, add `threading` to the existing imports:

```python
import threading
```

After the existing `QUALITY_SUBDIR` constant (around line 21), add:

```python
# ---------------------------------------------------------------------------
# Render timeout constants
# ---------------------------------------------------------------------------
RENDER_TIMEOUT_BASE         = 60.0
RENDER_TIMEOUT_PER_ANIM     = 5.0
RENDER_TIMEOUT_AUDIO_RATIO  = 3.0
RENDER_TIMEOUT_QUALITY_MULT = {"low": 0.5, "medium": 1.0, "high": 2.0}
RENDER_TIMEOUT_MIN          = 90.0
RENDER_TIMEOUT_MAX          = 1200.0
```

After `_check_tools()` (around line 30), add these two new functions:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_render_timeout.py -v
```

Expected: 9 PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest -q
```

Expected: all 55 tests pass (50 + 5 new)

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_render_timeout.py
git commit -m "feat: add subprocess_with_timeout and _compute_render_timeout to main.py"
```

---

## Task 3: Render retry loop + ffmpeg / docker build timeouts

**Files:**
- Modify: `main.py`

This task refactors `_render()` and `_render_preview()` to use 3-attempt retry loops, adds `RenderFailed`, applies `subprocess_with_timeout` to the Docker Popen loops, adds `timeout=` to `ffmpeg` and `_ensure_docker_image`, and catches `RenderFailed` + `TimeoutExhausted` in `main()` and `run()` respectively.

- [ ] **Step 1: Add `RenderFailed` and update `_ensure_docker_image` with a timeout**

In `main.py`, add `RenderFailed` right after the imports block (before `_check_tools`):

```python
class RenderFailed(Exception):
    """Raised when a render attempt fails (timeout or non-zero exit)."""
```

Update `_ensure_docker_image` to add `timeout=` to both subprocess calls:

```python
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
```

- [ ] **Step 2: Extract `_render_once` from `_render`**

Replace the entire `_render()` function with `_render_once()` + a new `_render()`:

```python
def _render_once(run_id: str, output_dir: Path, verbose: bool, timeout: float) -> Path:
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

    final_mp4 = output_dir / run_id / "final.mp4"
    wav_path = output_dir / run_id / "voiceover.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-i", str(wav_path),
             "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", str(final_mp4)],
            check=True, capture_output=True, timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise RenderFailed("ffmpeg merge timed out after 120s")
    return final_mp4


def _render(run_id: str, verbose: bool = False) -> Path:
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
            return _render_once(run_id, output_dir, verbose, timeout)
        except RenderFailed as e:
            for d in ["media", "media_preview"]:
                shutil.rmtree(output_dir / run_id / d, ignore_errors=True)
            if attempt < 3:
                print(f"\n  [render] {e} — retrying (attempt {attempt + 1}/3)...")
            else:
                raise
```

- [ ] **Step 3: Extract `_render_preview_once` from `_render_preview`**

Replace the entire `_render_preview()` function with `_render_preview_once()` + a new `_render_preview()`:

```python
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
```

- [ ] **Step 4: Update `run()` to catch `TimeoutExhausted`**

Add `from pipeline.retry import TimeoutExhausted` to the imports in `main.py`:

```python
from pipeline.retry import TimeoutExhausted
```

Update the `run()` function to catch `TimeoutExhausted`:

```python
async def run(topic: str, effort: str, thread_id: str, audience: str = "intermediate",
              tone: str = "casual", theme: str = "chalkboard") -> None:
    print(f"\nChalkboard — topic: {topic!r} | effort: {effort} | run: {thread_id}\n")

    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        input_state = {"topic": topic, "effort_level": effort, "audience": audience,
                       "tone": tone, "theme": theme}

        while True:
            try:
                async for event in graph.astream(input_state, config=config, stream_mode="updates"):
                    _print_progress(event)
                    if "__interrupt__" in event:
                        interrupt_value = event["__interrupt__"][0].value
                        resume_cmd = _handle_interrupt(interrupt_value)
                        input_state = resume_cmd
                        break
                else:
                    break
            except TimeoutExhausted as e:
                print(f"\n  [pipeline] {e}")
                resume_cmd = _handle_interrupt(str(e))
                input_state = resume_cmd
```

- [ ] **Step 5: Update `main()` to catch `RenderFailed`**

Replace the `if not args.no_render:` block in `main()` with:

```python
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
                    final = _render(thread_id, verbose=args.verbose)
                    print(f"\nDone → {final}")
                    _run_visual_qa(thread_id, final)
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
```

- [ ] **Step 6: Run full test suite**

```bash
pytest -q
```

Expected: all 55 tests pass (no new tests in this task — render retry is tested via integration in Task 4)

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "feat: add render retry loop with adaptive timeout and RenderFailed escalation"
```

---

## Task 4: Make agents `async def` + wrap Claude calls with `api_call_with_retry`

**Files:**
- Modify: `pipeline/agents/script_agent.py`
- Modify: `pipeline/agents/fact_validator.py`
- Modify: `pipeline/agents/manim_agent.py`
- Modify: `pipeline/agents/code_validator.py`
- Modify: `tests/test_script_agent.py`
- Modify: `tests/test_fact_validator.py`
- Modify: `tests/test_manim_agent.py`
- Modify: `tests/test_code_validator.py`

When agents become `async def`, all existing tests that call `agent(state)` synchronously will get a coroutine back instead of a result. Every test call must be wrapped with `asyncio.run(agent(state))`.

- [ ] **Step 1: Update `script_agent.py`**

Replace the `script_agent` function (keep everything above it unchanged):

```python
import asyncio
from pipeline.retry import api_call_with_retry, TIMEOUT_SCRIPT_AGENT

async def script_agent(state: PipelineState, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    tools = []
    if state.get("user_approved_search") or state["effort_level"] == "high":
        tools = [{"type": "web_search_20250305", "name": "web_search"}]

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_message(state)}],
            tools=tools if tools else anthropic.NOT_GIVEN,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "script": {"type": "string"},
                            "segments": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "estimated_duration_sec": {"type": "number"},
                                    },
                                    "required": ["text", "estimated_duration_sec"],
                                    "additionalProperties": False,
                                },
                            },
                            "needs_web_search": {"type": "boolean"},
                        },
                        "required": ["script", "segments", "needs_web_search"],
                        "additionalProperties": False,
                    },
                }
            },
        )

    response = await api_call_with_retry(_call, timeout=TIMEOUT_SCRIPT_AGENT, label="script_agent")
    data = json.loads(response.content[0].text)
    return {
        "script": data["script"],
        "script_segments": data["segments"],
        "needs_web_search": data.get("needs_web_search", False),
        "status": "validating",
    }
```

- [ ] **Step 2: Update `fact_validator.py`**

Replace the `fact_validator` function (keep everything above it unchanged):

```python
import asyncio
from pipeline.retry import api_call_with_retry, TIMEOUT_FACT_VALIDATOR

async def fact_validator(state: PipelineState, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()
    effort = state["effort_level"]
    instruction = EFFORT_INSTRUCTIONS[effort]

    user_msg = (
        f"Review the factual accuracy of this educational script.\n"
        f"Instructions: {instruction}\n\n"
        f"Script:\n{state['script']}"
    )

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": user_msg}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )

    response = await api_call_with_retry(_call, timeout=TIMEOUT_FACT_VALIDATOR, label="fact_validator")
    result = ValidationResult.model_validate_json(response.content[0].text)

    if result.verdict == "needs_revision":
        return {
            "fact_feedback": result.feedback,
            "script_attempts": state["script_attempts"] + 1,
        }
    else:
        return {
            "fact_feedback": None,
            "script_attempts": state["script_attempts"],
        }
```

- [ ] **Step 3: Update `manim_agent.py`**

Replace the `manim_agent` function (keep everything above it unchanged):

```python
import asyncio
from pipeline.retry import api_call_with_retry, TIMEOUT_MANIM_AGENT

async def manim_agent(state: PipelineState, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    user_msg = (
        f"Create a Manim animation for this educational script.\n\n"
        f"Topic: {state['topic']}\n\n"
        f"Narration segments with timings:\n{_format_segments(state['script_segments'])}\n\n"
        f"Full script for context:\n{state['script']}\n\n"
        f"{THEME_SPECS[state.get('theme', 'chalkboard')]}"
    )
    if state.get("code_feedback"):
        user_msg += f"\n\nPrevious attempt had issues. Rewrite the scene fully, addressing:\n{state['code_feedback']}"

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16384,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {"manim_code": {"type": "string"}},
                        "required": ["manim_code"],
                        "additionalProperties": False,
                    },
                }
            },
        )

    response = await api_call_with_retry(_call, timeout=TIMEOUT_MANIM_AGENT, label="manim_agent")
    data = json.loads(response.content[0].text)
    return {"manim_code": data["manim_code"], "status": "validating"}
```

- [ ] **Step 4: Update `code_validator.py`**

Replace the `code_validator` function (keep everything above it unchanged):

```python
import asyncio
from pipeline.retry import api_call_with_retry, TIMEOUT_CODE_VALIDATOR

async def code_validator(state: PipelineState, client=None) -> dict:
    code = state["manim_code"]
    attempts = state["code_attempts"]

    # Step 1: syntax check (free, fast — no Claude call)
    try:
        ast.parse(code)
    except SyntaxError as e:
        return {
            "code_feedback": f"Syntax error: {e}",
            "code_attempts": attempts + 1,
        }

    # Step 2: semantic review via Claude
    if client is None:
        client = anthropic.Anthropic()
    user_msg = (
        f"Review this Manim CE code for correctness and coherence with the script.\n\n"
        f"Script:\n{state['script']}\n\n"
        f"Manim code:\n{code}\n\n"
        f"Check: Does the animation visualize the script? Are Manim CE v0.20 APIs used correctly? "
        f"Is the class named ChalkboardScene?\n\n"
        f"Sync check: The scene must load _seg_data from (Path(__file__).parent / \"segments.json\") "
        f"and use _d[i] (not hardcoded float literals) for all self.wait() calls. "
        f"If any self.wait() call uses a hardcoded float literal, return needs_revision."
    )

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": user_msg}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )

    response = await api_call_with_retry(_call, timeout=TIMEOUT_CODE_VALIDATOR, label="code_validator")
    result = ValidationResult.model_validate_json(response.content[0].text)
    if result.verdict == "needs_revision":
        return {
            "code_feedback": result.feedback,
            "code_attempts": attempts + 1,
        }
    else:
        return {
            "code_feedback": None,
            "code_attempts": attempts,
        }
```

- [ ] **Step 5: Run the test suite to see which agent tests break**

```bash
pytest tests/test_script_agent.py tests/test_fact_validator.py tests/test_manim_agent.py tests/test_code_validator.py -v 2>&1 | head -40
```

Expected: tests fail because `agent(state)` now returns a coroutine, not a result.

- [ ] **Step 6: Update all agent tests to use `asyncio.run()`**

In `tests/test_script_agent.py`, add `import asyncio` at the top and wrap every direct agent call:

Every occurrence of:
```python
result = script_agent(base_state)
```
becomes:
```python
result = asyncio.run(script_agent(base_state))
```

And every occurrence of:
```python
script_agent(base_state)
```
(without capturing result) becomes:
```python
asyncio.run(script_agent(base_state))
```

Apply the same pattern to `test_fact_validator.py`, `test_manim_agent.py`, and `test_code_validator.py` — add `import asyncio` and wrap all agent calls with `asyncio.run()`.

- [ ] **Step 7: Run agent tests to verify they pass**

```bash
pytest tests/test_script_agent.py tests/test_fact_validator.py tests/test_manim_agent.py tests/test_code_validator.py -v
```

Expected: all pass

- [ ] **Step 8: Run full test suite**

```bash
pytest -q
```

Expected: all 55 tests pass

- [ ] **Step 9: Commit**

```bash
git add pipeline/agents/script_agent.py pipeline/agents/fact_validator.py \
        pipeline/agents/manim_agent.py pipeline/agents/code_validator.py \
        tests/test_script_agent.py tests/test_fact_validator.py \
        tests/test_manim_agent.py tests/test_code_validator.py
git commit -m "feat: make agents async and wrap Claude calls with api_call_with_retry"
```

---

## Task 5: TTS backend timeouts

**Files:**
- Modify: `pipeline/tts/openai_tts.py`
- Modify: `pipeline/tts/elevenlabs_tts.py`
- Modify: `pipeline/tts/kokoro_tts.py`

Each TTS backend's `generate_audio` already is async (wraps a sync function with `asyncio.to_thread`). We replace the inner sync function approach with direct `api_call_with_retry` calls per segment (OpenAI, ElevenLabs) or for the whole call (Kokoro).

- [ ] **Step 1: Update `pipeline/tts/openai_tts.py`**

Replace the entire file contents:

```python
# pipeline/tts/openai_tts.py
# Requires: pip install openai
import io
import wave
from pathlib import Path
from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_SEGMENT

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]

OPENAI_MODEL = "gpt-4o-mini-tts"
OPENAI_VOICE = "alloy"


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    if openai is None:
        raise ImportError("Install openai: pip install openai")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_pcm: list[bytes] = []
    durations: list[float] = []
    wav_params = None

    for segment in segments:
        def _call(seg=segment):
            response = openai.audio.speech.create(
                model=OPENAI_MODEL,
                voice=OPENAI_VOICE,
                input=seg["text"],
                response_format="wav",
            )
            with wave.open(io.BytesIO(response.content)) as wf:
                params = wf.getparams()
                frames = wf.readframes(wf.getnframes())
                actual_nframes = len(frames) // (wf.getnchannels() * wf.getsampwidth())
                duration = actual_nframes / wf.getframerate()
                return params, frames, duration

        params, frames, duration = await api_call_with_retry(
            _call, timeout=TIMEOUT_TTS_SEGMENT, label="openai_tts"
        )
        if wav_params is None:
            wav_params = params
        all_pcm.append(frames)
        durations.append(duration)

    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setnchannels(wav_params.nchannels)
        out_wav.setsampwidth(wav_params.sampwidth)
        out_wav.setframerate(wav_params.framerate)
        out_wav.writeframes(b"".join(all_pcm))

    return output_path, durations
```

- [ ] **Step 2: Update `pipeline/tts/elevenlabs_tts.py`**

Replace the entire file contents:

```python
# pipeline/tts/elevenlabs_tts.py
# Requires: pip install elevenlabs
import os
import wave
from pathlib import Path
from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_SEGMENT

try:
    from elevenlabs import ElevenLabs
except ImportError:
    ElevenLabs = None  # type: ignore[assignment,misc]

ELEVENLABS_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George" — swap via ELEVENLABS_VOICE_ID env var
SAMPLE_RATE = 24000


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    if ElevenLabs is None:
        raise ImportError("Install elevenlabs: pip install elevenlabs")

    voice_id = os.getenv("ELEVENLABS_VOICE_ID", ELEVENLABS_VOICE_ID)
    client = ElevenLabs()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    all_pcm: list[bytes] = []
    durations: list[float] = []

    for segment in segments:
        def _call(seg=segment):
            audio_iter = client.text_to_speech.convert(
                voice_id=voice_id,
                text=seg["text"],
                model_id="eleven_turbo_v2_5",
                output_format="pcm_24000",
            )
            pcm = b"".join(audio_iter)
            duration = len(pcm) / (SAMPLE_RATE * 2)
            return pcm, duration

        pcm, duration = await api_call_with_retry(
            _call, timeout=TIMEOUT_TTS_SEGMENT, label="elevenlabs_tts"
        )
        all_pcm.append(pcm)
        durations.append(duration)

    with wave.open(str(output_path), "wb") as out_wav:
        out_wav.setnchannels(1)
        out_wav.setsampwidth(2)
        out_wav.setframerate(SAMPLE_RATE)
        out_wav.writeframes(b"".join(all_pcm))

    return output_path, durations
```

- [ ] **Step 3: Update `pipeline/tts/kokoro_tts.py`**

Replace the entire file contents:

```python
# pipeline/tts/kokoro_tts.py
import asyncio
import numpy as np
import soundfile as sf
from pathlib import Path
from pipeline.retry import api_call_with_retry, TIMEOUT_TTS_KOKORO

try:
    from kokoro import KPipeline
except ImportError:
    KPipeline = None  # type: ignore[assignment,misc]

SAMPLE_RATE = 24000
DEFAULT_VOICE = "af_heart"


def _generate_sync(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    if KPipeline is None:
        raise ImportError("Install kokoro: pip install kokoro")
    pipeline = KPipeline(lang_code="a")
    all_audio: list[np.ndarray] = []
    durations: list[float] = []

    for segment in segments:
        seg_chunks: list[np.ndarray] = []
        for _gs, _ps, audio in pipeline(segment["text"], voice=DEFAULT_VOICE):
            seg_chunks.append(audio)
        seg_audio = np.concatenate(seg_chunks) if seg_chunks else np.array([], dtype=np.float32)
        durations.append(len(seg_audio) / SAMPLE_RATE)
        all_audio.append(seg_audio)

    full_audio = np.concatenate(all_audio) if all_audio else np.array([], dtype=np.float32)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_path), full_audio, SAMPLE_RATE)
    return output_path, durations


async def generate_audio(segments: list[dict], output_path: Path) -> tuple[Path, list[float]]:
    return await api_call_with_retry(
        lambda: _generate_sync(segments, output_path),
        timeout=TIMEOUT_TTS_KOKORO,
        label="kokoro_tts",
    )
```

- [ ] **Step 4: Run TTS tests**

```bash
pytest tests/test_openai_tts.py tests/test_elevenlabs_tts.py tests/test_kokoro_tts.py -v
```

Expected: all pass (the tests mock the underlying API clients, which still work when called via `asyncio.to_thread`)

- [ ] **Step 5: Run full test suite**

```bash
pytest -q
```

Expected: all 55 tests pass

- [ ] **Step 6: Commit**

```bash
git add pipeline/tts/openai_tts.py pipeline/tts/elevenlabs_tts.py pipeline/tts/kokoro_tts.py
git commit -m "feat: add per-segment timeout retry to TTS backends"
```

---

## Task 6: `visual_qa.py` timeouts

**Files:**
- Modify: `pipeline/visual_qa.py`

Add `timeout=60` to the `ffprobe` call, `timeout=30` to each `ffmpeg` frame extraction call, and wrap the Claude vision call with `api_call_with_retry`.

- [ ] **Step 1: Update `pipeline/visual_qa.py`**

Replace the entire file contents:

```python
# pipeline/visual_qa.py
import asyncio
import base64
import json
import subprocess
from pathlib import Path
import anthropic
from config import CLAUDE_MODEL
from pipeline.retry import api_call_with_retry, TIMEOUT_VISUAL_QA

SCHEMA = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["warning", "error"]},
                    "description": {"type": "string"},
                },
                "required": ["severity", "description"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["passed", "issues"],
    "additionalProperties": False,
}


def _extract_frames(video_path: Path, qa_dir: Path, n_frames: int = 5) -> list[Path]:
    """Extract n evenly-spaced frames from video_path into qa_dir."""
    qa_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    duration = float(result.stdout.strip())

    if duration <= 0.0:
        raise ValueError(f"Video has no duration (got {duration!r}): {video_path}")

    frame_paths = []
    for i in range(n_frames):
        t = duration * i / max(1, n_frames - 1)
        frame_path = qa_dir / f"frame_{i:02d}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(t), "-i", str(video_path),
             "-frames:v", "1", str(frame_path)],
            capture_output=True, check=True, timeout=30,
        )
        frame_paths.append(frame_path)

    return frame_paths


def visual_qa(video_path: Path, qa_dir: Path, client=None) -> dict:
    """
    Run visual QA on a rendered video by sampling frames and reviewing with Claude.
    Returns {"passed": bool, "issues": [{"severity": "warning"|"error", "description": str}]}
    """
    if client is None:
        client = anthropic.Anthropic()

    frame_paths = _extract_frames(video_path, qa_dir)

    content = [
        {
            "type": "text",
            "text": (
                "You are reviewing frames from an educational animation video for visual quality issues. "
                "Check each frame for: overlapping text or shapes, text extending off-screen, "
                "unreadably small text, poor color contrast, and visual clutter. "
                "A frame passes if all elements are clearly readable and none extend beyond the frame boundary."
            ),
        }
    ]

    for i, frame_path in enumerate(frame_paths):
        frame_data = base64.standard_b64encode(frame_path.read_bytes()).decode()
        content.append({"type": "text", "text": f"Frame {i + 1}/{len(frame_paths)}:"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": frame_data},
        })

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )

    response = asyncio.run(
        api_call_with_retry(_call, timeout=TIMEOUT_VISUAL_QA, label="visual_qa")
    )
    return json.loads(response.content[0].text)
```

**Note:** `visual_qa()` is called from `main.py` in a synchronous context (after `asyncio.run(run(...))` has completed). `asyncio.run()` here creates a fresh event loop for the retry wrapper — this is safe because we're no longer inside an existing event loop at that point.

- [ ] **Step 2: Run visual QA tests**

```bash
pytest tests/test_visual_qa.py -v
```

Expected: all pass

- [ ] **Step 3: Run full test suite**

```bash
pytest -q
```

Expected: all 55 tests pass

- [ ] **Step 4: Commit**

```bash
git add pipeline/visual_qa.py
git commit -m "feat: add timeouts to visual_qa ffprobe/ffmpeg/Claude calls"
```

---

## Self-Review

**Spec coverage:**
- `pipeline/retry.py` with `TimeoutExhausted` + `api_call_with_retry` + constants → Task 1 ✓
- `subprocess_with_timeout` in `main.py` → Task 2 ✓
- `_compute_render_timeout` (adaptive formula, all constants) → Task 2 ✓
- Render retry loop (3 attempts, clean up media/, notify user) → Task 3 ✓
- `_render_preview` retry loop → Task 3 ✓
- `ffmpeg` + `_ensure_docker_image` timeouts → Task 3 ✓
- `TimeoutExhausted` catch in `run()` → Task 3 ✓
- `RenderFailed` escalation in `main()` → Task 3 ✓
- All 4 agents `async def` + `api_call_with_retry` → Task 4 ✓
- OpenAI TTS per-segment retry → Task 5 ✓
- ElevenLabs TTS per-segment retry → Task 5 ✓
- Kokoro TTS whole-call retry → Task 5 ✓
- `visual_qa` subprocess timeouts + Claude retry → Task 6 ✓

**Placeholder scan:** No TBDs, no "add error handling" vagueness, all code blocks are complete.

**Type consistency:**
- `subprocess_with_timeout` returns `tuple[int, collections.deque, bool]` — used consistently in `_render_once` and `_render_preview_once` ✓
- `RenderFailed` defined in `main.py`, caught in `main()` — no cross-module reference ✓
- `TimeoutExhausted` defined in `pipeline/retry.py`, imported in `main.py` via `from pipeline.retry import TimeoutExhausted` ✓
- `RENDER_TIMEOUT_MIN` used as the preview timeout in `_render_preview_once` — consistent with Task 2 definition ✓
- All agent functions: `async def agent(state: PipelineState, client=None) -> dict` — unchanged signature except `async` ✓
