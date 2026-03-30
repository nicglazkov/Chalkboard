# Context Injection Design

**Goal:** Allow users to pass local files (PDFs, images, code, docs) as source material alongside their topic prompt, so the pipeline can turn lecture notes, papers, codebases, or reports into animations.

**Architecture:** Preprocessing step in `main.py` before the graph runs. `pipeline/context.py` handles file discovery, content block creation, and token measurement. Content blocks are threaded into `script_agent` and `manim_agent` as a parameter — never stored in `PipelineState` (only file paths are stored). LangGraph checkpointing is unaffected.

**Tech Stack:** `python-docx` (Word extraction), `pathspec` (gitignore parsing). Anthropic SDK's `messages.count_tokens` and `models.retrieve` for token reporting.

---

## CLI

Two new repeatable flags:

```
--context path          One file or directory. Can be specified multiple times.
--context-ignore pat    Extra glob pattern to exclude (e.g. "*.log", "tests/"). Repeatable.
```

Examples:
```bash
python main.py --topic "explain this codebase" --context ./src --context ./docs
python main.py --topic "summarize this paper" --context paper.pdf
python main.py --topic "visualize this" --context ./repo --context-ignore "*.lock" --context-ignore "dist/"
```

---

## Token reporting

Always printed when `--context` is passed:

```
Context: 12 files, ~38k tokens  (model window: 200k, ~19% used by context)
```

- Token count: `client.messages.count_tokens(model=CLAUDE_MODEL, messages=[{"role": "user", "content": blocks}])`
- Context window: `client.models.retrieve(CLAUDE_MODEL).context_window`

If tokens > 10k, pause for confirmation:

```
Context is large. Proceed? (y/n):
```

If `n`, exit cleanly before any pipeline API calls. If tokens ≤ 10k, proceed without prompting.

Hard error (regardless of threshold) if context alone exceeds 90% of the context window:

```
Error: context files use X% of the model context window. Reduce files before proceeding.
```

If the token count call itself fails, print a warning and skip the report — proceed without prompting.

---

## File discovery (`pipeline/context.py`)

### `collect_files(paths, ignore_patterns) -> list[Path]`

- Files passed directly are added as-is (error if not found).
- Directories are walked recursively.
- At each directory level, any `.gitignore` found there is parsed with `pathspec` and applied to that subtree.
- `ignore_patterns` (from `--context-ignore`) are applied as additional glob patterns globally.
- Hidden directories (`.git/`, `.worktrees/`, etc.) are skipped by default.
- Empty result after filtering: warning, zero blocks contributed from that path.
- Unreadable file (permissions error): warning, skip.

---

## Content block creation (`pipeline/context.py`)

### `load_context_blocks(files) -> list[dict]`

Each file produces a label block followed by a content block:

```python
[
    {"type": "text", "text": "--- file: src/main.py ---"},
    {"type": "text", "text": "<file contents>"},
]
```

| File type | Block type | Method |
|-----------|-----------|--------|
| `.txt`, `.md`, and common code/config extensions (`.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.c`, `.cpp`, `.rb`, `.swift`, `.kt`, `.sh`, `.yaml`, `.json`, `.toml`, `.csv`, `.html`, `.css`, `.xml`, `.ini`, `.env`) | `text` | `file.read_text(errors="replace")` |
| `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` | `image` (base64) | `base64.b64encode(file.read_bytes())` with correct `media_type` |
| `.pdf` | `document` (base64) | `base64.b64encode(file.read_bytes())` with `media_type: "application/pdf"` |
| `.docx` | `text` | `python-docx` paragraph extraction; error if not installed |
| anything else | — | skip, print `Warning: skipping unsupported file type: <path>` |

PDF document blocks require the Anthropic beta header `"anthropic-beta": "pdfs-2024-09-25"`. The `anthropic.Anthropic()` client is initialized with `default_headers={"anthropic-beta": "pdfs-2024-09-25"}` when any PDF is present in the context.

