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
  agents/
    script_agent.py     Script generation (Claude)
    fact_validator.py   Fact checking (Claude)
    manim_agent.py      Manim code generation (Claude)
    code_validator.py   Syntax + semantic code review (Claude)
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

### script_agent
- Model: `CLAUDE_MODEL` (claude-sonnet-4-6)
- `max_tokens`: 2048
- Output: `{"script": str, "segments": [{text, estimated_duration_sec}], "needs_web_search": bool}`
- Web search tool enabled when `effort_level == "high"` or `user_approved_search == True`
- Reads `audience` and `tone` from state to inject targeting instructions into user message via `AUDIENCE_INSTRUCTIONS` and `TONE_INSTRUCTIONS` dicts

### fact_validator
- Model: `CLAUDE_MODEL`
- `max_tokens`: 1024
- Output: `{"verdict": "approved"|"needs_revision", "feedback": str}`
- Effort-based instructions: low = light check, medium = spot-check, high = thorough

### manim_agent
- Model: `CLAUDE_MODEL`
- `max_tokens`: 16384 — Manim scenes are long; 4096 was too small
- Output: `{"manim_code": str}`
- Scene class **must** be named `ChalkboardScene`
- System prompt includes Manim v0.20.1 API pitfalls (see below)
- Reads `theme` from state to inject a color palette block into user message via `THEME_SPECS` dict (background color, primary/accent/secondary palette)

### code_validator
- Model: `CLAUDE_MODEL`
- `max_tokens`: 2048
- Fast path: `ast.parse()` syntax check before calling Claude
- Output: `{"verdict": "approved"|"needs_revision", "feedback": str}`

---

## Manim v0.20.1 known API pitfalls

These are baked into `manim_agent`'s system prompt. When Claude generates code that fails to render, add the pattern here:

- **`Brace.get_text(*text)`** does not accept `font_size` — scale the returned object instead: `t = brace.get_text('x'); t.scale(0.8)`
- **`VGroup(*self.mobjects)`** fails if `self.mobjects` contains non-VMobject items (camera, etc.) — use `*[FadeOut(m) for m in self.mobjects]`
- **`VGroup.arrange()`** returns `None` — don't chain, assign first
- Always pass `run_time` as a keyword arg: `self.play(anim, run_time=1.0)`

When Manim rendering fails, read the traceback from `docker run` output. Most failures are API misuse in the generated code. Patch `output/<run_id>/scene.py` to verify the fix, then add the pattern to `manim_agent.py`'s system prompt.

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
2. _ensure_docker_image() — build chalkboard-render image if not present (once)
3. docker run ...         — Manim render inside container, prints RENDER_COMPLETE:<path>
4. ffmpeg merge           — host-side: video + voiceover.wav → final.mp4
```

**The audio merge runs on the host** (not in Docker) because Linux ffmpeg's native AAC encoder omits the encoder-delay edit list (`elst` box) that QuickTime requires, resulting in silent playback. Host ffmpeg (macOS/Windows/Linux native builds) produces standard AAC-LC that is compatible with all browsers, QuickTime, and native players.

`--no-render` flag skips steps 1–4 and only runs the pipeline (useful for testing or when Docker is unavailable).

---

## Checkpointing and resume

LangGraph uses `AsyncSqliteSaver` (stored in `pipeline_state.db`) to checkpoint after every node. On resume (`--run-id`), execution resumes from the last successful checkpoint.

**Important:** If a node raises an unhandled exception, LangGraph does not save that node's output — the checkpoint stays at the pre-node state. The next resume re-runs the failed node from scratch. This means retrying after a Python-level bug (not a validation failure) does not increment attempt counters.

---

## escalate_to_user

`escalate_to_user` uses LangGraph's `interrupt()` to pause the graph and wait for user input. It **must be `async`**. If declared as a sync function, LangGraph wraps it in `run_in_executor` (thread pool), which drops the `contextvars` context that `interrupt()` requires — causing `RuntimeError: Called get_config outside of a runnable context`.

---

## Testing

```bash
pytest                    # run all tests
pytest tests/test_graph.py  # specific file
```

Tests mock the Anthropic client at the graph node level (`pipeline.graph.script_agent`, etc.), not at `anthropic.Anthropic`. Patching `anthropic.Anthropic` fails because the module reference is shared across imports.

Graph integration tests (`test_graph.py`) use `MemorySaver` (in-memory checkpointer) so they don't touch the filesystem.

---

## Known issues / future work

- **Animation duration doesn't match voiceover**: The Manim scene runs longer than the narration. The `segments.json` contains per-segment timing, but manim_agent doesn't yet use these to pace the animation precisely.
- **Kokoro multi-voice**: Currently uses a single voice. Could be extended to use different voices for different segments.
- **ElevenLabs voice selection**: Voice ID is hardcoded to "George". Could be a config option.
- **High effort web search**: `needs_web_search` is returned by script_agent but the approval gate for web search is not fully wired — `user_approved_search` never gets set to `True` in the current main.py.
- **No streaming output**: Claude responses are synchronous. Could stream script and code generation for faster perceived progress.
