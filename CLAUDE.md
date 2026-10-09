# Chalkboard: Architecture & Contributor Guide

This document is the reference for anyone (human or AI agent) contributing to Chalkboard. It covers architecture, design decisions, known pitfalls, and how to extend the project. User-facing setup lives in `README.md`; a Claude Code skill for running the tool lives in `.claude/skills/chalkboard/SKILL.md`.

## Versioning (required for every change)

- **Every change merged to main bumps `VERSION`** and adds a `CHANGELOG.md` entry: `python scripts/bump_version.py patch "what changed"` (patch by default, `minor` for notable features, `major` for breaking changes). Commit both files. CI (`version` job) fails a pull request whose `VERSION` is not greater than main's or has no changelog heading; if main moved on, merge it and bump past its version. Pushes to main are tagged `v<VERSION>` by `.github/workflows/tag.yml`.
- **Check what a running server is** with `curl <base>/version` (pretty JSON; `/api/version` is the same, compact): version, git commit/branch/dirty, uptime, Python/Manim/ffmpeg, model, render backend, latest changelog entry, endpoints. `python main.py --version` for the CLI. `pipeline/version.py` reads `VERSION` and the git facts (null when git is unavailable, never guessed).

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
  render_trigger.py  Calls TTS, applies pacing, writes output files
  pacing.py          Pace presets, pause points, post-TTS silence insertion, pre-TTS estimates
  scene_parts.py     Scene written in parts: part_ranges, extract_method, assemble, route_feedback
  chapters.py        Chapters from [[ch: Title]] markers on the paced timeline: chapters.txt, timeline API
  cues.py            Cue / beat / chapter markers: parse_markers, parse_cues, parse_chapter_markers, timing helpers
  retry.py           TimeoutExhausted, api_call_with_retry, timeout constants
  context.py         collect_files, load_context_blocks, fetch_url_blocks, measure_context
  visual_qa.py       Post-render frame sampling + Claude review
  telemetry.py       Per-run event sink (ContextVar): peek / usage / render / tts events
  partial_json.py    extract_string_field: growing value of one field from streaming JSON
  pricing.py         Claude list prices -> cost_usd (unknown model => None)
  run_stats.py       run_stats.json: totals + stage timings from timestamped events
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
  run_data.py         Timeline (segments, chapters + voiceover peaks), quality (cue_log, layout, QA) per run
  insights.py         /api/stats and /api/estimate from library + run_stats.json
  status.py           /api/status probes (free endpoints only, cached 60 s, unknown when unsure)
  voices.py           /api/voices + real, cached voice samples (output/_voice_samples/)
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
| `speed` | float | Narration speed multiplier (default `1.0`); multiplied by the pace's `speech_speed` before it reaches the TTS backend |
| `pace` | str \| None | `relaxed` / `normal` / `brisk` (`pipeline/pacing.py`); `None` = `PACE`, else `relaxed`. Resolved by `render_trigger` (recorded in `manifest.json`), `layout_checker` and `manim_agent` (estimates) |
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
| `scene_parts` | list[dict] \| None | Set when manim_agent wrote the scene in parts: `[{"segments": [a, b], "code": "def part_K(...)", "imports": [...]}]`; `manim_code` is their assembly. `None` for a single-response scene |
| `scene_plan` | dict \| None | The shared visual plan (`title`, `style`, per-segment `visuals` / `end_state`) the parts follow |

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
    if state["script_attempts"] >= SCRIPT_ATTEMPT_LIMIT:   # 3
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

**Fact check in non-interactive runs.** When the fact check rejects the script for the `SCRIPT_ATTEMPT_LIMIT`-th time (3) and `interactive` is `False` (`--yes`, the server), `fact_validator` returns `fact_feedback=None` with a printed note and a `warning` telemetry event carrying the remaining notes (kept in `run_stats.json` `warnings`): the run continues with the latest script, which already went through every earlier round of fixes, instead of escalating to a dead end with no video (run f82835bb, 2026-10-09, high effort: the third rejection listed one inconsistent example value, two wording fixes and three clarity notes, and the run ended with no video). Interactive runs still escalate.

**Layout-only failures.** When `code_attempts` hits 3 on a layout failure, a scene that ran end to end (`layout_renderable=True`) is rendered anyway in non-interactive runs (visual QA still reviews it). A scene that crashed, or any failure in an interactive run, escalates.

---

## Claude calls (`pipeline/llm.py`)

Every agent (plus visual QA, the quiz and context measurement) talks to Claude through `pipeline/llm.py`:

