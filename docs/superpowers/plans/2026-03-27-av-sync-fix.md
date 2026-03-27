# A/V Sync Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate audio/video desync by making generated Manim scenes load actual TTS durations from `segments.json` at render time instead of using hardcoded estimates.

**Architecture:** `manim_agent`'s system prompt is updated to require scenes load durations from `segments.json` at the start of `construct()`. `_format_segments` is updated to emit 0-based segment indices and a total count header so Claude has an unambiguous integer for the padding constant. `code_validator`'s prompt is extended to reject any scene that uses hardcoded float literals in `self.wait()` calls.

**Tech Stack:** Python, Anthropic API (claude-sonnet-4-6), pytest, unittest.mock

---

## File Map

| File | Change |
|------|--------|
| `pipeline/agents/manim_agent.py` | Update `SYSTEM_PROMPT` (imports constraint, new loading requirement, new pitfalls) and rewrite `_format_segments` (0-based, total count header) |
| `pipeline/agents/code_validator.py` | Append sync check to `user_msg` |
| `tests/test_manim_agent.py` | Update `VALID_SCENE` fixture to use `_d[i]` pattern |
| `tests/test_code_validator.py` | Update `VALID_CODE` fixture; add `test_code_validator_rejects_hardcoded_wait` |

---

### Task 1: Update VALID_SCENE fixture in test_manim_agent.py

**Files:**
- Modify: `tests/test_manim_agent.py`

Context: `VALID_SCENE` is used as the mock return value from Claude in all `test_manim_agent_*` tests. It currently has `self.wait(2.0)` — a hardcoded float. After Task 3 updates the code validator prompt, a real Claude call would reject this. Updating the fixture now ensures the test suite documents the correct expected output shape.

- [ ] **Step 1: Open and read the current fixture**

`tests/test_manim_agent.py` lines 14–22. Current `VALID_SCENE`:

```python
VALID_SCENE = '''
from manim import *

class ChalkboardScene(Scene):
    def construct(self):
        title = Text("B-Trees")
        self.play(Write(title))
        self.wait(2.0)
'''
```

- [ ] **Step 2: Replace VALID_SCENE with the dynamic loading pattern**

Replace the entire `VALID_SCENE` constant (lines 14–22) with:

```python
VALID_SCENE = '''
from manim import *
import json
from pathlib import Path

class ChalkboardScene(Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 1 - len(_d))
        title = Text("B-Trees")
        self.play(Write(title), run_time=1.0)
        self.wait(max(0.0, _d[0] - 1.0))
'''
```

Note: `1` in `max(0, 1 - len(_d))` is the segment count for this one-segment fixture — not a float duration.

- [ ] **Step 3: Run the manim agent tests to confirm they still pass**

```bash
cd /Users/nic/Documents/code/Chalkboard && pytest tests/test_manim_agent.py -v
```

Expected output (all 3 tests pass):
```
tests/test_manim_agent.py::test_manim_agent_generates_chalkboard_scene PASSED
tests/test_manim_agent.py::test_manim_agent_includes_durations_in_prompt PASSED
tests/test_manim_agent.py::test_manim_agent_includes_feedback_on_revision PASSED
```

---

### Task 2: Update VALID_CODE fixture and add rejection test in test_code_validator.py

**Files:**
- Modify: `tests/test_code_validator.py`

Context: `VALID_CODE` currently has `self.wait(1.0)` — a hardcoded float. Update it to use `_d[i]`. Then add a new test that exercises the rejection path when Claude says a scene uses hardcoded floats.

Note on test design: `code_validator` mocks Claude's response. The rejection test mocks Claude returning `"needs_revision"` for a BAD_CODE scene that has `self.wait(2.5)`. This confirms the validator correctly surfaces Claude's feedback. The real prompt change (Task 4) is what teaches Claude to actually detect this pattern in production.

- [ ] **Step 1: Replace VALID_CODE (lines 8–14)**

Replace:
```python
VALID_CODE = """
from manim import *
class ChalkboardScene(Scene):
    def construct(self):
        self.play(Write(Text("Hello")))
        self.wait(1.0)
"""
```

With:
```python
VALID_CODE = """
from manim import *
import json
from pathlib import Path

class ChalkboardScene(Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 1 - len(_d))
        self.play(Write(Text("Hello")))
        self.wait(_d[0])
"""
```

- [ ] **Step 2: Add rejection test at the end of the file**

Append after `test_code_validator_increments_attempts_on_semantic_fail`:

