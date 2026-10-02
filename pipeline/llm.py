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
from pipeline.pricing import call_cost

_client_lock = threading.Lock()
_clients: dict[bool, anthropic.Anthropic] = {}


class ClaudeRefused(RuntimeError):
    """Claude declined the request (stop_reason == "refusal")."""


class ClaudeTruncated(RuntimeError):
    """The response hit max_tokens before the structured output closed."""


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


def model_params(agent: str) -> dict:
    """model / thinking / effort kwargs for an agent, valid for the chosen model."""
    model = agent_model(agent)
    params: dict = {"model": model}
    if model.startswith("claude-haiku"):
        return params  # Haiku 4.5: no adaptive thinking, no effort parameter
    params["thinking"] = {"type": "adaptive"}
    params["output_config"] = {"effort": agent_effort(agent)}
    return params


def response_text(response) -> str:
    """Text of the final text block, after checking the stop reason."""
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        details = getattr(response, "stop_details", None)
        raise ClaudeRefused(f"Claude declined the request: {details}")
    block = next((b for b in reversed(response.content) if getattr(b, "type", "text") == "text"), None)
    if block is None:
        raise RuntimeError(
            f"no text block in response (content types: {[getattr(b, 'type', '?') for b in response.content]})"
        )
    if stop == "max_tokens":
        raise ClaudeTruncated("response hit max_tokens before finishing")
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
):
    """Blocking structured-output call. Returns (parsed_dict, response).

    Run it via ``api_call_with_retry`` (it is sync; agents call it in a thread).
    """
    if client is None:
        client = get_client()
    kwargs = model_params(agent)
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
    report_usage(agent, response, requested_model=kwargs.get("model"))
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


def report_usage(agent: str, response, requested_model: str | None = None) -> dict | None:
    """Emit a `usage` event from response.usage (no-op without a sink)."""
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
    telemetry.emit("usage", updates)
    return updates


def web_search_tool(agent: str) -> dict:
    """Newest web search tool the agent's model supports."""
    model = agent_model(agent)
    old = model.startswith(("claude-haiku", "claude-sonnet-4-5", "claude-opus-4-5"))
    return {"type": "web_search_20250305" if old else "web_search_20260209", "name": "web_search"}
