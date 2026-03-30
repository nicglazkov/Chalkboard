# Context Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to pass local files (PDFs, images, code, docs) via `--context` CLI flags as source material injected into the script and manim agents.

**Architecture:** `pipeline/context.py` handles file discovery (recursive, gitignore-aware), content block creation, and token measurement. `main.py` loads files before the graph runs, reports token usage, and threads `context_blocks` through `run()` → `build_graph()` → agents as a parameter. `PipelineState` stores only file paths (not content) so the checkpoint DB stays lean.

**Tech Stack:** `python-docx` (Word extraction), `pathspec` (gitignore parsing), Anthropic SDK `messages.count_tokens` + `models.retrieve`.

---

## File Structure

| File | Change |
|------|--------|
| `pipeline/state.py` | Add `context_file_paths: list[str]` |
| `pipeline/graph.py` | `_init_state` initialises new field; `build_graph` accepts `context_blocks` and wraps agents |
| `tests/conftest.py` | Add `context_file_paths=[]` to `base_state` fixture |
| `pipeline/context.py` | **New** — `collect_files`, `load_context_blocks`, `measure_context` |
| `tests/test_context.py` | **New** — unit tests for all three functions |
| `pipeline/agents/script_agent.py` | Accept `context_blocks=None`; build list content when present; PDF beta header |
| `pipeline/agents/manim_agent.py` | Same |
| `tests/test_script_agent.py` | Add 2 context tests |
| `tests/test_manim_agent.py` | Add 2 context tests |
| `main.py` | `--context` / `--context-ignore` flags; `_report_context`; updated `run()` signature |
| `requirements.txt` | Add `python-docx`, `pathspec` |

---

## Task 1: Add `context_file_paths` to PipelineState

**Files:**
- Modify: `pipeline/state.py`
- Modify: `pipeline/graph.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add field to `pipeline/state.py`**

Add one line to `PipelineState` after the `status` field:

```python
class PipelineState(TypedDict):
    topic: str
    run_id: str
    script: str
    script_segments: list[dict]
    manim_code: str
    script_attempts: int
    code_attempts: int
    fact_feedback: str | None
    code_feedback: str | None
    effort_level: Literal["low", "medium", "high"]
    audience: Literal["beginner", "intermediate", "expert"]
    tone: Literal["casual", "formal", "socratic"]
    theme: Literal["chalkboard", "light", "colorful"]
    needs_web_search: bool
    user_approved_search: bool
    status: Literal["drafting", "validating", "needs_user_input", "approved", "failed"]
    context_file_paths: list[str]
```

- [ ] **Step 2: Update `_init_state` in `pipeline/graph.py`**

Add one line to the return dict in `_init_state`:

```python
def _init_state(state: PipelineState, config: RunnableConfig | None = None) -> dict:
    """Populate default fields at graph entry."""
    run_id = (config or {}).get("configurable", {}).get("thread_id", str(uuid.uuid4()))
    return {
        "run_id": run_id,
        "script": state.get("script", ""),
        "script_segments": state.get("script_segments", []),
        "manim_code": state.get("manim_code", ""),
        "script_attempts": state.get("script_attempts", 0),
        "code_attempts": state.get("code_attempts", 0),
        "fact_feedback": state.get("fact_feedback"),
        "code_feedback": state.get("code_feedback"),
        "needs_web_search": state.get("needs_web_search", False),
        "user_approved_search": state.get("user_approved_search", False),
        "audience": state.get("audience", "intermediate"),
        "tone": state.get("tone", "casual"),
        "theme": state.get("theme", "chalkboard"),
        "context_file_paths": state.get("context_file_paths", []),
        "status": "drafting",
    }
```

- [ ] **Step 3: Update `base_state` fixture in `tests/conftest.py`**

Add `context_file_paths=[]` to the `PipelineState(...)` constructor call:

```python
@pytest.fixture
def base_state() -> PipelineState:
    return PipelineState(
        topic="explain how B-trees work",
        run_id="test-run-001",
        script="",
        script_segments=[],
        manim_code="",
        script_attempts=0,
        code_attempts=0,
        fact_feedback=None,
        code_feedback=None,
        effort_level="medium",
        audience="intermediate",
        tone="casual",
        theme="chalkboard",
        needs_web_search=False,
        user_approved_search=False,
        status="drafting",
        context_file_paths=[],
    )
