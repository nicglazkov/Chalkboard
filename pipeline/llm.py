# pipeline/llm.py
"""Single place every agent talks to Claude through.

Why this exists:
  * Current Claude models always think, so a response's first content block is
    often a ``thinking`` block. Reading ``response.content[0].text`` (what the
    agents used to do) breaks; ``response_text`` takes the last text block.
  * Model + effort are chosen per agent from config (env-overridable), so a
    user can run the cheap path (e.g. Sonnet for validators) without code edits.
  * Long generations (manim_agent) stream so they never hit HTTP timeouts.
"""
from __future__ import annotations

import json
import threading
import time

import anthropic

from config import agent_effort, agent_model
from pipeline import telemetry
from pipeline.partial_json import extract_string_field
from pipeline.retry import TimeoutExhausted, api_call_with_retry
from pipeline.pricing import call_cost

_client_lock = threading.Lock()
_clients: dict[bool, anthropic.Anthropic] = {}


class ClaudeRefused(RuntimeError):
    """Claude declined the request (stop_reason == "refusal")."""


class ClaudeTruncated(RuntimeError):
    """The response hit max_tokens before the structured output closed.

    max_tokens caps thinking AND text together, so a call can also run out
    while still thinking (``thinking_only``: no text block at all).
    """

    def __init__(self, message: str = "response hit max_tokens before finishing", *,
                 thinking_only: bool = False, output_tokens: int | None = None):
        super().__init__(message)
        self.thinking_only = thinking_only
        self.output_tokens = output_tokens


class ClaudeOutOfRoom(TimeoutExhausted):
    """Every output budget step ran out of room (see call_json_budgeted).

    A TimeoutExhausted, so callers that already degrade gracefully on an
    exhausted Claude call (fact check, code review, research) keep doing so."""

    def __init__(self, message: str, *, steps: list[dict]):
        super().__init__(message)
        self.steps = steps


def get_client(pdf: bool = False) -> anthropic.Anthropic:
    """Shared client (connection pooling). SDK retries 429/5xx itself."""
    with _client_lock:
        if pdf not in _clients:
            kwargs: dict = {"max_retries": 3}
            if pdf:
                kwargs["default_headers"] = {"anthropic-beta": "pdfs-2024-09-25"}
            _clients[pdf] = anthropic.Anthropic(**kwargs)
        return _clients[pdf]


def has_pdf(context_blocks) -> bool:
    return bool(context_blocks) and any(b.get("type") == "document" for b in context_blocks)


def model_params(agent: str, effort: str | None = None) -> dict:
    """model / thinking / effort kwargs for an agent, valid for the chosen model.

    ``effort`` overrides the agent's configured effort (budget step-down).
    """
    model = agent_model(agent)
    params: dict = {"model": model}
    if model.startswith("claude-haiku"):
        return params  # Haiku 4.5: no adaptive thinking, no effort parameter
    params["thinking"] = {"type": "adaptive"}
    params["output_config"] = {"effort": effort or agent_effort(agent)}
    return params


# ── Output budgets ────────────────────────────────────────────────────────────
# max_tokens is a ceiling on thinking + text together (adaptive thinking counts
# against it), and it is not billed: only generated tokens are. So budgets are
# sized for the job, and a call that runs out of room is retried with more room
# (then less thinking) instead of being repeated unchanged.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# Larger budgets always stream: long non-streaming requests risk HTTP timeouts
# (the SDK refuses one it expects to run past 10 minutes).
NON_STREAMING_MAX = 16000
# Used when the Models API cannot tell us the model's output cap. Every current
# Claude model supports at least this much with streaming.
DEFAULT_OUTPUT_CEILING = 64000
_output_caps: dict[str, int] = {}


def model_max_output(model: str, client=None) -> int:
    """The model's max output tokens (Models API `max_tokens`), cached per process.

    Falls back to DEFAULT_OUTPUT_CEILING when the lookup fails; a fallback is
    not cached, so a later call can still learn the real value.
    """
    if model in _output_caps:
        return _output_caps[model]
    try:
        info = (client or get_client()).models.retrieve(model)
        cap = getattr(info, "max_tokens", None)
    except Exception:
        cap = None
    if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
        _output_caps[model] = cap
        return cap
    return DEFAULT_OUTPUT_CEILING


def lower_effort(effort: str) -> str | None:
    """One effort level down, or None at the bottom (or for an unknown level)."""
    if effort not in EFFORT_LEVELS:
        return None
    i = EFFORT_LEVELS.index(effort)
    return EFFORT_LEVELS[i - 1] if i > 0 else None


