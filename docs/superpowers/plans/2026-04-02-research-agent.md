# Research Agent (#2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated `research_agent` node that performs targeted web research before `script_agent` runs, producing a structured research brief that grounds the script in accurate, current facts — active only when `--effort high`.

**Architecture:** A new async LangGraph node `research_agent` is inserted between `init` and `script_agent` when `effort_level == "high"`. It calls Claude with the `web_search` tool and returns `{"research_brief": str, "research_sources": list[str]}`, which `script_agent` then injects into its user message. When `research_brief` is present, `script_agent`'s own web search is disabled (research already covered it).

**Tech Stack:** Python 3.10+, `anthropic` SDK (web_search tool), LangGraph, pytest

---

## Files

- **Create:** `pipeline/agents/research_agent.py`
- **Create:** `tests/test_research_agent.py`
- **Modify:** `pipeline/state.py` — add `research_brief`, `research_sources` fields
- **Modify:** `pipeline/retry.py` — add `TIMEOUT_RESEARCH_AGENT`
- **Modify:** `pipeline/graph.py` — add node, `_after_init` routing, edge
- **Modify:** `pipeline/agents/script_agent.py` — inject brief; skip web search if brief present
- **Modify:** `tests/conftest.py` — add new fields to `base_state`
- **Modify:** `tests/test_graph.py` — add routing tests
- **Modify:** `CLAUDE.md` — document research_agent node

---

### Task 1: Add state fields and timeout constant

**Files:**
- Modify: `pipeline/state.py`
- Modify: `pipeline/retry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_state.py — add to existing file (it already imports PipelineState)
def test_research_fields_in_pipeline_state():
    from typing import get_type_hints
    hints = get_type_hints(PipelineState)
    assert "research_brief" in hints
    assert "research_sources" in hints
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_state.py::test_research_fields_in_pipeline_state -v
```
Expected: FAIL with `AssertionError`

- [ ] **Step 3: Add fields to PipelineState**

In `pipeline/state.py`, add two lines after `template: str | None`:

```python
    research_brief: str | None
    research_sources: list[str]
```

- [ ] **Step 4: Add timeout constant**

In `pipeline/retry.py`, add after `TIMEOUT_SCRIPT_AGENT`:

```python
TIMEOUT_RESEARCH_AGENT = 120.0   # research_agent (web search, may take multiple queries)
```

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/test_state.py::test_research_fields_in_pipeline_state -v
```

- [ ] **Step 6: Update conftest.py base_state**

In `tests/conftest.py`, add two fields to the `base_state` fixture dict (after `template=None`):

```python
        research_brief=None,
        research_sources=[],
```

- [ ] **Step 7: Run full test suite to confirm no regressions**

```
pytest --tb=short -q
```
Expected: all existing tests pass.

- [ ] **Step 8: Commit**

```bash
git add pipeline/state.py pipeline/retry.py tests/conftest.py
git commit -m "feat: add research_brief/research_sources state fields and TIMEOUT_RESEARCH_AGENT"
```

---

### Task 2: Implement research_agent

**Files:**
- Create: `pipeline/agents/research_agent.py`
- Create: `tests/test_research_agent.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_agent.py`:

```python
# tests/test_research_agent.py
import asyncio
import json
import pytest
from unittest.mock import MagicMock, patch
from pipeline.agents.research_agent import research_agent

DUMMY_BRIEF = "B-trees are self-balancing search trees used in databases."
DUMMY_SOURCES = ["https://en.wikipedia.org/wiki/B-tree"]


def _mock_response(brief=DUMMY_BRIEF, sources=None):
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({
        "research_brief": brief,
        "sources": sources or DUMMY_SOURCES,
    }))]
    return msg


