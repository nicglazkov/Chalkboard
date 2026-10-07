# pipeline/run_stats.py
"""run_stats.json: the measured record of one run (CLI or server).

Everything here is computed from timestamped events the run emitted (graph
node completions, `usage`, `tts`, `render`, ...). Nothing is estimated: a
value that was not observed is None.

stage_seconds: a pipeline node's time is the gap between the previous stage
boundary and its completion event (the graph runs one node at a time, so the
gap is that node's work). Post-graph phases (render, visual_qa, quiz) run from
their "running" event to their "done"/"failed" event. Time between phases that
no event covers is not attributed to any stage.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from pipeline import version as _version
from pipeline.telemetry import now_iso

# Events that are not graph nodes.
TELEMETRY_NODES = {"peek", "usage", "tts", "render", "visual_qa", "quiz"}
PHASE_NODES = {"render", "visual_qa", "quiz"}


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def totals(events: list[dict]) -> dict:
    """Token / cost / TTS totals from usage and tts events.

    cost_usd is None if any call's cost is unknown.
    tts_chars is None when no TTS completion was observed in these events.
    """
    usage = [e["updates"] for e in events if e.get("node") == "usage"]
    inp = sum((u.get("input_tokens") or 0) for u in usage)
    out = sum((u.get("output_tokens") or 0) for u in usage)
    searches = sum((u.get("web_searches") or 0) for u in usage)
    costs = [u.get("cost_usd") for u in usage]
    cost = round(sum(costs), 6) if all(c is not None for c in costs) else None
    tts_done = [e["updates"] for e in events
                if e.get("node") == "tts" and (e.get("updates") or {}).get("status") == "done"]
    tts_chars = sum(int(u.get("chars") or 0) for u in tts_done) if tts_done else None
    # Any token count missing from a reported call makes that total unknown.
    complete = all(u.get("input_tokens") is not None and u.get("output_tokens") is not None for u in usage)
    return {
        "calls": len(usage),
        "input_tokens": inp if complete else None,
        "output_tokens": out if complete else None,
        "web_searches": searches,
        "cost_usd": cost,
        "tts_chars": tts_chars,
    }


def stage_seconds(events: list[dict], started_at: str) -> dict[str, float]:
    out: dict[str, float] = {}
    prev = _parse_ts(started_at)
    open_phase: str | None = None
    for e in events:
        node = e.get("node")
        ts = e.get("ts")
        if not node or not ts:
            continue
        t = _parse_ts(ts)
        status = (e.get("updates") or {}).get("status")
        if node in PHASE_NODES:
            if status == "running" and open_phase is None:
                open_phase, prev = node, t
            elif status in ("done", "failed") and open_phase == node:
                out[node] = out.get(node, 0.0) + (t - prev).total_seconds()
                open_phase, prev = None, t
            continue
        if node in TELEMETRY_NODES or node == "__end__":
            continue
        # Graph node completion.
        out[node] = out.get(node, 0.0) + (t - prev).total_seconds()
        prev = t
    return {k: round(v, 3) for k, v in out.items()}


def build(events: list[dict], *, started_at: str, finished_at: str, settings: dict,
          result: str, resumed: bool = False) -> dict:
    t = totals(events)
    return {
        "run_id": settings.get("run_id"),
        **_version.stamp(),
        "started_at": started_at,
        "finished_at": finished_at,
        "total_seconds": round((_parse_ts(finished_at) - _parse_ts(started_at)).total_seconds(), 3),
        "stage_seconds": stage_seconds(events, started_at),
        "claude_calls": t["calls"],
        "input_tokens": t["input_tokens"],
        "output_tokens": t["output_tokens"],
        "web_searches": t["web_searches"],
        "cost_usd": t["cost_usd"],
        "tts_chars": t["tts_chars"],
        "narrator": settings.get("narrator"),
        "quality": settings.get("quality"),
        "effort": settings.get("effort"),
        # Did research_agent run? Unknown for a resumed run (it may have run before).
        "research": None if resumed else any(e.get("node") == "research_agent" for e in events),
        "source": settings.get("source"),
        "resumed": resumed,
        "result": result,
    }


def write(run_dir: Path, stats: dict) -> Path | None:
    """Write run_stats.json. A resumed invocation (CLI --run-id) never replaces
    an existing record: it is appended to `later_invocations` instead."""
    if not run_dir.is_dir():
        return None
    path = run_dir / "run_stats.json"
    if stats.get("resumed"):
        existing = read(run_dir)
        if existing is not None:
            existing.setdefault("later_invocations", []).append(stats)
            stats = existing
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(stats, indent=2))
    tmp.replace(path)
    return path


def read(run_dir: Path) -> dict | None:
    try:
        data = json.loads((run_dir / "run_stats.json").read_text())
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def manifest_settings(run_dir: Path) -> dict:
    """quality / narrator / effort as recorded by render_trigger (None if absent)."""
    try:
        m = json.loads((run_dir / "manifest.json").read_text())
    except Exception:
        return {}
    return {k: m.get(k) for k in ("quality", "narrator", "effort")}


class Recorder:
    """Thread-safe event list for the CLI (the server keeps job.events)."""

    def __init__(self):
        self.started_at = now_iso()
        self.events: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, event: dict) -> None:
        event = {**event, "ts": event.get("ts") or now_iso()}
        with self._lock:
            self.events.append(event)

    def record(self, node: str, updates: dict) -> None:
        self({"node": node, "updates": updates})

    def record_graph(self, event: dict) -> None:
        for node, updates in event.items():
            if node != "__end__":
                self.record(node, updates if isinstance(updates, dict) else {})
