# Chalkboard: Architecture & Contributor Guide

This document is the reference for anyone (human or AI agent) contributing to Chalkboard. It covers architecture, design decisions, known pitfalls, and how to extend the project. User-facing setup lives in `README.md`; a Claude Code skill for running the tool lives in `.claude/skills/chalkboard/SKILL.md`.

---

## What this project does

Chalkboard takes a topic and produces a narrated Manim animation. It can be used through the web UI (`python run_server.py`) or the CLI (`python main.py`). The pipeline runs fully automatically with retry logic at each stage.

```
main.py
  └─ LangGraph pipeline (pipeline/graph.py)
       ├─ init              normalize state, set run_id
       ├─ research_agent    (effort=high only) web research brief
       ├─ script_agent      Claude writes the narration script
       ├─ fact_validator    Claude fact-checks the script
       ├─ manim_agent       Claude writes Manim scene code on the design system
       ├─ code_validator    syntax + AST guards + advisory Claude review
       ├─ layout_checker    headless dry-run; bounding boxes, zones, timing
       ├─ render_trigger    TTS → audio, write output files
       └─ escalate_to_user  prompt (or auto-abort) when max retries hit
```

After the graph completes, `main.py` renders the scene through `pipeline/render.py` (natively or in Docker), merges the voiceover with host ffmpeg into `final.mp4`, then runs visual QA.

---

## Repo layout

```
pipeline/
  graph.py           LangGraph state machine + routing functions
  state.py           PipelineState TypedDict, ValidationResult
  llm.py             Every Claude call: call_json, model_params, response_text, get_client
  render.py          Render backends (local / docker): render_cmd, check_cmd, video_path
  ast_guards.py      Deterministic AST checks on generated scene code (run_guards)
  design_tokens.py   Shim that loads docker/chalkboard_tokens.py by path and re-exports it
  render_trigger.py  Calls TTS, writes output files
  retry.py           TimeoutExhausted, api_call_with_retry, timeout constants
  context.py         collect_files, load_context_blocks, fetch_url_blocks, measure_context
  visual_qa.py       Post-render frame sampling + Claude review
  agents/
    research_agent.py   Web research brief (Claude + web search), async
    script_agent.py     Script generation (Claude), async
    fact_validator.py   Fact checking (Claude), async
    manim_agent.py      Manim code generation (Claude, streamed), async
    code_validator.py   Syntax + AST guards + advisory Claude review, async
    layout_checker.py   Headless scene dry-run + layout_report.json parsing
    orchestrator.py     escalate_to_user node
  tts/
    base.py             Backend registry (get_backend), atempo helpers
    kokoro_tts.py       Local TTS (PyTorch), model cached per process
    openai_tts.py       OpenAI TTS API, segments in parallel
    elevenlabs_tts.py   ElevenLabs REST (httpx); request stitching on eleven_v4
    voices.py           Named narrators: (backend, voice id, model) per name
docker/                 Scene runtime (imported by every generated scene) + render image
  chalkboard_base.py        ChalkboardSceneBase mixin: per-segment bounding-box, zone and timing checks
  chalkboard_style.py       House typography: shared LaTeX preamble, text/code fonts (applied on import)
  chalkboard_tokens.py      Design tokens, SINGLE SOURCE of colors/sizes/spacing/strokes/motion
  chalkboard_components.py  Components (ChalkBox, ChalkCode, EquationGroup, ...)
  chalkboard_moves.py       Moves (reveal_with_emphasis, derivation_step, ...)
  chalkboard_templates/     AlgorithmTemplate, CodeTemplate, CompareTemplate, DerivationTemplate,
                            HowtoTemplate, TimelineTemplate (+ _base.py)
  examples/design_system_demo.py  Demo scenes that exercise the whole design system
  Dockerfile                manimcommunity/manim:v0.21.0 + fonts + TeX packages; PYTHONPATH=/render
  render.sh                 Render entrypoint in the image; --check mode for the layout dry-run
tests/                One test file per module
config.py             Env var loading, CLAUDE_MODEL, agent_model(), agent_effort(), RENDER_BACKEND
main.py               CLI entry point, async graph runner, render + QA + quiz
run_server.py         uvicorn entrypoint (python run_server.py [--reload] [--port N] [--host H])
requirements.txt          Pipeline + server dependencies
requirements-render.txt   requirements.txt + manim==0.21.0 (local rendering)
requirements-dev.txt      requirements.txt + pytest, pytest-asyncio
server/
  app.py              FastAPI app factory (create_app), lifespan: library init + backfill
  jobs.py             Job dataclass, JobStore, run_job (MAX_CONCURRENT_JOBS semaphore), _do_render
  models.py           Pydantic CreateJobRequest / JobResponse
  routes.py           /api/jobs routes, /api/claude-status, task spawning
  library.py          VideoMeta model, LibraryStore ABC, SQLiteLibraryStore
  library_routes.py   /api/library routes + /library page routes
  upload.py           Multipart upload handling for /api/jobs/upload
  static/
    index.html        Generate page (form → SSE progress → player + downloads)
    library.html      Library grid (/library)
    video.html        Video detail page (/library/{run_id})
    app.css           Shared styles for all pages
    app.js            Shared helpers, nav (tabs, Claude status, jobs menu, theme toggle), window.jobStatus store
```

---

## State schema

`PipelineState` (TypedDict in `pipeline/state.py`), every field:

| Field | Type | Description |
|-------|------|-------------|
| `topic` | str | User-provided topic |
| `title` | str | YouTube-style title generated by `script_agent` |
| `run_id` | str | UUID for this run, used as output directory name |
| `effort_level` | str | `"low"` / `"medium"` / `"high"` |
| `audience` | str | `"beginner"` / `"intermediate"` / `"expert"` |
| `tone` | str | `"casual"` / `"formal"` / `"socratic"` |
| `theme` | str | `"chalkboard"` / `"light"` / `"colorful"` |
| `script` | str | Full narration script |
| `script_segments` | list[dict] | `[{"text": str, "estimated_duration_sec": float, "cue_text"?: str}]`; `text` is clean, `cue_text` keeps the `[[k]]` cue markers |
| `manim_code` | str | Complete Python source for the Manim scene |
| `script_attempts` | int | Number of times the script has been revised (starts 0) |
| `code_attempts` | int | Hard code failures so far (syntax, AST guards, layout). Starts 0 |
| `fact_feedback` | str \| None | Feedback from fact_validator; **None means approved** |
| `code_feedback` | str \| None | Feedback from code_validator / layout_checker; **None means approved** |
| `needs_web_search` | bool | script_agent flagged it wants web search |
| `user_approved_search` | bool | User approved web search (unused in current routing) |
| `context_file_paths` | list[str] | Paths of loaded context files; informational only, never read by agents |
| `speed` | float | Narration speed multiplier (default `1.0`), passed to the TTS backend |
| `template` | str \| None | Scene template (`algorithm`, `code`, `compare`, `derivation`, `howto`, `timeline`); `None` = none |
| `research_brief` | str \| None | Brief from `research_agent`; `None` if not run or effort is not high |
| `research_sources` | list[str] | URLs/citations from `research_agent` |
| `search_warning` | str \| None | Set by `research_agent` when search failed or found little; printed by `_print_progress` |
| `interactive` | bool | `False` = never wait for stdin: `escalate_to_user` auto-aborts, `TimeoutExhausted` re-raises, layout-only failures render anyway. CLI: `sys.stdin.isatty() and not --yes`; server: always `False` |
| `quality` | str \| None | `"low"` / `"medium"` / `"high"` / `"4k"`; `None` = `MANIM_QUALITY`. Written into `manifest.json` by `render_trigger` |
| `narrator` | str \| None | Name from `pipeline/tts/voices.py` (`aria`, `milo`, `kokoro`, `alloy`); `None` = `NARRATOR`, else `TTS_BACKEND`'s default voice. Resolved by `render_trigger`, recorded in `manifest.json` |
| `layout_renderable` | bool | Set by `layout_checker`: `True` when the last dry-run ran to completion (only layout/timing violations, no crash) |
| `claude_review_failures` | int | Count of advisory rejections by code_validator's Claude review; reset to 0 by code_validator's own hard failures (syntax, Mobject arithmetic, AST guards). Layout failures leave it as is, so once it is over the limit later rejections go straight to the dry-run |
| `code_feedback_advisory` | bool | `True` when the current `code_feedback` came from the Claude review, not a deterministic check |

`_init_state` in `graph.py` fills defaults for the optional fields, including `quality`, `narrator`, `claude_review_failures` and `code_feedback_advisory`. A new field has to be threaded through `state.py`, `_init_state`, `main.run()` (parameter and `input_state`) and `server/jobs.py` (`Job`, `JobStore.create`, the `run(...)` call). `layout_renderable` has no default there; it is only read with `.get()`.

### Critical invariant: None = approved

Both `fact_feedback` and `code_feedback` use `None` as the approval signal. The routing functions check `not state.get("...")` to decide whether to proceed. If a validator returns any truthy string (even `"Looks good!"`), routing treats it as a failure and retries. **Always return `None` on approval.**

---

## Routing logic

```python
# pipeline/graph.py
CLAUDE_REVIEW_ADVISORY_LIMIT = 2

def _after_fact_validator(state):
    if not state.get("fact_feedback"):        # None = approved
        return "manim_agent"
    if state["script_attempts"] >= 3:
        return "escalate_to_user"
    return "script_agent"

def _after_code_validator(state):
    if not state.get("code_feedback"):        # approved
        return "layout_checker"
    if (state.get("code_feedback_advisory")
            and state.get("claude_review_failures", 0) > CLAUDE_REVIEW_ADVISORY_LIMIT):
        return "layout_checker"               # Claude review is advisory: proceed anyway
    if state["code_attempts"] >= 3:
        return "escalate_to_user"
    return "manim_agent"

def _after_layout_checker(state):
    if not state.get("code_feedback"):        # passed
        return "render_trigger"
    if state["code_attempts"] >= 3:
        if state.get("layout_renderable") and not state.get("interactive", True):
            return "render_trigger"           # layout warnings only: render the last scene anyway
        return "escalate_to_user"
    return "manim_agent"
```

**Do not** check attempt counters to determine approval; check the feedback field. The counters only decide when to give up.

**Advisory Claude review.** The deterministic gates are the AST checks in `code_validator` and the headless dry-run in `layout_checker`. Claude's semantic review can reject a scene (triggering a revision), but its rejections increment `claude_review_failures`, not `code_attempts`, so a nit-picking review cannot exhaust the retry budget meant for real bugs. Once the review has rejected more than `CLAUDE_REVIEW_ADVISORY_LIMIT` times in a row, the scene goes to the layout dry-run anyway.

**Layout-only failures.** When `code_attempts` hits 3 on a layout failure, a scene that ran end to end (`layout_renderable=True`) is rendered anyway in non-interactive runs (visual QA still reviews it). A scene that crashed, or any failure in an interactive run, escalates.

---

## Claude calls (`pipeline/llm.py`)

Every agent (plus visual QA, the quiz and context measurement) talks to Claude through `pipeline/llm.py`:

- **`call_json(agent, *, content, schema, system=None, max_tokens=16000, tools=None, client=None, stream=False)`**: blocking structured-output call returning `(parsed_dict, response)`. Agents run it inside `api_call_with_retry` (it is sync). `stream=True` uses `client.messages.stream(...).get_final_message()`; `manim_agent` streams because scene code is long.
- **`model_params(agent)`**: `model` from `config.agent_model(agent)`, `thinking={"type": "adaptive"}` and `output_config={"effort": agent_effort(agent)}`. Haiku models get neither thinking nor effort.
- **`response_text(response)`**: returns the **last** text block. Current models think, so `content[0]` is often a `thinking` block (and with web search, server tool blocks come first). Never read `response.content[0].text`. Raises `ClaudeRefused` on `stop_reason == "refusal"` and `ClaudeTruncated` on `max_tokens`.
- **`get_client(pdf=False)`**: shared client per process (`max_retries=3`, so the SDK retries 429/5xx itself); `pdf=True` adds the `pdfs-2024-09-25` beta header. `has_pdf(context_blocks)` decides.
- **`web_search_tool(agent)`**: `web_search_20260209`, or `web_search_20250305` for Haiku / Sonnet 4.5 / Opus 4.5.

**Models and effort** (`config.py`): `CLAUDE_MODEL` defaults to `claude-opus-5-5`. `agent_model(agent)` reads `CLAUDE_MODEL_<AGENT>`, `agent_effort(agent)` reads `CLAUDE_EFFORT_<AGENT>` with defaults research=medium, script=high, fact=medium, manim=high, code_validator=medium, visual_qa=high, quiz=low. Agent keys: `research`, `script`, `fact`, `manim`, `code_validator`, `visual_qa`, `quiz`. `measure_context` counts tokens against the `manim` model (the agent that sees the most context).

Structured output schemas must have `"additionalProperties": false` on **every nested object**, not just the top level.

---

## Agents

All agents are `async def` and wrap their `call_json` call with `api_call_with_retry` from `pipeline/retry.py`. LangGraph awaits async nodes directly.