```

- [ ] **Step 4: Run full test suite to verify nothing broke**

```bash
python3 -m pytest -q
```

Expected: 64 passed

- [ ] **Step 5: Commit**

```bash
git add pipeline/state.py pipeline/graph.py tests/conftest.py
git commit -m "feat: add context_file_paths field to PipelineState"
```

---

## Task 2: Add `python-docx` and `pathspec` to requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Update `requirements.txt`**

```
langgraph>=1.1.3
langgraph-checkpoint-sqlite>=3.0.3
aiosqlite>=0.20
anthropic>=0.77.1
kokoro>=0.9.4
soundfile
numpy
pydantic>=2.0
python-dotenv
pytest
pytest-asyncio
pathspec
python-docx
# Optional TTS backends (uncomment as needed):
openai
# elevenlabs
```

- [ ] **Step 2: Install new dependencies**

```bash
pip install pathspec python-docx
```

Expected: both install successfully

- [ ] **Step 3: Run full test suite to verify nothing broke**

```bash
python3 -m pytest -q
```

Expected: 64 passed

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add pathspec and python-docx dependencies"
```

---

## Task 3: Create `pipeline/context.py` — `collect_files`

**Files:**
- Create: `pipeline/context.py`
- Create: `tests/test_context.py`

- [ ] **Step 1: Write failing tests for `collect_files`**

Create `tests/test_context.py`:

```python
# tests/test_context.py
import pytest
from pathlib import Path
from pipeline.context import collect_files


def test_collect_files_single_file(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    result = collect_files([str(f)])
    assert result == [f.resolve()]


def test_collect_files_directory_recursive(tmp_path):
    (tmp_path / "a.py").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("y")
    result = collect_files([str(tmp_path)])
    names = [p.name for p in result]
    assert "a.py" in names
    assert "b.py" in names


def test_collect_files_respects_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "main.py").write_text("code")
    (tmp_path / "debug.log").write_text("log content")
    result = collect_files([str(tmp_path)])
    names = [p.name for p in result]
    assert "main.py" in names
    assert "debug.log" not in names


def test_collect_files_respects_extra_ignore_patterns(tmp_path):
    (tmp_path / "main.py").write_text("code")
    (tmp_path / "test_main.py").write_text("tests")
    result = collect_files([str(tmp_path)], ignore_patterns=["test_*.py"])
    names = [p.name for p in result]
    assert "main.py" in names
    assert "test_main.py" not in names


def test_collect_files_skips_hidden_directories(tmp_path):
    hidden = tmp_path / ".git"
    hidden.mkdir()
    (hidden / "config").write_text("git config")
    (tmp_path / "main.py").write_text("code")
    result = collect_files([str(tmp_path)])
    assert not any(".git" in str(p) for p in result)
    assert any(p.name == "main.py" for p in result)


def test_collect_files_raises_on_missing_path():
    with pytest.raises(FileNotFoundError, match="Context path not found"):
        collect_files(["/nonexistent/path/does_not_exist.txt"])


def test_collect_files_warns_on_empty_directory(tmp_path, capsys):
    (tmp_path / ".gitignore").write_text("*\n")
    (tmp_path / "ignored.txt").write_text("content")
    result = collect_files([str(tmp_path)])
    assert result == []
    captured = capsys.readouterr()
    assert "Warning" in captured.out


def test_collect_files_deduplicates_when_same_path_passed_twice(tmp_path):
    f = tmp_path / "file.py"
    f.write_text("x")
    result = collect_files([str(f), str(f)])
    assert len(result) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_context.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.context'`

- [ ] **Step 3: Create `pipeline/context.py` with `collect_files`**

