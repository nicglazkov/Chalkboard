# A/V Sync Fix — Implementation Spec

## Goal

Eliminate audio/video desync by making the Manim scene load actual TTS durations from `segments.json` at render time instead of using hardcoded estimates.

## Background

`script_agent` estimates segment durations at 150 wpm. `manim_agent` bakes those estimates as hardcoded `self.wait(X)` floats. TTS runs after the animation code is finalized — actual durations land in `segments.json` but are never used. Each segment drifts independently and errors accumulate.

`render_trigger` already writes `segments.json` with actual per-segment durations. The fix is to have `scene.py` read that file instead of using hardcoded values.

## Architecture

No pipeline changes. The fix is contained in two source files and two test files:

- `pipeline/agents/manim_agent.py` — prompt update, pitfall entries, `_format_segments` change
- `pipeline/agents/code_validator.py` — prompt update to check for the pattern
- `tests/test_manim_agent.py` — update `VALID_SCENE` fixture
- `tests/test_code_validator.py` — update `VALID_CODE` fixture; add rejection test

### Data flow (unchanged)

`render_trigger` runs synchronously and writes all files before returning. Docker is only invoked after the LangGraph pipeline completes and `main.py` calls `_render()`. Both files are fully on disk before Docker ever runs — there is no ordering race.

Actual write order inside `render_trigger`:
1. `scene.py` written
2. `segments.json` written
3. `script.txt` written
4. `manifest.json` written

Then later, after pipeline ends:
```
main.py → _render() → Docker → scene.py reads segments.json → exact durations used
```

## segments.json format (already written by render_trigger)

```json
[
  {"text": "B-trees are balanced...", "actual_duration_sec": 4.2},
  {"text": "Each node can hold...", "actual_duration_sec": 5.8}
]
```

## Changes to manim_agent.py

### 1. Remove "only import" constraint

The existing STRICT REQUIREMENTS line:
> `"Use 'from manim import *' as the only import"`

Replace with:
> `"Use 'from manim import *' plus stdlib imports as needed (json, pathlib, etc.)"`

### 2. Update `_format_segments`

Change to 0-based indexing and add a total count header so Claude has an unambiguous integer for the padding constant:

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

Key changes vs current:
- Loop is **0-based** (`enumerate(segments)` not `enumerate(segments, 1)`) so segment number and `_d` index always match — `Segment 0 → _d[0]`, `Segment 1 → _d[1]`, no off-by-one confusion for Claude
- Header line emits the exact total count `n` as a literal integer so Claude can substitute it directly in the padding line
- `max(0, n-1)` in the header guards against an empty segment list (which should not happen in practice but avoids a misleading `_d[-1]` in the prompt)

**`test_manim_agent_includes_durations_in_prompt` needs no changes.** It asserts `"1.5" in content` and `"2.3" in content`; those floats still appear in the new format as `est. 1.5s` and `est. 2.3s`.

### 3. Add dynamic loading requirement to STRICT REQUIREMENTS

Append the following bullet at the end of the STRICT REQUIREMENTS block (after the existing run_time bullet):

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

"Replace N with the exact integer" must appear verbatim so Claude substitutes the literal (e.g. `5`) rather than emitting `N` as a bare name causing a `NameError` at render time.

The `X` in `_d[i] - X` refers to the numeric literal that was passed as `run_time=` to the `self.play()` call immediately above — not a variable named `run_time`.

### 4. Add pitfall entries to KNOWN API PITFALLS

Append the following bullets at the end of the KNOWN API PITFALLS block:

```
- Never hardcode self.wait(X) with a float literal — always use _d[i] loaded from segments.json
- Use max(0.0, _d[i] - X) where X is the run_time= value passed to self.play() in that segment
- Pad _d with: _d = _d + [2.0] * max(0, N - len(_d)) where N is the literal integer segment count
- Segment numbers and _d indices are both 0-based — Segment 0 → _d[0], Segment 1 → _d[1]
```

### Required scene.py pattern

```python
from manim import *
import json
from pathlib import Path

class ChalkboardScene(Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, 5 - len(_d))  # 5 = exact integer from "Total segments: 5"

        # Segment 0 — animation takes 2.0s, pad the rest
        title = Text("B-Trees")
        self.play(Write(title), run_time=2.0)
        self.wait(max(0.0, _d[0] - 2.0))

        # Segment 1 — no internal animation, full wait
        self.wait(_d[1])
```

## Changes to code_validator.py

Append the following to the end of the `user_msg` f-string in `code_validator`, after the existing `"Check: ..."` sentence:

```
Sync check: The scene must load _seg_data from (Path(__file__).parent / "segments.json")
and use _d[i] (not hardcoded float literals) for all self.wait() calls.
If any self.wait() call uses a hardcoded float literal, return needs_revision.
```

## Changes to test fixtures

### tests/test_manim_agent.py — update VALID_SCENE

Replace hardcoded-float `self.wait()` with the dynamic pattern:

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

### tests/test_code_validator.py — update VALID_CODE

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

### tests/test_code_validator.py — add rejection test

Use the same `patch` pattern as the other tests in the file. `_mock_response(verdict, feedback)` already exists in the file and takes two string arguments.

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

## Edge cases

### segments.json absent at render time

This should not happen in normal operation — `render_trigger` always writes `segments.json` before Docker runs. If it does happen (e.g. manual Docker invocation before pipeline completes), the scene will raise `FileNotFoundError`. This is intentional: a loud failure is better than silent wrong timing.

### Index out of bounds

Mitigated by the padding line. The `n` value in the header is an exact integer that Claude substitutes directly into the padding expression.

## Files changed

| File | Change |
|------|--------|
| `pipeline/agents/manim_agent.py` | Remove "only import" constraint; make `_format_segments` 0-based with total count header; add dynamic loading requirement and pitfall entries to SYSTEM_PROMPT |
| `pipeline/agents/code_validator.py` | Add sync pattern check to `user_msg` |
| `tests/test_manim_agent.py` | Update `VALID_SCENE` fixture to use `_d[i]` pattern |
| `tests/test_code_validator.py` | Update `VALID_CODE` fixture; add `test_code_validator_rejects_hardcoded_wait` |

## Files NOT changed

- `pipeline/render_trigger.py` — already writes `segments.json` correctly
- `pipeline/graph.py` — no pipeline changes
- `pipeline/state.py` — no new state fields
- `main.py` — no changes
- `docker/render.sh` — no changes
