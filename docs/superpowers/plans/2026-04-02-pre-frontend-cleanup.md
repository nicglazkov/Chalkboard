# Pre-Frontend Backend Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pipeline usable in non-interactive/API contexts by replacing hard-coded `print()` progress reporting with a structured callback and removing the blocking `stdin` prompt from `escalate_to_user`.

**Architecture:** Add `on_progress: Callable | None` to `run()` (defaults to the existing `_print_progress`). Add `interactive: bool = True` to `PipelineState` and `run()`; when `False`, `escalate_to_user` skips the stdin prompt and sets `status="failed"` immediately. The same flag gates the `TimeoutExhausted` prompt in `run()`. CLI behaviour is unchanged — both default to `True`.

**Tech Stack:** Python 3.10+, asyncio, pytest

---

## Files

- **Modify:** `pipeline/state.py` — add `interactive: bool`
- **Modify:** `pipeline/graph.py` — pass `interactive` through `_init_state`
- **Modify:** `pipeline/agents/orchestrator.py` — skip stdin when `interactive=False`
- **Modify:** `main.py` — add `on_progress` callback parameter to `run()`; gate TimeoutExhausted prompt
- **Modify:** `tests/conftest.py` — add `interactive=True` to base_state
- **Modify:** `tests/test_orchestrator.py` — add non-interactive test
- **Modify:** `tests/test_graph.py` — test non-interactive escalation path

---

### Task 1: Add `interactive` field to state

**Files:**
- Modify: `pipeline/state.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state.py — add:
def test_interactive_field_in_pipeline_state():
    from typing import get_type_hints
    hints = get_type_hints(PipelineState)
    assert "interactive" in hints
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_state.py::test_interactive_field_in_pipeline_state -v
```

- [ ] **Step 3: Add field to PipelineState**

In `pipeline/state.py`, add after `research_sources: list[str]`:

```python
    interactive: bool
```

- [ ] **Step 4: Update conftest.py**

In `tests/conftest.py`, add to `base_state` (after `research_sources=[]`):

```python
        interactive=True,
```

- [ ] **Step 5: Add to _init_state in graph.py**

In `pipeline/graph.py`, `_init_state`, add:

```python
        "interactive": state.get("interactive", True),
```

- [ ] **Step 6: Run full suite**

```
pytest --tb=short -q
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add pipeline/state.py pipeline/graph.py tests/conftest.py
git commit -m "feat: add interactive field to PipelineState for non-interactive API mode"
```

---

### Task 2: Non-interactive escalation in orchestrator

**Files:**
- Modify: `pipeline/agents/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_orchestrator.py`:

```python
def test_escalate_non_interactive_returns_failed_immediately():
    """When interactive=False, escalate_to_user must not read stdin and must return failed."""
    import asyncio
    from pipeline.agents.orchestrator import escalate_to_user

    state = {
        "topic": "test",
        "script_attempts": 3,
        "code_attempts": 0,
        "fact_feedback": "Wrong claim.",
        "code_feedback": None,
        "interactive": False,
    }

    # If stdin were read, asyncio.to_thread(input, ...) would block forever.
    # This must return immediately.
    result = asyncio.run(escalate_to_user(state))
    assert result["status"] == "failed"
```

- [ ] **Step 2: Run to verify it fails (or hangs — kill with Ctrl+C if so)**

```
pytest tests/test_orchestrator.py::test_escalate_non_interactive_returns_failed_immediately -v --timeout=5
```
Expected: FAIL or hang (stdin blocking).

- [ ] **Step 3: Update orchestrator.py**

Replace `escalate_to_user` in `pipeline/agents/orchestrator.py`:

```python
async def escalate_to_user(state: PipelineState) -> dict:
    message = _build_escalation_message(state)
    print(message)

    if not state.get("interactive", True):
        print("  [non-interactive mode — auto-aborting]")
        return {"status": "failed"}

    print("\nEnter action (retry_script / retry_code / abort):")
    try:
        action = (await asyncio.to_thread(input, "  action: ")).strip()
        guidance = ""
        if action in ("retry_script", "retry_code"):
            guidance = (await asyncio.to_thread(input, "  guidance: ")).strip()
    except EOFError:
        print("  [non-interactive stdin — defaulting to abort]")
        action = "abort"
        guidance = ""

    if action == "retry_script":
        return {
            "script_attempts": 0,
            "fact_feedback": guidance,
            "code_feedback": None,
            "status": "drafting",
        }
    elif action == "retry_code":
        return {
            "code_attempts": 0,
            "code_feedback": guidance,
            "fact_feedback": None,
            "status": "validating",
        }
    else:
        return {"status": "failed"}
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_orchestrator.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/agents/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: skip stdin in escalate_to_user when interactive=False"
```

---

### Task 3: Add `on_progress` callback to `run()`

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_callback.py`:

```python
# tests/test_run_callback.py
import asyncio
import pytest
from unittest.mock import patch, MagicMock
from main import run


def _make_script_state():
    return {
        "script": "Test.",
        "script_segments": [{"text": "Test.", "estimated_duration_sec": 1.0}],
        "needs_web_search": False,
        "status": "validating",
    }


def test_on_progress_callback_receives_events(tmp_path):
    """on_progress must be called for each pipeline event instead of print."""
    events_received = []

    def capture_progress(event: dict):
        events_received.append(event)

    async def mock_script_agent(state, **kw):
        return _make_script_state()

    async def mock_fact_validator(state, **kw):
        return {"fact_feedback": None, "status": "validating"}

    async def mock_manim_agent(state, **kw):
        code = "from manim import *\nclass ChalkboardScene(Scene):\n    def construct(self): self.wait(1.0)"
        return {"manim_code": code, "status": "validating"}

    async def mock_code_validator(state, **kw):
        return {"code_feedback": None, "code_attempts": 0}

    async def mock_tts(segments, path, speed=1.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [1.0]

    with patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        asyncio.run(run(
            topic="test",
            effort="low",
            thread_id="test-callback",
            on_progress=capture_progress,
        ))

    assert len(events_received) > 0
    # At minimum, script_agent and render_trigger events should be present
    node_names = [list(e.keys())[0] for e in events_received if e and "__end__" not in e]
    assert "script_agent" in node_names
```

- [ ] **Step 2: Run to verify it fails**

```
pytest tests/test_run_callback.py -v
```
Expected: FAIL — `run()` doesn't accept `on_progress` parameter.

- [ ] **Step 3: Update run() in main.py**

Update the `run()` signature and body. Find the function (currently around line 522):

```python
async def run(
    topic: str,
    effort: str,
    thread_id: str,
    audience: str = "intermediate",
    tone: str = "casual",
    theme: str = "chalkboard",
    context_blocks=None,
    context_file_paths=None,
    speed: float = 1.0,
    template: str | None = None,
    on_progress: "Callable[[dict], None] | None" = None,  # NEW
    interactive: bool = True,                              # NEW
) -> None:
```

In `input_state`, add `"interactive": interactive`.

Replace the `async for event` loop:

```python
        while True:
            try:
                async for event in graph.astream(input_state, config=config, stream_mode="updates"):
                    if on_progress is not None:
                        on_progress(event)
                    else:
                        _print_progress(event)
                break
            except TimeoutExhausted as e:
                print(f"\n  [pipeline] {e}")
                if not interactive:
                    return
                print("\nEnter action (retry / abort):")
                action = (await asyncio.to_thread(input, "  action: ")).strip()
                if action != "retry":
                    return
                input_state = None  # resume from last checkpoint
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_run_callback.py -v
```

- [ ] **Step 5: Run full suite**

```
pytest --tb=short -q
```
Expected: all tests pass. CLI behaviour is unchanged.

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_run_callback.py
git commit -m "feat: add on_progress callback and interactive flag to run() for API use"
```
