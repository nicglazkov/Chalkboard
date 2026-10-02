"""UI data endpoints: timeline, waveform, quality, stats, estimate, status, voices, job totals."""
import asyncio
import json
import os
import time
import wave
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import run_data, status as status_mod
from server.jobs import JobStore
from server.library import SQLiteLibraryStore, VideoMeta


def _write_wav(path: Path, seconds=1.0, rate=8000):
    t = np.arange(int(seconds * rate)) / rate
    amp = np.where(t < seconds / 2, 0.2, 0.8)           # quiet first half, loud second
    pcm = (np.sin(2 * np.pi * 220 * t) * amp * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def _make_run(out: Path, rid: str, *, stats: dict | None = None, report: dict | None = None,
              qa: dict | None = None, final=True):
    d = out / rid
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"run_id": rid, "topic": "t", "quality": "low",
                                                 "narrator": "kokoro", "effort": "low"}))
    (d / "segments.json").write_text(json.dumps([
        {"text": "First segment text.", "actual_duration_sec": 0.4, "cues": [0.1]},
        {"text": "Second one.", "actual_duration_sec": 0.6, "cues": [0.2, 0.4]},
    ]))
    _write_wav(d / "voiceover.wav")
    time.sleep(0.01)
    if report is not None:
        (d / "layout_report.json").write_text(json.dumps(report))
    time.sleep(0.01)
    if final:
        (d / "final.mp4").write_bytes(b"\0" * 10)
    if stats is not None:
        (d / "run_stats.json").write_text(json.dumps(stats))
    if qa is not None:
        (d / "qa_report.json").write_text(json.dumps(qa))
    return d


