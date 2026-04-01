# Chalkboard — Architecture & Contributor Guide

This document is the authoritative reference for anyone (human or AI agent) contributing to Chalkboard. It covers architecture, design decisions, known pitfalls, and how to extend the project.

---

## What this project does

Chalkboard takes a topic string and produces a narrated Manim animation. The pipeline runs fully automatically with retry logic at each stage.

```
main.py
  └─ LangGraph pipeline (pipeline/graph.py)
       ├─ init              — normalize state, set run_id
       ├─ script_agent      — Claude writes narration script
       ├─ fact_validator    — Claude fact-checks the script
       ├─ manim_agent       — Claude writes Manim scene code
       ├─ code_validator    — syntax check + Claude semantic review
       ├─ render_trigger    — TTS → audio, write output files
       └─ escalate_to_user  — interrupt() when max retries hit
```

After the pipeline completes, the user runs Docker to render the Manim animation, then merges the voiceover with local ffmpeg.

---

## Repo layout

```
pipeline/
  graph.py           LangGraph state machine + routing functions
  state.py           PipelineState TypedDict, ValidationResult
  render_trigger.py  Calls TTS, writes output files
  retry.py           TimeoutExhausted, api_call_with_retry, timeout constants
  context.py         collect_files, load_context_blocks, measure_context
  agents/
    script_agent.py     Script generation (Claude) — async
    fact_validator.py   Fact checking (Claude) — async
    manim_agent.py      Manim code generation (Claude) — async
    code_validator.py   Syntax + semantic code review (Claude) — async
    orchestrator.py     escalate_to_user node (LangGraph interrupt)
  tts/
    base.py             Backend registry (get_backend)
    kokoro_tts.py       Local TTS (PyTorch)
    openai_tts.py       OpenAI TTS API
    elevenlabs_tts.py   ElevenLabs TTS API
docker/
  Dockerfile          Extends manimcommunity/manim:v0.20.1
  render.sh           Manim render entrypoint (no audio merge)
tests/                One test file per module
config.py             Env var loading + CLAUDE_MODEL constant
main.py               CLI entry point, async graph runner
```

---

## State schema

`PipelineState` (TypedDict in `pipeline/state.py`) — every field:

| Field | Type | Description |
|-------|------|-------------|
| `topic` | str | User-provided topic |
| `run_id` | str | UUID for this run, used as output directory name |
| `effort_level` | str | `"low"` / `"medium"` / `"high"` |
| `audience` | str | `"beginner"` / `"intermediate"` / `"expert"` |
| `tone` | str | `"casual"` / `"formal"` / `"socratic"` |
| `theme` | str | `"chalkboard"` / `"light"` / `"colorful"` |
| `script` | str | Full narration script |
| `script_segments` | list[dict] | `[{"text": str, "estimated_duration_sec": float}]` |
| `manim_code` | str | Complete Python source for the Manim scene |
| `script_attempts` | int | Number of times script has been revised (starts 0) |
| `code_attempts` | int | Number of times Manim code has been revised (starts 0) |
| `fact_feedback` | str \| None | Feedback from fact_validator; **None means approved** |
| `code_feedback` | str \| None | Feedback from code_validator; **None means approved** |
| `needs_web_search` | bool | script_agent flagged it wants web search |
| `user_approved_search` | bool | User approved web search (unused in current routing) |
| `context_file_paths` | list[str] | Paths of loaded context files; empty list if none. Informational only — never read by agents. |
| `status` | str | `"drafting"` / `"validating"` / `"approved"` / `"failed"` |

### Critical invariant: None = approved

Both `fact_feedback` and `code_feedback` use `None` as the approval signal. The routing functions check `not state.get("feedback")` to decide whether to proceed. If a validator returns any truthy string (even `"Looks good!"`), routing treats it as a failure and retries. **Always return `None` on approval.**

---

## Routing logic