```python
# pipeline/context.py
import base64
import os
from pathlib import Path

try:
    import pathspec as _pathspec
except ImportError:
    _pathspec = None  # type: ignore[assignment]

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None  # type: ignore[assignment,misc]

TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".swift", ".kt", ".sh",
    ".bash", ".zsh", ".fish", ".yaml", ".yml", ".json", ".toml", ".csv",
    ".html", ".css", ".scss", ".xml", ".ini", ".env", ".sql", ".graphql",
    ".proto", ".tf", ".hcl", ".vue", ".php", ".scala", ".clj", ".hs",
    ".ml", ".r", ".lua", ".pl", ".ex", ".exs", ".erl", ".dart", ".elm",
    ".gitignore", ".editorconfig", ".dockerignore", ".makefile",
}

IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def collect_files(paths: list[str], ignore_patterns: list[str] | None = None) -> list[Path]:
    """
    Collect all files from paths (files or directories).
    Directories are walked recursively, respecting .gitignore files and ignore_patterns.
    Returns deduplicated list of resolved Paths.
    Raises FileNotFoundError if any path does not exist.
    """
    if _pathspec is None:
        raise ImportError("Install pathspec: pip install pathspec")

    ignore_patterns = ignore_patterns or []
    extra_spec = (
        _pathspec.PathSpec.from_lines("gitwildmatch", ignore_patterns)
        if ignore_patterns else None
    )
    result: list[Path] = []

    for path_str in paths:
        p = Path(path_str).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Context path not found: {path_str}")
        if p.is_file():
            result.append(p)
        else:
            collected = _walk_directory(p, extra_spec)
            if not collected:
                print(f"  Warning: no files found in {path_str} after filtering")
            result.extend(collected)

    # Deduplicate while preserving order
    seen: set[Path] = set()
    deduped: list[Path] = []
    for f in result:
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    return deduped


def _walk_directory(root: Path, extra_spec) -> list[Path]:
    """Walk root recursively, respecting .gitignore at each directory level."""
    result: list[Path] = []
    gitignore_specs: dict[Path, object] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)

        # Skip hidden directories in-place (modifying dirnames stops os.walk descending)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))

        # Load .gitignore for this directory if present
        gitignore_path = current / ".gitignore"
        if gitignore_path.exists():
            try:
                spec = _pathspec.PathSpec.from_lines(
                    "gitwildmatch", gitignore_path.read_text().splitlines()
                )
                gitignore_specs[current] = spec
            except Exception:
                pass

        for filename in sorted(filenames):
            file_path = current / filename

            # Check gitignore specs from this dir and all parent dirs within root
            skip = False
            for ancestor, spec in gitignore_specs.items():
                try:
                    rel = file_path.relative_to(ancestor)
                    if spec.match_file(str(rel)):
                        skip = True
                        break
                except ValueError:
                    pass
            if skip:
                continue

            # Check extra ignore patterns relative to root
            if extra_spec:
                try:
                    rel = file_path.relative_to(root)
                    if extra_spec.match_file(str(rel)):
                        continue
                except ValueError:
                    pass

            try:
                file_path.stat()  # permission check
                result.append(file_path)
            except PermissionError:
                print(f"  Warning: cannot read {file_path} — skipping")

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_context.py -v
```

Expected: 8 PASS

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest -q
```

Expected: 72 passed (64 + 8)

- [ ] **Step 6: Commit**

```bash
git add pipeline/context.py tests/test_context.py
git commit -m "feat: add pipeline/context.py with collect_files"
```

---

## Task 4: Add `load_context_blocks` and `measure_context`

**Files:**
- Modify: `pipeline/context.py`
- Modify: `tests/test_context.py`

- [ ] **Step 1: Add failing tests to `tests/test_context.py`**

Append to `tests/test_context.py`:

```python
import base64
from unittest.mock import MagicMock, patch
from pipeline.context import load_context_blocks, measure_context


# ---------------------------------------------------------------------------
# load_context_blocks
# ---------------------------------------------------------------------------