def budget_steps(max_tokens: int, ceiling: int, effort: str | None) -> list[dict]:
    """The budget ladder for one call: as asked, then the model's full output
    cap, then the full cap at one effort level lower. At most three steps; a
    step identical to the previous one is skipped."""
    first = min(max_tokens, ceiling)
    steps = [{"max_tokens": first, "effort": effort}]
    if ceiling > first:
        steps.append({"max_tokens": ceiling, "effort": effort})
    lower = lower_effort(effort) if effort else None
    if lower:
        steps.append({"max_tokens": ceiling, "effort": lower})
    return steps[:3]


def response_text(response) -> str:
    """Text of the final text block, after checking the stop reason."""
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        details = getattr(response, "stop_details", None)
        raise ClaudeRefused(f"Claude declined the request: {details}")
    block = next((b for b in reversed(response.content) if getattr(b, "type", "text") == "text"), None)
    usage = getattr(response, "usage", None)
    out = _int_or_none(getattr(usage, "output_tokens", None))
    if block is None:
        types = [getattr(b, "type", "?") for b in response.content]
        if stop == "max_tokens":
            # Thinking used the whole budget before any answer was written.
            raise ClaudeTruncated(
                f"response hit max_tokens while still thinking (no text block; content types: {types}"
                + (f"; {out} output tokens" if out is not None else "") + ")",
                thinking_only=True, output_tokens=out,
            )
        raise RuntimeError(f"no text block in response (content types: {types})")
    if stop == "max_tokens":
        raise ClaudeTruncated(
            "response hit max_tokens before finishing"
            + (f" ({out} output tokens)" if out is not None else ""),
            output_tokens=out,
        )
    return block.text


def call_json(
    agent: str,
    *,
    content,
    schema: dict,
    system: str | None = None,
    max_tokens: int = 16000,
    tools: list | None = None,
    client=None,
    stream: bool = False,
    effort: str | None = None,
):
    """Blocking structured-output call. Returns (parsed_dict, response).

    Run it via ``api_call_with_retry`` (it is sync; agents call it in a thread),
    or via ``call_json_budgeted``, which also handles running out of room.
    Budgets above NON_STREAMING_MAX always stream.
    """
    if client is None:
        client = get_client()
    if max_tokens > NON_STREAMING_MAX:
        stream = True
    kwargs = model_params(agent, effort)
    kwargs["output_config"] = {
        **kwargs.get("output_config", {}),
        "format": {"type": "json_schema", "schema": schema},
    }
    kwargs.update(max_tokens=max_tokens, messages=[{"role": "user", "content": content}])
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools
    peek = PEEK_FIELDS.get(agent) if telemetry.wants_peek() else None
    if peek is not None:
        response = _stream_with_peek(client, kwargs, *peek)
    elif stream:
        with client.messages.stream(**kwargs) as s:
            response = s.get_final_message()
    else:
        response = client.messages.create(**kwargs)
    report_usage(agent, response, requested_model=kwargs.get("model"),
                 max_tokens=max_tokens, effort=kwargs.get("output_config", {}).get("effort"))
    data = json.loads(response_text(response))
    if peek is not None:
        value = data.get(peek[1]) if isinstance(data, dict) else None
        telemetry.emit("peek", {"stage": peek[0], "text": value if isinstance(value, str) else "",
                                "done": True})
    return data, response


# agent -> (peek stage, JSON string field whose growing value is published)
PEEK_FIELDS = {
    "script": ("script", "script"),
    "fact": ("fact_check", "feedback"),
    "manim": ("scene_code", "manim_code"),
}
PEEK_INTERVAL = 0.25  # seconds between peek events (at most ~4/s)


def _stream_with_peek(client, kwargs: dict, stage: str, field: str):
    """Stream the call and publish the field's text as Claude writes it.

    The text comes from the live stream (the SDK's per-block text snapshot),
    parsed incrementally; nothing is replayed after the fact.
    """
    last_sent = None
    last_t = 0.0
    with client.messages.stream(**kwargs) as s:
        for event in s:
            if getattr(event, "type", None) != "text":
                continue
            now = time.monotonic()
            if now - last_t < PEEK_INTERVAL:
                continue
            value = extract_string_field(getattr(event, "snapshot", "") or "", field)
            if value is None or value == last_sent:
                continue
            telemetry.emit("peek", {"stage": stage, "text": value, "done": False})
            last_sent, last_t = value, now
        return s.get_final_message()