- **`call_json(agent, *, content, schema, system=None, max_tokens=16000, tools=None, client=None, stream=False, effort=None)`**: blocking structured-output call returning `(parsed_dict, response)` (it is sync). `stream=True` uses `client.messages.stream(...).get_final_message()`; budgets above `NON_STREAMING_MAX` (16000) always stream (the SDK refuses long non-streaming requests). `effort` overrides the agent's effort for this call. After every call it emits a `usage` event (tokens, web searches, `cost_usd` from `pipeline/pricing.py`, plus `max_tokens`, `effort` and `stop_reason`) to the telemetry sink, if one is set, before the response is parsed, so a call that ran out of room is still counted.
- **Output budgets** (`call_json_budgeted`, async; every agent uses it): `max_tokens` caps adaptive thinking **plus** the answer, and it is not billed unless used. A response that runs out of room (`stop_reason == "max_tokens"`, including the thinking-only case where no text block exists) raises `ClaudeTruncated(thinking_only=...)`, which `api_call_with_retry(..., passthrough=(ClaudeTruncated,))` hands straight back instead of repeating the identical call. `call_json_budgeted` then walks `budget_steps(max_tokens, ceiling, effort)`: the requested budget, the model's whole output cap (`model_max_output`: Models API `max_tokens`, cached; 128000 for Opus 5.5; `DEFAULT_OUTPUT_CEILING` 64000 if the lookup fails), then the cap at one effort level lower. Each escalation prints `[label] out of output room ... retrying with max_tokens=..., effort ...` and emits a `budget` event (in `run_stats.json` `budget_retries`). When every step runs out it raises `ClaudeOutOfRoom`, a `TimeoutExhausted` subclass, so the graceful handlers (fact check, code review, research) keep working. `max_steps=1` disables escalation (manim_agent's single-response scene, whose fallback is writing in parts); `on_last_step(kwargs)` can change the request for the last step (script_agent adds a length limit). Per-attempt timeouts grow with the budget (`budget_timeout`: at least `max_tokens / 40 tok/s + 60 s`). Measured 2026-10-09 on Opus 5.5: the af68f322 scene call, replayed with room to spare, used 80,863 output tokens in 694 s: about 22.5k for the answer (an 845-line scene, hand-written although the run asked for the derivation template) and about 58k of thinking, which is why the old fixed 48000 failed three times in a row. When the sink asks for peeks (`telemetry.Sink(..., peek=True)`, the server), the `script`, `fact` and `manim` agents always stream and publish `peek` events: the growing value of `script` / `feedback` / `manim_code`, read from the SDK's live text snapshot with `pipeline/partial_json.extract_string_field`, at most every `PEEK_INTERVAL` (0.25 s), then a final `done: true`. With no sink (tests, plain library use) the call path is unchanged.
- **Telemetry** (`pipeline/telemetry.py`): a ContextVar holds the run's `Sink`; `asyncio.to_thread` copies context, so `call_json`, Kokoro and the render reader (all in worker threads) reach it. `emit(node, updates)` stamps `ts` at emission and never raises. The server's sink (`server/jobs._job_sink`) appends directly on the loop thread and via `call_soon_threadsafe` from others; the CLI's sink is a `run_stats.Recorder` (no peeks). Render progress: `ChalkboardSceneBase.begin_segment` prints `CB_SEGMENT n` in a real render, `main._render_once` parses it and Manim's `Animation N` lines into throttled `render` events (`_RenderProgress`). TTS progress: backends accept `on_segment(index, chars)`; `render_trigger` turns it into `tts` events (backends without it get one `done` event with the text length).
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
- Agent key `research`, `max_tokens` 32000 (budgeted), timeout `TIMEOUT_RESEARCH_AGENT` = 300s
- Output: `{"research_brief": str, "sources": list[str], "search_warning": str|null}`
- Only runs when `effort_level == "high"` (`_after_init`). Uses `web_search_tool("research")`
- When present, `research_brief` is injected into `script_agent`'s message and `script_agent`'s own web search is disabled
- **Graceful fallback:** on `TimeoutExhausted`, `RuntimeError` or `ValueError` it returns `research_brief=None` and a `search_warning`; the script then relies on training data. The pipeline does not abort.

### script_agent
- Agent key `script`, `max_tokens` `SCRIPT_MAX_TOKENS` = 32000 (budgeted; the old 16000 default truncated twice on a high-effort rewrite with a research brief and 44k tokens of context, run f82835bb), timeout `TIMEOUT_SCRIPT_AGENT` = 300s. The last budget step appends `TIGHTEN_NOTE` (at most 8 short segments, plan briefly) instead of failing
- Output: `{"title": str, "script": str, "segments": [{text, estimated_duration_sec}], "needs_web_search": bool}`
- Web search enabled when `effort_level == "high"` or `user_approved_search`, **unless** `research_brief` is set
- Injects `AUDIENCE_INSTRUCTIONS` / `TONE_INSTRUCTIONS`
- **Narration is spoken:** the system prompt requires math and code to be written the way a lecturer says them ("e to the x", "x squared", "n log n"), never symbols, LaTeX or code syntax, because TTS reads the text aloud while the animation shows the notation.
- **Cue markers:** segment text carries `[[1]]`, `[[2]]`, ... right before the words where visuals land (2-5 per segment, renumbered per segment). The agent returns segments with clean `text` plus the marked `cue_text`, and a clean `script` (see Word-level sync below).
- **Presenting:** the prompt asks for a teacher's delivery (one idea per short sentence, a question before its answer, signposts, a closing recap) and allows sparse `[[beat]]` markers (0-2 per segment) where a deliberate pause belongs (see Pacing below). Durations are estimated for speech only; pauses are added by pacing.

### fact_validator
- Agent key `fact`, `max_tokens` 16000 (budgeted), timeout `TIMEOUT_FACT_VALIDATOR` = 180s
- Output: `{"verdict": "approved"|"needs_revision", "feedback": str}`
- Effort-based instructions: low = light check, medium = spot-check, high = thorough

