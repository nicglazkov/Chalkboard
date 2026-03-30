# Timeout & Retry Design

**Goal:** Eliminate all indefinite hang points across the Chalkboard pipeline by adding adaptive timeouts, 3-attempt retry loops with user notification, and clean escalation paths when all attempts fail.

**Architecture:** Two-layer wrapper approach — `subprocess_with_timeout()` for Docker/ffmpeg calls (needs process cleanup), `api_call_with_retry()` for Claude/TTS calls (stateless retries). Both share the same 3-attempt pattern and `[label] failed — retrying (attempt N/3)...` notification format. A shared `TimeoutExhausted` exception carries exhaustion through LangGraph's async boundary to a single catch point in `main.py`.

**Tech Stack:** Python stdlib only (`asyncio`, `threading`, `pathlib`) — no new dependencies.

---

## Hang Point Inventory

Every location that can block indefinitely today:

| Location | What hangs | Timeout today |
|----------|-----------|---------------|
| `main.py` Docker render (stdout loop) | `for line in process.stdout` blocks if Manim hangs | None |
| `main.py` Docker render (verbose) | `process.wait()` blocks forever | None |
| `main.py` Docker preview (stdout loop) | Same as render | None |
| `main.py` ffmpeg merge (render + preview) | `subprocess.run()` with no timeout | None |
| `main.py` `_ensure_docker_image()` build | `subprocess.run()` on Docker build | None |
| `docker/render.sh` Manim render | Manim itself has no timeout | None |
| `pipeline/agents/script_agent.py` | `client.messages.create()` | None |
| `pipeline/agents/fact_validator.py` | `client.messages.create()` | None |
| `pipeline/agents/manim_agent.py` | `client.messages.create()` (max_tokens=16384) | None |
| `pipeline/agents/code_validator.py` | `client.messages.create()` | None |
| `pipeline/visual_qa.py` | `client.messages.create()` + ffprobe/ffmpeg | None |
| `pipeline/render_trigger.py` | TTS backend call | None |
| `pipeline/tts/openai_tts.py` | `openai.audio.speech.create()` per segment | None |
| `pipeline/tts/elevenlabs_tts.py` | `client.text_to_speech.convert()` per segment | None |
| `pipeline/tts/kokoro_tts.py` | `KPipeline()` init + generator loop | None |

---

## Component 1: `pipeline/retry.py` (new)

Contains the async retry wrapper and the shared exception type.

### `TimeoutExhausted`

```python
class TimeoutExhausted(Exception):
    pass
```

Raised when all attempts of an `api_call_with_retry` call are exhausted. Propagates through LangGraph's `graph.astream()` boundary and is caught in `main.py`'s `run()` loop.

### `api_call_with_retry(fn, timeout, max_attempts=3, label)`

Wraps any synchronous callable (passed to `asyncio.to_thread`) in `asyncio.wait_for`. On `asyncio.TimeoutError` or any other `Exception`, prints the retry notification and retries. Raises `TimeoutExhausted` after all attempts fail.

```python
async def api_call_with_retry(fn, timeout, max_attempts=3, label="API call"):
    for attempt in range(1, max_attempts + 1):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        except (asyncio.TimeoutError, Exception) as e:
            if attempt == max_attempts:
                raise TimeoutExhausted(
                    f"{label} failed after {max_attempts} attempts: {e}"
                )
            print(
                f"  [{label}] failed ({e.__class__.__name__}) — "
                f"retrying (attempt {attempt + 1}/{max_attempts})..."
            )
```

### Timeout constants

All timeout values are module-level constants in `pipeline/retry.py`:

| Constant | Value | Applied to |
|----------|-------|-----------|
| `TIMEOUT_SCRIPT_AGENT` | 120s | `script_agent` (may use web search tool) |
| `TIMEOUT_FACT_VALIDATOR` | 60s | `fact_validator` |
| `TIMEOUT_MANIM_AGENT` | 180s | `manim_agent` (16384 max_tokens) |
| `TIMEOUT_CODE_VALIDATOR` | 60s | `code_validator` |
| `TIMEOUT_VISUAL_QA` | 90s | `visual_qa` (5 base64 images) |
| `TIMEOUT_TTS_SEGMENT` | 30s | OpenAI + ElevenLabs per segment |
| `TIMEOUT_TTS_KOKORO` | 120s | Kokoro full call (includes model load) |

