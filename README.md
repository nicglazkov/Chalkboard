# Chalkboard

Turn any topic into a narrated, animated explainer video — fully automated.

```
topic → script → fact-check → Manim animation → code review → voiceover → final.mp4
```

Chalkboard is a multi-agent LangGraph pipeline powered by Claude. It writes an educational script, validates the facts, generates Manim animation code, validates the code, synthesizes a voiceover, and renders everything to video. Each stage has automatic retry logic; when it gets stuck it asks you for guidance.

---

## Quick start

### 1. Clone and install

**Prerequisites:** Python 3.10+, [Docker](https://docker.com), [ffmpeg](https://ffmpeg.org) (`brew install ffmpeg` / `apt install ffmpeg`)

```bash
git clone https://github.com/nicglazkov/Chalkboard.git
cd Chalkboard
pip install -r requirements.txt
```

### 2. Set up API keys

```bash
cp .env.example .env
```

Open `.env` and fill in your keys:

```
ANTHROPIC_API_KEY=sk-ant-...
TTS_BACKEND=openai          # see TTS section below
OPENAI_API_KEY=sk-...       # if using TTS_BACKEND=openai
```

### 3. Run

```bash
python main.py --topic "explain how B-trees work" --effort medium
```

That's it. The pipeline runs, renders the animation in Docker, and merges the voiceover — outputting `output/<run-id>/final.mp4`.

> **First run only:** Docker will build the render image automatically (~30s). Subsequent runs use the cached image.

> **High quality:** Set `MANIM_QUALITY=high` in `.env` for 1080p60 output.

---

## CLI flags

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--topic` | **Yes** | — | Topic to explain, e.g. `"how B-trees work"` |
| `--effort` | No | `medium` | Validation thoroughness — see [Effort levels](#effort-levels) |
| `--audience` | No | `intermediate` | Target audience: `beginner`, `intermediate`, `expert` |
| `--tone` | No | `casual` | Narration tone: `casual`, `formal`, `socratic` |
| `--theme` | No | `chalkboard` | Visual color theme: `chalkboard`, `light`, `colorful` |
| `--run-id` | No | auto | Resume a previous run using its ID |
| `--preview` | No | off | Render a fast low-quality preview (480p15) to `preview.mp4` instead of the full HD render |
| `--no-render` | No | off | Run the AI pipeline only — skip Docker render and ffmpeg merge |
| `--verbose` | No | off | Stream raw Docker/Manim output to the terminal while rendering |

> `--verbose` and `--preview` cannot be combined.

After a full render, Chalkboard automatically runs a visual quality check: it samples 5 frames from `final.mp4` and flags any overlapping elements, off-screen text, or readability issues.

---

## TTS backends

| Backend | Quality | Cost | Requires |
|---------|---------|------|----------|
| `openai` | Great | API | `OPENAI_API_KEY` |
| `elevenlabs` | Great | API | `pip install elevenlabs`, `ELEVENLABS_API_KEY` |
| `kokoro` | Best | Free (local) | PyTorch ≥ 2.4, `espeak-ng` — **not available on Intel Macs** |

Set `TTS_BACKEND` in your `.env` file. Default is `kokoro`.

> **Intel Mac users:** PyTorch ≥ 2.4 has no x86_64 macOS wheels. Use `openai` or `elevenlabs`.
>
> To install `espeak-ng` for Kokoro: `brew install espeak-ng` / `apt install espeak-ng`

---

## Effort levels

`--effort` controls how thorough the validation is and whether web search is used.

| Level | Fact-check | Web search | Segments |
|-------|-----------|------------|---------|
| `low` | Light — obvious errors only | Never | 3–4 |
| `medium` (default) | Spot-check key claims | With approval | 4–6 |
| `high` | Thorough | Always enabled | 5–8 |

---

## Resuming a crashed run

Every run is checkpointed. If it crashes or you abort, resume with the same run ID:

```bash
python main.py --topic "..." --run-id <previous-run-id>
```

### Preview → full render workflow

Run `--preview` first to quickly check the visuals at low quality, then do the full HD render with `--run-id` (the pipeline result is already checkpointed — it won't re-run):

```bash
# Step 1: generate script + animation, render preview
python main.py --topic "how B-trees work" --preview
# → output/<run-id>/preview.mp4 (480p, fast)

# Step 2: full HD render (pipeline skipped — uses checkpoint)
python main.py --topic "how B-trees work" --run-id <run-id>
# → output/<run-id>/final.mp4 (full quality + visual QA)
```

---

## Configuration

All settings can be overridden via `.env` or environment variables:

| Variable | Default | Options |
|----------|---------|---------|
| `TTS_BACKEND` | `kokoro` | `kokoro`, `openai`, `elevenlabs` |
| `MANIM_QUALITY` | `medium` | `low`, `medium`, `high` |
| `DEFAULT_EFFORT` | `medium` | `low`, `medium`, `high` |
| `OUTPUT_DIR` | `./output` | any path |
| `CHECKPOINT_DB` | `pipeline_state.db` | any path |

---

## Development

### Run tests

```bash
pytest
```

### Project structure

```
pipeline/
  agents/         # script_agent, fact_validator, manim_agent, code_validator, orchestrator
  tts/            # kokoro, openai, elevenlabs backends
  graph.py        # LangGraph state machine
  state.py        # PipelineState TypedDict + ValidationResult
  render_trigger.py  # writes output files, calls TTS
docker/
  Dockerfile      # extends manimcommunity/manim:v0.20.1
  render.sh       # renders scene.py inside Docker
tests/            # one test file per module
config.py         # env var loading
main.py           # CLI entry point
```

See [CLAUDE.md](CLAUDE.md) for full architecture documentation, design decisions, and contribution guidelines.