```python
def test_code_validator_rejects_hardcoded_wait(base_state):
    BAD_CODE = """
from manim import *
class ChalkboardScene(Scene):
    def construct(self):
        t = Text("hello")
        self.play(Write(t), run_time=1.0)
        self.wait(2.5)
"""
    base_state["manim_code"] = BAD_CODE
    base_state["script"] = "Hello world."
    mock_resp = _mock_response("needs_revision", "self.wait uses hardcoded float")

    with patch("pipeline.agents.code_validator.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = mock_resp
        result = code_validator(base_state)

    assert result["code_feedback"] == "self.wait uses hardcoded float"
    assert result["code_attempts"] == 1
```

- [ ] **Step 3: Run code validator tests to confirm they all pass**

```bash
cd /Users/nic/Documents/code/Chalkboard && pytest tests/test_code_validator.py -v
```

Expected output (all 4 tests pass):
```
tests/test_code_validator.py::test_code_validator_passes_valid_code PASSED
tests/test_code_validator.py::test_code_validator_fails_on_syntax_error_without_claude_call PASSED
tests/test_code_validator.py::test_code_validator_increments_attempts_on_semantic_fail PASSED
tests/test_code_validator.py::test_code_validator_rejects_hardcoded_wait PASSED
```

---

### Task 3: Update manim_agent.py SYSTEM_PROMPT and _format_segments

**Files:**
- Modify: `pipeline/agents/manim_agent.py`

This is the core change. Two things:
1. Rewrite `_format_segments` to be 0-based and emit a total count header
2. Update `SYSTEM_PROMPT` with the new imports constraint, dynamic loading requirement, and new pitfall bullets

- [ ] **Step 1: Replace _format_segments**

Current (lines starting around `def _format_segments`):
```python
def _format_segments(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        duration = seg.get("estimated_duration_sec", 0.0)
        text = seg.get("text", "")
        lines.append(f"  Segment {i} ({duration:.1f}s): {text}")
    return "\n".join(lines)
```

Replace with:
```python
def _format_segments(segments: list[dict]) -> str:
    n = len(segments)
    header = f"Total segments: {n} (use _d[0] through _d[{max(0, n-1)}])"
    lines = [header]
    for i, seg in enumerate(segments):  # 0-based
        duration = seg.get("estimated_duration_sec", 0.0)
        text = seg.get("text", "")
        lines.append(f"  Segment {i} — est. {duration:.1f}s — use _d[{i}] at runtime: {text}")
    return "\n".join(lines)
```

- [ ] **Step 2: Update SYSTEM_PROMPT — imports constraint and new STRICT REQUIREMENTS bullets**

In `SYSTEM_PROMPT`, find:
```
- Use `from manim import *` as the only import
```

Replace with:
```
- Use `from manim import *` plus stdlib imports as needed (json, pathlib, etc.)
```

Then find the end of the STRICT REQUIREMENTS block (after the `run_time` bullet):
```
- The code must be syntactically valid Python
```

Append after it (keep the existing bullet, add these new ones):
```
- At the start of construct(), load actual segment durations:
    _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
    _d = [s["actual_duration_sec"] for s in _seg_data]
    _d = _d + [2.0] * max(0, N - len(_d))
  Replace N with the exact integer from "Total segments: N" shown above.
- Never hardcode a float literal as the argument to self.wait() — always use _d[i]
- When an animation fills part of a segment's time, subtract the animation's run_time from _d[i]:
    self.wait(max(0.0, _d[i] - X))  where X is the numeric value passed to run_time= above
```

- [ ] **Step 3: Update SYSTEM_PROMPT — new KNOWN API PITFALLS bullets**

Find the end of the KNOWN API PITFALLS block (after the `VGroup(*self.mobjects)` bullet):
```
- Never use VGroup(*self.mobjects) — self.mobjects can contain non-VMobjects; use *[FadeOut(m) for m in self.mobjects] instead
```

Append after it:
```
- Never hardcode self.wait(X) with a float literal — always use _d[i] loaded from segments.json
- Use max(0.0, _d[i] - X) where X is the run_time= value passed to self.play() in that segment
- Pad _d with: _d = _d + [2.0] * max(0, N - len(_d)) where N is the literal integer segment count
- Segment numbers and _d indices are both 0-based — Segment 0 → _d[0], Segment 1 → _d[1]
```

- [ ] **Step 4: Verify the full updated SYSTEM_PROMPT looks correct**

The complete updated `SYSTEM_PROMPT` should read:

