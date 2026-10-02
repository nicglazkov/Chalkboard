# server/insights.py
"""Dashboard numbers (/api/stats) and time/cost estimates (/api/estimate).

Computed only from the library index and per-run files (run_stats.json,
layout_report.json, qa_report.json). Nothing is assumed for runs that lack a
record: they are left out and the counts say how many runs a number rests on.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline import run_stats
from server import run_data

MIN_RUNS_FOR_ESTIMATE = 3


def _parse_created(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _finished_records(output_dir: Path, run_ids: list[str]) -> list[dict]:
    """run_stats records of complete, non-resumed, successful runs."""
    out = []
    for rid in run_ids:
        rs = run_stats.read(output_dir / rid)
        if not rs or rs.get("result") != "done" or rs.get("resumed"):
            continue
        if not isinstance(rs.get("total_seconds"), (int, float)):
            continue
        out.append(rs)
    return out


def stats(videos: list, output_dir: Path) -> dict:
    now = datetime.now(tz=timezone.utc)
    week_ago = now - timedelta(days=7)
    created = [_parse_created(v.created_at) for v in videos]
    this_week = sum(1 for c in created if c is not None and c >= week_ago)

    lags: list[float] = []
    sync_runs = 0
    qa_total = qa_passed = 0
    for v in videos:
        q = run_data.quality(output_dir / v.run_id)
        if q["sync"]:
            sync_runs += 1
            lags.extend(c["lag_s"] for c in q["sync"]["cues"])
        if q["visual_qa"] and isinstance(q["visual_qa"].get("passed"), bool):
            qa_total += 1
            qa_passed += q["visual_qa"]["passed"]

    records = _finished_records(output_dir, [v.run_id for v in videos])
    secs = [r["total_seconds"] for r in records]
    return {
        "videos_total": len(videos),
        # Rolling 7 days by created_at (completion time / final.mp4 time).
        "videos_this_week": this_week,
        "median_sync_lag_s": round(statistics.median(lags), 3) if lags else None,
        "sync_runs": sync_runs,
        "sync_cues": len(lags),
        "qa_pass_rate": round(qa_passed / qa_total, 4) if qa_total else None,
        "qa_runs": qa_total,
        "median_run_seconds": round(statistics.median(secs), 1) if secs else None,
        "timed_runs": len(secs),
        "computed_at": now.isoformat(timespec="seconds"),
    }


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile of measured values (same as numpy's default)."""
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def estimate(output_dir: Path, run_ids: list[str], *, effort: str | None, quality: str | None,
             narrator: str | None, research: bool | None) -> dict:
    records = _finished_records(output_dir, run_ids)

    def match(r: dict, exact: bool) -> bool:
        if quality and r.get("quality") != quality:
            return False
        if not exact:
            return True
        if effort and r.get("effort") != effort:
            return False
        if narrator and r.get("narrator") != narrator:
            return False
        if research is not None and r.get("research") != research:
            return False
        return True

    basis = "exact"
    chosen = [r for r in records if match(r, True)]
    if len(chosen) < MIN_RUNS_FOR_ESTIMATE:
        basis = "same_quality"
        chosen = [r for r in records if match(r, False)]
    if len(chosen) < MIN_RUNS_FOR_ESTIMATE:
        return {"based_on_runs": len(chosen), "basis": None, "median_seconds": None,
                "p25_seconds": None, "p75_seconds": None, "median_cost_usd": None,
                "cost_runs": 0}
    secs = [r["total_seconds"] for r in chosen]
    costs = [r["cost_usd"] for r in chosen if isinstance(r.get("cost_usd"), (int, float))]
    return {
        "based_on_runs": len(chosen),
        "basis": basis,
        "median_seconds": round(statistics.median(secs), 1),
        "p25_seconds": round(_quantile(secs, 0.25), 1),
        "p75_seconds": round(_quantile(secs, 0.75), 1),
        "median_cost_usd": round(statistics.median(costs), 4) if len(costs) >= MIN_RUNS_FOR_ESTIMATE else None,
        "cost_runs": len(costs),
    }