@pytest.fixture
def env(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr("server.routes.OUTPUT_DIR", str(out))
    from server.routes import make_router
    from server.library_routes import make_library_router
    lib = SQLiteLibraryStore(str(tmp_path / "lib.db"))
    asyncio.run(lib.init())
    store = JobStore()
    app = FastAPI()
    app.include_router(make_router(store, lib))
    app.include_router(make_library_router(lib, output_dir=out))
    return TestClient(app), out, lib, store


# ── timeline + waveform ───────────────────────────────────────────────────────

def test_library_timeline_measured(env):
    client, out, lib, _ = env
    _make_run(out, "r1")
    tl = client.get("/api/library/r1/timeline").json()
    assert tl["duration_s"] == pytest.approx(1.0, abs=0.01)
    assert [s["start_s"] for s in tl["segments"]] == [0.0, 0.4]
    assert tl["segments"][1]["cues"] == [0.2, 0.4]
    assert tl["rendered_segments"] == 2
    wf = tl["waveform"]
    assert len(wf) == 400 and max(wf) == 1.0
    assert np.mean(wf[:200]) < 0.5 < np.mean(wf[200:])   # real envelope: quiet then loud
    assert (out / "r1" / "waveform.json").exists()


def test_waveform_cache_invalidated_when_wav_changes(tmp_path):
    _write_wav(tmp_path / "voiceover.wav", seconds=1.0)
    a = run_data.waveform(tmp_path, buckets=10)
    time.sleep(0.01)
    _write_wav(tmp_path / "voiceover.wav", seconds=2.0)
    b = run_data.waveform(tmp_path, buckets=10)
    assert a is not None and b is not None
    assert json.loads((tmp_path / "waveform.json").read_text())["key"]["size"] == (tmp_path / "voiceover.wav").stat().st_size


def test_timeline_before_tts_has_null_durations(tmp_path):
    seg = [{"text": "Only text", "estimated_duration_sec": 5.0}]
    events = [{"node": "script_agent", "updates": {"script_segments": seg}, "ts": "x"}]
    tl = run_data.job_timeline(tmp_path / "missing", events)
    assert tl["duration_s"] is None and tl["waveform"] is None
    assert tl["segments"][0]["duration_s"] is None and tl["segments"][0]["start_s"] is None


def test_partial_durations_stop_start_times():
    tl = run_data.segments_timeline([{"text": "a", "actual_duration_sec": 1.0}, {"text": "b"}, {"text": "c"}])
    assert [s["start_s"] for s in tl] == [0.0, 1.0, None]


def test_rendered_segments_from_events():
    ev = [{"node": "render", "updates": {"status": "running", "segment": 2, "segments": 5}}]
    assert run_data._rendered_from_events(ev) == 2
    ev.append({"node": "render", "updates": {"status": "done"}})
    assert run_data._rendered_from_events(ev) == 5
    assert run_data._rendered_from_events([]) is None


def test_job_timeline_404(env):
    client, *_ = env
    assert client.get("/api/jobs/nope/timeline").status_code == 404


# ── quality ───────────────────────────────────────────────────────────────────

CUE_LOG = [{"segment": 0, "cue": 1, "spoken_at": 1.0, "visual_at": 1.0, "lag": 0.0},
           {"segment": 1, "cue": 1, "spoken_at": 3.0, "visual_at": 3.5, "lag": 0.5},
           {"segment": 1, "cue": 2, "spoken_at": 4.0, "visual_at": 4.1, "lag": 0.1}]


def test_quality_from_render_report_and_qa(env):
    client, out, *_ = env
    _make_run(out, "r1", report={"passed": True, "violations": [], "cue_log": CUE_LOG, "mode": "render"},
              qa={"passed": False, "issues": [{"severity": "warning", "description": "x"}],
                  "checked_at": "2026-10-02T00:00:00+00:00", "history": [{}, {}]})
    q = client.get("/api/library/r1/quality").json()
    assert q["sync"]["median_lag_s"] == 0.1 and q["sync"]["worst_lag_s"] == 0.5
    assert q["sync"]["source"] == "render" and len(q["sync"]["cues"]) == 3
    assert q["layout"]["passed"] is True
    assert q["visual_qa"]["passed"] is False and q["visual_qa"]["attempts"] == 2


def test_quality_ignores_dry_run_cue_log(env):
    client, out, *_ = env
    _make_run(out, "r1", report={"passed": True, "violations": [], "cue_log": CUE_LOG, "mode": "dry_run"})
    q = client.get("/api/library/r1/quality").json()
    assert q["sync"] is None
    assert q["layout"]["source"] == "dry_run"
    assert q["visual_qa"] is None


def test_quality_old_report_attributed_by_file_times(env):
    client, out, *_ = env
    d = _make_run(out, "r1", report={"passed": True, "violations": [], "cue_log": CUE_LOG})
    assert client.get("/api/library/r1/quality").json()["sync"]["source"] == "render"
    # A report newer than final.mp4 (a later dry-run) cannot be attributed.
    os.utime(d / "layout_report.json", (time.time() + 60, time.time() + 60))
    assert client.get("/api/library/r1/quality").json()["sync"] is None


def test_quality_missing_everything_is_null(env):
    client, out, *_ = env
    (out / "bare").mkdir()
    assert client.get("/api/library/bare/quality").json() == {"sync": None, "layout": None, "visual_qa": None}
    assert client.get("/api/library/bare/timeline").status_code == 404


# ── library list items ────────────────────────────────────────────────────────

def test_library_items_expose_files_and_nulls(env):
    client, out, lib, _ = env
    _make_run(out, "with", stats={"result": "done", "total_seconds": 100.0, "cost_usd": 0.5})
    _make_run(out, "without")
    asyncio.run(lib.add_video(VideoMeta(run_id="with", topic="a", created_at="2026-10-01T00:00:00Z")))
    asyncio.run(lib.add_video(VideoMeta(run_id="without", topic="b", created_at="2026-10-01T00:00:00Z")))
    items = {v["run_id"]: v for v in client.get("/api/library").json()["videos"]}
    assert items["with"]["has_run_stats"] is True and items["with"]["cost_usd"] == 0.5
    assert items["without"]["has_run_stats"] is False
    assert items["without"]["cost_usd"] is None and items["without"]["run_seconds"] is None
    assert items["without"]["thumb_url"] is None
    assert items["with"]["video_url"] == "/api/jobs/with/files/final.mp4"


def test_backfill_leaves_unrecorded_fields_null(tmp_path):
    from server.app import _backfill
    out = tmp_path / "out"
    d = out / "old"
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"topic": "old run"}))
    (d / "final.mp4").write_bytes(b"\0")
    lib = SQLiteLibraryStore(str(tmp_path / "l.db"))
    asyncio.run(lib.init())
    asyncio.run(_backfill(lib, out))
    v = asyncio.run(lib.get_video("old"))
    assert v.duration_sec is None and v.quality is None and v.effort is None and v.speed is None
    # refresh corrects a row an older version filled with defaults
    asyncio.run(lib.add_video(v.model_copy(update={"duration_sec": 0.0, "quality": "medium"})))
    asyncio.run(_backfill(lib, out, refresh=True))
    v = asyncio.run(lib.get_video("old"))
    assert v.duration_sec is None and v.quality is None