```python
# pipeline/graph.py

def _after_fact_validator(state):
    if not state.get("fact_feedback"):   # None = approved → proceed
        return "manim_agent"
    if state["script_attempts"] >= 3:    # too many failures → escalate
        return "escalate_to_user"
    return "script_agent"                # retry

def _after_code_validator(state):
    if not state.get("code_feedback"):   # None = approved → proceed
        return "render_trigger"
    if state["code_attempts"] >= 3:      # too many failures → escalate
        return "escalate_to_user"
    return "manim_agent"                 # retry
```

**Do not** check attempt counters to determine approval — check the feedback field. Approval can happen on attempt 1, 2, or 3; the counter just tracks when to give up.

---

## Agent prompts and output formats

All agents use Claude with `output_config` JSON schema (structured outputs). The schema must have `"additionalProperties": false` on **every nested object**, not just the top level — the API rejects schemas that omit this on nested objects.

All four agents are `async def` and wrap their `messages.create()` call with `api_call_with_retry` from `pipeline/retry.py`. LangGraph awaits async nodes directly.

### script_agent
- Model: `CLAUDE_MODEL` (claude-sonnet-4-6)
- `max_tokens`: 4096
- Timeout: `TIMEOUT_SCRIPT_AGENT` = 120s (may use web search tool)
- Output: `{"script": str, "segments": [{text, estimated_duration_sec}], "needs_web_search": bool}`
- Web search tool enabled when `effort_level == "high"` or `user_approved_search == True`
- Reads `audience` and `tone` from state to inject targeting instructions into user message via `AUDIENCE_INSTRUCTIONS` and `TONE_INSTRUCTIONS` dicts

### fact_validator
- Model: `CLAUDE_MODEL`
- `max_tokens`: 1024
- Timeout: `TIMEOUT_FACT_VALIDATOR` = 60s
- Output: `{"verdict": "approved"|"needs_revision", "feedback": str}`
- Effort-based instructions: low = light check, medium = spot-check, high = thorough

### manim_agent
- Model: `CLAUDE_MODEL`
- `max_tokens`: 16384 — Manim scenes are long; 4096 was too small
- Timeout: `TIMEOUT_MANIM_AGENT` = 180s
- Output: `{"manim_code": str}`
- Scene class **must** be named `ChalkboardScene`
- System prompt includes Manim v0.20.1 API pitfalls (see below)
- Reads `theme` from state to inject a color palette block into user message via `THEME_SPECS` dict (background color, primary/accent/secondary palette)

### code_validator
- Model: `CLAUDE_MODEL`
- `max_tokens`: 2048
- Timeout: `TIMEOUT_CODE_VALIDATOR` = 60s
- Fast path: `ast.parse()` syntax check before calling Claude (no timeout needed — pure Python)
- Output: `{"verdict": "approved"|"needs_revision", "feedback": str}`

---

## Manim v0.20.1 known API pitfalls

These are baked into `manim_agent`'s system prompt. When Claude generates code that fails to render, add the pattern here:

- **`Brace.get_text(*text)`** does not accept `font_size` — scale the returned object instead: `t = brace.get_text('x'); t.scale(0.8)`
- **`VGroup(*self.mobjects)`** fails if `self.mobjects` contains non-VMobject items (camera, etc.) — use `*[FadeOut(m) for m in self.mobjects]`
- **`VGroup.arrange()`** returns `None` — don't chain, assign first
- Always pass `run_time` as a keyword arg: `self.play(anim, run_time=1.0)`
- **Pointer labels + descriptive text below an array**: use `buff=0.85` or more so pointer triangles/labels don't overlap the description line
- **Animating a label to track a pointer**: never use `obj.copy().next_to(...)` inside `.animate` — `.animate` captures positions before the frame; use `boxes[i].get_top() + UP * 0.55` or similar absolute offsets instead

When Manim rendering fails, read the traceback from `docker run` output. Most failures are API misuse in the generated code. Patch `output/<run_id>/scene.py` to verify the fix, then add the pattern to `manim_agent.py`'s system prompt.