def test_load_context_blocks_text_file_produces_label_and_text(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello world")
    blocks = load_context_blocks([f])
    assert len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert "notes.txt" in blocks[0]["text"]
    assert blocks[0]["text"].startswith("--- file:")
    assert blocks[1] == {"type": "text", "text": "hello world"}


def test_load_context_blocks_python_file(tmp_path):
    f = tmp_path / "script.py"
    f.write_text("def foo(): pass")
    blocks = load_context_blocks([f])
    assert any("def foo" in b.get("text", "") for b in blocks)


def test_load_context_blocks_image_png(tmp_path):
    f = tmp_path / "image.png"
    raw = b"\x89PNG\r\n\x1a\n"
    f.write_bytes(raw)
    blocks = load_context_blocks([f])
    image_block = next(b for b in blocks if b.get("type") == "image")
    assert image_block["source"]["media_type"] == "image/png"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["data"] == base64.standard_b64encode(raw).decode()


def test_load_context_blocks_image_jpeg_media_type(tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    blocks = load_context_blocks([f])
    image_block = next(b for b in blocks if b.get("type") == "image")
    assert image_block["source"]["media_type"] == "image/jpeg"


def test_load_context_blocks_pdf_file(tmp_path):
    f = tmp_path / "paper.pdf"
    raw = b"%PDF-1.4"
    f.write_bytes(raw)
    blocks = load_context_blocks([f])
    doc_block = next(b for b in blocks if b.get("type") == "document")
    assert doc_block["source"]["media_type"] == "application/pdf"
    assert doc_block["source"]["data"] == base64.standard_b64encode(raw).decode()


def test_load_context_blocks_docx_extracts_text(tmp_path):
    f = tmp_path / "notes.docx"
    mock_doc = MagicMock()
    mock_doc.paragraphs = [
        MagicMock(text="First paragraph"),
        MagicMock(text="Second paragraph"),
        MagicMock(text=""),  # empty — should be filtered
    ]
    with patch("pipeline.context.DocxDocument", return_value=mock_doc):
        blocks = load_context_blocks([f])
    combined = " ".join(b.get("text", "") for b in blocks)
    assert "First paragraph" in combined
    assert "Second paragraph" in combined


def test_load_context_blocks_unsupported_extension_skipped(tmp_path, capsys):
    f = tmp_path / "data.xyz"
    f.write_bytes(b"\x00\x01\x02")
    blocks = load_context_blocks([f])
    assert blocks == []
    captured = capsys.readouterr()
    assert "Warning: skipping unsupported file type" in captured.out


def test_load_context_blocks_multiple_files(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("aaa")
    b = tmp_path / "b.txt"
    b.write_text("bbb")
    blocks = load_context_blocks([a, b])
    text_contents = [bl.get("text", "") for bl in blocks]
    assert any("a.txt" in t for t in text_contents)
    assert any("b.txt" in t for t in text_contents)
    assert any("aaa" in t for t in text_contents)
    assert any("bbb" in t for t in text_contents)


# ---------------------------------------------------------------------------
# measure_context
# ---------------------------------------------------------------------------

def test_measure_context_returns_token_count_and_window():
    mock_client = MagicMock()
    mock_client.messages.count_tokens.return_value = MagicMock(input_tokens=42000)
    mock_client.models.retrieve.return_value = MagicMock(context_window=200000)

    blocks = [{"type": "text", "text": "hello"}]
    token_count, context_window = measure_context(blocks, mock_client)

    assert token_count == 42000
    assert context_window == 200000


def test_measure_context_calls_correct_api(tmp_path):
    from config import CLAUDE_MODEL
    mock_client = MagicMock()
    mock_client.messages.count_tokens.return_value = MagicMock(input_tokens=100)
    mock_client.models.retrieve.return_value = MagicMock(context_window=200000)

    blocks = [{"type": "text", "text": "test"}]
    measure_context(blocks, mock_client)

    mock_client.messages.count_tokens.assert_called_once_with(
        model=CLAUDE_MODEL,
        messages=[{"role": "user", "content": blocks}],
    )
    mock_client.models.retrieve.assert_called_once_with(CLAUDE_MODEL)
```

- [ ] **Step 2: Run tests to verify new ones fail**

```bash
python3 -m pytest tests/test_context.py -v
```

Expected: 8 pass (Task 3 tests), ~10 fail with `ImportError: cannot import name 'load_context_blocks'`

- [ ] **Step 3: Add `load_context_blocks` and `measure_context` to `pipeline/context.py`**

Append to `pipeline/context.py` (after `_walk_directory`):

```python
def load_context_blocks(files: list[Path]) -> list[dict]:
    """
    Convert a list of files to Anthropic content blocks.
    Each file gets a label block then a content block.
    Unsupported extensions are skipped with a warning.
    """
    if DocxDocument is None and any(f.suffix.lower() == ".docx" for f in files):
        raise ImportError("Install python-docx: pip install python-docx")

    blocks: list[dict] = []

    for file_path in files:
        ext = file_path.suffix.lower()

        if ext in TEXT_EXTENSIONS:
            blocks.append({"type": "text", "text": f"--- file: {file_path} ---"})
            blocks.append({"type": "text", "text": file_path.read_text(errors="replace")})

        elif ext in IMAGE_MEDIA_TYPES:
            data = base64.standard_b64encode(file_path.read_bytes()).decode()
            blocks.append({"type": "text", "text": f"--- file: {file_path} ---"})
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": IMAGE_MEDIA_TYPES[ext],
                    "data": data,
                },
            })

        elif ext == ".pdf":
            data = base64.standard_b64encode(file_path.read_bytes()).decode()
            blocks.append({"type": "text", "text": f"--- file: {file_path} ---"})
            blocks.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": data,
                },
            })

        elif ext == ".docx":
            doc = DocxDocument(str(file_path))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            blocks.append({"type": "text", "text": f"--- file: {file_path} ---"})
            blocks.append({"type": "text", "text": text})

        else:
            print(f"  Warning: skipping unsupported file type: {file_path}")

    return blocks


