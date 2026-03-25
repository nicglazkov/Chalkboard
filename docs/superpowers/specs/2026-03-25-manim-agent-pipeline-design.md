# Manim Agent Pipeline — Design Spec

**Date:** 2026-03-25
**Project:** Chalkboard
**Status:** Approved for implementation

---

## Overview

A multi-agent LangGraph pipeline that takes a user-provided topic and produces a ready-to-render Manim CE animation script plus a voiceover audio file. All generation and validation happens in the pipeline; rendering is deferred to a Docker container that runs independently after the pipeline writes approved files to `/output/`.

---

## Decisions Made

| Concern | Choice | Rationale |
|---|---|---|
| Manim version | Manim Community Edition v0.20.1 | Headless Cairo renderer, official Docker image, rich LLM training corpus |
| Docker base image | `manimcommunity/manim:v0.20.1` (pinned) | Zero dependency management, updated per release, extendable via `FROM` |
| TTS default | Kokoro TTS (local, Apache 2.0) | Free, offline, #2 TTS Arena quality, `pip install kokoro` |
| TTS alternatives | OpenAI TTS (`gpt-4o-mini-tts`), ElevenLabs | Swappable via `TTS_BACKEND` config |
| TTS integration | Option A: pipeline generates audio, Manim uses `self.wait()` | Full pipeline visibility, no custom adapter, audio reusable across re-renders |
| Output format | MP4 (H.264), WAV audio (merged by Docker ffmpeg step) | Universal compatibility, WAV is lossless and trivial to merge |
| Dev resolution | 720p / 30fps (`-qm`) | Fast iteration |
| Prod resolution | 1080p / 60fps (`-qh`) | 3Blue1Brown standard |
| LangGraph checkpointing | `AsyncSqliteSaver` (MVP), upgrade path to `AsyncPostgresSaver` | Zero infrastructure, crash recovery, one-import upgrade |
| Structured outputs | Anthropic SDK `output_config` with `json_schema` + Pydantic | Native typed responses, no tool-forcing workarounds |
| Graph architecture | Flat graph with conditional edges (Approach 1) | Simplest to implement, trace, and debug |

---

## Graph Structure

```
START
  └─→ script_agent
        └─→ fact_validator
              ├─→ [pass] manim_agent
              │             └─→ code_validator
              │                   ├─→ [pass] render_trigger → END
              │                   ├─→ [fail, code_attempts < 3] manim_agent
              │                   └─→ [fail, code_attempts ≥ 3] escalate_to_user → END
              ├─→ [fail, script_attempts < 3] script_agent
              └─→ [fail, script_attempts ≥ 3] escalate_to_user → END
```

Two human-in-the-loop pause points using LangGraph's `interrupt()` + `Command(resume=value)`:

1. **Web search gate** — before `script_agent` proceeds if `needs_web_search=True` and `user_approved_search=False`
2. **Escalation** — terminal node when either retry budget is exhausted

Both require `AsyncSqliteSaver` as the checkpointer (same `thread_id` for resume).

---

## State Schema

```python
class PipelineState(TypedDict):
    topic: str
    script: str
    script_segments: list[dict]       # [{text: str, estimated_duration_sec: float}]
    manim_code: str
    script_attempts: int              # 0–3; independent budget for script loop
    code_attempts: int                # 0–3; independent budget for code loop
    fact_feedback: str | None
    code_feedback: str | None
    effort_level: Literal["low", "medium", "high"]
    needs_web_search: bool
    user_approved_search: bool
    status: Literal["drafting", "validating", "needs_user_input", "approved", "failed"]
```

Note: `script_attempts` and `code_attempts` are independent — a script that takes 2 tries does not reduce the Manim code's 3-attempt budget.

---

## Agent Responsibilities

### `script_agent`
- **Inputs:** `topic`, `effort_level`, `fact_feedback` (None on first run), `user_approved_search`
- Generates a full educational narration, segmented into chunks with estimated duration (`word_count / 2.5` seconds per segment)
- On revision: full rewrite incorporating `fact_feedback` — never patches
- On `effort=high` or `user_approved_search=True`: uses Claude `web_search` tool
- On `effort=low`: skips web search, lighter prose
- **Outputs:** `script`, `script_segments`, `needs_web_search`, `status=validating`

### `fact_validator`
- **Inputs:** `script`, `effort_level`
- `effort=low`: flags obvious errors only
- `effort=medium`: spot-checks key claims
- `effort=high`: thorough review, flags anything uncertain
- Returns structured JSON: `{verdict: "approved"|"needs_revision", feedback: str}`
- **Outputs:** `fact_feedback`, increments `script_attempts` on fail

### `manim_agent`
- **Inputs:** `script`, `script_segments` (with durations), `code_feedback` (None on first run)
- Generates a single Manim CE `Scene` subclass; animation blocks separated by `self.wait(estimated_duration_sec)` aligned to narration segments; animations use `self.play(..., run_time=X)`
- On revision: full rewrite incorporating `code_feedback` — never patches
- **Outputs:** `manim_code`, `status=validating`