---

## Timeout & retry infrastructure

All indefinite hang points are protected. Two mechanisms:

**`api_call_with_retry(fn, timeout, max_attempts=3, label)` — `pipeline/retry.py`**

Wraps any sync callable in `asyncio.to_thread` with `asyncio.wait_for`. On any exception or timeout, prints `  [label] failed (ExcType) — retrying (attempt N/M)...` and retries. Raises `TimeoutExhausted` after all attempts. Used by all agents, all TTS backends, and `visual_qa`. `TimeoutExhausted` propagates through LangGraph's `graph.astream()` and is caught in `run()`, which routes it through `_handle_interrupt` (same UX as validation exhaustion).

**`subprocess_with_timeout(cmd, timeout, on_line=None)` — `main.py`**

Uses `threading.Timer` to call `process.kill()` after `timeout` seconds. The stdout loop runs on the calling thread (live progress preserved). Returns `(returncode, lines_buffer, timed_out)`. Used for Docker render calls.

**Timeout constants** (all in `pipeline/retry.py`):

| Constant | Value | Used by |
|----------|-------|---------|
| `TIMEOUT_SCRIPT_AGENT` | 120s | script_agent |
| `TIMEOUT_FACT_VALIDATOR` | 60s | fact_validator |
| `TIMEOUT_MANIM_AGENT` | 180s | manim_agent |
| `TIMEOUT_CODE_VALIDATOR` | 60s | code_validator |
| `TIMEOUT_VISUAL_QA` | 90s | visual_qa |
| `TIMEOUT_TTS_SEGMENT` | 30s | OpenAI, ElevenLabs (per segment) |
| `TIMEOUT_TTS_KOKORO` | 120s | Kokoro (full call) |

---

## TTS backends

All backends implement the same async contract:

```python
async def generate_audio(
    segments: list[dict],   # [{"text": str, "estimated_duration_sec": float}]
    output_path: Path,      # write voiceover.wav here
) -> tuple[Path, list[float]]:  # (wav_path, actual_durations_per_segment)
```

**Critical:** OpenAI and ElevenLabs TTS APIs return streaming WAV/PCM responses with invalid or overflowed headers. Do not concatenate raw response bytes. Instead:
- **OpenAI**: response is WAV with `nframes=0xFFFFFFFF` header; use `wave.open()` to extract PCM, write a clean WAV
- **ElevenLabs**: request `output_format="pcm_24000"` (raw PCM, no headers); write WAV manually
- **Kokoro**: produces clean PCM chunks; concatenate directly

### Adding a new TTS backend

1. Create `pipeline/tts/yourbackend_tts.py` implementing `generate_audio(segments, output_path)`
2. Register it in `pipeline/tts/base.py` `get_backend()` dispatch
3. Add to the TTS table in `README.md`
4. Add tests in `tests/test_yourbackend_tts.py`

---

## Output files (per run)

`render_trigger.py` writes to `output/<run_id>/`:

| File | Contents |
|------|----------|
| `scene.py` | Complete Manim Python source |
| `voiceover.wav` | Concatenated TTS audio for all segments |
| `segments.json` | `[{"text": str, "actual_duration_sec": float}]` — uses actual TTS durations, not estimates |
| `script.txt` | Full narration script as plain text |
| `manifest.json` | `{run_id, topic, scene_class_name, quality, timestamp}` |

`manifest.json` is read by `docker/render.sh` to know which class to render and at what quality.

---

## Render workflow

`main.py` orchestrates the full pipeline end-to-end after the graph completes:

```
1. _check_tools()         — verify docker and ffmpeg are in PATH
2. _ensure_docker_image() — build chalkboard-render image if not present (once, timeout 600s)
3. docker run ...         — Manim render inside container, prints RENDER_COMPLETE:<path>
4. ffmpeg merge           — host-side: video + voiceover.wav → final.mp4 (timeout 120s)
```