```python
SYSTEM_PROMPT = """You are an expert Manim Community Edition (v0.20.1) developer.
Generate a complete, runnable Manim scene for an educational animation.

STRICT REQUIREMENTS:
- The scene class MUST be named exactly `ChalkboardScene` (inherits from Scene)
- Use `from manim import *` plus stdlib imports as needed (json, pathlib, etc.)
- Each narration segment gets an animation block followed by self.wait(duration_sec)
- Use self.play(..., run_time=X) for animations
- The code must be syntactically valid Python
- At the start of construct(), load actual segment durations:
    _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
    _d = [s["actual_duration_sec"] for s in _seg_data]
    _d = _d + [2.0] * max(0, N - len(_d))
  Replace N with the exact integer from "Total segments: N" shown above.
- Never hardcode a float literal as the argument to self.wait() — always use _d[i]
- When an animation fills part of a segment's time, subtract the animation's run_time from _d[i]:
    self.wait(max(0.0, _d[i] - X))  where X is the numeric value passed to run_time= above

KNOWN API PITFALLS (v0.20.1):
- Brace.get_text(*text) does NOT accept font_size — set it on the returned object: t = brace.get_text('x'); t.scale(0.8)
- VGroup.arrange() returns None — assign before arranging, don't chain
- Always pass run_time as a keyword arg: self.play(anim, run_time=1.0)
- Never use VGroup(*self.mobjects) — self.mobjects can contain non-VMobjects; use *[FadeOut(m) for m in self.mobjects] instead
- Never hardcode self.wait(X) with a float literal — always use _d[i] loaded from segments.json
- Use max(0.0, _d[i] - X) where X is the run_time= value passed to self.play() in that segment
- Pad _d with: _d = _d + [2.0] * max(0, N - len(_d)) where N is the literal integer segment count
- Segment numbers and _d indices are both 0-based — Segment 0 → _d[0], Segment 1 → _d[1]

Respond with JSON only: {"manim_code": "<complete Python code as string>"}"""
```

- [ ] **Step 5: Run manim agent tests**

```bash
cd /Users/nic/Documents/code/Chalkboard && pytest tests/test_manim_agent.py -v
```

Expected: all 3 tests pass. The `test_manim_agent_includes_durations_in_prompt` test asserts `"1.5" in content` and `"2.3" in content` — both still appear in the new format as `est. 1.5s` and `est. 2.3s`.

---

### Task 4: Append sync check to code_validator.py user_msg

**Files:**
- Modify: `pipeline/agents/code_validator.py`

- [ ] **Step 1: Locate user_msg in code_validator**

In `code_validator()`, find the `user_msg` string (around line 35–41):

```python
user_msg = (
    f"Review this Manim CE code for correctness and coherence with the script.\n\n"
    f"Script:\n{state['script']}\n\n"
    f"Manim code:\n{code}\n\n"
    f"Check: Does the animation visualize the script? Are Manim CE v0.20 APIs used correctly? "
    f"Is the class named ChalkboardScene?"
)
```

- [ ] **Step 2: Append sync check sentence**

Replace the `user_msg` assignment with:

```python
user_msg = (
    f"Review this Manim CE code for correctness and coherence with the script.\n\n"
    f"Script:\n{state['script']}\n\n"
    f"Manim code:\n{code}\n\n"
    f"Check: Does the animation visualize the script? Are Manim CE v0.20 APIs used correctly? "
    f"Is the class named ChalkboardScene?\n\n"
    f"Sync check: The scene must load _seg_data from (Path(__file__).parent / \"segments.json\") "
    f"and use _d[i] (not hardcoded float literals) for all self.wait() calls. "
    f"If any self.wait() call uses a hardcoded float literal, return needs_revision."
)
```

- [ ] **Step 3: Run all tests**

```bash
cd /Users/nic/Documents/code/Chalkboard && pytest -v
```

Expected output (all tests pass):
```
tests/test_code_validator.py::test_code_validator_passes_valid_code PASSED
tests/test_code_validator.py::test_code_validator_fails_on_syntax_error_without_claude_call PASSED
tests/test_code_validator.py::test_code_validator_increments_attempts_on_semantic_fail PASSED
tests/test_code_validator.py::test_code_validator_rejects_hardcoded_wait PASSED
tests/test_manim_agent.py::test_manim_agent_generates_chalkboard_scene PASSED
tests/test_manim_agent.py::test_manim_agent_includes_durations_in_prompt PASSED
tests/test_manim_agent.py::test_manim_agent_includes_feedback_on_revision PASSED
... (all other tests pass)
```

---

### Task 5: Commit

- [ ] **Step 1: Stage changed files**

```bash
cd /Users/nic/Documents/code/Chalkboard && git add pipeline/agents/manim_agent.py pipeline/agents/code_validator.py tests/test_manim_agent.py tests/test_code_validator.py
```

- [ ] **Step 2: Commit**

```bash
git commit -m "$(cat <<'EOF'
fix: sync Manim animation timing to actual TTS durations

Generated scenes now load actual per-segment durations from
segments.json at render time instead of using hardcoded estimates.
code_validator now rejects scenes with hardcoded self.wait() floats.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Verify clean state**

```bash
cd /Users/nic/Documents/code/Chalkboard && git status
```

Expected: `nothing to commit, working tree clean`