def measure_context(blocks: list[dict], client) -> tuple[int, int]:
    """
    Returns (token_count, context_window) using the Anthropic API.
    Both values are fetched live — nothing is hardcoded.
    """
    from config import CLAUDE_MODEL

    response = client.messages.count_tokens(
        model=CLAUDE_MODEL,
        messages=[{"role": "user", "content": blocks}],
    )
    token_count = response.input_tokens

    model_info = client.models.retrieve(CLAUDE_MODEL)
    context_window = model_info.context_window

    return token_count, context_window
```

- [ ] **Step 4: Run tests to verify all pass**

```bash
python3 -m pytest tests/test_context.py -v
```

Expected: 20 PASS (8 from Task 3 + 12 new)

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest -q
```

Expected: 76 passed (64 + 12 new — note: 8 from Task 3 already counted in 72, so 76 total)

Wait — after Task 3 you had 72. After Task 4 you add 12 more tests. Total: 84.

Run: `python3 -m pytest -q`
Expected: 84 passed

- [ ] **Step 6: Commit**

```bash
git add pipeline/context.py tests/test_context.py
git commit -m "feat: add load_context_blocks and measure_context to pipeline/context.py"
```

---

## Task 5: Update agents and `build_graph`

**Files:**
- Modify: `pipeline/agents/script_agent.py`
- Modify: `pipeline/agents/manim_agent.py`
- Modify: `pipeline/graph.py`
- Modify: `tests/test_script_agent.py`
- Modify: `tests/test_manim_agent.py`

- [ ] **Step 1: Add failing tests to `tests/test_script_agent.py`**

Append to `tests/test_script_agent.py`:

```python
def test_script_agent_with_context_blocks_sends_list_content(base_state):
    context_blocks = [
        {"type": "text", "text": "--- file: notes.txt ---"},
        {"type": "text", "text": "Important source material"},
    ]
    segments = [{"text": "Script.", "estimated_duration_sec": 1.0}]
    mock_response = _make_claude_response("Script.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        asyncio.run(script_agent(base_state, context_blocks=context_blocks))

    call_args = client_instance.messages.create.call_args
    content = call_args.kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    assert any("source material" in b.get("text", "") for b in content)
    assert any("Important source material" in b.get("text", "") for b in content)


def test_script_agent_without_context_blocks_sends_string_content(base_state):
    segments = [{"text": "Script.", "estimated_duration_sec": 1.0}]
    mock_response = _make_claude_response("Script.", segments)

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.script_agent import script_agent
        asyncio.run(script_agent(base_state))

    call_args = client_instance.messages.create.call_args
    content = call_args.kwargs["messages"][0]["content"]
    assert isinstance(content, str)
```

- [ ] **Step 2: Add failing tests to `tests/test_manim_agent.py`**

Read `tests/test_manim_agent.py` first to understand its helper functions. Then append:

```python
def test_manim_agent_with_context_blocks_sends_list_content(base_state):
    import asyncio
    base_state["script"] = "Script about trees."
    base_state["script_segments"] = [{"text": "Trees.", "estimated_duration_sec": 2.0}]
    context_blocks = [
        {"type": "text", "text": "--- file: diagram.py ---"},
        {"type": "text", "text": "class Tree: pass"},
    ]
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"manim_code": "from manim import *"}')]

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.manim_agent import manim_agent
        asyncio.run(manim_agent(base_state, context_blocks=context_blocks))

    call_args = client_instance.messages.create.call_args
    content = call_args.kwargs["messages"][0]["content"]
    assert isinstance(content, list)
    assert any("source material" in b.get("text", "") for b in content)
    assert any("class Tree" in b.get("text", "") for b in content)


def test_manim_agent_without_context_blocks_sends_string_content(base_state):
    import asyncio
    base_state["script"] = "Script."
    base_state["script_segments"] = [{"text": "S.", "estimated_duration_sec": 1.0}]
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='{"manim_code": "from manim import *"}')]

    with patch("pipeline.agents.manim_agent.anthropic.Anthropic") as MockClient:
        client_instance = MockClient.return_value
        client_instance.messages.create.return_value = mock_response
        from pipeline.agents.manim_agent import manim_agent
        asyncio.run(manim_agent(base_state))

    call_args = client_instance.messages.create.call_args
    content = call_args.kwargs["messages"][0]["content"]
    assert isinstance(content, str)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_script_agent.py tests/test_manim_agent.py -v 2>&1 | tail -20
```