**The audio merge runs on the host** (not in Docker) because Linux ffmpeg's native AAC encoder omits the encoder-delay edit list (`elst` box) that QuickTime requires, resulting in silent playback. Host ffmpeg (macOS/Windows/Linux native builds) produces standard AAC-LC that is compatible with all browsers, QuickTime, and native players.

`--no-render` flag skips steps 1–4 and only runs the pipeline (useful for testing or when Docker is unavailable).

### Adaptive render timeout

Step 3 uses `_compute_render_timeout(run_id, output_dir)` to set a per-run timeout instead of a fixed value:

```
timeout = (BASE + anim_count × PER_ANIM + audio_duration × AUDIO_RATIO) × quality_mult
timeout = clamp(timeout, MIN=90s, MAX=1200s)
```

Constants (in `main.py`): `BASE=60s`, `PER_ANIM=5s`, `AUDIO_RATIO=3.0`, `quality_mult={low:0.5, medium:1.0, high:2.0}`. Animation count comes from `_count_animations(scene.py)`; audio duration from `segments.json`.

### Render retry

`_render()` and `_render_preview()` each attempt the Docker render up to 3 times. Between attempts, `media/` and `media_preview/` are deleted so Manim starts clean. On the third failure, `RenderFailed` propagates to `main()`, which prompts the user with `retry_render / abort`. `subprocess_with_timeout` (threading.Timer + process.kill) is used for all Docker subprocess calls to guarantee the process is killed on timeout.

---

## Visual QA

After each full render, `main.py` runs `_run_qa_loop()` which calls `_run_visual_qa()` which calls `pipeline/visual_qa.py`.

**Frame extraction (`_extract_frames`):** uses `ffprobe` to get duration, then `ffmpeg` to grab evenly-spaced frames. Sampling rate is controlled by `--qa-density`:

| Density | `seconds_per_frame` | `max_frames` |
|---------|--------------------:|-------------:|
| `zero`  | — (skip QA)         | —            |
| `normal` (default) | 30s | 10 |
| `high`  | 15s                 | 20           |

Minimum is always 5 frames regardless of density. Formula: `n_frames = max(5, min(int(duration / spf), max_frames))`.

**Sampling formula:** `t = duration * i / n_frames` — samples at 0%, 1/n, …, (n-1)/n of the video duration. Never seeks to the exact end (ffmpeg silently produces no frame at `t == duration`).

**Claude review:** `visual_qa()` sends frames + optional `scene_code` to Claude with a structured-output schema. Returns `{"passed": bool, "issues": [{severity, description}]}`. When `scene_code` is included, Claude can reference the specific method/construct responsible for each issue.

**QA loop (`_run_qa_loop`):** if QA finds `error`-severity issues, calls `_qa_regenerate_scene()` (re-invokes `manim_agent` with issues as `code_feedback`), deletes old render artifacts, and re-renders. Up to 2 regeneration attempts. Warnings do not trigger regeneration.

**`--qa-density zero`** skips all of the above — `_run_visual_qa` returns `None` immediately.

---

## Checkpointing and resume

LangGraph uses `AsyncSqliteSaver` (stored in `pipeline_state.db`) to checkpoint after every node. On resume (`--run-id`), execution resumes from the last successful checkpoint.

**Important:** If a node raises an unhandled exception, LangGraph does not save that node's output — the checkpoint stays at the pre-node state. The next resume re-runs the failed node from scratch. This means retrying after a Python-level bug (not a validation failure) does not increment attempt counters.

---

## escalate_to_user

