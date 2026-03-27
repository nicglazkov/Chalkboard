# Chalkboard

Turn any topic into a narrated, animated explainer video — fully automated.

```
topic → script → fact-check → Manim animation → code review → voiceover → final.mp4
```

Chalkboard is a multi-agent LangGraph pipeline powered by Claude. It writes an educational script, validates the facts, generates Manim animation code, validates the code, synthesizes a voiceover, and renders everything to video. Each stage has automatic retry logic; when it gets stuck it asks you for guidance.

---

## Quick start

### 1. Clone and install

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

Outputs are written to `output/<run-id>/`. When the pipeline finishes, render the video:

```bash
# Build the render image once
docker build -f docker/Dockerfile -t chalkboard-render .

# Render (replace <run-id> with the ID printed by main.py)
docker run --rm -v "$(pwd)/output:/output" chalkboard-render <run-id>

# Merge voiceover on the host (avoids macOS/QuickTime AAC compatibility issues)
RUN_ID=<run-id>
ffmpeg -i "output/$RUN_ID/media/videos/scene/720p30/ChalkboardScene.mp4" \
       -i "output/$RUN_ID/voiceover.wav" \
       -c:v copy -c:a aac -b:a 128k \
       "output/$RUN_ID/final.mp4"
```

> **High quality:** Replace `720p30` with `1080p60` in the ffmpeg command and set `MANIM_QUALITY=high`.

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