### research_agent
- Agent key `research`, default `max_tokens` (16000), timeout `TIMEOUT_RESEARCH_AGENT` = 300s
- Output: `{"research_brief": str, "sources": list[str], "search_warning": str|null}`
- Only runs when `effort_level == "high"` (`_after_init`). Uses `web_search_tool("research")`
- When present, `research_brief` is injected into `script_agent`'s message and `script_agent`'s own web search is disabled
- **Graceful fallback:** on `TimeoutExhausted`, `RuntimeError` or `ValueError` it returns `research_brief=None` and a `search_warning`; the script then relies on training data. The pipeline does not abort.

### script_agent
- Agent key `script`, default `max_tokens`, timeout `TIMEOUT_SCRIPT_AGENT` = 300s
- Output: `{"title": str, "script": str, "segments": [{text, estimated_duration_sec}], "needs_web_search": bool}`
- Web search enabled when `effort_level == "high"` or `user_approved_search`, **unless** `research_brief` is set
- Injects `AUDIENCE_INSTRUCTIONS` / `TONE_INSTRUCTIONS`
- **Narration is spoken:** the system prompt requires math and code to be written the way a lecturer says them ("e to the x", "x squared", "n log n"), never symbols, LaTeX or code syntax, because TTS reads the text aloud while the animation shows the notation.
- **Cue markers:** segment text carries `[[1]]`, `[[2]]`, ... right before the words where visuals land (2-5 per segment, renumbered per segment). The agent returns segments with clean `text` plus the marked `cue_text`, and a clean `script` (see Word-level sync below).

### fact_validator
- Agent key `fact`, `max_tokens` 8000, timeout `TIMEOUT_FACT_VALIDATOR` = 180s
- Output: `{"verdict": "approved"|"needs_revision", "feedback": str}`
- Effort-based instructions: low = light check, medium = spot-check, high = thorough

### manim_agent
- Agent key `manim`, `max_tokens` 48000, **streamed**, timeout `TIMEOUT_MANIM_AGENT` = 900s
- Output: `{"manim_code": str}`
- Scene class **must** be `ChalkboardScene(ChalkboardSceneBase, Scene)`
- The system prompt targets **Manim CE v0.21.0** and teaches the design system: required scaffold (imports of tokens, components, moves, templates), the component / move / template vocabulary, a MATH section (all math through `math_tex` / `tex` / `EquationGroup` / `ChalkMatrix`, house macros, role-colored terms, aligned derivations), the token API and what is forbidden in scene code (raw hex/Manim color constants, raw `font_size`/`buff`/`stroke_width`/`run_time` literals, math in `Text`), role and motion semantics, five annotated exemplars (the fifth: word-level sync with `self.cue(k)`), the WORD-LEVEL SYNC rules, the verified pitfall list (below), LAYOUT RULES and the CLEAN SLATE rule
- `_format_segments` shows each segment's marked `cue_text` with the estimated time of each marker (measured `cues` when present, e.g. on QA regeneration)
- `THEME_SPECS` are generated from the tokens via `render_prompt_block(theme)`, so prompt and renderer never drift
- `TEMPLATE_SPECS` (algorithm, code, compare, derivation, howto, timeline) tell the agent to instantiate the matching template class with a `beats` dict
- **Revision rounds:** when `code_feedback` is set and prior code exists, the agent is asked for minimal targeted edits to the prior scene instead of a rewrite
- manim_agent does not touch `code_attempts`; code_validator and layout_checker count failures

### code_validator
- Agent key `code_validator`, `max_tokens` 8000, timeout `TIMEOUT_CODE_VALIDATOR` = 240s
- Gates, in order (each hard failure increments `code_attempts`, resets `claude_review_failures`, sets `code_feedback_advisory=False`):
  1. `ast.parse()` syntax check
  2. Mobject arithmetic scan (`Text(...) + 0` and friends crash at render time)
  3. `run_guards(tree, code)` from `pipeline/ast_guards.py`
- Then the Claude review (advisory, see Routing). The review prompt lists the design-system APIs as real and confirmed-correct Manim idioms so the reviewer does not flag them.
- Output: `{"verdict": "approved"|"needs_revision", "feedback": str}`