`escalate_to_user` is an `async def` LangGraph node that prompts the user when max retries are hit. It uses `asyncio.to_thread(input, prompt)` (not LangGraph's `interrupt()`) to read from stdin without blocking the event loop. On `EOFError` (non-interactive stdin), it defaults to `"abort"`.

**Why not `interrupt()`:** LangGraph 1.1.3's `interrupt()` calls `get_config()` which requires Python 3.11+ in async nodes — on Python 3.10 `var_child_runnable_config.get()` returns `None`, causing `RuntimeError: Called get_config outside of a runnable context`. The `asyncio.to_thread` approach works on 3.10+.

The node **must be `async def`**. LangGraph checks `inspect.iscoroutinefunction()` to decide whether to `await` a node — a sync wrapper would run in a thread pool, which drops the contextvars context.

---

## Testing

```bash
pytest                    # run all tests
pytest tests/test_graph.py  # specific file
```

Tests mock the Anthropic client at the graph node level (`pipeline.graph.script_agent`, etc.), not at `anthropic.Anthropic`. Patching `anthropic.Anthropic` fails because the module reference is shared across imports.

Graph integration tests (`test_graph.py`) use `MemorySaver` (in-memory checkpointer) so they don't touch the filesystem.

**Async agent mocks:** Since all four agents are now `async def`, graph tests must use `async def mock_agent(state, **kw)` functions (patched via `new=mock_agent`), not `AsyncMock`. LangGraph checks `inspect.iscoroutinefunction()` to decide whether to `await` a node — `AsyncMock` instances fail this check and cause the node to return a coroutine object instead of a result.

---

## Context injection

Users can pass local files as source material via `--context path` (repeatable) and `--context-ignore pattern` (repeatable). The pipeline loads the files before the graph runs, reports token usage, and injects the content into `script_agent` and `manim_agent`.

**Architecture:** preprocessing in `main.py` → `pipeline/context.py` → agents as a parameter. Content blocks are never stored in `PipelineState` (only file paths are stored). LangGraph checkpointing is unaffected.

**`pipeline/context.py`** — three public functions:

- `collect_files(paths, ignore_patterns=None) -> list[Path]` — walks directories recursively, applies `.gitignore` via `pathspec`, applies extra patterns, skips hidden directories, deduplicates. Raises `FileNotFoundError` for missing paths.
- `load_context_blocks(files) -> list[dict]` — converts files to Anthropic content blocks. Text/code → `text` blocks; images → base64 `image` blocks; PDFs → base64 `document` blocks; `.docx` → python-docx paragraph extraction. Each file gets a `--- file: <path> ---` label block followed by the content block.
- `measure_context(blocks, client) -> tuple[int, int]` — returns `(token_count, context_window)` via `client.messages.count_tokens` and `client.models.retrieve`. Nothing hardcoded.

**Token reporting** (in `_report_context`, `main.py`): always prints the count when `--context` is passed. Prompts if tokens > 10k. Hard-exits if context exceeds 90% of the model context window. If the count API call fails, prints a warning and proceeds.

**Agent integration:** `build_graph(context_blocks=None)` creates async closure wrappers around `script_agent` and `manim_agent` when `context_blocks` is truthy, so LangGraph's `inspect.iscoroutinefunction()` check passes on the closures. On retry loops (fact/code validation failures), the same closures are invoked — context is preserved automatically.

**PDF beta header:** when any context block has `type == "document"`, agents initialize `anthropic.Anthropic(default_headers={"anthropic-beta": "pdfs-2024-09-25"})`.

**Resume behavior:** `context_blocks` is not in `PipelineState`. On `--run-id` resume, re-pass `--context` to inject source material. If `--run-id` is used without `--context`, a note is printed.

---

## Known issues / future work

- **Animation duration doesn't match voiceover**: The Manim scene runs longer than the narration. The `segments.json` contains per-segment timing, but manim_agent doesn't yet use these to pace the animation precisely.
- **Kokoro multi-voice**: Currently uses a single voice. Could be extended to use different voices for different segments.
- **ElevenLabs voice selection**: Voice ID is hardcoded to "George". Could be a config option.
- **High effort web search**: `needs_web_search` is returned by script_agent but the approval gate for web search is not fully wired — `user_approved_search` never gets set to `True` in the current main.py.
- **No streaming output**: Claude responses are synchronous. Could stream script and code generation for faster perceived progress.