# ── stats + estimate ──────────────────────────────────────────────────────────

def _stats(sec, cost=0.1, **kw):
    return {"result": "done", "resumed": False, "total_seconds": sec, "cost_usd": cost,
            "quality": "low", "effort": "low", "narrator": "kokoro", "research": False, **kw}


def test_stats_endpoint(env):
    client, out, lib, _ = env
    _make_run(out, "a", stats=_stats(100), report={"passed": True, "violations": [], "cue_log": CUE_LOG, "mode": "render"},
              qa={"passed": True, "issues": [], "checked_at": "x"})
    _make_run(out, "b")
    for rid in ("a", "b"):
        asyncio.run(lib.add_video(VideoMeta(run_id=rid, topic=rid, created_at="2020-01-01T00:00:00Z")))
    s = client.get("/api/stats").json()
    assert s["videos_total"] == 2 and s["videos_this_week"] == 0
    assert s["sync_runs"] == 1 and s["median_sync_lag_s"] == 0.1
    assert s["qa_runs"] == 1 and s["qa_pass_rate"] == 1.0
    assert s["timed_runs"] == 1 and s["median_run_seconds"] == 100.0


def test_stats_empty_library_is_null(env):
    client, *_ = env
    s = client.get("/api/stats").json()
    assert s["videos_total"] == 0
    assert s["median_sync_lag_s"] is None and s["qa_pass_rate"] is None and s["median_run_seconds"] is None


def test_estimate_needs_three_runs(env):
    client, out, *_ = env
    _make_run(out, "a", stats=_stats(100))
    _make_run(out, "b", stats=_stats(200))
    e = client.get("/api/estimate?effort=low&quality=low&narrator=kokoro").json()
    assert e["based_on_runs"] == 2 and e["median_seconds"] is None and e["median_cost_usd"] is None


def test_estimate_exact_then_quality_fallback(env):
    client, out, *_ = env
    for i, sec in enumerate([100, 200, 300, 400]):
        _make_run(out, f"r{i}", stats=_stats(sec, cost=0.1 * (i + 1)))
    _make_run(out, "failed", stats=_stats(5, result="failed"))
    _make_run(out, "resumed", stats=_stats(5, resumed=True))
    e = client.get("/api/estimate?effort=low&quality=low&narrator=kokoro").json()
    assert e["basis"] == "exact" and e["based_on_runs"] == 4
    assert e["median_seconds"] == 250.0 and e["p25_seconds"] == 175.0 and e["p75_seconds"] == 325.0
    assert e["median_cost_usd"] == pytest.approx(0.25)
    e = client.get("/api/estimate?effort=high&quality=low").json()
    assert e["basis"] == "same_quality" and e["based_on_runs"] == 4
    e = client.get("/api/estimate?quality=4k").json()
    assert e["based_on_runs"] == 0 and e["median_seconds"] is None


# ── status ────────────────────────────────────────────────────────────────────