Expected: the 4 new tests fail with `TypeError` (unexpected keyword argument `context_blocks`)

- [ ] **Step 4: Update `pipeline/agents/script_agent.py`**

Change the `script_agent` function signature and add context block handling. Replace the function (keep everything above it unchanged):

```python
async def script_agent(state: PipelineState, client=None, context_blocks=None) -> dict:
    if client is None:
        has_pdf = context_blocks and any(b.get("type") == "document" for b in context_blocks)
        kwargs = {"default_headers": {"anthropic-beta": "pdfs-2024-09-25"}} if has_pdf else {}
        client = anthropic.Anthropic(**kwargs)

    tools = []
    if state.get("user_approved_search") or state["effort_level"] == "high":
        tools = [{"type": "web_search_20250305", "name": "web_search"}]

    if context_blocks:
        content = [
            {
                "type": "text",
                "text": "The following files are provided as source material. Use them to inform the script content, facts, and framing:",
            }
        ]
        content.extend(context_blocks)
        content.append({"type": "text", "text": _build_user_message(state)})
    else:
        content = _build_user_message(state)

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            tools=tools if tools else anthropic.NOT_GIVEN,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "script": {"type": "string"},
                            "segments": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "estimated_duration_sec": {"type": "number"},
                                    },
                                    "required": ["text", "estimated_duration_sec"],
                                    "additionalProperties": False,
                                },
                            },
                            "needs_web_search": {"type": "boolean"},
                        },
                        "required": ["script", "segments", "needs_web_search"],
                        "additionalProperties": False,
                    },
                }
            },
        )

    response = await api_call_with_retry(_call, timeout=TIMEOUT_SCRIPT_AGENT, label="script_agent")

    data = json.loads(response.content[0].text)
    return {
        "script": data["script"],
        "script_segments": data["segments"],
        "needs_web_search": data.get("needs_web_search", False),
        "status": "validating",
    }
```

- [ ] **Step 5: Update `pipeline/agents/manim_agent.py`**

Change the `manim_agent` function signature. Replace the function (keep everything above it unchanged):

```python
async def manim_agent(state: PipelineState, client=None, context_blocks=None) -> dict:
    if client is None:
        has_pdf = context_blocks and any(b.get("type") == "document" for b in context_blocks)
        kwargs = {"default_headers": {"anthropic-beta": "pdfs-2024-09-25"}} if has_pdf else {}
        client = anthropic.Anthropic(**kwargs)

    user_msg = (
        f"Create a Manim animation for this educational script.\n\n"
        f"Topic: {state['topic']}\n\n"
        f"Narration segments with timings:\n{_format_segments(state['script_segments'])}\n\n"
        f"Full script for context:\n{state['script']}\n\n"
        f"{THEME_SPECS[state.get('theme', 'chalkboard')]}"
    )

    if state.get("code_feedback"):
        user_msg += f"\n\nPrevious attempt had issues. Rewrite the scene fully, addressing:\n{state['code_feedback']}"

    if context_blocks:
        content = [
            {
                "type": "text",
                "text": "The following files are provided as source material. Use them to inform what the animation should visualize:",
            }
        ]
        content.extend(context_blocks)
        content.append({"type": "text", "text": user_msg})
    else:
        content = user_msg

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16384,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {"manim_code": {"type": "string"}},
                        "required": ["manim_code"],
                        "additionalProperties": False,
                    },
                }
            },
        )

    response = await api_call_with_retry(_call, timeout=TIMEOUT_MANIM_AGENT, label="manim_agent")

    data = json.loads(response.content[0].text)
    return {"manim_code": data["manim_code"], "status": "validating"}
```

- [ ] **Step 6: Update `build_graph` in `pipeline/graph.py`**

Replace the `build_graph` function:

```python
def build_graph(checkpointer=None, context_blocks=None) -> StateGraph:
    if context_blocks:
        async def _script_agent(state):
            return await script_agent(state, context_blocks=context_blocks)

        async def _manim_agent(state):
            return await manim_agent(state, context_blocks=context_blocks)
    else:
        _script_agent = script_agent
        _manim_agent = manim_agent

    builder = StateGraph(PipelineState)

    builder.add_node("init", _init_state)
    builder.add_node("script_agent", _script_agent)
    builder.add_node("fact_validator", fact_validator)
    builder.add_node("manim_agent", _manim_agent)
    builder.add_node("code_validator", code_validator)
    builder.add_node("escalate_to_user", escalate_to_user)
    builder.add_node("render_trigger", render_trigger)

    builder.add_edge(START, "init")
    builder.add_edge("init", "script_agent")
    builder.add_edge("script_agent", "fact_validator")
    builder.add_conditional_edges("fact_validator", _after_fact_validator,
        ["script_agent", "manim_agent", "escalate_to_user"])
    builder.add_edge("manim_agent", "code_validator")
    builder.add_conditional_edges("code_validator", _after_code_validator,
        ["manim_agent", "render_trigger", "escalate_to_user"])
    builder.add_edge("render_trigger", END)
    builder.add_conditional_edges("escalate_to_user", _after_escalate,
        ["script_agent", "manim_agent", END])

    return builder.compile(checkpointer=checkpointer)
```

- [ ] **Step 7: Run agent + graph tests to verify they pass**

```bash
python3 -m pytest tests/test_script_agent.py tests/test_manim_agent.py tests/test_graph.py -v
```

Expected: all pass

- [ ] **Step 8: Run full test suite**

```bash
python3 -m pytest -q
```

Expected: 88 passed (84 + 4 new)

- [ ] **Step 9: Commit**

```bash
git add pipeline/agents/script_agent.py pipeline/agents/manim_agent.py \
        pipeline/graph.py \
        tests/test_script_agent.py tests/test_manim_agent.py
git commit -m "feat: add context_blocks support to agents and build_graph"
```

---

## Task 6: Update `main.py` with CLI flags and token reporting

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add import for `pipeline/context.py` to `main.py`**

Add to the imports block (after the `from pipeline.retry import TimeoutExhausted` line):

```python
from pipeline.context import collect_files, load_context_blocks, measure_context
```

- [ ] **Step 2: Add `_report_context` function to `main.py`**

Add this function after `_check_tools()`:

```python
def _report_context(blocks: list[dict]) -> bool:
    """
    Print context token report. Returns True if pipeline should proceed, False to abort.
    Always prints the report. Prompts for confirmation only when tokens > 10k.
    """
    import anthropic as _anthropic
    try:
        client = _anthropic.Anthropic()
        token_count, context_window = measure_context(blocks, client)
        pct = int(token_count / context_window * 100)
        n_files = sum(
            1 for b in blocks
            if b.get("type") == "text" and b.get("text", "").startswith("--- file:")
        )
        print(
            f"\nContext: {n_files} file{'s' if n_files != 1 else ''}, "
            f"~{token_count // 1000}k tokens  "
            f"(model window: {context_window // 1000}k, ~{pct}% used by context)"
        )
        if pct >= 90:
            raise SystemExit(
                f"Error: context files use {pct}% of the model context window. "
                "Reduce files before proceeding."
            )
        if token_count > 10_000:
            answer = input("\nContext is large. Proceed? (y/n): ").strip().lower()
            return answer == "y"
    except SystemExit:
        raise
    except Exception as e:
        print(f"  Warning: could not measure context tokens ({e}) — proceeding without report.")
    return True
```

- [ ] **Step 3: Update `run()` signature and body in `main.py`**

Replace the `run()` function signature and the two lines inside that reference `build_graph` and `input_state`:

```python
async def run(topic: str, effort: str, thread_id: str, audience: str = "intermediate",
              tone: str = "casual", theme: str = "chalkboard",
              context_blocks=None, context_file_paths=None) -> None:
    print(f"\nChalkboard — topic: {topic!r} | effort: {effort} | run: {thread_id}\n")

    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
        graph = build_graph(checkpointer=checkpointer, context_blocks=context_blocks)
        config = {"configurable": {"thread_id": thread_id}}
        input_state = {
            "topic": topic,
            "effort_level": effort,
            "audience": audience,
            "tone": tone,
            "theme": theme,
            "context_file_paths": context_file_paths or [],
        }

        while True:
            try:
                async for event in graph.astream(input_state, config=config, stream_mode="updates"):
                    _print_progress(event)

                    if "__interrupt__" in event:
                        interrupt_value = event["__interrupt__"][0].value
                        resume_cmd = _handle_interrupt(interrupt_value)
                        input_state = resume_cmd
                        break
                else:
                    break
            except TimeoutExhausted as e:
                print(f"\n  [pipeline] {e}")
                resume_cmd = _handle_interrupt(str(e))
                input_state = resume_cmd
```

