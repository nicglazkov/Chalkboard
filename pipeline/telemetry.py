# pipeline/telemetry.py
"""Per-run event sink for live progress (peeks, usage, render, TTS).

The server sets a sink for each job; the CLI sets one that only records.
A ContextVar carries it, and asyncio.to_thread copies the context, so code
running in worker threads (call_json, Kokoro, the render subprocess reader)
reaches the same sink. With no sink set, emit() is a no-op.

Events have the job-event shape: {"node": str, "updates": dict, "ts": iso}.
`ts` is stamped here, at the moment the event happened, not when the
consumer gets around to it.
"""
from __future__ import annotations

import contextvars
from datetime import datetime, timezone
from typing import Callable

_sink: contextvars.ContextVar["Sink | None"] = contextvars.ContextVar("chalkboard_sink", default=None)


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


class Sink:
    """Callable that receives events. `peek=True` asks call_json to stream
    and publish partial output (costs nothing extra, but changes the request
    path, so the CLI leaves it off)."""

    def __init__(self, fn: Callable[[dict], None], *, peek: bool = False):
        self._fn = fn
        self.peek = peek

    def __call__(self, event: dict) -> None:
        self._fn(event)


def set_sink(sink: Sink | None) -> contextvars.Token:
    return _sink.set(sink)


def reset_sink(token: contextvars.Token) -> None:
    _sink.reset(token)


def current() -> Sink | None:
    return _sink.get()


def wants_peek() -> bool:
    s = _sink.get()
    return bool(s and s.peek)


def emit(node: str, updates: dict) -> None:
    """Send one event to the current sink; never raises into the pipeline."""
    s = _sink.get()
    if s is None:
        return
    try:
        s({"node": node, "updates": updates, "ts": now_iso()})
    except Exception as e:  # telemetry must never break a run
        print(f"  [telemetry] sink error: {e}")