### manim_agent
- Agent key `manim`, **streamed**, base timeout `TIMEOUT_MANIM_AGENT` = 900s (scaled with the budget)
- Output: `{"manim_code": str}` (single response) or a scene written in parts (below)
- **Single response**: `max_tokens` `MANIM_MAX_TOKENS` = 128000, clamped to the model cap, no escalation (`max_steps=1`). If it still runs out of room it is rewritten in parts (`budget` event with `fallback: "scene_parts"`); with `SCENE_CHUNKING=off` it gets the budget ladder instead and fails when that runs out.
- **Written in parts** (`pipeline/scene_parts.py`), up front when `should_chunk(state)`: `SCENE_CHUNKING` = `auto` (default: >= `CHUNK_MIN_SEGMENTS` (7) segments or >= `CHUNK_MIN_CUE_CHARS` (5000) characters of marked segment text, with or without a template: the measured 8-segment scene needed 63% of the cap in one response, and it ignored its derivation template), `always` or `off`. Segments are split into balanced consecutive parts of about `SEGMENTS_PER_PART` (3). One plan call (`PLAN_SCHEMA`: persistent title, shared style = roles per concept, zones, recurring components; per segment what lands on each cue and the `end_state`; sees the context files) then one call per part, concurrently (`PART_MODE` appended to the system prompt; `PART_SCHEMA` `{imports, code}`; each part sees the whole script, every segment, the theme block and the plan, not the context files). A part is one method `def part_K(self, t, _d, seg_items)`: part 0 begins segment 0 and creates `self.title_mob`; every other segment, including a later part's first, starts with `next_segment(N, clear=seg_items)`; it returns what it leaves on screen. `extract_method` rejects anything else (one immediate re-ask). `assemble` writes the scaffold imports once (plus the parts' extra import statements, validated), `construct()` with the duration loading, `T(theme=...)`, background, the part calls in order, `end_layout_check()` and the final FadeOut, then the part methods, so the AST guards, code review and dry-run see one normal scene. A template (a whole-scene beats dict) cannot be split: the parts are told to compose its look from components and moves.
- **Revision of a scene in parts**: when `scene_parts` is set and `manim_code` is still exactly their assembly, `route_feedback` maps the feedback to parts by the assembled line numbers it cites (`line N`) and the segments it names (`[Segment N ...]`); feedback naming neither goes to every part. Only those parts are rewritten (minimal edits, given their current code and their line span), concurrently, and the scene is reassembled. A visual-QA revision starts from `scene.py` without `scene_parts`; `split_assembled` reads the parts back from the code (exact round trip through `assemble`, else `None`), so QA fixes are part revisions too (the parts get no plan then and keep their look). A scene that is not an assembly is revised as a single response; a wide one is rewritten in parts with the feedback given to the plan.
- Scene class **must** be `ChalkboardScene(ChalkboardSceneBase, Scene)`
- The system prompt targets **Manim CE v0.21.0** and teaches the design system: required scaffold (imports of tokens, components, moves, templates), the component / move / template vocabulary, a MATH section (all math through `math_tex` / `tex` / `EquationGroup` / `ChalkMatrix`, house macros, role-colored terms, aligned derivations), the token API and what is forbidden in scene code (raw hex/Manim color constants, raw `font_size`/`buff`/`stroke_width`/`run_time` literals, math in `Text`), role and motion semantics, five annotated exemplars (the fifth: word-level sync with `self.cue(k)`), the WORD-LEVEL SYNC rules, the verified pitfall list (below), LAYOUT RULES and the CLEAN SLATE rule
- `_format_segments` shows each segment's marked `cue_text` with its paced estimate (`pacing.estimate_segment`: duration, each marker's time, "last word ~Xs, then hold still"; measured values when present, e.g. on QA regeneration)
- PACING rules in the prompt: the hold after a segment's last word is stillness (`hold_busy` otherwise), one main reveal per cue, a settle after key reveals via `self.speech_time_left()`
- `THEME_SPECS` are generated from the tokens via `render_prompt_block(theme)`, so prompt and renderer never drift
- `TEMPLATE_SPECS` (algorithm, code, compare, derivation, howto, timeline) tell the agent to instantiate the matching template class with a `beats` dict
- **Revision rounds:** when `code_feedback` is set and prior code exists, the agent is asked for minimal targeted edits to the prior scene instead of a rewrite
- manim_agent does not touch `code_attempts`; code_validator and layout_checker count failures

### code_validator
- Agent key `code_validator`, `max_tokens` 16000 (budgeted), timeout `TIMEOUT_CODE_VALIDATOR` = 240s
- Gates, in order (each hard failure increments `code_attempts`, resets `claude_review_failures`, sets `code_feedback_advisory=False`):
  1. `ast.parse()` syntax check
  2. Mobject arithmetic scan (`Text(...) + 0` and friends crash at render time)
  3. `run_guards(tree, code)` from `pipeline/ast_guards.py`
- Then the Claude review (advisory, see Routing). The review prompt lists the design-system APIs as real and confirmed-correct Manim idioms so the reviewer does not flag them.
- Output: `{"verdict": "approved"|"needs_revision", "feedback": str}`

### layout_checker
- No Claude call. Timeout `TIMEOUT_LAYOUT_CHECKER` = 180s
- Writes `scene.py` and a stub `segments.json` from `pacing.estimate_segment` at the run's pace (estimated speech at the delivery speed plus lead-in, pauses and hold; `cues` and `speech_end_sec` estimated from character positions; measured values are kept when the segments already have them), so the hold is never an overrun to `output/<run_id>/`, deletes any stale `layout_report.json`, then runs `render.check_cmd(run_dir)`: a native Python dry-run (local) or `docker run ... chalkboard-render --check`. Both set `dry_run=True`, `frame_rate=1`
- `ChalkboardSceneBase` writes `layout_report.json`: `{"passed": bool, "violations": [{type, segment, description, ...}]}`
- Violation types: `timing_overrun` (1.5s tolerance), `off_screen` (0.1 unit tolerance), `overlap` (partial intersection; full containment is ignored), `zone_boundary_overlap` (containment across the left/right zones), `zone_collision` (a left-zone element crossing x = -0.5 while right-zone content is present, or the mirror case). Elements entirely above y = 2.9 (title band) are excluded from zone checks. `sync_drift` (visuals more than the tolerance behind the narration at a segment boundary) comes from `next_segment` / `end_layout_check`. `cue_late` (a `self.cue(k)` reached more than 0.6 s after its word) and `cue_unused` (a segment with cue markers whose scene never calls `cue()`; template-driven scenes are exempt) come from the word-level sync. `hold_busy` (an animation still running more than `_HOLD_BUSY_TOL` = 0.5 s past the segment's `speech_end_sec`, i.e. inside the silent hold) comes from pacing. `label_on_curve` (a plotted curve running through a text label, tested on the curve's sampled path) comes from the geometry pass. The report also carries `cue_log`: `[{segment, cue, spoken_at, visual_at, lag}]` for every `cue()` call, and `mode`: `"render"` when a real render wrote it (it overwrites the dry-run report, and is what /api/library/{id}/quality reads for sync) or `"dry_run"`
- Return values: passed → `code_feedback=None, layout_renderable=True`; violations → formatted feedback, `code_attempts + 1`, `layout_renderable=True`; crash, timeout, missing or unreadable report, failure to start → feedback with `layout_renderable=False` (the stderr tail is included for crashes)
- `ChalkboardSceneBase` overrides `play()` (accumulates `run_time`, skipping `Wait` so `wait()` is not double counted) and `wait()` to measure per-segment time. Generated scenes call `self.begin_segment(n, duration=_d[n])` and `self.end_layout_check()`

