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
| `--speed` | No | `1.0` | Narration speed multiplier (e.g. `1.25` for 25% faster). OpenAI: native (0.25–4.0). Kokoro/ElevenLabs: ffmpeg atempo. |
| `--run-id` | No | auto | Resume a previous run using its ID |
| `--preview` | No | off | Render a fast low-quality preview (480p15) to `preview.mp4` instead of the full HD render |
| `--no-render` | No | off | Run the AI pipeline only — skip Docker render and ffmpeg merge |
| `--verbose` | No | off | Stream raw Docker/Manim output to the terminal while rendering |
| `--context` | No | — | File or directory to use as source material. Repeatable. |
| `--context-ignore` | No | — | Glob pattern to exclude from context directories. Repeatable. |
| `--url` | No | — | URL to fetch as source material (HTML stripped to text). Repeatable. |
| `--burn-captions` | No | off | Burn subtitles into the video (re-encodes; `captions.srt` is always written regardless) |
| `--qa-density` | No | `normal` | Visual QA frame sampling: `zero` (skip), `normal` (1/30s, up to 10 frames), `high` (1/15s, up to 20 frames) |
| `--yes` | No | off | Skip confirmation prompts (e.g. large-context warning when `--url` content exceeds 10k tokens) |

> `--verbose` and `--preview` cannot be combined.

After a full render, Chalkboard automatically runs a visual quality check: it samples frames from `final.mp4` and asks Claude to flag overlapping elements, off-screen text, or readability issues. If errors are found, it regenerates the Manim scene and re-renders (up to 2 attempts). Use `--qa-density high` for longer or more complex animations, or `--qa-density zero` to skip QA entirely.

---

## Context injection

Pass local files or URLs as source material so the pipeline builds animations from your content:

```bash
# Explain a codebase
python main.py --topic "explain this codebase" --context ./src --context ./docs

# Turn a paper into an animation
python main.py --topic "summarize this paper" --context paper.pdf

# Use a repo, excluding lock files and build output
python main.py --topic "visualize this" --context ./repo --context-ignore "*.lock" --context-ignore "dist/"

# Ground the script in a web article
python main.py --topic "explain this concept" --url https://en.wikipedia.org/wiki/...

# Combine files and URLs
python main.py --topic "explain my project" --context ./README.md --url https://example.com/blog-post

# Obsidian vault page
python main.py --topic "visualize my notes" --context ~/Documents/vault/page.md
```

Supported file types: text and code files (`.py`, `.js`, `.md`, `.yaml`, …), images (`.png`, `.jpg`, `.webp`, …), PDFs, and Word docs (`.docx`). URLs are fetched with HTML stripped to plain text, truncated at 100k chars.

Before the pipeline starts, Chalkboard reports how many tokens the context uses:

```
Context: 12 files, ~38k tokens  (model window: 200k, ~19% used by context)
```

If context exceeds 10k tokens you'll be prompted to confirm — pass `--yes` to skip this prompt for scripted or non-interactive runs. If it exceeds 90% of the model's context window, Chalkboard aborts with an error.

**Resuming with context:** `--context` and `--url` are not stored in the checkpoint. Pass them again on resume to re-inject source material:

```bash
python main.py --topic "..." --run-id <id> --context ./src
```

**Prerequisites:** `pip install pathspec` (required). `pip install python-docx` only for `.docx`. `pip install httpx beautifulsoup4` only for `--url`.

---

## Captions & chapter markers

Every full render automatically produces:

- **`captions.srt`** — subtitle file (one entry per script segment, cumulative timestamps)
- **Chapter atoms embedded in `final.mp4`** — visible in QuickTime, VLC, and most players' chapter menus
- **YouTube chapter list printed to stdout** — copy-paste into your video description

```
  Chapters:
    0:00  A B-tree is a self-balancing search tree...
    0:42  Each node can hold multiple keys and child...
    1:18  Insertion works by finding the correct lea...
```

To also **burn subtitles into the video** (re-encodes, slower):

```bash
python main.py --topic "..." --burn-captions
```

---

## Narration speed

Adjust the speaking pace with `--speed` (default `1.0`):

```bash
python main.py --topic "..." --speed 1.25   # 25% faster
python main.py --topic "..." --speed 0.85   # 15% slower
```

OpenAI TTS uses its native speed parameter (0.25–4.0). Kokoro and ElevenLabs are processed via ffmpeg `atempo` after generation. Either way, `segments.json` records the post-speed actual durations, so chapter and SRT timestamps are always accurate.

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
| `DEFAULT_AUDIENCE` | `intermediate` | `beginner`, `intermediate`, `expert` |
| `DEFAULT_TONE` | `casual` | `casual`, `formal`, `socratic` |
| `DEFAULT_THEME` | `chalkboard` | `chalkboard`, `light`, `colorful` |
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
  context.py      # collect_files, load_context_blocks, fetch_url_blocks, measure_context
  graph.py        # LangGraph state machine
  state.py        # PipelineState TypedDict + ValidationResult
  render_trigger.py  # writes output files, calls TTS
  retry.py        # timeout constants, api_call_with_retry, TimeoutExhausted
docker/
  Dockerfile      # extends manimcommunity/manim:v0.20.1
  render.sh       # renders scene.py inside Docker
tests/            # one test file per module
config.py         # env var loading
main.py           # CLI entry point
```

See [CLAUDE.md](CLAUDE.md) for full architecture documentation, design decisions, and contribution guidelines.