### layout_checker
- No Claude call. Timeout `TIMEOUT_LAYOUT_CHECKER` = 180s
- Writes `scene.py` and a stub `segments.json` (estimated durations, and `cues` estimated from each marker's character position; measured `cues`/`actual_duration_sec` are kept when the segments already have them) to `output/<run_id>/`, deletes any stale `layout_report.json`, then runs `render.check_cmd(run_dir)`: a native Python dry-run (local) or `docker run ... chalkboard-render --check`. Both set `dry_run=True`, `frame_rate=1`
- `ChalkboardSceneBase` writes `layout_report.json`: `{"passed": bool, "violations": [{type, segment, description, ...}]}`
- Violation types: `timing_overrun` (1.5s tolerance), `off_screen` (0.1 unit tolerance), `overlap` (partial intersection; full containment is ignored), `zone_boundary_overlap` (containment across the left/right zones), `zone_collision` (a left-zone element crossing x = -0.5 while right-zone content is present, or the mirror case). Elements entirely above y = 2.9 (title band) are excluded from zone checks. `sync_drift` (visuals more than the tolerance behind the narration at a segment boundary) comes from `next_segment` / `end_layout_check`. `cue_late` (a `self.cue(k)` reached more than 0.6 s after its word) and `cue_unused` (a segment with cue markers whose scene never calls `cue()`; template-driven scenes are exempt) come from the word-level sync. The report also carries `cue_log`: `[{segment, cue, spoken_at, visual_at, lag}]` for every `cue()` call
- Return values: passed → `code_feedback=None, layout_renderable=True`; violations → formatted feedback, `code_attempts + 1`, `layout_renderable=True`; crash, timeout, missing or unreadable report, failure to start → feedback with `layout_renderable=False` (the stderr tail is included for crashes)
- `ChalkboardSceneBase` overrides `play()` (accumulates `run_time`, skipping `Wait` so `wait()` is not double counted) and `wait()` to measure per-segment time. Generated scenes call `self.begin_segment(n, duration=_d[n])` and `self.end_layout_check()`

### Word-level sync (cue markers)

Segment-level sync pins every segment start to the narration; cue markers pin the animations inside a segment to the words.

1. **Script** (`script_agent`): `[[k]]` markers before the cued words. `pipeline/cues.py` owns parsing: `parse_cues(text) -> (clean, {k: char_offset})` (offset = first letter of the cued word in the clean text), `strip_cues`, `clean_segments`. Clean text is what everything else reads (fact check, TTS, `script.txt`, captions, chapters, visual QA, quiz, library); the marked text lives only in a segment's `cue_text`.
2. **Times** (TTS): backends return `(path, durations, cue_times)`; `cue_times[i][k-1]` is the time of marker k in seconds from segment i's audio start (`None` for a skipped number). ElevenLabs uses `POST /v1/text-to-speech/{voice}/with-timestamps` (same body and stitching; JSON `audio_base64` + `alignment.characters` / `character_start_times_seconds`, which match the input text one to one, verified 2026-10-02) via `cue_times_from_alignment`; Kokoro maps `KPipeline.Result.tokens` (`text`, `start_ts` relative to each chunk) via `cue_times_from_tokens`; OpenAI returns the old 2-tuple and `render_trigger` fills `proportional_cue_times`. Speed scaling divides cue times by `speed` too.
3. **segments.json** per segment: `text` (clean), `actual_duration_sec`, `cues`, and `cue_text` when the segment has markers.
4. **Scene** (`ChalkboardSceneBase.cue(k)`): waits until `segment narration start + cues[k-1]` and returns, so the next `play()` starts on the word. Segment narration start = sum of the earlier segment budgets (the voiceover is the segments back to back). Cues load lazily from `segments.json` next to the scene module (`sys.modules[type(self).__module__].__file__`), falling back to the report directory; tests assign `scene._cues = {seg: [t, ...]}` (or a list of lists). Unknown cue numbers warn and return `False` without waiting; `has_cue(k)` checks first. Templates call `_cue(1)` before each segment's main reveal and `_cue(2)` before the second element (callout, right-hand point, next derivation line), and `_rest` waits out `segment_time_left()`.
5. **Scene clock:** in a real render (`not config.dry_run` and `frame_rate >= 10`) `_scene_time()` is `renderer.time`, the frames actually written. Manim rounds every play UP to whole frames (`np.arange(0, run_time, 1/fps)`) and static waits DOWN (`int(duration / dt)`), so summing requested run times drifts from the video by a frame per play. The 1 fps dry-run and unit tests keep the internal `_sync_tracked` clock.
6. **Captions:** `main._caption_cues` writes one SRT line per sentence (long sentences split at commas), timed by interpolating through the segment's cue anchors.

---

## Design system

Generated scenes are composed from a runtime that lives in `docker/` and is importable by module name during rendering (`PYTHONPATH` includes `docker/` locally, `/render` in the image).

- **Tokens** (`docker/chalkboard_tokens.py`): the single source of truth for colors (per theme: `surface` and `role`), type sizes, spacing, stroke widths and motion (`snap`, `emphasis`, `settle`, `grand`, each a `{run_time, rate_func}`), plus `lag`. Accessed through `T(theme=...)`: `t.role()`, `t.surface()`, `t.type()`, `t.space()`, `t.stroke_width()`, `t.motion()`, `t.lag()`, `t.bg`, `t.body`. No Manim import at module level. `render_prompt_block(theme)` formats the table for the prompt. **Edit tokens only here**; `pipeline/design_tokens.py` loads this file by path and registers it as `sys.modules["chalkboard_tokens"]` so pipeline and renderer share one class.
- **Components** (`docker/chalkboard_components.py`): `ChalkBox`, `ChalkArrow`, `ChalkCode`, `Callout`, `StepCounter`, `ChalkAxis`, `ChalkAxes`, `ChalkPanel`, `ChalkBadge`, `EquationGroup`, `ChalkMatrix`, `NetworkNode`, plus `math_tex()`, `tex()`, `resolve_motion()`. Every component takes `theme=` and has `.highlight(role)` / `.mute()`. Size arguments are floors (components grow to fit their label).
- **Moves** (`docker/chalkboard_moves.py`): `reveal_with_emphasis`, `compare_split`, `focus_zoom`, `morph_show_equivalence`, `cascade_reveal`, `progressive_step`, `annotate_and_pause`, `chapter_transition`, `derivation_step`, `transform_equation`, `emphasize_term`. Each takes the scene first and plays its own animations.
- **Templates** (`docker/chalkboard_templates/`): `Template(scene, beats, theme=...)` validates `beats` strictly at construction (raising `ValueError`, which surfaces in the layout dry-run and flows back to the agent) and `render_all(segment_durations)` emits every `begin_segment`, animation and wait. A template-driven scene has no `# ── Segment N:` blocks of its own.
- **House style** (`docker/chalkboard_style.py`): imported by `chalkboard_base`, so every scene gets it. Sets Manim-wide defaults: one `TexTemplate` for `Tex`/`MathTex` whose preamble loads babel, lmodern, microtype, amsmath, amssymb, mathtools, bm, mathrsfs, dsfont, cancel, xcolor, siunitx and defines `\R \N \Z \Q \C \E \dd \Var \Cov \tr \rank \argmax \argmin`; `Text`/`MarkupText`/`Paragraph` font = CMU Serif when installed (else Inter, DejaVu Sans), so prose and math share Computer Modern; `Code` font = JetBrains Mono NL, else DejaVu Sans Mono. Code fonts must be ligature-free (ligatures break Manim's per-glyph `Code` layout on `<=`, `->`, `==`). Override with `CHALKBOARD_TEXT_FONT` / `CHALKBOARD_CODE_FONT`.
- **AST guards** (`pipeline/ast_guards.py`): `run_guards()` runs every check in `_ALL_GUARDS` and returns one numbered feedback list or `None`. Structural: numeric `self.wait()` literals, scene base inheritance and import, `Code(code=...)`, `begin_segment` / `end_layout_check` presence. Design system: raw hex colors, Manim color constants, required design-system imports, raw primitive construction (use the component). Heuristic: `.code` attribute access, deep `next_to` chains, `segments.json` loading. Crash patterns: `self.play(*[...])` over a filtered comprehension that may be empty, `GrowArrow`. Math: math-looking strings in `Text`. Geometry: horizontal array overflowing the left zone.
- **Demo** (`docker/examples/design_system_demo.py`): `DemoDerivation`, `DemoCalculus`, `DemoLinearAlgebra`, `DemoAlgorithm`. Render from the repo root with `PYTHONPATH=docker CHALKBOARD_REPORT_DIR=/tmp manim -ql docker/examples/design_system_demo.py DemoCalculus`; each writes a `layout_report.json`, so it doubles as a layout regression check.

**Adding to the design system:** add the component/move/template in `docker/`, export it, add it to the manim_agent prompt (vocabulary and REQUIRED SCAFFOLD imports), to the "real APIs" list in code_validator's review prompt, to `_MOBJECT_CONSTRUCTORS` in `code_validator.py` if it builds a mobject, to `chalkboard_templates/__init__.py` and `TEMPLATE_SPECS` / `TEMPLATE_CHOICES` (`main.py`) for a template, and tests under `tests/test_chalkboard_*.py`. `docker/Dockerfile` copies `docker/chalkboard_*.py` and `docker/chalkboard_templates/` into the image.

---

## Manim v0.21.0 known API pitfalls

These are baked into `manim_agent`'s system prompt (verified on v0.21.0). When generated code fails to render, add the pattern there and here, and add an AST guard when the pattern is mechanically detectable:

- **`Brace.get_text(*text)`** does not accept `font_size` (TypeError); scale the result: `lbl = brace.get_text("x"); lbl.scale(0.8)`. For math labels use `brace.get_tex(r"...")`.
- **`VGroup.arrange()` returns the group**, so `VGroup(*items).arrange(RIGHT, buff=...)` chains fine.
- Always pass `run_time` as a keyword argument (or `**resolve_motion(...)`).
- **Never `VGroup(*self.mobjects)`**: `self.mobjects` can contain non-VMobjects; use `*[FadeOut(m) for m in self.mobjects]`.
- **`DASHED` is not a Manim constant**: use `DashedLine(start, end, ...)`.
- **`self.wait(0)` raises ValueError**: guard with `_r = max(0.0, _d[i] - X); if _r > 0: self.wait(_r)`. Matters especially when `--speed > 1.0` shortens segments.
- **`self.play()` with zero animations raises ValueError**: never star-unpack a filtered list that may be empty; build it and guard with `if anims:`.
- Pad durations with `_d = _d + [2.0] * max(0, N - len(_d))`; segment numbers and `_d` indices are both 0-based.
- Pointer labels plus a description line below an array need at least `buff=t.space("lg")` between them.
- Never animate a label with `ptr.copy().next_to(...)` inside `.animate` (positions are captured before the frame); pass the destination coordinate directly.
- **`Code`**: takes `code_string=` not `code=`, and `paragraph_config={"font_size": N}` not `font_size=` (both TypeError); lines are `code_obj.code_lines[i]` (`.code` does not exist); there is no `Code.highlight_lines()`. Scenes use `ChalkCode` instead.
- **No `+` / `-` on Mobjects**: `Mobject.__add__` raises NotImplementedError at render time.
- **No `GrowArrow` on a `ChalkArrow`** (a VGroup with no points of its own); use `Create` or `FadeIn`.
- **`TransformMatchingTex`** only matches whole MathTex parts; `derivation_step` / `transform_equation` fall back to glyph-shape matching.
- **`MathTex(..., color=X)` / `Tex(..., color=X)` leave glyphs white in 0.21**; `math_tex()` / `tex()` / `EquationGroup` apply color correctly. Call `.set_color()` if building MathTex directly.
- **`Text(...).color` reads back as black in 0.21** even when constructed with `color=`; compare colors via the token you passed.

When rendering fails, read the traceback (renderer output, or the stderr tail in the layout feedback). Patch `output/<run_id>/scene.py` to verify a fix, then encode it in the prompt.

---

### How the dry-run measures geometry

`_measure()` in `docker/chalkboard_base.py` takes the bounding box from a mobject's points (`get_all_points()`). Do not use `hasattr(m, "get_bounding_box")`: Manim's `Mobject.__getattr__` fabricates any `get_*` name, so it is always true and the call then fails (this silently disabled every geometry check until 2026-10-02). Invisible mobjects are skipped. Plain `VGroup`/`Group` containers are flattened so members are compared with each other. Partial overlaps are ignored when either side is a connector (`Line`, arrows, `Brace`, `ParametricFunction` curves, unfilled outlines) or the overlap is thinner than `_MIN_OVERLAP` (0.1). `zone_collision` counts an element as side-zone content only when its center is beyond `_ZONE_SIDE_CENTER` (1.5) and flags it only when it reaches into the far zone. Calibration: zero geometry flags on 14 real videos from 2026-10-02; the positive control `test_real_scene_flags_overlapping_text_and_offscreen` must keep passing.

## Layout discipline (overlap prevention)

`manim_agent`'s prompt contains **LAYOUT RULES** and a **CLEAN SLATE** rule; `chalkboard_base` and the AST guards enforce them.

### Coordinate zones

The canvas is x in [-7.11, +7.11], y in [-4.0, +4.0]. Anchors in the prompt:

| Zone | Anchor | Purpose |
|------|--------|---------|
| `title_anchor` | `UP * 3.4` | Persistent title (title band, y > 2.9) |
| `left_anchor` | `LEFT * 3.5 + UP * 0.3` | Code, arrays, diagrams, plots |
| `right_anchor` | `RIGHT * 3.5 + UP * 0.3` | Callouts, derivations, annotations |
| `center_anchor` | `ORIGIN` | Full-width single element |
| `bottom_anchor` | `DOWN * 3.3` | Captions, one-line notes |

When both side zones are populated, left content stays at x < -0.5 and right content at x > +0.5. For N elements of width W with leftmost center x_0: `right_edge = x_0 + (N - 0.5) × W` must be < -0.5 for left-zone content. `next_to()` chains are limited to 2 levels from a fixed anchor. Elements stay ~0.3 units inside the canvas edge.

### Clean slate between segments

```python
seg_items = []
elem = ...; reveal_with_emphasis(self, elem); seg_items.append(elem)
# At the start of segment N+1, BEFORE any new content:
self.play(*[FadeOut(m) for m in seg_items], run_time=0.5)
seg_items = []
```

The persistent title is never in `seg_items`; multi-segment elements are faded out explicitly when no longer needed. code_validator's review checks this for each `# ── Segment N:` block (N > 0); template-driven scenes are exempt because the template owns segment boundaries.

---

## Timeout & retry infrastructure

**`api_call_with_retry(fn, timeout, max_attempts=3, label)`** (`pipeline/retry.py`) runs a sync callable in `asyncio.to_thread` under `asyncio.wait_for`, retrying on timeouts and errors. It raises `TimeoutExhausted` after the last attempt, or **immediately** for errors that cannot succeed on retry: `BadRequestError`, `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, and quota errors (`insufficient_quota`, `credit balance is too low`). The Anthropic SDK additionally retries 429/5xx inside each attempt (`max_retries=3`). `TimeoutExhausted` propagates out of `graph.astream()` and is handled in `run()`: interactive runs offer `retry / abort`; non-interactive runs re-raise.

**`subprocess_with_timeout(cmd, timeout, on_line=None, env=None)`** (`main.py`) kills the process via `threading.Timer` after `timeout`; returns `(returncode, lines_buffer, timed_out)`. Used for render subprocesses.

**Timeout constants** (`pipeline/retry.py`):

| Constant | Value | Used by |
|----------|-------|---------|
| `TIMEOUT_SCRIPT_AGENT` | 300s | script_agent |
| `TIMEOUT_RESEARCH_AGENT` | 300s | research_agent |
| `TIMEOUT_FACT_VALIDATOR` | 180s | fact_validator |
| `TIMEOUT_MANIM_AGENT` | 900s | manim_agent (streams up to 48k tokens) |
| `TIMEOUT_CODE_VALIDATOR` | 240s | code_validator |
| `TIMEOUT_LAYOUT_CHECKER` | 180s | layout_checker |
| `TIMEOUT_VISUAL_QA` | 240s | visual_qa |
| `TIMEOUT_TTS_SEGMENT` | 60s | OpenAI, ElevenLabs (per segment) |
| `TIMEOUT_TTS_KOKORO` | 120s | Kokoro (full call) |

---

## TTS backends

All backends implement the same async contract:

```python
async def generate_audio(
    segments: list[dict],   # [{"text": str, "estimated_duration_sec": float}]
    output_path: Path,      # write voiceover.wav here
    speed: float = 1.0,     # playback speed multiplier
) -> tuple[Path, list[float]] | tuple[Path, list[float], list[list[float | None]]]:
    # (wav_path, actual_durations_per_segment[, cue_times_per_segment])
```

Speak the clean text: `parse_cues(segment_cue_text(seg))` gives it plus the marker offsets (see Word-level sync).

- **OpenAI** (`openai_tts.py`): segments synthesized in parallel (`asyncio.gather`, at most `MAX_CONCURRENT = 6` at once), each through `api_call_with_retry`; results are concatenated in segment order. `speed=` goes to the API. Env: `OPENAI_TTS_MODEL` (default `gpt-4o-mini-tts`), `OPENAI_TTS_VOICE` (default `alloy`), `OPENAI_TTS_INSTRUCTIONS` (sent only when the model name starts with `gpt-`).
- **Kokoro** (`kokoro_tts.py`): `KPipeline` is created once per process (`functools.cache` on `_pipeline()`, so it runs on the GPU when CUDA is available and is not reloaded per job). Voice: `KOKORO_VOICE` (default `af_heart`). Tests clear the cache in `conftest.py`.
- **ElevenLabs** (`elevenlabs_tts.py`): plain REST via httpx (no SDK). Default model `eleven_v4`, default voice Skye. On models that support it (everything except `eleven_v3`, which rejects `previous_text`/`next_text`/`previous_request_ids` with `unsupported_model`, verified 2026-10-02) segments are synthesized **in order** and each request carries `previous_request_ids` (last 3), `previous_text` and `next_text`, so prosody carries across segment joins. v3 runs segments in parallel (`ELEVENLABS_CONCURRENCY`, default 3) without context. Env: `ELEVENLABS_MODEL_ID`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_STITCH=0`.
- **Narrators** (`voices.py`): `aria`/`milo` (ElevenLabs Skye/Bradley on `eleven_v4`, ids pinned because the library renames voices; chosen in an April 2026 blind test and an October 2026 v4 audition), `kokoro`, `alloy`. `render_trigger` resolves `state["narrator"]` or `NARRATOR` (config) to a backend + `voice=`/`model=` kwargs; unset keeps the `TTS_BACKEND` default. The manifest records `narrator`. `scripts/tts_bench.py` compares voices on one script.
- **Speed:** OpenAI natively; Kokoro and ElevenLabs generate at 1.0x and then `_apply_speed_to_wav` (ffmpeg `atempo`, chained via `_build_atempo` outside [0.5, 2.0]); durations are divided by `speed`.

**Critical:** OpenAI returns WAV with an overflowed header (`nframes=0xFFFFFFFF`), so PCM is extracted with `wave.open()` and a clean WAV is written. ElevenLabs is requested as `pcm_24000` and wrapped manually. Kokoro produces clean PCM. Never concatenate raw response bytes.

### Adding a new TTS backend

1. Create `pipeline/tts/yourbackend_tts.py` implementing `generate_audio(segments, output_path, speed=1.0, *, voice=None, model=None)`
2. Native speed: pass `speed` to the API. Otherwise call `_apply_speed_to_wav(output_path, speed)` and divide durations by `speed`
3. Register it in `pipeline/tts/base.py` `get_backend()`
4. Add it to the TTS table in `README.md`
5. Add tests in `tests/test_yourbackend_tts.py`

---

## Output files (per run)

`render_trigger.py` writes to `output/<run_id>/`:

| File | Contents |
|------|----------|
| `scene.py` | Complete Manim Python source |
| `voiceover.wav` | Concatenated TTS audio for all segments (at final speed) |
| `segments.json` | `[{"text": str, "actual_duration_sec": float, "cues": [float], "cue_text"?: str}]`, post-speed actual durations and cue-marker times (seconds from the segment start) |
| `script.txt` | Full narration script |
| `manifest.json` | `{run_id, scene_class_name, quality, topic, title, effort, audience, tone, theme, template, speed}`; `quality` is `state["quality"] or MANIM_QUALITY` |

Written later by `main.py`: `layout_report.json` (layout_checker, before render_trigger), `media/` or `media_preview/` (Manim output), `captions.srt` and `chapters.txt` (`_generate_caption_files`, before the merge), `final.mp4` or `preview.mp4`, `thumb.jpg` (`_extract_thumbnail`), `qa_frames/` (visual QA), `quiz.json` (`--quiz`).

`chapters.txt` is FFMETADATA1 passed to ffmpeg as `-f ffmetadata -i chapters.txt -map_metadata 2`. `--burn-captions` adds `-vf subtitles=<path>` and switches `-c:v copy` to `libx264 -preset fast -crf 18`.

---

## Render workflow

`pipeline/render.py` owns where Manim runs. `RENDER_BACKEND` (`config.py`) is `auto` (default), `local` or `docker`; `backend()` resolves `auto` once per process to `local` when `import manim` works and `latex`, `dvisvgm` and `ffmpeg` are on `PATH`, else `docker`.

| | local | docker |
|---|---|---|
| Render | `python -m manim render -q? --media_dir <run_dir>/media scene.py ChalkboardScene` | `docker run --rm -v <output>:/output [-e PREVIEW_MODE=1] chalkboard-render <run_id>` |
| Layout check | `python -c <_CHECK_SNIPPET> <run_dir>` | `docker run --rm -v <run_dir>:/output chalkboard-render --check` |
| Runtime modules | `docker/` prepended to `PYTHONPATH` (`local_env`) | copied to `/render` in the image |
| Report location | `CHALKBOARD_REPORT_DIR=<run_dir>` | `/output` |
| Setup | `pip install -r requirements-render.txt` + TeX Live, dvisvgm, ffmpeg, cairo/pango, fonts | image built on first use by `ensure_ready()` (`docker build -f docker/Dockerfile`, 900s timeout) |

Quality maps to Manim flags `low=-ql (480p15)`, `medium=-qm (720p30)`, `high=-qh (1080p60)`, `4k=-qk (2160p60)`; `video_path()` computes `<media>/videos/scene/<subdir>/ChalkboardScene.mp4`. The Docker image is `manimcommunity/manim:v0.21.0` plus ffmpeg, CMU Serif, Inter and DejaVu fonts and the `tlmgr` packages for the preamble. **The Docker path has not been re-tested end to end since the move to 0.21.0 and the design system**; local is the exercised path.

`main.py` after the graph completes:

```
1. _check_tools()            ffmpeg (+ docker for the docker backend) on PATH; skipped with --no-render
2. render.ensure_ready()     build chalkboard-render if missing (docker backend only)
3. _render_once()            render.render_cmd(...) with the adaptive timeout and live progress
4. _generate_caption_files() captions.srt + chapters.txt
5. ffmpeg merge (host)       video + voiceover.wav (+ chapters) → final.mp4 (timeout 120s)
6. _extract_thumbnail()      thumb.jpg at 10% of the duration
7. _run_qa_loop()            visual QA, regenerate + re-render on errors (up to 2 times)
```

The audio merge runs on the host because Linux ffmpeg's native AAC encoder omits the encoder-delay edit list QuickTime needs. `--no-render` stops after the graph.

### Adaptive render timeout

```
timeout = (BASE + anim_count × PER_ANIM + audio_duration × AUDIO_RATIO) × quality_mult
timeout = clamp(timeout, MIN=90s, MAX=1200s)
```

`BASE=60s`, `PER_ANIM=5s`, `AUDIO_RATIO=3.0`, `quality_mult={low:0.5, medium:1.0, high:2.0, 4k:4.0}`. The preview render uses the 90s minimum.

### Render retry

`_render()` and `_render_preview()` try up to 3 times, deleting `media/` and `media_preview/` between attempts. After the third failure `RenderFailed` reaches `main()`: interactive runs offer `retry_render / abort`, non-interactive runs exit with status 1. An existing `final.mp4` / `preview.mp4` short-circuits the render.

---

## Visual QA

After each full render, `_run_qa_loop()` → `_run_visual_qa()` → `pipeline/visual_qa.py`.

| Density | `seconds_per_frame` | `max_frames` |
|---------|--------------------:|-------------:|
| `zero`  | skip QA | |
| `normal` (default) | 30s | 10 |
| `high`  | 15s | 20 |

Minimum 5 frames; with `segments.json`, frames are also sampled at segment boundaries, and on the first pass `layout_report.json` is cross-referenced. Claude (agent `visual_qa`) returns `{"passed": bool, "issues": [{severity, description}]}` with the scene code as reference. On `error` issues, `_qa_regenerate_scene()` re-invokes `manim_agent` with the issues as `code_feedback`, using the run's theme, template, audience, tone and effort from `manifest.json` (so a resumed run keeps its settings). The new code must pass `run_guards` and the headless dry-run (`layout_checker`; only a crash rejects it, layout warnings do not); otherwise the current scene is kept. Code_validator's Claude review is skipped. The previous `final.mp4` is kept as `final.prev.mp4` until the re-render (with the same `burn_captions`) succeeds and is restored if it fails. Up to 2 times. Warnings never trigger regeneration. QA exceptions are caught and the run continues.

---

## Checkpointing and resume

LangGraph's `AsyncSqliteSaver` (`CHECKPOINT_DB`, default `pipeline_state.db`) checkpoints after every node, keyed by `thread_id` = run id. `run()` calls `graph.aget_state(config)` first:

- no checkpoint: start fresh with the input state
- checkpoint with pending nodes: print `resuming at <node>` and stream with `input_state=None`, continuing from the checkpoint (CLI arguments such as `--quality` or `--template` are not re-applied)
- checkpoint with nothing pending: print `already complete` and return, so `main.py` goes straight to rendering

If a node raises, its output is not saved; the next resume re-runs that node from scratch without incrementing attempt counters.

---

## escalate_to_user

`escalate_to_user` is an `async def` node reached when attempts are exhausted. When `state["interactive"]` is `False` it prints the escalation message and returns `{"status": "failed"}` without reading stdin. Otherwise it reads `retry_script / retry_code / abort` (and guidance) with `asyncio.to_thread(input, ...)`; `EOFError` defaults to abort. `retry_*` resets the matching attempt counter and stores the guidance as feedback.

**Why not `interrupt()`:** LangGraph 1.1.3's `interrupt()` calls `get_config()`, which needs Python 3.11+ in async nodes. The `asyncio.to_thread` approach works on 3.10+. The node **must be `async def`** (LangGraph uses `inspect.iscoroutinefunction()` to decide whether to await).

**Non-interactive is the default for automation:** the CLI sets `interactive = sys.stdin.isatty() and not args.yes`, and `server/jobs.py` always passes `interactive=False`. In that mode nothing blocks on stdin: escalation aborts, `TimeoutExhausted` re-raises, render failures exit 1, and layout-only failures render anyway. The only remaining `input()` is the large-context confirmation in `_report_context`, skipped by `--yes`.

---

## Video Library

`server/library.py`: `VideoMeta` (16 persisted fields: `run_id`, `topic`, `title`, `created_at`, `duration_sec`, `quality`, `thumb_path`, `script`, `effort`, `audience`, `tone`, `theme`, `template`, `speed`, `status`, `narrator`; `init()` adds the `title` / `narrator` columns to older databases; `output_files` is computed from disk), the `LibraryStore` ABC (`init`, `add_video`, `get_video`, `list_videos`, `delete_video`) and `SQLiteLibraryStore` (`aiosqlite`, WAL, `library.db`). A Postgres store can implement the same interface and be passed to `create_app(library_store=...)`.

`_backfill(store, output_dir)` (`server/app.py`) indexes every `output/` directory with `manifest.json` and `final.mp4` at startup and, throttled to once per 10 s, on `GET /api/library` so CLI runs show up without a restart (idempotent; file mtime as `created_at`; fills `narrator` on older rows). Consequence: `DELETE /api/library/{id}` without `?files=true` is undone by the next listing, because the files are still there; the web UI always deletes with `files=true`. Old manifests missing fields are read with `.get(field, default)`.

Routes: `make_library_router(store)` (`GET/DELETE /api/library...`) and `make_pages_router()` (`/library`, `/library/{run_id}`).

---

## API server

```bash
python run_server.py                  # 127.0.0.1:8000 (SERVER_HOST / SERVER_PORT)
python run_server.py --host 0.0.0.0   # serve the LAN; there is no auth
python run_server.py --reload         # dev (kills in-flight jobs on reload)
```

- **`server/app.py`**: `create_app(...)` factory, mounts `server/static/` at `/`, library init + backfill in the `lifespan` handler. Module-level `app` for uvicorn.
- **`server/jobs.py`**: `Job` dataclass (status, events, output_files, async queue, pipeline params including `quality`), in-memory `JobStore`, `run_job(job, output_dir, library_store)` which waits on a module-level `asyncio.Semaphore(MAX_CONCURRENT_JOBS)` (default 3) and then runs pipeline + render + QA + quiz, `_do_render(run_id, burn_captions)`.
- **`server/routes.py`**: `make_router(store, library_store)`. Job tasks are created with `_spawn()`, which keeps a strong reference in `_running_tasks` (the event loop only holds weak references, so an unreferenced task can be garbage-collected mid-run). `/api/claude-status` parses the Claude status RSS with a 5-minute cache. `/api/meta` returns server defaults, the narrators (with `available` = API key present, Kokoro always), the resolved render backend, `CLAUDE_MODEL` and the count of pending/running jobs. The multipart upload route builds a `CreateJobRequest` from its form fields first, so it validates (422) exactly like the JSON route before any file is saved.
- **`server/models.py`**: `CreateJobRequest` (topic, effort, audience, tone, theme, template, speed, burn_captions, quiz, urls, github, qa_density, quality), `JobResponse` (id, status, topic, events, error, output_files). The multipart upload route takes the same fields as form fields (`quality=""` means default).
- **Frontend:** `index.html`, `library.html` and `video.html` each load `/app.css` and `/app.js` in `<head>`. `app.js` exposes helpers on `window.CB`, renders the nav (Generate / Library tabs, Claude status, jobs menu, theme toggle) and the `window.jobStatus` store. No build step. (`job-status.js` was removed; its role moved into `app.js`.)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/jobs` | Create job (202 + `JobResponse`) |
| `POST` | `/api/jobs/upload` | Create job with multipart file uploads |
| `GET` | `/api/jobs` | List jobs |
| `GET` | `/api/jobs/{id}` | Get job (404 if missing) |
| `GET` | `/api/jobs/{id}/events` | SSE stream, one event per node update, then `{"done": true}` |
| `GET` | `/api/jobs/{id}/files/{filename}` | Serve an output file (path-traversal-safe; also works for library runs) |
| `GET` | `/api/claude-status` | Claude status summary |
| `GET` | `/api/meta` | Defaults, narrators + availability, render backend, model, running jobs |

Lifecycle: `pending → running → completed | failed`. `job.error` is set when the pipeline raises or the render fails (`"render failed; pipeline output preserved"`). `run_job` fetches URL/GitHub context, forwards `burn_captions` and `quality`, skips QA for `qa_density="zero"` or a failed render, and runs the quiz independently of render success.

Current `JobStore` is in-memory (jobs are lost on restart). Auth is not implemented.

---

## Context injection

`--context` / `--context-ignore` / `--url` / `--github` (and the web upload zone) are preprocessed in `main.py` / `server/jobs.py` via `pipeline/context.py`, then passed to `build_graph(context_blocks=...)`. Content blocks are never stored in `PipelineState` (only file paths), so checkpointing is unaffected.

- `collect_files(paths, ignore_patterns=None)`: walks directories, honors `.gitignore` via `pathspec` plus extra patterns, skips hidden directories, dedupes.
- `load_context_blocks(files)`: text/code → `text`, images → base64 `image`, PDFs → base64 `document`, `.docx` → paragraph text; each preceded by a `--- file: <path> ---` label.
- `fetch_url_blocks(url)`: `httpx` + BeautifulSoup, scripts/nav/header/footer stripped, truncated at 100k chars.
- `measure_context(blocks, client)`: `(token_count, context_window)` from `count_tokens` and `models.retrieve` for `agent_model("manim")`.

`_report_context` prints the count, prompts above 10k tokens unless `--yes`, and exits above 90% of the window. `build_graph` wraps `script_agent` and `manim_agent` in async closures carrying `context_blocks` (so retries keep the context). PDFs switch the client to the PDF beta header (`get_client(pdf=True)`). On `--run-id` resume, context must be passed again. `--github` resolves to `https://raw.githubusercontent.com/<owner>/<repo>/HEAD/README.md` (`_github_to_raw_url`). `--quiz` runs `_generate_quiz(run_id)` after render (agent `quiz`), reading `script.txt` and writing `quiz.json`.

---

## Testing

```bash
pip install -r requirements-dev.txt   # requirements.txt + pytest, pytest-asyncio
pytest                        # 634 tests (with Manim + TeX installed)
pytest tests/test_graph.py    # one file
```

- `tests/test_chalkboard_components.py`, `test_chalkboard_moves.py` and `test_chalkboard_templates.py` are gated on `pytest.importorskip("manim")`; install `requirements-render.txt` (and TeX, since they typeset) to run them. Without Manim they are skipped, not failed.
- `tests/conftest.py` has an autouse fixture that clears the cached Anthropic clients (`pipeline.llm._clients`) and the cached Kokoro pipeline before and after each test, so tests that patch `anthropic.Anthropic` get a fresh client.
- Graph-level tests mock agents at the node level (`pipeline.graph.script_agent`, etc.) with `async def` functions (patched via `new=`), not `AsyncMock`: LangGraph's `inspect.iscoroutinefunction()` check fails on `AsyncMock`. `test_graph.py` uses `MemorySaver`.
- Agent tests pass a mock `client` into the agent. Mock responses need a content block with `type="text"` holding the JSON, since `response_text` returns the last text block.

---

## Known issues / future work

- **Docker render path untested on 0.21.0**: the image was updated with the design system but not re-run end to end.
- **QA regeneration skips the Claude review**: `_qa_regenerate_scene` runs the AST guards and the dry-run, not code_validator's review.
- **Kokoro multi-voice**: a single voice per run.
- **High-effort web search gate**: `needs_web_search` is returned by script_agent but `user_approved_search` is never set to `True` by `main.py`.
- **In-memory job store** and **no auth** on the server.