def test_research_agent_returns_brief(base_state):
    base_state["effort_level"] = "high"
    with patch("pipeline.agents.research_agent.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.create.return_value = _mock_response()
        result = asyncio.run(research_agent(base_state))
    assert result["research_brief"] == DUMMY_BRIEF
    assert result["research_sources"] == DUMMY_SOURCES


def test_research_agent_uses_web_search_tool(base_state):
    base_state["effort_level"] = "high"
    with patch("pipeline.agents.research_agent.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.create.return_value = _mock_response()
        asyncio.run(research_agent(base_state))
    call_kwargs = instance.messages.create.call_args.kwargs
    tools = call_kwargs.get("tools", [])
    assert any(t.get("type") == "web_search_20250305" for t in tools)


def test_research_agent_includes_topic_in_message(base_state):
    base_state["topic"] = "explain quicksort"
    base_state["effort_level"] = "high"
    with patch("pipeline.agents.research_agent.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.create.return_value = _mock_response()
        asyncio.run(research_agent(base_state))
    messages = instance.messages.create.call_args.kwargs["messages"]
    assert "quicksort" in messages[0]["content"]


def test_research_agent_accepts_injected_client(base_state):
    """Allows callers to inject a mock client (same pattern as other agents)."""
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_response()
    result = asyncio.run(research_agent(base_state, client=mock_client))
    assert result["research_brief"] == DUMMY_BRIEF
    mock_client.messages.create.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_research_agent.py -v
```
Expected: ImportError — `research_agent` doesn't exist yet.

- [ ] **Step 3: Implement research_agent.py**

Create `pipeline/agents/research_agent.py`:

```python
# pipeline/agents/research_agent.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.retry import api_call_with_retry, TIMEOUT_RESEARCH_AGENT
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are a research assistant preparing material for an educational video script writer.
Given a topic, perform targeted web searches to gather accurate, up-to-date facts, figures, and key points.
Compile them into a concise research brief.

Focus on:
- Core factual claims with specific numbers, dates, or names where relevant
- Common misconceptions to address or avoid
- Current state of knowledge (recent developments)
- 2–5 credible sources

Respond with valid JSON only:
{
  "research_brief": "<compiled research as a readable summary, 150-300 words>",
  "sources": ["<url or citation>"]
}"""


async def research_agent(state: PipelineState, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Topic: {state['topic']}"}],
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "research_brief": {"type": "string"},
                            "sources": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["research_brief", "sources"],
                        "additionalProperties": False,
                    },
                }
            },
        )

    response = await api_call_with_retry(
        _call, timeout=TIMEOUT_RESEARCH_AGENT, label="research_agent"
    )
    data = json.loads(response.content[0].text)
    return {
        "research_brief": data["research_brief"],
        "research_sources": data["sources"],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_research_agent.py -v
```
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/agents/research_agent.py tests/test_research_agent.py pipeline/retry.py
git commit -m "feat: implement research_agent with web search and structured output"
```

---

### Task 3: Wire research_agent into the graph

**Files:**
- Modify: `pipeline/graph.py`
- Modify: `tests/test_graph.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_graph.py`:

```python
def test_high_effort_routes_through_research_agent(tmp_path):
    """With effort_level=high, graph must pass through research_agent before script_agent."""
    visited = []

    async def mock_research_agent(state, **kw):
        visited.append("research_agent")
        return {"research_brief": "Facts about B-trees.", "research_sources": []}

    async def mock_script_agent(state, **kw):
        visited.append("script_agent")
        return _make_script_state()

    async def mock_fact_validator(state, **kw):
        return _make_approved_state()

    async def mock_manim_agent(state, **kw):
        return _make_manim_state()

    async def mock_code_validator(state, **kw):
        return _make_code_approved_state()

    async def mock_tts(segments, path, speed=1.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.research_agent", new=mock_research_agent), \
         patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-research-routing"}}
        result = asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "high"},
            config=config,
        ))

    assert result["status"] == "approved"
    assert visited.index("research_agent") < visited.index("script_agent")


def test_medium_effort_skips_research_agent(tmp_path):
    """With effort_level=medium, graph goes directly to script_agent."""
    visited = []

    async def mock_research_agent(state, **kw):
        visited.append("research_agent")
        return {"research_brief": "should not be called", "research_sources": []}

    async def mock_script_agent(state, **kw):
        visited.append("script_agent")
        return _make_script_state()

    async def mock_fact_validator(state, **kw):
        return _make_approved_state()

    async def mock_manim_agent(state, **kw):
        return _make_manim_state()

    async def mock_code_validator(state, **kw):
        return _make_code_approved_state()

    async def mock_tts(segments, path, speed=1.0):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00")
        return path, [2.0]

    with patch("pipeline.graph.research_agent", new=mock_research_agent), \
         patch("pipeline.graph.script_agent", new=mock_script_agent), \
         patch("pipeline.graph.fact_validator", new=mock_fact_validator), \
         patch("pipeline.graph.manim_agent", new=mock_manim_agent), \
         patch("pipeline.graph.code_validator", new=mock_code_validator), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_tts), \
         patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)):

        graph = build_graph()
        config = {"configurable": {"thread_id": "test-skip-research"}}
        asyncio.run(graph.ainvoke(
            {"topic": "explain B-trees", "effort_level": "medium"},
            config=config,
        ))

    assert "research_agent" not in visited
    assert "script_agent" in visited
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_graph.py::test_high_effort_routes_through_research_agent tests/test_graph.py::test_medium_effort_skips_research_agent -v
```
Expected: FAIL — `research_agent` not imported in graph.py.

- [ ] **Step 3: Update graph.py**

In `pipeline/graph.py`, add import and routing:

```python
# Add to imports at top:
from pipeline.agents.research_agent import research_agent

# Add routing function after _after_code_validator:
def _after_init(state: PipelineState) -> str:
    if state.get("effort_level") == "high":
        return "research_agent"
    return "script_agent"

# In _init_state, add two lines after "template": state.get("template"):
        "research_brief": state.get("research_brief"),
        "research_sources": state.get("research_sources", []),

# In build_graph(), replace:
#   builder.add_edge("init", "script_agent")
# with:
    builder.add_node("research_agent", research_agent)
    builder.add_edge("research_agent", "script_agent")
    builder.add_conditional_edges("init", _after_init, ["research_agent", "script_agent"])
```

Full updated `build_graph` node/edge section (replace the existing add_node/add_edge block):

```python
    builder.add_node("init", _init_state)
    builder.add_node("research_agent", research_agent)
    builder.add_node("script_agent", _script_agent)
    builder.add_node("fact_validator", fact_validator)
    builder.add_node("manim_agent", _manim_agent)
    builder.add_node("code_validator", code_validator)
    builder.add_node("escalate_to_user", escalate_to_user)
    builder.add_node("render_trigger", render_trigger)

    builder.add_edge(START, "init")
    builder.add_conditional_edges("init", _after_init, ["research_agent", "script_agent"])
    builder.add_edge("research_agent", "script_agent")
    builder.add_edge("script_agent", "fact_validator")
    builder.add_conditional_edges("fact_validator", _after_fact_validator,
        ["script_agent", "manim_agent", "escalate_to_user"])
    builder.add_edge("manim_agent", "code_validator")
    builder.add_conditional_edges("code_validator", _after_code_validator,
        ["manim_agent", "render_trigger", "escalate_to_user"])
    builder.add_edge("render_trigger", END)
    builder.add_conditional_edges("escalate_to_user", _after_escalate,
        ["script_agent", "manim_agent", END])
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_graph.py -v
```
Expected: all graph tests pass (including two new ones).

- [ ] **Step 5: Commit**

```bash
git add pipeline/graph.py tests/test_graph.py
git commit -m "feat: wire research_agent into graph, routes on effort_level=high"
```

---

### Task 4: Inject research brief into script_agent

**Files:**
- Modify: `pipeline/agents/script_agent.py`
- Modify: `tests/test_script_agent.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_script_agent.py`:

```python
def test_research_brief_injected_into_message(base_state):
    """When research_brief is set, it appears in the user message."""
    base_state["research_brief"] = "B-trees store multiple keys per node."
    base_state["research_sources"] = ["https://example.com"]
    base_state["script"] = ""
    base_state["script_segments"] = []

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.create.return_value = _mock_response()
        asyncio.run(script_agent(base_state))

    messages = instance.messages.create.call_args.kwargs["messages"]
    content = messages[0]["content"] if isinstance(messages[0]["content"], str) else str(messages[0]["content"])
    assert "B-trees store multiple keys per node." in content


def test_web_search_disabled_when_brief_present(base_state):
    """When research_brief is set, script_agent must not enable web_search (research already done)."""
    base_state["effort_level"] = "high"
    base_state["research_brief"] = "Some research."
    base_state["research_sources"] = []

    with patch("pipeline.agents.script_agent.anthropic.Anthropic") as MockClient:
        instance = MockClient.return_value
        instance.messages.create.return_value = _mock_response()
        asyncio.run(script_agent(base_state))

    call_kwargs = instance.messages.create.call_args.kwargs
    # tools should be NOT_GIVEN or an empty list — no web_search
    tools = call_kwargs.get("tools", anthropic.NOT_GIVEN)
    if tools is not anthropic.NOT_GIVEN:
        assert not any(t.get("type") == "web_search_20250305" for t in (tools or []))
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_script_agent.py::test_research_brief_injected_into_message tests/test_script_agent.py::test_web_search_disabled_when_brief_present -v
```
Expected: FAIL.

- [ ] **Step 3: Update _build_user_message and web_search logic in script_agent.py**

In `pipeline/agents/script_agent.py`, update `_build_user_message`:

```python
def _build_user_message(state: PipelineState) -> str:
    topic = state["topic"]
    effort = state["effort_level"]
    feedback = state.get("fact_feedback")
    web_approved = state.get("user_approved_search", False)

    msg = f"Topic: {topic}\nEffort level: {effort}"
    msg += f"\n{AUDIENCE_INSTRUCTIONS[state.get('audience', 'intermediate')]}"
    msg += f"\n{TONE_INSTRUCTIONS[state.get('tone', 'casual')]}"

    if state.get("research_brief"):
        msg += f"\n\nResearch brief (ground your script in these facts):\n{state['research_brief']}"
        if state.get("research_sources"):
            sources = "\n".join(f"  - {s}" for s in state["research_sources"])
            msg += f"\n\nSources consulted:\n{sources}"

    if feedback:
        msg += f"\n\nPrevious attempt had issues. Please rewrite the script fully, addressing this feedback:\n{feedback}"
    if web_approved:
        msg += "\n\nWeb search has been approved — use it if needed."
    if effort == "low":
        msg += "\n\nEffort=low: keep the script concise, 3–4 segments, no web search needed."
    return msg
```

And update the tools logic in `script_agent()` — disable web_search when brief is already present:

```python
    tools = []
    # Skip web_search if research_agent already provided a brief (effort=high path)
    if (state.get("user_approved_search") or state["effort_level"] == "high") and not state.get("research_brief"):
        tools = [{"type": "web_search_20250305", "name": "web_search"}]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_script_agent.py -v
```
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/agents/script_agent.py tests/test_script_agent.py
git commit -m "feat: inject research_brief into script_agent; skip web_search when brief present"
```

---

### Task 5: Update CLAUDE.md and run full suite

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add research_agent to CLAUDE.md**

In `CLAUDE.md`, in the **Agent prompts and output formats** section, add after `### script_agent`:

```markdown
### research_agent
- Model: `CLAUDE_MODEL`
- `max_tokens`: 2048
- Timeout: `TIMEOUT_RESEARCH_AGENT` = 120s
- Output: `{"research_brief": str, "sources": list[str]}`
- Only runs when `effort_level == "high"` — routed via `_after_init` conditional edge
- Uses `web_search_20250305` tool to gather facts before scripting
- When present, `research_brief` is injected into `script_agent`'s user message and `script_agent`'s own web search is disabled
```

In the pipeline graph diagram at the top of CLAUDE.md, update to:

```
├─ init              — normalize state, set run_id
├─ research_agent    — (effort=high only) web research brief
├─ script_agent      — Claude writes narration script
```

In the **State schema** table, add:

| `research_brief` | `str \| None` | Research brief from `research_agent`; `None` if not yet run or effort ≠ high |
| `research_sources` | `list[str]` | URLs/citations from `research_agent` |

- [ ] **Step 2: Run full test suite**

```
pytest --tb=short -q
```
Expected: all tests pass.

- [ ] **Step 3: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs: document research_agent node, state fields, and graph routing"
```