- [ ] **Step 4: Add `--context` and `--context-ignore` flags and context loading to `main()`**

Add the two new argparse arguments after the existing `--preview` argument:

```python
    parser.add_argument(
        "--context", action="append", dest="context", default=[], metavar="PATH",
        help="File or directory to include as context. Repeatable.",
    )
    parser.add_argument(
        "--context-ignore", action="append", dest="context_ignore", default=[], metavar="PATTERN",
        help="Glob pattern to exclude from context directories. Repeatable.",
    )
```

Replace the `thread_id = ...` and `asyncio.run(run(...))` lines with:

```python
    thread_id = args.run_id or str(uuid.uuid4())

    context_blocks = None
    context_file_paths: list[str] = []
    if args.context:
        files = collect_files(args.context, ignore_patterns=args.context_ignore or None)
        context_blocks = load_context_blocks(files)
        context_file_paths = [str(f) for f in files]
        if not _report_context(context_blocks):
            raise SystemExit("Aborted.")
    elif args.run_id:
        print("Note: resuming without context files. Pass --context to include source material.")

    asyncio.run(run(
        args.topic, args.effort, thread_id,
        audience=args.audience, tone=args.tone, theme=args.theme,
        context_blocks=context_blocks, context_file_paths=context_file_paths,
    ))
```

- [ ] **Step 5: Run full test suite**

```bash
python3 -m pytest -q
```

Expected: 88 passed (no new tests for CLI — covered by existing integration)

- [ ] **Step 6: Manual smoke test (no API calls)**

```bash
# Verify --help shows new flags
python3 main.py --help | grep -E "context"
```

Expected output includes:
```
--context PATH        File or directory to include as context. Repeatable.
--context-ignore PATTERN
                      Glob pattern to exclude from context directories. Repeatable.
```

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "feat: add --context and --context-ignore CLI flags with token reporting"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|-----------------|------|
| `--context PATH` repeatable flag | Task 6 ✓ |
| `--context-ignore PATTERN` repeatable flag | Task 6 ✓ |
| Recursive directory traversal | Task 3 ✓ |
| Respects `.gitignore` via `pathspec` | Task 3 ✓ |
| Extra ignore patterns | Task 3 ✓ |
| Hidden directories skipped | Task 3 ✓ |
| Missing path → hard error | Task 3 ✓ |
| Empty directory → warning | Task 3 ✓ |
| Text/code files → text block | Task 4 ✓ |
| Images → base64 image block | Task 4 ✓ |
| PDFs → base64 document block | Task 4 ✓ |
| `.docx` → python-docx text extraction | Task 4 ✓ |
| Unsupported extension → skip with warning | Task 4 ✓ |
| File label blocks (`--- file: path ---`) | Task 4 ✓ |
| `measure_context` returns `(tokens, window)` from API | Task 4 ✓ |
| Always print token report | Task 6 ✓ |
| Prompt if tokens > 10k | Task 6 ✓ |
| Hard error if context > 90% of window | Task 6 ✓ |
| Token count failure → warning, proceed | Task 6 ✓ |
| `context_file_paths` in PipelineState | Task 1 ✓ |
| `script_agent` accepts `context_blocks` | Task 5 ✓ |
| `manim_agent` accepts `context_blocks` | Task 5 ✓ |
| PDF beta header on Anthropic client | Task 5 ✓ |
| `build_graph` wraps agents with closure when context present | Task 5 ✓ |
| Resume without `--context` prints note | Task 6 ✓ |
| `python-docx` + `pathspec` in requirements.txt | Task 2 ✓ |

**Placeholder scan:** None found. All steps contain complete code.

**Type consistency:**
- `collect_files` returns `list[Path]` — used as-is in `load_context_blocks(files: list[Path])` ✓
- `load_context_blocks` returns `list[dict]` — passed as `context_blocks` throughout ✓
- `measure_context` returns `tuple[int, int]` — destructured as `(token_count, context_window)` ✓
- `build_graph(context_blocks=None)` matches agent wrapper closures that call `script_agent(state, context_blocks=context_blocks)` ✓
- `run(..., context_blocks=None, context_file_paths=None)` matches `asyncio.run(run(..., context_blocks=context_blocks, context_file_paths=context_file_paths))` in `main()` ✓