### `code_validator`
- **Inputs:** `manim_code`, `script`
- Step 1 (local, fast): `ast.parse(manim_code)` syntax check — if this fails, return immediately without a Claude call
- Step 2 (Claude): semantic review — does the animation visualize the script? Are Manim CE APIs used correctly?
- Returns structured JSON: `{verdict: "approved"|"needs_revision", feedback: str}`
- **Outputs:** `code_feedback`, increments `code_attempts` on fail

### `render_trigger`
- **Inputs:** approved `script`, `script_segments`, `manim_code`
- Calls TTS provider (Kokoro default, sync wrapped in `asyncio.to_thread`; OpenAI/ElevenLabs cloud alternatives)
- Writes to `output/{run_id}/`:
  - `scene.py` — Manim Python code
  - `voiceover.wav` — generated audio (24kHz, float32 via `soundfile`)
  - `segments.json` — `[{text, estimated_duration_sec}]`
  - `script.txt` — human-readable narration
- **Outputs:** `status=approved`

### `escalate_to_user`
- Surfaces last feedback + attempt history in readable CLI format
- Uses `interrupt()` to pause; user can provide guidance, change topic, or abort
- On resume: routes back to `script_agent` or `manim_agent` with reset attempt counter, or exits

---

## TTS Integration

Kokoro `KPipeline` is synchronous. In the async pipeline, it is called via `asyncio.to_thread`. Output is numpy float32 arrays at 24kHz, written to `.wav` using `soundfile`.

Swapping providers is a single `TTS_BACKEND` env-var change:
- `"kokoro"` → `KPipeline` (local)
- `"openai"` → `openai.audio.speech.create()` (cloud)
- `"elevenlabs"` → `elevenlabs.generate()` (cloud)

The TTS abstraction is a simple function `generate_audio(text, segments) -> Path` that all three backends implement.

---

## Docker Render Container

The pipeline writes files only. Docker runs separately after the pipeline completes:

```dockerfile
FROM manimcommunity/manim:v0.20.1
# Extend here for additional LaTeX packages if needed:
# RUN tlmgr install <pkg>
```

Two-step render script:
```bash
# Step 1: render Manim scene
manim -qm /output/{run_id}/scene.py MyScene

# Step 2: merge with voiceover
ffmpeg -i /output/{run_id}/MyScene.mp4 \
       -i /output/{run_id}/voiceover.wav \
       -c:v copy -c:a aac \
       /output/{run_id}/final.mp4
```

Dev quality: `-qm` (720p/30fps). Production: `-qh` (1080p/60fps), configurable.

---

## CLI Interface

```bash
python main.py --topic "explain how B-trees work" --effort medium
```

- Streams step-by-step progress to stdout
- Pauses at web search gate with `[y/N]` prompt
- Shows validation feedback on each retry loop
- Pauses at escalation with blockers listed and options to retry, skip, or abort
- Confirms before triggering render (writing to `/output/`)

---

## Config

`config.py` with environment variable overrides:

```python
TTS_BACKEND   = os.getenv("TTS_BACKEND", "kokoro")     # "kokoro" | "openai" | "elevenlabs"
MANIM_QUALITY = os.getenv("MANIM_QUALITY", "medium")   # "low" | "medium" | "high"
DEFAULT_EFFORT = os.getenv("DEFAULT_EFFORT", "medium")
OUTPUT_DIR    = os.getenv("OUTPUT_DIR", "./output")
CHECKPOINT_DB = os.getenv("CHECKPOINT_DB", "pipeline_state.db")
CLAUDE_MODEL  = "claude-sonnet-4-6"
```

---

## File Structure

```
manim_agent_pipeline/
├── pipeline/
│   ├── state.py                  # PipelineState TypedDict
│   ├── agents/
│   │   ├── script_agent.py
│   │   ├── manim_agent.py
│   │   ├── fact_validator.py
│   │   ├── code_validator.py
│   │   └── orchestrator.py       # escalate_to_user node
│   ├── graph.py                  # LangGraph graph + conditional edges
│   ├── render_trigger.py         # TTS generation + file writing
│   └── tts/
│       ├── base.py               # generate_audio() interface
│       ├── kokoro_tts.py
│       ├── openai_tts.py
│       └── elevenlabs_tts.py
├── output/                       # approved files land here (gitignored)
├── docker/
│   └── Dockerfile
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-03-25-manim-agent-pipeline-design.md
├── main.py                       # CLI entrypoint
├── config.py
└── README.md
```

---

## Key Dependencies

**Pipeline environment (host):**
```
langgraph>=1.1.3
langgraph-checkpoint-sqlite>=3.0.3
anthropic>=0.49.0
kokoro>=0.9.4
soundfile
openai                   # optional, for OpenAI TTS backend
elevenlabs               # optional, for ElevenLabs backend
```

**System (host, for Kokoro):**
```
espeak-ng
```

**Docker render container:**
```
manimcommunity/manim:v0.20.1  (includes ffmpeg via PyAV, Cairo, Pango, minimal TeX Live)
```

---

## What Is Not In Scope (MVP)

- Word-level voice sync (upgrade: `manim-voiceover` with custom Kokoro adapter)
- Multi-language support
- Interactive scene preview
- PostgreSQL checkpointing (upgrade path: one import swap)
- Parallel validation of script and code
- CI/CD or cloud deployment
