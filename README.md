# Chalkboard

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/nicglazkov/Chalkboard)
[![Docs](https://img.shields.io/badge/docs-guide-blue)](https://nicglazkov.github.io/Chalkboard/guide.html)
[![CLI Reference](https://img.shields.io/badge/docs-CLI-blue)](https://nicglazkov.github.io/Chalkboard/cli.html)
[![API Reference](https://img.shields.io/badge/docs-API-blue)](https://nicglazkov.github.io/Chalkboard/api.html)

Turn any topic into a narrated, animated explainer video, fully automated.

```
topic → script → fact-check → animation → validate → video
```

Chalkboard is a multi-agent LangGraph pipeline powered by Claude. It writes an educational script, checks the facts, generates Manim animation code on top of a built-in design system, validates that code (static checks plus a headless layout dry-run), synthesizes a voiceover, and renders everything to video. Each stage retries automatically. Use it through the web UI or the CLI.

---

## Quick start

### 1. Clone and install

**Prerequisites:** Python 3.10+ and [ffmpeg](https://ffmpeg.org). To render you also need **either** a local Manim + TeX install **or** [Docker](https://docker.com) (see [Rendering](#rendering)).

```bash
git clone https://github.com/nicglazkov/Chalkboard.git
cd Chalkboard
pip install -r requirements.txt               # pipeline, server, TTS
pip install -r requirements-render.txt        # optional: render locally (manim 0.21.0)
```

### 2. Set up API keys

```bash
cp .env.example .env
```

Open `.env` and fill in your keys:

```
ANTHROPIC_API_KEY=sk-ant-...
TTS_BACKEND=openai          # see TTS backends below
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

Either way, the pipeline runs, renders the animation (locally or in Docker, whichever is available), and merges the voiceover into `output/<run-id>/final.mp4`.

> **Quality:** pass `--quality high` (1080p60) or `--quality 4k` (2160p60), or set `MANIM_QUALITY` in `.env`. The default is `medium` (720p30).

---

## Rendering

Scenes are rendered by Manim CE **0.21.0**. `RENDER_BACKEND` picks where:

| Value            | What happens                                                                                       |
| ---------------- | -------------------------------------------------------------------------------------------------- |
| `auto` (default) | `local` if `manim` is importable and `latex`, `dvisvgm` and `ffmpeg` are on `PATH`, else `docker`   |
| `local`          | Runs Manim natively on this machine. Faster: no container start, no image build                     |
| `docker`         | Runs Manim in the `chalkboard-render` image (no local TeX needed)                                   |

Both backends run the same layout dry-run and the same scene runtime (`docker/*.py`). The voiceover merge always runs with host ffmpeg.

### Local rendering

```bash
pip install -r requirements-render.txt   # installs manim==0.21.0
```

Plus system packages: a TeX distribution with `latex` and `dvisvgm`, ffmpeg, cairo/pango, and fonts.

**Ubuntu / Debian:**
```bash
sudo apt install texlive texlive-latex-extra texlive-fonts-extra texlive-science lmodern cm-super \
  dvisvgm ffmpeg libcairo2-dev libpango1.0-dev pkg-config fonts-cmu
```

**macOS** (untested): install [MacTeX](https://tug.org/mactex/), or BasicTeX plus the LaTeX packages the house preamble uses (the `tlmgr install` line in `docker/Dockerfile` lists them). Then `brew install ffmpeg cairo pango pkg-config` and install the CMU Serif (Computer Modern Unicode) font.

Scene text uses CMU Serif when installed, so prose matches the LaTeX math (falling back to Inter, then DejaVu Sans); code uses JetBrains Mono NL or DejaVu Sans Mono. Override with `CHALKBOARD_TEXT_FONT` / `CHALKBOARD_CODE_FONT`.

### Docker rendering

With `RENDER_BACKEND=docker` (or `auto` without a local TeX install), the first render builds the `chalkboard-render` image from `docker/Dockerfile` (`manimcommunity/manim:v0.21.0` plus fonts and TeX packages). This takes a few minutes once; later runs reuse the cached image.

> The Docker image was moved to Manim 0.21.0 along with the design system but has not been re-tested end to end since. Local rendering is the tested path.

### Run it on a GPU box or another machine

Chalkboard can run on a different machine from the one you browse from, for example a GPU box that runs Kokoro TTS and renders faster. Install there as above and start the server bound to all interfaces:

```bash
python run_server.py --host 0.0.0.0 --port 8000
```

Then open `http://<that-machine>:8000`. The server has **no authentication**, so only do this on a trusted network. Alternatively, keep the default `127.0.0.1` binding and use an SSH tunnel:

```bash
ssh -L 8000:localhost:8000 user@that-machine
# then open http://localhost:8000
```

The CLI works the same way over SSH; outputs land in `output/<run-id>/` on that machine.

---

## Models

Every Claude call goes through one helper (`pipeline/llm.py`). By default every agent uses **`claude-opus-5-5`** with adaptive thinking. You can change the model globally or per agent, and tune each agent's effort:

| Variable                | Default           | Effect                                                    |
| ----------------------- | ----------------- | --------------------------------------------------------- |
| `CLAUDE_MODEL`          | `claude-opus-5-5` | Model for every agent                                     |
| `CLAUDE_MODEL_<AGENT>`  | `CLAUDE_MODEL`    | Model for one agent, e.g. `CLAUDE_MODEL_CODE_VALIDATOR`   |
| `CLAUDE_EFFORT_<AGENT>` | see below         | Effort for one agent, e.g. `low`, `medium`, `high`        |

Agents and their default effort:

| `<AGENT>`        | Default effort | Role                                 |
| ---------------- | -------------- | ------------------------------------ |
| `RESEARCH`       | `medium`       | Web research brief (`--effort high`) |
| `SCRIPT`         | `high`         | Narration script                     |
| `FACT`           | `medium`       | Fact check                           |
| `MANIM`          | `high`         | Scene code generation                |
| `CODE_VALIDATOR` | `medium`       | Code review                          |
| `VISUAL_QA`      | `high`         | Review of frames from the render     |
| `QUIZ`           | `low`          | `--quiz` questions                   |

Example: keep the default for the script and scene code, use a cheaper model for the validators:

```
CLAUDE_MODEL_FACT=claude-sonnet-5-5
CLAUDE_MODEL_CODE_VALIDATOR=claude-sonnet-5-5
```

Haiku models are called without thinking or effort settings (they do not support them).

---

## CLI flags

| Flag               | Required | Default         | Description                                                                                                   |
| ------------------ | -------- | --------------- | ------------------------------------------------------------------------------------------------------------- |
| `--topic`          | **Yes**  |                 | Topic to explain, e.g. `"how B-trees work"`                                                                   |
| `--effort`         | No       | `medium`        | Validation thoroughness (see [Effort levels](#effort-levels))                                                 |
| `--audience`       | No       | `intermediate`  | Target audience: `beginner`, `intermediate`, `expert`                                                         |
| `--tone`           | No       | `casual`        | Narration tone: `casual`, `formal`, `socratic`                                                                |
| `--theme`          | No       | `chalkboard`    | Visual color theme: `chalkboard`, `light`, `colorful`                                                         |
| `--template`       | No       | none            | Scene template: `algorithm`, `code`, `compare`, `derivation`, `howto`, `timeline`                             |
| `--quality`        | No       | `MANIM_QUALITY` | Render resolution: `low` (480p15), `medium` (720p30), `high` (1080p60), `4k` (2160p60)                        |
| `--speed`          | No       | `1.0`           | Narration speed multiplier (e.g. `1.25`). OpenAI: native (0.25 to 4.0). Kokoro/ElevenLabs: ffmpeg atempo.     |
| `--run-id`         | No       | auto            | Resume a previous run from its checkpoint                                                                     |
| `--preview`        | No       | off             | Render a fast low-quality preview (480p15) to `preview.mp4` instead of the full render                        |
| `--no-render`      | No       | off             | Run the AI pipeline only, skipping render and ffmpeg merge                                                    |
| `--verbose`        | No       | off             | Stream raw Manim output to the terminal while rendering                                                       |
| `--context`        | No       |                 | File or directory to use as source material. Repeatable.                                                      |
| `--context-ignore` | No       |                 | Glob pattern to exclude from context directories. Repeatable.                                                 |
| `--url`            | No       |                 | URL to fetch as source material (HTML stripped to text). Repeatable.                                          |
| `--github`         | No       |                 | GitHub repo (`owner/repo` or URL); fetches its README as context. Repeatable.                                 |
| `--quiz`           | No       | off             | Generate comprehension questions (`quiz.json`) after the pipeline.                                            |
| `--burn-captions`  | No       | off             | Burn subtitles into the video (re-encodes; `captions.srt` is always written regardless)                       |
| `--qa-density`     | No       | `normal`        | Visual QA frame sampling: `zero` (skip), `normal` (1/30s, up to 10 frames), `high` (1/15s, up to 20 frames)   |
| `--yes`            | No       | off             | Never prompt: skip confirmations (e.g. large context) and abort instead of asking when retries run out        |

> `--verbose` and `--preview` cannot be combined.

**Unattended runs.** With `--yes`, or when stdin is not a terminal (cron, CI, a background job), Chalkboard does not stop to ask what to do when retries run out. If the scene still has layout warnings after its last retry, it renders that scene anyway (visual QA then gets a look at it); a scene that crashes, or a stage that keeps failing, aborts the run instead. Pass `--yes` for scripted runs that use large context, since that confirmation is skipped only by `--yes`. Out-of-credit and invalid-request API errors fail fast instead of being retried.

Before rendering, Chalkboard runs a **layout check**: it dry-runs the Manim scene headlessly and validates every segment's bounding boxes (off-screen elements, overlaps, elements crossing between the left and right zones) and animation timing against the audio budget. Violations are fed back to the Manim agent, which retries until the scene passes or attempts run out.

After a full render, Chalkboard runs a **visual quality check**: it samples frames from `final.mp4` and asks Claude to flag overlapping elements, off-screen text, or readability issues. If errors are found, it regenerates the scene and re-renders (up to 2 attempts). Use `--qa-density high` for longer animations, or `--qa-density zero` to skip QA.

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

# GitHub repo README
python main.py --topic "explain this project" --github nicglazkov/Chalkboard
```

Supported file types: text and code files (`.py`, `.js`, `.md`, `.yaml`, `.ps1`, `.bat`, ...), images (`.png`, `.jpg`, `.webp`, ...), PDFs, and Word docs (`.docx`). URLs are fetched with HTML stripped to plain text, truncated at 100k chars.

The **web UI** also supports context injection via the file upload zone in Advanced options. Drag and drop files or whole folders. Per-file limits: text/code 2 MB, images 5 MB, PDFs 20 MB, DOCX 10 MB, 24 MB total.

Before the pipeline starts, Chalkboard reports how many tokens the context uses:

```
Context: 12 files, ~38k tokens  (model window: 200k, ~19% used by context)
```

If context exceeds 10k tokens you'll be asked to confirm; `--yes` skips the prompt. If it exceeds 90% of the model's context window, Chalkboard aborts with an error.

**Resuming with context:** `--context`, `--url`, and `--github` are not stored in the checkpoint. Pass them again on resume:

```bash
python main.py --topic "..." --run-id <id> --context ./src
```

---

## Animation design system

Generated scenes are composed from a small design system that ships with the renderer (`docker/`), not from raw Manim primitives. This is what keeps runs consistent:

- **Tokens** (`chalkboard_tokens.py`): every color, font size, spacing, stroke width and motion timing, named by meaning (`focus_primary`, `context_muted`, `motion_snap`, ...). Themes only swap colors.
- **Components** (`chalkboard_components.py`): `ChalkBox`, `ChalkArrow`, `ChalkCode`, `Callout`, `StepCounter`, `ChalkAxis`, `ChalkAxes`, `ChalkPanel`, `ChalkBadge`, `EquationGroup`, `ChalkMatrix`, `NetworkNode`, plus `math_tex()` / `tex()`.
- **Moves** (`chalkboard_moves.py`): named animation patterns such as `reveal_with_emphasis`, `compare_split`, `progressive_step`, `derivation_step`, `emphasize_term`.
- **Templates** (`chalkboard_templates/`): whole-scene choreographies the agent fills with data.
- **House style** (`chalkboard_style.py`): one LaTeX preamble for all math (amsmath, mathtools, siunitx, cancel, ... and macros such as `\R`, `\E`, `\dd`, `\Var`, `\argmax`), CMU Serif for text (Computer Modern, matching the math), a ligature-free monospace for code.

Math is always typeset with LaTeX, and the narration says it in words ("x squared", "the derivative of f with respect to x") so the voice never reads symbols aloud.

`docker/examples/design_system_demo.py` renders a few demo scenes with all of this (see the command at the top of that file).

### Templates

`--template` tells the scene generator to build the video from one of these scene templates:

| Template     | Best for                          | Visual pattern                                                       |
| ------------ | --------------------------------- | -------------------------------------------------------------------- |
| `algorithm`  | Sorting, searching, DP traces     | Row of cells, focused cell per step, step counter, optional callouts |
| `code`       | Code walkthroughs                 | Highlighted source, lines highlighted per step, callouts             |
| `compare`    | A vs B trade-offs                 | Two panels with a divider, matched points revealed in pairs          |
| `derivation` | Math derivations and proofs       | Aligned equations, each line morphing out of the previous, notes     |
| `howto`      | Setup guides, recipes, procedures | Numbered steps, active step highlighted, completed steps dimmed      |
| `timeline`   | History, version timelines        | Horizontal axis with dated events revealed in order                  |

```bash
python main.py --topic "explain merge sort" --template algorithm
python main.py --topic "why the derivative of x squared is 2x" --template derivation
python main.py --topic "SQL vs NoSQL trade-offs" --template compare
```

Without `--template` the agent composes the scene freely (it may still use a template, e.g. a derivation for a math-heavy script). Templates compose with `--theme`, `--tone`, `--audience`, and `--speed`.

---

## Captions & chapter markers

Every full render produces:

- **`captions.srt`**: subtitle file (one entry per script segment)
- **Chapter atoms embedded in `final.mp4`**: visible in QuickTime, VLC, and most players' chapter menus
- **A YouTube chapter list printed to stdout**, ready to paste into a video description

To also **burn subtitles into the video** (re-encodes, slower), pass `--burn-captions`.

---

## Quiz generation

Add `--quiz` to generate comprehension questions alongside any video:

```bash
python main.py --topic "explain binary search" --quiz
```

After the pipeline finishes, Chalkboard writes `output/<run-id>/quiz.json`: 4 to 6 multiple-choice questions with answer keys and explanations. Works with `--no-render` too, since it only needs the script.

---

## Narration speed

```bash
python main.py --topic "..." --speed 1.25   # 25% faster
python main.py --topic "..." --speed 0.85   # 15% slower
```

OpenAI TTS uses its native speed parameter (0.25 to 4.0). Kokoro and ElevenLabs are processed with ffmpeg `atempo` after generation. Either way, `segments.json` records the actual post-speed durations, so chapters and captions line up.

---

## Narration (TTS)

Pick a voice per run with `--narrator` (CLI), `narrator` (API) or the Narrator menu in the web UI, or set a default with `NARRATOR` in `.env`:

| Narrator | Backend | Voice | Needs |
| -------- | ------- | ----- | ----- |
| `aria`   | ElevenLabs `eleven_v4` | Skye: young, bright, a little nerdy | `ELEVENLABS_API_KEY` |
| `milo`   | ElevenLabs `eleven_v4` | Bradley: earnest male narrator | `ELEVENLABS_API_KEY` |
| `kokoro` | Kokoro-82M (local) | `af_heart` | PyTorch 2.4+, `espeak-ng`; free |
| `alloy`  | OpenAI `gpt-4o-mini-tts` | alloy | `OPENAI_API_KEY` |

With no narrator set, `TTS_BACKEND` (`kokoro`, `openai`, `elevenlabs`) picks the backend with its default voice.

Why these: Aria and Milo came out of a blind listening test (April 2026) against OpenAI, Fish and Gemini voices, then an October 2026 audition of `eleven_v4` against `eleven_v3`. Eleven v4 (September 2026) led the Artificial Analysis TTS arena when this was written and supports **request stitching**: segments are synthesized separately (their lengths drive the animation), and v4 is told about the neighbouring segments so intonation carries across the joins. `eleven_v3` rejects stitching, so it runs segments in parallel without context. Compare voices on your own machine with `python scripts/tts_bench.py --out bench/` (same script for every voice, with timing and a Whisper round-trip check).

**Word-level sync.** The script marks the words where visuals land (`[[1]]`, `[[2]]`, ... inside each segment; the markers are never spoken or shown). The TTS backend reports when each marked word is spoken: ElevenLabs from its per-character timestamps, Kokoro from its token timings, OpenAI by estimate from character position. The scene calls `self.cue(k)` before each cued animation, so an equation or label appears as its word is said, not seconds before or after. Late cues are reported by the layout check, and every cue's spoken vs shown time is logged in `layout_report.json` (`cue_log`). Captions are split per sentence on the same timings.

- **ElevenLabs**: `ELEVENLABS_MODEL_ID` (default `eleven_v4`), `ELEVENLABS_VOICE_ID` (used with `TTS_BACKEND=elevenlabs`), `ELEVENLABS_STITCH=0` to turn stitching off, `ELEVENLABS_CONCURRENCY` for parallel v3 requests (match your plan's limit). Cost is roughly 1-3k characters per 1-3 minute video.
- **Kokoro** loads its model once per process and runs on the GPU when CUDA is available. Voice: `KOKORO_VOICE`. Install `espeak-ng` with `brew install espeak-ng` / `apt install espeak-ng`.
- **OpenAI** synthesizes segments in parallel (up to 6 at once). Configure with `OPENAI_TTS_MODEL`, `OPENAI_TTS_VOICE` and `OPENAI_TTS_INSTRUCTIONS` (sent only to `gpt-*` models).

> **Intel Mac users:** PyTorch 2.4+ has no x86_64 macOS wheels, so Kokoro is unavailable. Use an ElevenLabs or OpenAI narrator.

---

## Effort levels

`--effort` controls how thorough the validation is and whether web search is used.

| Level              | Fact-check                 | Web search                                   | Segments |
| ------------------ | -------------------------- | -------------------------------------------- | -------- |
| `low`              | Light, obvious errors only | Never                                        | 3 to 4   |
| `medium` (default) | Spot-check key claims      | No                                           | 4 to 6   |
| `high`             | Thorough                   | Via research_agent (pre-script web research) | 5 to 8   |

---

## Resuming a run

Every run is checkpointed after each pipeline stage. If it crashes or you stop it, resume with the same run ID:

```bash
python main.py --topic "..." --run-id <previous-run-id>
```

The pipeline continues from the last completed stage. If the pipeline had already finished, it skips straight to rendering (an existing `final.mp4` is reused, not re-rendered, unless `--quality` asks for a different resolution). Other options (`--template`, `--narrator`, `--theme`, ...) are not re-applied to a checkpointed run, and context (`--context`, `--url`, `--github`) has to be passed again.

### Preview, then full render

```bash
# Step 1: generate script + animation, render a 480p preview
python main.py --topic "how B-trees work" --preview
# → output/<run-id>/preview.mp4

# Step 2: full render from the checkpoint (the pipeline is not re-run)
python main.py --topic "how B-trees work" --run-id <run-id>
# → output/<run-id>/final.mp4 (plus visual QA)
```

The render quality is stored in the run's `manifest.json` when the pipeline finishes. Passing a different `--quality` with `--run-id` updates the manifest and re-renders the finished run at that resolution (for example `--quality 4k` after a `low` first pass).

---

## Configuration

All settings can be set in `.env` or as environment variables (see `.env.example`):

| Variable                  | Default                               | Options / meaning                                          |
| ------------------------- | ------------------------------------- | ---------------------------------------------------------- |
| `ANTHROPIC_API_KEY`       | required                              | Claude API key                                             |
| `CLAUDE_MODEL`            | `claude-opus-5-5`                     | Model for every agent (see [Models](#models))              |
| `CLAUDE_MODEL_<AGENT>`    | `CLAUDE_MODEL`                        | Per-agent model override                                   |
| `CLAUDE_EFFORT_<AGENT>`   | per agent                             | Per-agent effort, e.g. `low`, `medium`, `high`             |
| `RENDER_BACKEND`          | `auto`                                | `auto`, `local`, `docker` (see [Rendering](#rendering))    |
| `MANIM_QUALITY`           | `medium`                              | `low`, `medium`, `high`, `4k`                              |
| `TTS_BACKEND`             | `kokoro`                              | `kokoro`, `openai`, `elevenlabs`                           |
| `OPENAI_TTS_MODEL`        | `gpt-4o-mini-tts`                     | OpenAI speech model                                        |
| `OPENAI_TTS_VOICE`        | `alloy`                               | OpenAI voice                                               |
| `OPENAI_TTS_INSTRUCTIONS` | a teaching-voice prompt               | Delivery instructions (`gpt-*` TTS models only)            |
| `KOKORO_VOICE`            | `af_heart`                            | Kokoro voice                                               |
| `NARRATOR`                | unset                                 | Default narrator: `aria`, `milo`, `kokoro`, `alloy`        |
| `ELEVENLABS_VOICE_ID`     | Skye (`1iNDh1muacMMMHXvS7Ym`)         | ElevenLabs voice for `TTS_BACKEND=elevenlabs`              |
| `DEFAULT_EFFORT`          | `medium`                              | `low`, `medium`, `high`                                    |
| `DEFAULT_AUDIENCE`        | `intermediate`                        | `beginner`, `intermediate`, `expert`                       |
| `DEFAULT_TONE`            | `casual`                              | `casual`, `formal`, `socratic`                             |
| `DEFAULT_THEME`           | `chalkboard`                          | `chalkboard`, `light`, `colorful`                          |
| `OUTPUT_DIR`              | `./output`                            | any path                                                   |
| `CHECKPOINT_DB`           | `pipeline_state.db`                   | any path                                                   |
| `SERVER_HOST`             | `127.0.0.1`                           | Server bind address (overridden by `--host`)               |
| `SERVER_PORT`             | `8000`                                | Server port (overridden by `--port`)                       |
| `MAX_CONCURRENT_JOBS`     | `3`                                   | Server jobs that run at once; the rest wait their turn     |
| `CHALKBOARD_TEXT_FONT`    | CMU Serif, else Inter                | Font family for scene text                                 |
| `CHALKBOARD_CODE_FONT`    | JetBrains Mono NL, else DejaVu Sans Mono | Font family for code (use a ligature-free face)         |

---

## API server

Chalkboard includes a FastAPI server that exposes the pipeline over HTTP with SSE streaming for live progress.

### Start

```bash
python run_server.py                    # http://127.0.0.1:8000
python run_server.py --reload           # dev mode (auto-reload)
python run_server.py --port 9000
python run_server.py --host 0.0.0.0     # serve your LAN (no auth: trusted networks only)
```

The server runs up to `MAX_CONCURRENT_JOBS` jobs at once (default 3); further jobs wait in line.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST /api/jobs` | Create job | Start the pipeline for a topic (JSON body) |
| `POST /api/jobs/upload` | Create job with files | Multipart form (same fields plus file uploads) |
| `GET /api/jobs` | List jobs | All jobs in this server session |
| `GET /api/jobs/{id}` | Get job | Poll status and output file list |
| `GET /api/jobs/{id}/events` | SSE stream | Live pipeline progress events |
| `GET /api/jobs/{id}/files/{filename}` | Download | Serve `final.mp4`, `captions.srt`, etc. |
| `GET /api/claude-status` | Claude status | Parsed Claude status feed (cached 5 minutes), shown in the UI nav |
| `GET /api/meta` | Server info | Defaults (quality, narrator, effort, ...), narrators with availability, render backend, model, running jobs |

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
    "quality": "high",
    "narrator": "aria",
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

`quality` is `low`, `medium`, `high` or `4k`; omit it (or send `null`) to use the server's `MANIM_QUALITY`. `narrator` is `aria`, `milo`, `kokoro` or `alloy`; omit it to use `NARRATOR` (or `TTS_BACKEND`). `GET /api/meta` lists the narrators and which ones this server has keys for. The multipart route validates these fields the same way (422 on an unknown value).

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

The server includes a built-in UI with no build step. Start the server and open `http://localhost:8000`:

- A generate form with **Topic**, **Effort** and **Audience** up front, plus **Advanced options**: Tone, Theme, Template, Quality, Speed, Visual QA density, Burn captions, Generate quiz, URL and GitHub inputs, and a **file upload zone** (drag and drop files or folders)
- Live stage-by-stage progress while the pipeline runs, and a jobs menu for jobs in flight
- A video player with download links when the job completes
- A shared nav with Generate / Library tabs, a Claude API status indicator and a light/dark theme toggle

The pages live in `server/static/` (`index.html`, `library.html`, `video.html`) and share `app.css` and `app.js`.

### Video Library

A library browser is available at `http://localhost:8000/library`:

- Responsive grid with thumbnails, title, duration, quality and date
- **Search** across topic and script text; **sort** by newest, oldest, longest, or shortest; pagination
- **Detail page** at `/library/{run_id}` with the player, downloads, generation settings, an interactive transcript (click a line to seek) and subtitles from `captions.srt`
- **Generate again with these settings** pre-fills the generate form
- Theme-keyed fallback thumbnails for runs without a rendered thumbnail

All generated videos are indexed into a SQLite database (`library.db`); existing runs in `output/` are backfilled at startup. Storage sits behind a `LibraryStore` interface, so it can be swapped for another backend.

#### Library API

| Method | Path | Description |
|--------|------|-------------|
| `GET /api/library` | List videos | Supports `q`, `sort`, `limit`, `offset` query params |
| `GET /api/library/{run_id}` | Get video | Full metadata + dynamic `output_files` list |
| `DELETE /api/library/{run_id}` | Delete video | Removes from the index; `?files=true` also deletes `output/<run_id>/`. Without it the run is re-indexed on the next library listing, because its files are still in `output/` |

---

## Python SDK

If you'd rather call Chalkboard from a script than the CLI, there's a typed sync Python client at [`sdk/python/`](sdk/python). It targets the **hosted** version at <https://chalkboard.studio/api/v1>:

```python
from chalkboard import ChalkboardClient

client = ChalkboardClient(api_key="chk_live_...")  # create one at /account
job    = client.create_job(topic="How hash tables work")
final  = client.wait_for_completion(job.id, timeout=600)
client.download_file(final.id, "final.mp4", out_path="hash-tables.mp4")
```

The same client also works against a **self-hosted** Chalkboard (`python run_server.py`): pass `base_url="http://127.0.0.1:8000/api"` (no `/v1`) and leave out `api_key`, since this repo's server has no auth. Hosted-only methods (cancel, retry, rerender, webhooks, API keys) are not available there; see [`sdk/python/README.md`](sdk/python/README.md).

Install:

```bash
pip install https://github.com/nicglazkov/Chalkboard/releases/download/sdk-py/v0.1.1/chalkboard_sdk-0.1.1-py3-none-any.whl
```

Full SDK reference + examples: [`sdk/python/README.md`](sdk/python/README.md). Hosted API docs: <https://chalkboard.studio/docs/api>.

---

## Development

### Run tests

```bash
pip install -r requirements-dev.txt   # requirements.txt + pytest, pytest-asyncio
pytest
```

The component, move and template tests import Manim and are skipped without it; install `requirements-render.txt` (plus TeX) to run them.

### Project structure

```
pipeline/
  agents/            # research_agent, script_agent, fact_validator, manim_agent, code_validator, layout_checker, orchestrator
  tts/               # kokoro, openai, elevenlabs backends
  llm.py             # every Claude call: call_json, per-agent model/effort, thinking-safe parsing
  render.py          # render backends (local / docker): render + layout-check commands
  ast_guards.py      # deterministic checks on generated scene code
  design_tokens.py   # pipeline-side shim over docker/chalkboard_tokens.py
  context.py         # collect_files, load_context_blocks, fetch_url_blocks, measure_context
  graph.py           # LangGraph state machine
  state.py           # PipelineState TypedDict + ValidationResult
  render_trigger.py  # calls TTS, writes output files
  retry.py           # timeout constants, api_call_with_retry, TimeoutExhausted
  visual_qa.py       # post-render frame review
docker/
  chalkboard_base.py        # layout/timing checks every scene inherits
  chalkboard_style.py       # LaTeX preamble + fonts
  chalkboard_tokens.py      # design tokens (single source)
  chalkboard_components.py  # components
  chalkboard_moves.py       # moves
  chalkboard_templates/     # scene templates
  examples/                 # design_system_demo.py
  Dockerfile                # manimcommunity/manim:v0.21.0 + fonts + TeX packages
  render.sh                 # render entrypoint inside the image
server/              # FastAPI app, job store, routes, library, static frontend
sdk/python/          # typed Python client for the hosted (or self-hosted) API
tests/               # one test file per module
config.py            # env var loading, per-agent model/effort
main.py              # CLI entry point
run_server.py        # API server entry point
requirements.txt         # pipeline + server
requirements-render.txt  # adds manim 0.21.0 for local rendering
requirements-dev.txt     # adds pytest + pytest-asyncio
```

See [CLAUDE.md](CLAUDE.md) for architecture, design decisions, and contribution guidelines.