---

## Component 2: `subprocess_with_timeout` + adaptive render timeout (in `main.py`)

### `subprocess_with_timeout(cmd, timeout, on_line=None)`

Uses a `threading.Timer` to kill the subprocess after `timeout` seconds. The stdout loop runs on the calling thread — live progress output is preserved. When the timer fires, it sets a `timed_out` flag and calls `process.kill()`, which closes stdout and lets the loop exit naturally.

```python
def subprocess_with_timeout(cmd, timeout, on_line=None):
    timed_out = [False]
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )

    def _kill():
        timed_out[0] = True
        process.kill()

    timer = threading.Timer(timeout, _kill)
    timer.start()
    lines_buffer = collections.deque(maxlen=50)
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
```

Also used for ffmpeg (fixed 120s timeout) and ffprobe in `visual_qa.py` (fixed 60s timeout).

### `_compute_render_timeout(run_id, output_dir)`

Reads `segments.json` (written before render) and counts `self.play()` calls in `scene.py` via the existing `_count_animations()`. Reads `manifest.json` for quality.

**Formula:**

```
timeout = (BASE + animation_count × PER_ANIM + audio_duration × AUDIO_RATIO) × quality_mult
timeout = clamp(timeout, MIN=90, MAX=1200)
```

**Constants** (module-level in `main.py`):

| Constant | Value |
|----------|-------|
| `RENDER_TIMEOUT_BASE` | 60s |
| `RENDER_TIMEOUT_PER_ANIM` | 5s |
| `RENDER_TIMEOUT_AUDIO_RATIO` | 3.0 |
| `RENDER_TIMEOUT_QUALITY_MULT` | `{"low": 0.5, "medium": 1.0, "high": 2.0}` |
| `RENDER_TIMEOUT_MIN` | 90s |
| `RENDER_TIMEOUT_MAX` | 1200s |

Example outputs:

| Scenario | Timeout |
|----------|---------|
| 30s clip, 10 anims, medium | 200s (3.3 min) |
| 60s clip, 22 anims, medium | 350s (5.8 min) |
| 90s clip, 38 anims, high | 1040s (17 min) |
| 10s clip, 3 anims, low (floor) | 90s |

Preview renders use a fixed timeout of 90s (always low quality, always short).

### Render retry loop

`_render()` is split into `_render_once()` (single attempt, returns `(video_path, timed_out)`) and a new `_render()` wrapper that retries up to 3 times:

```
attempt 1 → timeout or non-zero exit
  → delete partial media/ output
  → print "  [render] timed out after Xs — retrying (attempt 2/3)..."
attempt 2 → same
attempt 3 → all fail
  → escalate to user
```

Between attempts, `output/<run_id>/media/` and `output/<run_id>/media_preview/` are deleted (with `shutil.rmtree`, ignore_errors=True) so Manim always starts clean and doesn't skip already-rendered frames from the previous hung attempt.

`_ensure_docker_image()` gets a fixed 600s timeout with no retry — a broken Docker build requires human investigation, not automatic retry.

---

## Component 3: Agent and TTS integration

### Agents (`pipeline/agents/`)

Each agent wraps its `client.messages.create()` call with `api_call_with_retry`. Example for `script_agent`:

```python
from pipeline.retry import api_call_with_retry, TIMEOUT_SCRIPT_AGENT

result = await api_call_with_retry(
    lambda: client.messages.create(...),
    timeout=TIMEOUT_SCRIPT_AGENT,
    label="script_agent",
)
```