### Word-level sync (cue markers)

Segment-level sync pins every segment start to the narration; cue markers pin the animations inside a segment to the words.

1. **Script** (`script_agent`): `[[k]]` markers before the cued words. `pipeline/cues.py` owns parsing: `parse_cues(text) -> (clean, {k: char_offset})` (offset = first letter of the cued word in the clean text), `strip_cues`, `clean_segments`. Clean text is what everything else reads (fact check, TTS, `script.txt`, captions, chapters, visual QA, quiz, library); the marked text lives only in a segment's `cue_text`.
2. **Times** (TTS): backends return `(path, durations, cue_times)`; `cue_times[i][k-1]` is the time of marker k in seconds from segment i's audio start (`None` for a skipped number). ElevenLabs uses `POST /v1/text-to-speech/{voice}/with-timestamps` (same body and stitching; JSON `audio_base64` + `alignment.characters` / `character_start_times_seconds`, which match the input text one to one, verified 2026-10-02) via `cue_times_from_alignment`; Kokoro maps `KPipeline.Result.tokens` (`text`, `start_ts` relative to each chunk) via `cue_times_from_tokens`; OpenAI returns the old 2-tuple and `render_trigger` fills `proportional_cue_times`. Speed scaling divides cue times by `speed` too.
3. **segments.json** per segment: `text` (clean), `actual_duration_sec`, `cues`, and `cue_text` when the segment has markers (cue, beat or chapter).
4. **Scene** (`ChalkboardSceneBase.cue(k)`): waits until `segment narration start + cues[k-1]` and returns, so the next `play()` starts on the word. Segment narration start = sum of the earlier segment budgets (the voiceover is the segments back to back). Cues load lazily from `segments.json` next to the scene module (`sys.modules[type(self).__module__].__file__`), falling back to the report directory; tests assign `scene._cues = {seg: [t, ...]}` (or a list of lists). Unknown cue numbers warn and return `False` without waiting; `has_cue(k)` checks first. Templates call `_cue(1)` before each segment's main reveal and `_cue(2)` before the second element (callout, right-hand point, next derivation line), and `_rest` waits out `segment_time_left()`.
5. **Scene clock:** in a real render (`not config.dry_run` and `frame_rate >= 10`) `_scene_time()` is `renderer.time`, the frames actually written. Manim rounds every play UP to whole frames (`np.arange(0, run_time, 1/fps)`) and static waits DOWN (`int(duration / dt)`), so summing requested run times drifts from the video by a frame per play. The 1 fps dry-run and unit tests keep the internal `_sync_tracked` clock.
6. **Captions:** `main._caption_cues` writes one SRT line per sentence (long sentences split at commas), timed through the segment's `anchors` (pacing: speech start, cue words, both sides of every inserted pause, speech end; the last line clears 0.6 s into the hold), else by interpolating through the cue anchors.
7. **`next_segment(n, d, clear=...)`** waits out the whole current segment (hold included), calls `begin_segment(n)`, then fades `clear` (charged to segment n, inside its silent lead-in). The fade never eats into the hold. `segment_time_left()` counts to the end of the segment's audio, `speech_time_left()` to its last word.
8. **Chapters** (`pipeline/chapters.py`): the script agent writes `[[ch: Short title]]` at the start of every segment and right before the cue marker of every distinct step, item or idea the visuals present (`[[ch: 3. Region test]] [[2]] Number three: ...`), so an enumeration of N items gets N chapters even when several items share a segment. `cues.parse_markers` strips them like beats (no clean-text consumer, TTS timing text, caption or fact check ever sees them); `cues.parse_chapter_markers(text) -> [(char_offset, title, cue_or_None)]` attaches each to the cue written right after (or right before) it. `build_chapters(segments)` reads `segments.json` (paced, final times): a chapter tied to a cue starts at segment start + `cues[k-1]` (when the visual lands), else at the segment start (offset 0), else through `anchors`, else `char_time`. Chapters closer than `MIN_GAP_S` (4 s) are merged by dropping the later one, but two numbered steps (`is_numbered`: "3. ...", "Step 3", "#3") never merge, and a numbered step replaces an unnumbered chapter just before it; repeated titles drop; the first chapter is moved to 0:00. Runs without any chapter marker (everything before 0.5.0) get one chapter per segment titled with its first 60 characters (`legacy_title`), exactly their old `chapters.txt`; nothing finer is invented. `main._generate_caption_files` writes `chapters.txt` (`ffmetadata`, escaped titles) and prints `youtube_list`; the timeline API returns the same list as `chapters`. The manim prompt asks for each chapter's cue to introduce that item visibly (own card / heading).

### Pacing (`pipeline/pacing.py`)

Presentational pacing is baked into the voiceover, so everything downstream (segment clock, cues, captions, chapters, waveform) includes it automatically.