def _int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def report_usage(agent: str, response, requested_model: str | None = None, *,
                 max_tokens: int | None = None, effort: str | None = None) -> dict | None:
    """Emit a `usage` event from response.usage (no-op without a sink).

    With ``max_tokens`` the event also carries the budget, the effort and the
    stop reason, so a run that ran out of room shows it in its usage record.
    """
    if telemetry.current() is None:
        return None
    usage = getattr(response, "usage", None)
    model = getattr(response, "model", None)
    if not isinstance(model, str):
        model = requested_model
    inp = _int_or_none(getattr(usage, "input_tokens", None))
    out = _int_or_none(getattr(usage, "output_tokens", None))
    stu = getattr(usage, "server_tool_use", None)
    searches = _int_or_none(getattr(stu, "web_search_requests", None)) if stu is not None else 0
    cache = sum(_int_or_none(getattr(usage, k, None)) or 0
                for k in ("cache_creation_input_tokens", "cache_read_input_tokens"))
    updates = {
        "agent": agent, "model": model,
        "input_tokens": inp, "output_tokens": out,
        "web_searches": searches,
        "cost_usd": call_cost(model, inp, out, searches, cache),
    }
    if cache:
        updates["cache_tokens"] = cache
    if max_tokens is not None:
        stop = getattr(response, "stop_reason", None)
        updates["max_tokens"] = max_tokens
        updates["effort"] = effort
        updates["stop_reason"] = stop if isinstance(stop, str) else None
    telemetry.emit("usage", updates)
    return updates


# Rough floor on streaming speed, used only to size per-attempt timeouts so a
# legitimately long generation is not killed by a timeout sized for a short one.
MIN_TOKENS_PER_SEC = 40.0


def budget_timeout(base: float, max_tokens: int) -> float:
    """Per-attempt timeout for a budget: the base, or long enough to stream the
    whole budget at MIN_TOKENS_PER_SEC (plus a minute of input/latency)."""
    return max(base, max_tokens / MIN_TOKENS_PER_SEC + 60.0)


async def call_json_budgeted(agent: str, *, label: str, timeout: float, max_tokens: int,
                             client=None, max_attempts: int = 3, on_last_step=None,
                             max_steps: int = 3, **kwargs):
    """``call_json`` under ``api_call_with_retry``, with output-budget escalation.

    A response that runs out of output room (stop_reason max_tokens, including
    the thinking-only case) is not repeated unchanged: the next step of
    ``budget_steps`` gives it the model's whole output cap, then one effort
    level less thinking. Transient errors are still retried by
    ``api_call_with_retry`` within a step. ``on_last_step(kwargs) -> kwargs``
    may change the request for the final step (e.g. a tighter instruction).
    ``max_steps=1`` disables escalation (the caller has a better fallback).

    Each escalation prints a log line and emits a ``budget`` telemetry event.
    Raises ``ClaudeOutOfRoom`` when every step ran out of room; other failures
    raise ``TimeoutExhausted`` as before.
    """
    ceiling = model_max_output(agent_model(agent), client)
    effort = None if agent_model(agent).startswith("claude-haiku") else agent_effort(agent)
    steps = budget_steps(max_tokens, ceiling, effort)[:max(1, max_steps)]
    tried: list[dict] = []
    for i, step in enumerate(steps):
        call_kwargs = dict(kwargs)
        if i == len(steps) - 1 and i > 0 and on_last_step is not None:
            call_kwargs = on_last_step(call_kwargs)

        def _call(step=step, call_kwargs=call_kwargs):
            return call_json(agent, max_tokens=step["max_tokens"], effort=step["effort"],
                             client=client, **call_kwargs)

        try:
            return await api_call_with_retry(
                _call, timeout=budget_timeout(timeout, step["max_tokens"]),
                max_attempts=max_attempts, label=label, passthrough=(ClaudeTruncated,),
            )
        except ClaudeTruncated as e:
            tried.append({**step, "thinking_only": e.thinking_only, "output_tokens": e.output_tokens})
            if i == len(steps) - 1:
                break
            nxt = steps[i + 1]
            what = "still thinking" if e.thinking_only else "mid-answer"
            print(f"  [{label}] out of output room {what} at max_tokens={step['max_tokens']}"
                  f" (effort {step['effort']}); retrying with max_tokens={nxt['max_tokens']}"
                  f", effort {nxt['effort']}")
            telemetry.emit("budget", {
                "agent": agent, "label": label, "reason": "thinking_only" if e.thinking_only else "max_tokens",
                "from_max_tokens": step["max_tokens"], "to_max_tokens": nxt["max_tokens"],
                "from_effort": step["effort"], "to_effort": nxt["effort"],
                "output_tokens": e.output_tokens,
            })
    raise ClaudeOutOfRoom(
        f"{label} ran out of output room at every budget step "
        + ", ".join(f"{s['max_tokens']}/{s['effort']}" for s in tried),
        steps=tried,
    )


def web_search_tool(agent: str) -> dict:
    """Newest web search tool the agent's model supports."""
    model = agent_model(agent)
    old = model.startswith(("claude-haiku", "claude-sonnet-4-5", "claude-opus-4-5"))
    return {"type": "web_search_20250305" if old else "web_search_20260209", "name": "web_search"}