def test_claude_check_unknown_on_timeout(monkeypatch):
    import anthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    class _C:
        def __init__(self, **kw):
            self.models = self

        def retrieve(self, model):
            raise anthropic.APITimeoutError(request=httpx.Request("GET", "https://x"))

    monkeypatch.setattr(anthropic, "Anthropic", _C)
    c = status_mod.check_claude()
    assert c["state"] == "unknown" and "timeout" in c["evidence"]


def test_claude_check_down_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert status_mod.check_claude()["state"] == "down"


def test_status_page_unreachable_is_unknown(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectTimeout("t")
    monkeypatch.setattr(status_mod.httpx, "get", boom)
    assert status_mod.check_claude_status_page()["state"] == "unknown"


def test_elevenlabs_timeout_is_unknown_and_plan_reason_reported(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")

    def timeout(*a, **k):
        raise httpx.ReadTimeout("t")
    monkeypatch.setattr(status_mod.httpx, "get", timeout)
    assert status_mod.check_elevenlabs()["state"] == "unknown"

    def fake(url, **k):
        req = httpx.Request("GET", url)
        if url.endswith("/v1/models"):
            return httpx.Response(200, json=[{"model_id": "eleven_v4"}], request=req)
        if "/v1/voices/" in url:
            return httpx.Response(200, json={"name": "Skye"}, request=req)
        return httpx.Response(401, json={"detail": {"message": "missing the permission user_read"}}, request=req)
    monkeypatch.setattr(status_mod.httpx, "get", fake)
    c = status_mod.check_elevenlabs()
    assert c["state"] == "ok"
    assert c["detail"]["plan_usage"] is None
    assert "user_read" in c["detail"]["plan_usage_reason"]


def test_openai_without_attempt_is_unknown(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(status_mod.httpx, "get",
                        lambda url, **k: httpx.Response(200, json={}, request=httpx.Request("GET", url)))
    c = status_mod.check_openai(tmp_path)
    assert c["state"] == "unknown" and "credit" in c["summary"]
    d = tmp_path / status_mod.VOICE_SAMPLES_DIR
    d.mkdir()
    (d / "alloy.attempt.json").write_text(json.dumps({"ok": False, "error": "insufficient_quota", "at": "t"}))
    c = status_mod.check_openai(tmp_path)
    assert c["state"] == "down" and "insufficient_quota" in c["evidence"]


def test_gpu_unknown_without_nvidia_smi(monkeypatch):
    monkeypatch.setattr(status_mod.shutil, "which", lambda name: None)
    assert status_mod.check_gpu()["state"] == "unknown"


def test_overall_rules():
    mk = lambda i, s: {"id": i, "state": s}
    assert status_mod.overall([mk("claude", "ok"), mk("renderer", "ok"), mk("disk", "ok")]) == "ok"
    assert status_mod.overall([mk("claude", "unknown"), mk("renderer", "ok"), mk("disk", "ok")]) == "unknown"
    assert status_mod.overall([mk("claude", "ok"), mk("renderer", "ok"), mk("disk", "ok"), mk("openai", "down")]) == "degraded"
    assert status_mod.overall([mk("claude", "down"), mk("renderer", "ok"), mk("disk", "ok")]) == "down"


def test_status_endpoint_shape_and_cache(env, monkeypatch):
    client, *_ = env
    ok = lambda cid: (lambda *a: status_mod._check(cid, cid, "ok", "s", "probe"))
    for name, cid in [("check_claude", "claude"), ("check_claude_status_page", "claude_status"),
                      ("check_elevenlabs", "elevenlabs"), ("check_openai", "openai"), ("check_kokoro", "kokoro"),
                      ("check_renderer", "renderer"), ("check_gpu", "gpu"), ("check_disk", "disk")]:
        monkeypatch.setattr(status_mod, name, ok(cid))
    monkeypatch.setattr(status_mod, "_cache", {"at": 0.0, "value": None})
    s = client.get("/api/status").json()
    assert s["overall"] == "ok" and s["cached"] is False
    assert {c["id"] for c in s["checks"]} >= {"claude", "renderer", "disk", "jobs"}
    assert all(c["evidence"] and c["checked_at"] for c in s["checks"])
    assert client.get("/api/status").json()["cached"] is True


# ── voices ────────────────────────────────────────────────────────────────────

def test_voice_sample_failure_is_503_with_reason(env, monkeypatch):
    client, out, *_ = env

    async def broken(*a, **k):
        raise RuntimeError("insufficient_quota: no credit")
    monkeypatch.setattr("pipeline.tts.base.get_backend", lambda name: broken)
    r = client.get("/api/voices/alloy/sample")
    assert r.status_code == 503 and "insufficient_quota" in r.json()["detail"]
    assert not list((out / "_voice_samples").glob("*.wav"))
    attempt = json.loads((out / "_voice_samples" / "alloy.attempt.json").read_text())
    assert attempt["ok"] is False
    assert client.get("/api/voices/nobody/sample").status_code == 404


def test_voice_sample_generated_once_and_cached(env, monkeypatch):
    client, out, *_ = env
    calls = []

    async def fake(segments, path, speed=1.0, *, voice=None, model=None):
        calls.append(segments[0]["text"])
        _write_wav(path)
        return path, [1.0]
    monkeypatch.setattr("pipeline.tts.base.get_backend", lambda name: fake)
    r1 = client.get("/api/voices/kokoro/sample")
    r2 = client.get("/api/voices/kokoro/sample")
    assert r1.status_code == r2.status_code == 200
    assert r1.headers["content-type"] == "audio/wav"
    assert len(calls) == 1


def test_voices_list(env, monkeypatch):
    client, out, *_ = env
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(status_mod, "probe_elevenlabs_voice", lambda v: (None, "GET ... -> timeout"))
    voices = {v["id"]: v for v in client.get("/api/voices").json()}
    assert voices["alloy"]["available"] is False and voices["alloy"]["sample_url"] is None
    assert voices["aria"]["available"] is None and voices["aria"]["sample_url"] == "/api/voices/aria/sample"


# ── job resource ──────────────────────────────────────────────────────────────

def test_job_totals_and_peek_compaction(env):
    client, out, lib, store = env
    job = store.create(topic="t", effort="low", audience="beginner", tone="casual", theme="chalkboard",
                       template=None, speed=1.0)
    job.append_event({"node": "usage", "updates": {"input_tokens": 10, "output_tokens": 5, "web_searches": 0,
                                                   "cost_usd": 0.001}})
    for i in range(5):
        job.append_event({"node": "peek", "updates": {"stage": "script", "text": "x" * i, "done": False}})
    job.append_event({"node": "peek", "updates": {"stage": "fact_check", "text": "ok", "done": True}})
    j = client.get(f"/api/jobs/{job.id}").json()
    assert j["totals"]["input_tokens"] == 10 and j["totals"]["cost_usd"] == 0.001
    assert j["totals"]["tts_chars"] is None
    peeks = [e for e in j["events"] if e["node"] == "peek"]
    assert [p["updates"]["stage"] for p in peeks] == ["script", "fact_check"]
    assert peeks[0]["updates"]["text"] == "xxxx"


def test_event_stream_fans_out_to_every_subscriber():
    store = JobStore()
    job = store.create(topic="t", effort="low", audience="beginner", tone="casual", theme="chalkboard",
                       template=None, speed=1.0)

    async def main():
        job.append_event({"node": "init", "updates": {}})
        a = job.event_stream()
        b = job.event_stream(replay=False)
        first_a = await a.__anext__()          # replayed backlog
        assert first_a["node"] == "init"
        nb = asyncio.ensure_future(b.__anext__())
        await asyncio.sleep(0)
        job.append_event({"node": "script_agent", "updates": {}})
        assert (await a.__anext__())["node"] == "script_agent"
        assert (await nb)["node"] == "script_agent"

    asyncio.run(main())
