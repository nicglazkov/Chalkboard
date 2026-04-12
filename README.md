# Chalkboard

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/nicglazkov/Chalkboard)
[![Docs](https://img.shields.io/badge/docs-guide-blue)](https://nicglazkov.github.io/Chalkboard/guide.html)
[![CLI Reference](https://img.shields.io/badge/docs-CLI-blue)](https://nicglazkov.github.io/Chalkboard/cli.html)
[![API Reference](https://img.shields.io/badge/docs-API-blue)](https://nicglazkov.github.io/Chalkboard/api.html)

Turn any topic into a narrated, animated explainer video — fully automated.

```
topic → script → fact-check → animation → validate → video
```

Chalkboard is a multi-agent LangGraph pipeline powered by Claude. It writes an educational script, validates the facts, generates Manim animation code, validates the code, synthesizes a voiceover, and renders everything to video. Each stage has automatic retry logic. Use it through the web UI or the CLI.

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

**Web UI:**
```bash
python run_server.py
# Open http://localhost:8000
```

**Or from the terminal:**
```bash
python main.py --topic "explain how B-trees work" --effort medium
```

Either way, the pipeline runs, renders the animation in Docker, and merges the voiceover into `output/<run-id>/final.mp4`.

> **First run only:** Docker will build the render image automatically (~30s). Subsequent runs use the cached image.

> **High quality:** Set `MANIM_QUALITY=high` in `.env` for 1080p60 output.

---

## CLI flags

| Flag               | Required | Default        | Description                                                                                                           |
| ------------------ | -------- | -------------- | --------------------------------------------------------------------------------------------------------------------- |
| `--topic`          | **Yes**  | —              | Topic to explain, e.g. `"how B-trees work"`                                                                           |
| `--effort`         | No       | `medium`       | Validation thoroughness (see [Effort levels](#effort-levels))                                                         |
| `--audience`       | No       | `intermediate` | Target audience: `beginner`, `intermediate`, `expert`                                                                 |
| `--tone`           | No       | `casual`       | Narration tone: `casual`, `formal`, `socratic`                                                                        |
| `--theme`          | No       | `chalkboard`   | Visual color theme: `chalkboard`, `light`, `colorful`                                                                 |
| `--template`       | No       | —              | Animation template: `algorithm`, `code`, `compare`, `howto`, `timeline`                                               |
| `--speed`          | No       | `1.0`          | Narration speed multiplier (e.g. `1.25` for 25% faster). OpenAI: native (0.25–4.0). Kokoro/ElevenLabs: ffmpeg atempo. |
| `--run-id`         | No       | auto           | Resume a previous run using its ID                                                                                    |
| `--preview`        | No       | off            | Render a fast low-quality preview (480p15) to `preview.mp4` instead of the full HD render                             |
| `--no-render`      | No       | off            | Run the AI pipeline only, skipping Docker render and ffmpeg merge                                                     |
| `--verbose`        | No       | off            | Stream raw Docker/Manim output to the terminal while rendering                                                        |
| `--context`        | No       | —              | File or directory to use as source material. Repeatable.                                                              |
| `--context-ignore` | No       | —              | Glob pattern to exclude from context directories. Repeatable.                                                         |
| `--url`            | No       | —              | URL to fetch as source material (HTML stripped to text). Repeatable.                                                  |
| `--github`         | No       | —              | GitHub repo (`owner/repo` or URL); fetches its README as context. Repeatable.                                         |
| `--quiz`           | No       | off            | Generate comprehension questions (`quiz.json`) after the pipeline.                                                    |
| `--burn-captions`  | No       | off            | Burn subtitles into the video (re-encodes; `captions.srt` is always written regardless)                               |
| `--qa-density`     | No       | `normal`       | Visual QA frame sampling: `zero` (skip), `normal` (1/30s, up to 10 frames), `high` (1/15s, up to 20 frames)           |
| `--yes`            | No       | off            | Skip confirmation prompts (e.g. large-context warning when `--url` content exceeds 10k tokens)                        |

> `--verbose` and `--preview` cannot be combined.

Before rendering, Chalkboard runs a **layout check**: it dry-runs the Manim scene headlessly inside Docker and validates every segment's bounding boxes (off-screen elements, overlapping elements) and animation timing against the audio budget. Layout violations are fed back to the Manim agent as code feedback; it retries until the scene passes or attempts are exhausted.

After a full render, Chalkboard automatically runs a **visual quality check**: it samples frames from `final.mp4` and asks Claude to flag overlapping elements, off-screen text, or readability issues. If errors are found, it regenerates the Manim scene and re-renders (up to 2 attempts). Use `--qa-density high` for longer or more complex animations, or `--qa-density zero` to skip QA entirely.

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

# GitHub repo README
python main.py --topic "explain this project" --github nicglazkov/Chalkboard

# GitHub URL form
python main.py --topic "explain this library" --github https://github.com/owner/repo
```

Supported file types: text and code files (`.py`, `.js`, `.md`, `.yaml`, `.ps1`, `.bat`, …), images (`.png`, `.jpg`, `.webp`, …), PDFs, and Word docs (`.docx`). URLs are fetched with HTML stripped to plain text, truncated at 100k chars.

The **web UI** also supports context injection via the file upload zone in Advanced options. Drag and drop files or entire folders directly onto the zone. Per-file limits: text/code 2 MB, images 5 MB, PDFs 20 MB, DOCX 10 MB, 24 MB total.

Before the pipeline starts, Chalkboard reports how many tokens the context uses:

```
Context: 12 files, ~38k tokens  (model window: 200k, ~19% used by context)
```

If context exceeds 10k tokens you'll be prompted to confirm. Pass `--yes` to skip this prompt for scripted or non-interactive runs. If it exceeds 90% of the model's context window, Chalkboard aborts with an error.

**Resuming with context:** `--context`, `--url`, and `--github` are not stored in the checkpoint. Pass them again on resume to re-inject source material:

```bash
python main.py --topic "..." --run-id <id> --context ./src
```

All required packages (`pathspec`, `python-docx`, `httpx`, `beautifulsoup4`) are included in `requirements.txt` and installed by `pip install -r requirements.txt`.

---

## Animation templates

`--template` injects layout and visual convention guidance into the Manim code generator, producing more structured and appropriate animations for specific content types.

| Template    | Best for                                     | Key visual pattern                                                     |
| ----------- | -------------------------------------------- | ---------------------------------------------------------------------- |
| `algorithm` | Sorting, searching, graph traversal          | Array cells + pointer arrows + step counter + explicit swap animations |
| `code`      | Code walkthroughs, implementation explainers | Manim `Code` object, incremental line reveal, callout annotations      |
| `compare`   | A vs B trade-offs, technology comparisons    | Two labeled columns, consistent color per side, summary row at end     |
| `howto`     | Setup guides, recipes, processes             | Numbered step list, active step highlighted, completed steps dimmed     |
| `timeline`  | History, version timelines, biographies      | Horizontal axis with dated markers, chronological left-to-right reveal |

```bash
python main.py --topic "explain merge sort" --template algorithm
python main.py --topic "walk through a binary search implementation" --template code
python main.py --topic "SQL vs NoSQL trade-offs" --template compare
python main.py --topic "how to set up a Python virtual environment" --template howto
python main.py --topic "history of the internet" --template timeline
```

Templates compose freely with `--theme`, `--tone`, `--audience`, and `--speed`.

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

## Quiz generation

Add `--quiz` to generate comprehension questions alongside any video:

```bash
python main.py --topic "explain binary search" --quiz
```

After the pipeline finishes, Chalkboard calls Claude with the completed script and writes `output/<run-id>/quiz.json`, a list of 4–6 multiple-choice questions with answer keys and explanations:

```json
[
  {
    "question": "What does binary search require about the input list?",
    "options": [
      "A) It must be unsorted",
      "B) It must be sorted",
      "C) It must have no duplicates",
      "D) It must be numeric"
    ],
    "answer": "B",
    "explanation": "Binary search only works on sorted lists because it relies on halving the search space based on order."
  }
]
```

Works with `--no-render` too. Quiz generation only needs the script, not the video.

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

| Backend      | Quality | Cost         | Requires                                                     |
| ------------ | ------- | ------------ | ------------------------------------------------------------ |
| `openai`     | Great   | API          | `OPENAI_API_KEY`                                             |
| `elevenlabs` | Great   | API          | `pip install elevenlabs`, `ELEVENLABS_API_KEY`               |
| `kokoro`     | Best    | Free (local) | PyTorch ≥ 2.4, `espeak-ng` (**not available on Intel Macs**) |

Set `TTS_BACKEND` in your `.env` file. The `.env.example` ships with `openai` (works on all platforms). The code default when unset is `kokoro`.

> **Intel Mac users:** PyTorch ≥ 2.4 has no x86_64 macOS wheels. Use `openai` or `elevenlabs`.
>
> To install `espeak-ng` for Kokoro: `brew install espeak-ng` / `apt install espeak-ng`

---

## Effort levels

`--effort` controls how thorough the validation is and whether web search is used.

| Level              | Fact-check                  | Web search     | Segments |
| ------------------ | --------------------------- | -------------- | -------- |
| `low`              | Light — obvious errors only | Never          | 3–4      |
| `medium` (default) | Spot-check key claims       | No             | 4–6      |
| `high`             | Thorough                    | Via research_agent (pre-script web research) | 5–8      |

---

## Resuming a crashed run

Every run is checkpointed. If it crashes or you abort, resume with the same run ID:

```bash
python main.py --topic "..." --run-id <previous-run-id>
```

### Preview → full render workflow

Run `--preview` first to quickly check the visuals at low quality, then do the full HD render with `--run-id` (the pipeline result is already checkpointed, so it won't re-run):

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

| Variable           | Default             | Options                              |
| ------------------ | ------------------- | ------------------------------------ |
| `TTS_BACKEND`      | `kokoro`            | `kokoro`, `openai`, `elevenlabs`     |
| `MANIM_QUALITY`    | `medium`            | `low`, `medium`, `high`              |
| `DEFAULT_EFFORT`   | `medium`            | `low`, `medium`, `high`              |
| `DEFAULT_AUDIENCE` | `intermediate`      | `beginner`, `intermediate`, `expert` |
| `DEFAULT_TONE`     | `casual`            | `casual`, `formal`, `socratic`       |
| `DEFAULT_THEME`    | `chalkboard`        | `chalkboard`, `light`, `colorful`    |
| `OUTPUT_DIR`       | `./output`          | any path                             |
| `CHECKPOINT_DB`    | `pipeline_state.db` | any path                             |
| `SERVER_PORT`      | `8000`              | API server port (overridden by `--port`) |

---

## API server

Chalkboard includes a FastAPI server that exposes the pipeline over HTTP with SSE streaming for live progress. Useful for building a frontend or scripting jobs programmatically.

### Start

```bash
python run_server.py          # http://localhost:8000
python run_server.py --reload # dev mode (auto-reload)
python run_server.py --port 9000
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST /api/jobs` | Create job | Start the pipeline for a topic (JSON body) |
| `POST /api/jobs/upload` | Create job with files | Multipart form (same fields plus file uploads) |
| `GET /api/jobs` | List jobs | All jobs in this server session |
| `GET /api/jobs/{id}` | Get job | Poll status and output file list |
| `GET /api/jobs/{id}/events` | SSE stream | Live pipeline progress events |
| `GET /api/jobs/{id}/files/{filename}` | Download | Serve `final.mp4`, `captions.srt`, etc. |

### Example

```bash
# Start a job (minimal)
curl -s -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{"topic": "explain recursion", "effort": "low"}' | python3 -m json.tool

# Start a job with all options
curl -s -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "explain recursion",
    "effort": "high",
    "audience": "beginner",
    "tone": "casual",
    "theme": "chalkboard",
    "template": "algorithm",
    "speed": 1.25,
    "burn_captions": true,
    "quiz": true,
    "qa_density": "normal",
    "urls": ["https://en.wikipedia.org/wiki/Recursion"],
    "github": ["nicglazkov/Chalkboard"]
  }' | python3 -m json.tool

# Start a job with local file uploads (multipart)
curl -s -X POST http://localhost:8000/api/jobs/upload \
  -F "topic=explain this codebase" \
  -F "effort=medium" \
  -F "files=@./README.md" \
  -F "files=@./main.py" | python3 -m json.tool

# Stream progress (SSE)
curl -s http://localhost:8000/api/jobs/<id>/events

# Download the video
curl -o final.mp4 http://localhost:8000/api/jobs/<id>/files/final.mp4
```

### Job response shape

```json
{
  "id": "uuid",
  "status": "pending | running | completed | failed",
  "topic": "explain recursion",
  "events": [{"node": "script_agent", "updates": {...}}],
  "error": null,
  "output_files": ["final.mp4", "captions.srt", "script.txt"]
}
```

### Web UI

The server includes a built-in single-page UI. Start the server and open `http://localhost:8000` in your browser:

- A form with **Topic**, **Effort**, and **Audience** always visible, plus an **Advanced options** section (collapsible) containing Tone, Theme, Template, Speed, Visual QA density, Burn Captions, Generate Quiz, URL inputs, GitHub repo inputs, and a **file upload zone**
- File upload zone supports drag-and-drop of individual files or entire folders, with inline per-file and total-size error display (text/code: 2 MB, images: 5 MB, PDFs: 20 MB, DOCX: 10 MB, total: 24 MB)
- A live stage-by-stage progress view as the pipeline runs
- A video player with download links when the job completes

No build step required. The UI lives in `server/static/index.html`.

### Video Library

A YouTube-style library browser is available at `http://localhost:8000/library`:

- **4-column responsive grid** with thumbnails, title, duration badge, quality badge, and date
- **Search** across topic and script text; **sort** by newest, oldest, longest, or shortest
- **Load-more pagination** — handles large collections without loading everything at once
- **Detail page** at `/library/{run_id}` with the video player, download links, generation settings, interactive transcript, and related videos
  - **Interactive transcript** — clickable `0:11 · segment text` rows; clicking seeks the video, active segment highlights and scrolls into view as the video plays
  - **CC subtitles** — native subtitle track loaded from `captions.srt`; toggle with the CC button in the player controls
- **Re-generate button** pre-fills the generate form with the same settings used for that video
- **CSS fallback thumbnails** keyed to the animation theme (chalkboard / light / colorful) for runs without a rendered thumbnail

All generated videos are automatically indexed into a SQLite database (`library.db`) when the server starts. Existing runs in `output/` are backfilled at startup. The storage layer uses a `LibraryStore` abstract interface so it can be swapped for a PostgreSQL backend for hosted deployments.

#### Library API

| Method | Path | Description |
|--------|------|-------------|
| `GET /api/library` | List videos | Supports `q`, `sort`, `limit`, `offset` query params |
| `GET /api/library/{run_id}` | Get video | Full metadata + dynamic `output_files` list |
| `DELETE /api/library/{run_id}` | Delete video | Removes from index (does not delete files) |

---

## Development

### Run tests

```bash
pytest
```

### Project structure

```
pipeline/
  agents/         # research_agent, script_agent, fact_validator, manim_agent, code_validator, layout_checker, orchestrator
  tts/            # kokoro, openai, elevenlabs backends
  context.py      # collect_files, load_context_blocks, fetch_url_blocks, measure_context
  graph.py        # LangGraph state machine
  state.py        # PipelineState TypedDict + ValidationResult
  render_trigger.py  # writes output files, calls TTS
  retry.py        # timeout constants, api_call_with_retry, TimeoutExhausted
docker/
  Dockerfile      # extends manimcommunity/manim:v0.20.1
  render.sh       # renders scene.py inside Docker
server/         # FastAPI app, job store, routes, library, static frontend
tests/            # one test file per module
config.py         # env var loading
main.py           # CLI entry point
run_server.py   # API server entrypoint
```

See [CLAUDE.md](CLAUDE.md) for full architecture documentation, design decisions, and contribution guidelines.