- **Presets** (`PRESETS`, `resolve_pace(name)`; name from `state["pace"]`, else `PACE`, else `relaxed`; `SCENE_HOLD_S` and `PACE_SPEECH_SPEED` override single values): `relaxed` hold 2.0 / lead-in 0.5 / sentence 0.65 / question 1.1 / payoff 0.9 / beat 1.1 / speed 0.94; `normal` 1.2 / 0.35 / 0.45 / 0.75 / 0.6 / 0.75 / 1.0; `brisk` 0.6 / 0.15 / 0.3 / 0.45 / 0.4 / 0.45 / 1.06. Every value is a **minimum total silence** (the voice's natural gap counts; only the missing part is inserted).
- **Pause points** (`pause_points`): sentence end (`.`/`!` followed by a non-lowercase word), question (`?`), payoff (`:`), beat (`[[beat]]`/`[[pause]]`, paragraph break). One per word boundary, the longest kind wins. A beat closing the segment makes the hold at least a beat.
- **Beat markers** are parsed by `cues.parse_markers` (`parse_cues` strips them too, so every clean-text consumer is unaffected); the offset is the next spoken word.
- **Timing the pause points:** `render_trigger` sends each backend `cue_text` rebuilt by `pacing.timing_text`: the real cues plus extra markers `[[n+1]]...` at the pause points. Backends time them exactly like cues (ElevenLabs alignment, Kokoro tokens); the clean text spoken is unchanged. OpenAI returns no times, so all are proportional estimates and insertion uses a wider window (±1 s) snapped to the nearest natural gap.
- **Insertion** (`pace_segment`, `apply_pacing`): per segment, 10 ms RMS frames, quiet = below max(0.003, 0.1 × p95). Lead-in and trailing silence are measured and topped up. Each pause goes in the longest quiet run inside [t-0.35, t+0.08] of the next word's start (estimated: nearest run in [t-1, t+1]), at its center; with no quiet run, at the quietest frame and the full length is inserted. Cue k shifts by the lead-in pad plus every pad at a char offset <= its own. `segments.json` gains `speech_start_sec`, `speech_end_sec`, `hold_sec`, `pauses` ([{at, sec, gap, kind}]) and `anchors`. If the WAV cannot be read (test doubles) pacing is skipped with a printed warning and `manifest.pacing.applied` is false.
- **Native speed:** effective speed = `pace.speech_speed × state.speed`. ElevenLabs sends `voice_settings` = the voice's stored settings (GET `/v1/voices/{id}/settings`) with `speed` replaced, for 0.7-1.2 (the documented range); outside it, or when the settings cannot be read, it falls back to ffmpeg `atempo` (never a partial `voice_settings`, which would replace the stored ones). Kokoro passes `speed=` to `KPipeline` (token times scale with it, measured 2026-10-06). OpenAI passes `speed=`.
- **Why not ElevenLabs pauses:** `eleven_v4`/`v3` do not support SSML `<break>`; audio tags like `[pause]` appear only as examples, with no stated length, and would add characters to the alignment (https://elevenlabs.io/docs/overview/capabilities/text-to-speech/best-practices, checked 2026-10-06).
- **Estimates before TTS** (`estimate_segment`): estimated speech / effective speed, pads of (target - 0.2 s assumed natural gap), plus lead-in and hold. Used by the layout stub and the manim prompt.

---

## Design system

Generated scenes are composed from a runtime that lives in `docker/` and is importable by module name during rendering (`PYTHONPATH` includes `docker/` locally, `/render` in the image).

- **Tokens** (`docker/chalkboard_tokens.py`): the single source of truth for colors (per theme: `surface` and `role`), type sizes, spacing, stroke widths and motion (`snap`, `emphasis`, `settle`, `grand`, each a `{run_time, rate_func}`), plus `lag`. Accessed through `T(theme=...)`: `t.role()`, `t.surface()`, `t.type()`, `t.space()`, `t.stroke_width()`, `t.motion()`, `t.lag()`, `t.bg`, `t.body`. No Manim import at module level. `render_prompt_block(theme)` formats the table for the prompt. **Edit tokens only here**; `pipeline/design_tokens.py` loads this file by path and registers it as `sys.modules["chalkboard_tokens"]` so pipeline and renderer share one class.
- **Components** (`docker/chalkboard_components.py`): `ChalkBox`, `ChalkArrow`, `ChalkCode`, `Callout`, `StepCounter`, `ChalkAxis`, `ChalkAxes`, `ChalkPanel`, `ChalkBadge`, `EquationGroup`, `ChalkMatrix`, `NetworkNode`, plus `math_tex()`, `tex()`, `resolve_motion()`. Every component takes `theme=` and has `.highlight(role)` / `.mute()`. Size arguments are floors (components grow to fit their label). `ChalkAxes` also has `hline(y, label=)` / `vline(x, label=)`: reference lines whose value label sits outside the plot (right of it / above it), so no curve can cross it.
- **Moves** (`docker/chalkboard_moves.py`): `reveal_with_emphasis`, `compare_split`, `focus_zoom`, `morph_show_equivalence`, `cascade_reveal`, `progressive_step`, `annotate_and_pause`, `chapter_transition`, `derivation_step`, `transform_equation`, `emphasize_term`. Each takes the scene first and plays its own animations.
- **Templates** (`docker/chalkboard_templates/`): `Template(scene, beats, theme=...)` validates `beats` strictly at construction (raising `ValueError`, which surfaces in the layout dry-run and flows back to the agent) and `render_all(segment_durations)` emits every `begin_segment`, animation and wait. A template-driven scene has no `# ── Segment N:` blocks of its own.
- **House style** (`docker/chalkboard_style.py`): imported by `chalkboard_base`, so every scene gets it. Sets Manim-wide defaults: one `TexTemplate` for `Tex`/`MathTex` whose preamble loads babel, lmodern, microtype, amsmath, amssymb, mathtools, bm, mathrsfs, dsfont, cancel, xcolor, siunitx and defines `\R \N \Z \Q \C \E \dd \Var \Cov \tr \rank \argmax \argmin`; `Text`/`MarkupText`/`Paragraph` font = CMU Serif when installed (else Inter, DejaVu Sans), so prose and math share Computer Modern; `Code` font = JetBrains Mono NL, else DejaVu Sans Mono. Code fonts must be ligature-free (ligatures break Manim's per-glyph `Code` layout on `<=`, `->`, `==`). Override with `CHALKBOARD_TEXT_FONT` / `CHALKBOARD_CODE_FONT`. Axis tick labels: `NumberLine.__init__` is patched so labels show the decimals the step needs (`tick_decimal_places([min, max, step])`: 0.5 -> 1, 0.25 -> 2, 1 or 1.0 -> 0); a missing `num_decimal_places` is set and one too small for the step is raised, a larger one is kept, log-scaled axes are untouched. Axes, NumberPlane, `ChalkAxis` and `ChalkAxes` all build NumberLines, so this covers raw calls too (run 3253240e printed "0, 1, 2, 2, 2" on a 0.5 V step because `ChalkAxes` hard-coded 0 decimals).
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
- **Tick label decimals**: Manim 0.21 infers `num_decimal_places` from `str(step)` only when no `decimal_number_config` is passed (`1.0` gives "1.0", `0.1 + 0.2` gives 17 places) and takes an explicit value as is, so 0 on a 0.5 step rounds ticks to "0, 1, 2, 2, 2". `chalkboard_style` fixes this for every NumberLine; scenes should pick the step they want printed and never pass `decimal_number_config`.

When rendering fails, read the traceback (renderer output, or the stderr tail in the layout feedback). Patch `output/<run_id>/scene.py` to verify a fix, then encode it in the prompt.

---

### How the dry-run measures geometry

`_measure()` in `docker/chalkboard_base.py` takes the bounding box from a mobject's points (`get_all_points()`). Do not use `hasattr(m, "get_bounding_box")`: Manim's `Mobject.__getattr__` fabricates any `get_*` name, so it is always true and the call then fails (this silently disabled every geometry check until 2026-10-02). Invisible mobjects are skipped. Plain `VGroup`/`Group` containers are flattened so members are compared with each other. Partial overlaps are ignored when either side is a connector (`Line`, arrows, `Brace`, `ParametricFunction` curves, unfilled outlines) or the overlap is thinner than `_MIN_OVERLAP` (0.1). `zone_collision` counts an element as side-zone content only when its center is beyond `_ZONE_SIDE_CENTER` (1.5) and flags it only when it reaches into the far zone. Calibration: zero geometry flags on 14 real videos from 2026-10-02; the positive control `test_real_scene_flags_overlapping_text_and_offscreen` must keep passing.

**Labels on curves.** Curves are connectors in the overlap check (their bounding box covers most of the plot), so a value label placed where the curve passes went unreported (run 3253240e, segment 5). `_lc_check_labels_on_curves` tests every visible `ParametricFunction` (`ax.plot`, `FunctionGraph`), sampled at 4 points per Bezier piece, against the box of every outermost visible text mobject (`MathTex`, `Tex`, `Text`, ...; text inside components counts, tick numbers inside a `NumberLine`/`Axes` do not), shrunk by `_CURVE_LABEL_INSET` (0.03). A hit is a `label_on_curve` violation. Calibration 2026-10-09: the dry-run of all 12 library scenes that plot curves plus the first scenes of 3253240e and 9043e20b gave two flags, both real (19f0ff0f segment 7: the load line runs through "(1.8 V, 35 uA)", visible in the frame and missed by QA; 9043e20b's first scene: the load line under "V_DD / R_L", which QA also reported) and no false flags. Positive control: `test_label_on_curve_flags_the_3253240e_segment_5_label`.

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

**`api_call_with_retry(fn, timeout, max_attempts=3, label, passthrough=())`** (`pipeline/retry.py`) runs a sync callable in `asyncio.to_thread` under `asyncio.wait_for`, retrying on timeouts and errors. It raises `TimeoutExhausted` after the last attempt, or **immediately** for errors that cannot succeed on retry: `BadRequestError`, `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, and quota errors (`insufficient_quota`, `credit balance is too low`). The Anthropic SDK additionally retries 429/5xx inside each attempt (`max_retries=3`). Exceptions of a `passthrough` type are re-raised at once, unwrapped (`call_json_budgeted` passes `ClaudeTruncated`: repeating a call that ran out of room fails the same way). `TimeoutExhausted` propagates out of `graph.astream()` and is handled in `run()`: interactive runs offer `retry / abort`; non-interactive runs re-raise.

**`subprocess_with_timeout(cmd, timeout, on_line=None, env=None)`** (`main.py`) kills the process via `threading.Timer` after `timeout`; returns `(returncode, lines_buffer, timed_out)`. Used for render subprocesses.

**Process exit and output** (`main.py`, run as a script): `_guard_stdio()` wraps stdout/stderr in `_PipeSafeStream`, so a reader that goes away (an ssh or `wsl.exe` relay that closed, a killed `tee`) no longer turns the next `print` into `BrokenPipeError`; output is dropped (fd pointed at `/dev/null`) and the run finishes its files. Run 244514b9 lost its QA loop and was recorded `failed` this way. After `main()` returns, `_exit_promptly(code)` joins non-daemon threads for `EXIT_GRACE_S` (10 s) and then `os._exit`s if any are still alive: an `api_call_with_retry` attempt that timed out leaves its `asyncio.to_thread` worker blocked in the HTTP call, and a normal interpreter exit would wait for it. `_run_cli()` maps `SystemExit` and uncaught exceptions to the exit status the interpreter would use.

**Timeout constants** (`pipeline/retry.py`):

| Constant | Value | Used by |
|----------|-------|---------|
| `TIMEOUT_SCRIPT_AGENT` | 300s | script_agent |
| `TIMEOUT_RESEARCH_AGENT` | 300s | research_agent |
| `TIMEOUT_FACT_VALIDATOR` | 180s | fact_validator |
| `TIMEOUT_MANIM_AGENT` | 900s | manim_agent (base; Claude calls scale it with the budget, see `budget_timeout`) |
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
    *, voice=None, model=None,
    on_segment=None,        # optional: on_segment(index, chars) as each segment finishes (live `tts` events)
) -> tuple[Path, list[float]] | tuple[Path, list[float], list[list[float | None]]]:
    # (wav_path, actual_durations_per_segment[, cue_times_per_segment])
```

Speak the clean text: `parse_cues(segment_cue_text(seg))` gives it plus the marker offsets (see Word-level sync).

- **OpenAI** (`openai_tts.py`): segments synthesized in parallel (`asyncio.gather`, at most `MAX_CONCURRENT = 6` at once), each through `api_call_with_retry`; results are concatenated in segment order. `speed=` goes to the API. Env: `OPENAI_TTS_MODEL` (default `gpt-4o-mini-tts`), `OPENAI_TTS_VOICE` (default `alloy`), `OPENAI_TTS_INSTRUCTIONS` (sent only when the model name starts with `gpt-`).
- **Kokoro** (`kokoro_tts.py`): `KPipeline` is created once per process (`functools.cache` on `_pipeline()`, so it runs on the GPU when CUDA is available and is not reloaded per job). Voice: `KOKORO_VOICE` (default `af_heart`). Tests clear the cache in `conftest.py`.
- **ElevenLabs** (`elevenlabs_tts.py`): plain REST via httpx (no SDK). Default model `eleven_v4`, default voice Skye. On models that support it (everything except `eleven_v3`, which rejects `previous_text`/`next_text`/`previous_request_ids` with `unsupported_model`, verified 2026-10-02) segments are synthesized **in order** and each request carries `previous_request_ids` (last 3), `previous_text` and `next_text`, so prosody carries across segment joins. v3 runs segments in parallel (`ELEVENLABS_CONCURRENCY`, default 3) without context. Env: `ELEVENLABS_MODEL_ID`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_STITCH=0`.
- **Narrators** (`voices.py`): `aria`/`milo` (ElevenLabs Skye/Bradley on `eleven_v4`, ids pinned because the library renames voices; chosen in an April 2026 blind test and an October 2026 v4 audition), `kokoro`, `alloy`. `render_trigger` resolves `state["narrator"]` or `NARRATOR` (config) to a backend + `voice=`/`model=` kwargs; unset keeps the `TTS_BACKEND` default. The manifest records `narrator`. `scripts/tts_bench.py` compares voices on one script.
- **Speed:** native everywhere (see Pacing): OpenAI `speed=`, Kokoro `speed=`, ElevenLabs `voice_settings.speed` within 0.7-1.2. Only ElevenLabs outside that range (or without readable stored settings) uses `_apply_speed_to_wav` (ffmpeg `atempo`, chained via `_build_atempo` outside [0.5, 2.0]), dividing durations and cue times by `speed`.

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
| `voiceover.wav` | Concatenated TTS audio for all segments (at final speed, with pacing silences) |
| `segments.json` | `[{"text": str, "actual_duration_sec": float, "cues": [float], "cue_text"?: str, "speech_start_sec", "speech_end_sec", "hold_sec", "pauses": [{at, sec, gap, kind}], "anchors": [[char, sec]]}]`, post-speed, post-pacing durations and cue-marker times (seconds from the segment start) |
| `script.txt` | Full narration script |
| `manifest.json` | `{run_id, scene_class_name, quality, topic, title, effort, audience, tone, theme, template, speed, pace, pacing, narrator}`; `quality` is `state["quality"] or MANIM_QUALITY`; `pacing` = the resolved preset values + `effective_speed` + `applied` |

Written later by `main.py`: `layout_report.json` (layout_checker, before render_trigger), `run_stats.json` (every CLI and server run: timings, tokens, cost, TTS chars, settings, result, per-agent usage `by_agent`, `budget_retries`, `scene_parts`, `warnings`; a run that fails before render_trigger gets its directory created for this file when it made Claude calls; `pipeline/run_stats.py`), `qa_report.json` (latest visual QA result + `history`, `main._save_qa_report`), `waveform.json` (cached peak envelope, written by the timeline endpoint), `media/` or `media_preview/` (Manim output), `captions.srt` and `chapters.txt` (`_generate_caption_files`, before the merge), `final.mp4` or `preview.mp4`, `thumb.jpg` (`_extract_thumbnail`), `qa_frames/` (visual QA), `quiz.json` (`--quiz`).

`chapters.txt` (from `pipeline/chapters.py`, one chapter per `[[ch: ...]]` marker, or per segment for older scripts) is FFMETADATA1 passed to ffmpeg as `-f ffmetadata -i chapters.txt -map_metadata 2`. `--burn-captions` adds `-vf subtitles=<path>` and switches `-c:v copy` to `libx264 -preset fast -crf 18`.

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

LangGraph's `AsyncSqliteSaver` (`CHECKPOINT_DB`, default `pipeline_state.db`) checkpoints after every node, keyed by `thread_id` = run id. `_open_checkpointer()` opens it with aiosqlite and a 30 s busy timeout (`CHECKPOINT_BUSY_TIMEOUT_S`); the saver puts the file in WAL mode, so concurrent CLI runs and server jobs (one file, one thread id each) wait for each other's write lock instead of failing (`tests/test_process_exit.py`). `run()` calls `graph.aget_state(config)` first:

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

`server/library.py`: `VideoMeta` (17 persisted fields: `run_id`, `topic`, `title`, `created_at`, `duration_sec`, `quality`, `thumb_path`, `script`, `effort`, `audience`, `tone`, `theme`, `template`, `speed`, `status`, `narrator`, `notes`; `init()` reads `PRAGMA table_info` and adds any of the `title` / `narrator` / `notes` columns an older database lacks, in place, keeping every row (new columns go in `_ADDED_COLUMNS`); `output_files` is computed from disk; fields the run files do not record are `None`, never a default). `notes` is the only user-owned field: `add_video` upserts every other column (`ON CONFLICT DO UPDATE`), so backfill and finished jobs never wipe notes; `set_notes` trims, stores empty as `NULL`, and returns `None` for an unknown run. Search (`list_videos(query=...)`) matches topic, title, script and notes. The `LibraryStore` ABC (`init`, `add_video`, `get_video`, `list_videos`, `delete_video`, `set_notes`) and `SQLiteLibraryStore` (`aiosqlite`, WAL, `library.db`). A Postgres store can implement the same interface and be passed to `create_app(library_store=...)`.

`_backfill(store, output_dir)` (`server/app.py`) indexes every `output/` directory with `manifest.json` and `final.mp4` at startup and, throttled to once per 10 s, on `GET /api/library` so CLI runs show up without a restart (idempotent; `final.mp4` mtime as `created_at`; at startup `refresh=True` also re-reads indexed runs and corrects rows that older code filled with defaults). Consequence: `DELETE /api/library/{id}` without `?files=true` is undone by the next listing, because the files are still there; the web UI always deletes with `files=true`. Old manifests missing fields give `None` (shown as unknown). List items add `has_run_stats`, `has_final`, `thumb_url`, `video_url`, `run_seconds`, `cost_usd`, `run_result` (`library_routes._list_item`).

Routes: `make_library_router(store)` (`GET/PATCH/DELETE /api/library...`) and `make_pages_router()` (`/library`, `/library/{run_id}`).

---

## API server

```bash
python run_server.py                  # 127.0.0.1:8000 (SERVER_HOST / SERVER_PORT)
python run_server.py --host 0.0.0.0   # serve the LAN; there is no auth
python run_server.py --reload         # dev (kills in-flight jobs on reload)
```

- **`server/app.py`**: `create_app(...)` factory, mounts `server/static/` at `/`, library init + backfill in the `lifespan` handler. Module-level `app` for uvicorn.
- **`server/jobs.py`**: `Job` dataclass (status, events, output_files, async queue, pipeline params including `quality`), in-memory `JobStore`, `run_job(job, output_dir, library_store)` which waits on a module-level `asyncio.Semaphore(MAX_CONCURRENT_JOBS)` (default 3) and then runs pipeline + render + QA + quiz, `_do_render(run_id, burn_captions)`.
- **`server/routes.py`**: `make_router(store, library_store)`. Job tasks are created with `_spawn()`, which keeps a strong reference in `_running_tasks` (the event loop only holds weak references, so an unreferenced task can be garbage-collected mid-run). `/api/claude-status` reads the status.claude.com JSON API (API component state, open incidents). `/api/meta` returns server defaults, the narrators (with `configured` = API key present, Kokoro always; availability is `/api/voices`), the resolved render backend, `CLAUDE_MODEL` and the count of pending/running jobs. The multipart upload route builds a `CreateJobRequest` from its form fields first, so it validates (422) exactly like the JSON route before any file is saved.
- **`server/models.py`**: `CreateJobRequest` (topic, effort, audience, tone, theme, template, speed, burn_captions, quiz, urls, github, qa_density, quality, narrator, pace), `JobResponse` (id, status, topic, events, error, output_files). The multipart upload route takes the same fields as form fields (`quality=""` means default).
- **Frontend:** `index.html`, `library.html` and `video.html` each load `/app.css` and `/app.js` in `<head>`. `app.js` exposes helpers on `window.CB`, renders the nav (Generate / Library tabs, Claude status, jobs menu, theme toggle) and the `window.jobStatus` store. No build step. (`job-status.js` was removed; its role moved into `app.js`.)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/jobs` | Create job (202 + `JobResponse`) |
| `POST` | `/api/jobs/upload` | Create job with multipart file uploads |
| `GET` | `/api/jobs` | List jobs |
| `GET` | `/api/jobs/{id}` | Get job (404 if missing) |
| `GET` | `/api/jobs/{id}/events` | SSE stream: replays stored events (`?replay=0` to skip), then node updates and live telemetry (`peek`, `usage`, `render`, `tts`), then `{"done": true}` |
| `GET` | `/api/jobs/{id}/files/{filename}` | Serve an output file (path-traversal-safe; also works for library runs) |
| `GET` | `/api/claude-status` | Claude status summary |
| `GET` | `/version`, `/api/version` | Version record (`server/version_routes.py`), `Cache-Control: no-store` |
| `GET` | `/api/meta` | Defaults, narrators + `configured` (key set), render backend, model, running jobs, `version` |
| `GET` | `/api/jobs/{id}/timeline` | Segments, measured durations, cues, waveform, rendered segments |
| `GET` | `/api/library/{id}/timeline` | Same, for a finished run |
| `GET` | `/api/library/{id}/quality` | Sync (render cue_log), layout report, visual QA |
| `GET` | `/api/library/{id}/stats` | The run's run_stats.json (or null) |
| `PATCH` | `/api/library/{id}` | Set notes: `{"notes": str\|null}` (trimmed, empty = null, max 10,000 chars; 404 unknown run); returns `{run_id, notes, saved_at}` |
| `GET` | `/api/stats` | Library-wide measured stats |
| `GET` | `/api/status` | Live health checks with evidence (cached 60 s) |
| `GET` | `/api/voices` | Narrators + evidence-based availability |
| `GET` | `/api/voices/{id}/sample` | Real TTS sample (cached; 503 with the reason on failure) |
| `GET` | `/api/estimate` | Time/cost percentiles from matching finished runs (>= 3) |

The UI data contract (event shapes, endpoints, null rules) is `docs/ui-data-contract.md`: every value is measured or computed from run files, or null.

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
pytest                        # 829 tests (with Manim + TeX installed)
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