### `measure_context(blocks, client) -> tuple[int, int]`

Returns `(token_count, context_window)`. Both values come from the Anthropic API — nothing is hardcoded.

---

## PipelineState change

One new field in `pipeline/state.py`:

```python
context_file_paths: list[str]  # paths of loaded context files; empty list if none
```

Set in `_init_state` from the paths passed into `run()`. Used for informational display only — never read by agents.

---

## Agent integration

### `run()` signature

```python
async def run(topic, effort, thread_id, audience, tone, theme,
              context_blocks=None, context_file_paths=None) -> None:
```

`context_blocks` is the live list of Anthropic content blocks. `context_file_paths` is stored in state for logging.

### `script_agent(state, client=None, context_blocks=None)`

When `context_blocks` is present, the user message becomes a list of content blocks:

```python
content = []
if context_blocks:
    content.append({
        "type": "text",
        "text": "The following files are provided as source material. Use them to inform the script content, facts, and framing:"
    })
    content.extend(context_blocks)
content.append({"type": "text", "text": _build_user_message(state)})
# pass content (list) instead of string to messages=[{"role": "user", "content": content}]
```

When `context_blocks` is `None`, the existing string-based user message is used unchanged — no behavior change for the no-context path.

### `manim_agent(state, client=None, context_blocks=None)`

Same pattern, different framing:

```python
content = []
if context_blocks:
    content.append({
        "type": "text",
        "text": "The following files are provided as source material. Use them to inform what the animation should visualize:"
    })
    content.extend(context_blocks)
content.append({"type": "text", "text": user_msg})
```

---

## Resume behavior

`context_blocks` is never stored in `PipelineState` or the checkpoint DB. On `--run-id` resume:

- If `--context` is re-specified: context is loaded fresh and applied to any re-run nodes.
- If `--context` is omitted: pipeline resumes without context. Print once at startup:

```
Note: resuming without context files. Pass --context to include source material.
```

---

## Error handling

| Situation | Behavior |
|-----------|----------|
| `--context` path not found | Hard error, exit before any API calls |
| Directory empty after filtering | Warning, continue |
| Unreadable file (permissions) | Warning, skip |
| Unsupported extension | Warning, skip |
| `.docx` but `python-docx` not installed | Hard error: "Install python-docx: pip install python-docx" |
| Token count API call fails | Warning, skip report, proceed without confirmation prompt |
| Context > 90% of context window | Hard error, exit |

---

## New files and changes

| File | Change |
|------|--------|
| `pipeline/context.py` | **New** — `collect_files`, `load_context_blocks`, `measure_context` |
| `tests/test_context.py` | **New** — unit tests for all three functions |
| `main.py` | `--context` + `--context-ignore` flags; token report + confirmation prompt; thread `context_blocks` into `run()` |
| `pipeline/state.py` | Add `context_file_paths: list[str]` |
| `pipeline/graph.py` | `_init_state` sets `context_file_paths` |
| `pipeline/agents/script_agent.py` | Accept `context_blocks=None`; build list-based content when present |
| `pipeline/agents/manim_agent.py` | Same |
| `requirements.txt` | Add `python-docx`, `pathspec` |

---

## Testing

`tests/test_context.py`:

- `collect_files` walks directories recursively
- `collect_files` respects `.gitignore` patterns via `pathspec`
- `collect_files` applies `--context-ignore` glob patterns
- `collect_files` skips hidden directories
- `collect_files` errors on missing path
- `load_context_blocks` produces correct block types per extension
- `load_context_blocks` labels each file with its filename
- `load_context_blocks` skips unsupported extensions with a warning
- `.docx` extraction produces a text block (mock python-docx)
- `measure_context` returns `(token_count, context_window)` (mock client)

Agent tests: no changes needed — `context_blocks=None` default keeps existing tests valid.