All four agents (`script_agent`, `fact_validator`, `manim_agent`, `code_validator`) follow this pattern. `api_call_with_retry` is async (uses `await asyncio.wait_for`), so each agent must become `async def`. LangGraph handles both sync and async node functions — async nodes are awaited directly instead of being dispatched to a thread pool. The underlying `client.messages.create()` remains synchronous (the Anthropic SDK has no async client) and is wrapped in `asyncio.to_thread` inside `api_call_with_retry`. No change to the LangGraph graph wiring is needed — only the agent function signatures change from `def` to `async def`.

### TTS backends (`pipeline/tts/`)

**OpenAI and ElevenLabs:** The per-segment loop becomes:

```python
for segment in segments:
    response = await api_call_with_retry(
        lambda s=segment: openai.audio.speech.create(input=s["text"], ...),
        timeout=TIMEOUT_TTS_SEGMENT,
        label="openai_tts",
    )
```

**Kokoro:** The full synchronous call is wrapped once:

```python
audio_data = await api_call_with_retry(
    lambda: _generate_kokoro_sync(segments),
    timeout=TIMEOUT_TTS_KOKORO,
    label="kokoro_tts",
)
```

### `visual_qa.py`

- ffprobe call → `subprocess_with_timeout(cmd, timeout=60)`
- ffmpeg frame extraction loop → `subprocess_with_timeout(cmd, timeout=30)` per frame
- Claude vision call → `api_call_with_retry(fn, timeout=TIMEOUT_VISUAL_QA, label="visual_qa")`

---

## Escalation Paths

### Render exhaustion (in `main.py`)

After 3 failed render attempts, print a summary and drop into the existing `_handle_interrupt` prompt with a new `retry_render` action:

```
  [render] all 3 attempts failed (timed out after 200s each).

  Enter action (retry_render / abort):
    action:
```

`retry_render` resets the attempt counter and calls `_render()` again. `abort` exits.

### Pipeline exhaustion (in LangGraph nodes)

`TimeoutExhausted` propagating out of a node surfaces through `graph.astream()`. The `run()` loop in `main.py` catches it:

```python
try:
    async for event in graph.astream(input_state, config=config, stream_mode="updates"):
        _print_progress(event)
        if "__interrupt__" in event:
            ...
except TimeoutExhausted as e:
    print(f"\n  [pipeline] {e}")
    resume_cmd = _handle_interrupt(str(e))
    input_state = resume_cmd
```

`_handle_interrupt` already handles `retry_script`, `retry_code`, and `abort` — timeout exhaustion uses the same UX as validation exhaustion. No new prompt format needed.

---

## File Structure

| File | Change |
|------|--------|
| `pipeline/retry.py` | **New** — `TimeoutExhausted`, `api_call_with_retry`, timeout constants |
| `main.py` | `subprocess_with_timeout`, `_compute_render_timeout`, render retry loop, `TimeoutExhausted` catch in `run()`, `threading` import |
| `pipeline/agents/script_agent.py` | Wrap `messages.create()` with `api_call_with_retry`; make async |
| `pipeline/agents/fact_validator.py` | Same |
| `pipeline/agents/manim_agent.py` | Same |
| `pipeline/agents/code_validator.py` | Same |
| `pipeline/tts/openai_tts.py` | Wrap per-segment call with `api_call_with_retry` |
| `pipeline/tts/elevenlabs_tts.py` | Same |
| `pipeline/tts/kokoro_tts.py` | Wrap full sync call with `api_call_with_retry` |
| `pipeline/visual_qa.py` | `subprocess_with_timeout` for ffprobe/ffmpeg; `api_call_with_retry` for Claude |
| `tests/test_retry.py` | **New** — unit tests for `api_call_with_retry` and `TimeoutExhausted` |
| `tests/test_render_timeout.py` | **New** — unit tests for `_compute_render_timeout` and `subprocess_with_timeout` |

---

## What Is Not Retried

- **`_ensure_docker_image()` build** — 600s hard timeout, fail-fast with a clear message. A broken image build needs human investigation.
- **`_handle_interrupt()` / LangGraph `interrupt()`** — user input blocks are intentional; no timeout added.
- **`_check_tools()`** — fast shelling out to `which`; not a hang risk.
