# server/status.py
"""/api/status: live health checks, each backed by a real probe.

Rules: every check says what it probed (`evidence`) and when (`checked_at`).
Nothing that costs money is called. A probe that cannot tell (timeout,
ambiguous error, no free endpoint) reports state "unknown" with the reason.
Results are cached for CACHE_TTL seconds.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

CACHE_TTL = 60.0
HTTP_TIMEOUT = 10.0
VOICE_SAMPLES_DIR = "_voice_samples"

_cache: dict = {"at": 0.0, "value": None}


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _check(id_: str, name: str, state: str, summary: str, evidence: str, detail: dict | None = None) -> dict:
    return {"id": id_, "name": name, "state": state, "summary": summary,
            "evidence": evidence, "detail": detail or {}, "checked_at": _now()}


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int | None, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr or "").strip()
    except FileNotFoundError:
        return None, "not found"
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:.0f}s"
    except Exception as e:  # pragma: no cover
        return None, str(e)


# ── Claude ────────────────────────────────────────────────────────────────────

def check_claude() -> dict:
    import anthropic
    from config import CLAUDE_MODEL
    name = "Claude API"
    if not os.getenv("ANTHROPIC_API_KEY"):
        return _check("claude", name, "down", "ANTHROPIC_API_KEY is not set",
                      "environment: ANTHROPIC_API_KEY missing")
    ev = f"GET /v1/models/{CLAUDE_MODEL} (free; no tokens)"
    try:
        client = anthropic.Anthropic(max_retries=0, timeout=HTTP_TIMEOUT)
        m = client.models.retrieve(CLAUDE_MODEL)
        return _check("claude", name, "ok", f"{CLAUDE_MODEL} reachable with our key",
                      f"{ev} -> 200", {"model": CLAUDE_MODEL, "display_name": getattr(m, "display_name", None)})
    except anthropic.AuthenticationError as e:
        return _check("claude", name, "down", "API key rejected", f"{ev} -> 401: {str(e)[:160]}")
    except anthropic.PermissionDeniedError as e:
        return _check("claude", name, "down", "key lacks permission for the model", f"{ev} -> 403: {str(e)[:160]}")
    except anthropic.NotFoundError as e:
        return _check("claude", name, "down", f"model {CLAUDE_MODEL} not found for this key", f"{ev} -> 404: {str(e)[:160]}")
    except anthropic.RateLimitError as e:
        return _check("claude", name, "warn", "rate limited right now", f"{ev} -> 429: {str(e)[:160]}")
    except anthropic.InternalServerError as e:
        return _check("claude", name, "warn", "API returned a server error", f"{ev} -> 5xx: {str(e)[:160]}")
    except anthropic.APITimeoutError:
        return _check("claude", name, "unknown", "probe timed out; cannot tell", f"{ev} -> timeout after {HTTP_TIMEOUT:.0f}s")
    except anthropic.APIConnectionError as e:
        return _check("claude", name, "down", "cannot connect to api.anthropic.com", f"{ev} -> connection error: {str(e)[:160]}")
    except Exception as e:
        return _check("claude", name, "unknown", "probe failed in an unexpected way", f"{ev} -> {type(e).__name__}: {str(e)[:160]}")


def check_claude_status_page() -> dict:
    """Anthropic's own status page (Statuspage JSON API)."""
    name = "Claude status page"
    url = "https://status.claude.com/api/v2/summary.json"
    try:
        r = httpx.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        return _check("claude_status", name, "unknown", "status page unreachable", f"GET {url} -> {type(e).__name__}: {str(e)[:120]}")
    comp = next((c for c in d.get("components", []) if "api" in (c.get("name") or "").lower()), None)
    comp_status = comp.get("status") if comp else None
    open_incidents = [{"name": i.get("name"), "status": i.get("status"), "impact": i.get("impact"),
                       "url": i.get("shortlink")} for i in d.get("incidents", [])]
    state = {"operational": "ok", "degraded_performance": "warn", "partial_outage": "warn",
             "major_outage": "down", "under_maintenance": "warn"}.get(comp_status, "unknown")
    summary = f"{comp.get('name')}: {comp_status}" if comp else "API component not listed"
    return _check("claude_status", name, state, summary,
                  f"GET {url} -> indicator {d.get('status', {}).get('indicator')!r}",
                  {"component": comp.get("name") if comp else None, "component_status": comp_status,
                   "open_incidents": open_incidents, "page": "https://status.claude.com"})


# ── TTS ───────────────────────────────────────────────────────────────────────

def last_attempt(output_dir: Path, narrator: str) -> dict | None:
    """Most recent real voice-sample attempt for a narrator (written by the
    voices route), so a known failure (e.g. no credit) is reported as seen."""
    p = output_dir / VOICE_SAMPLES_DIR / f"{narrator}.attempt.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def probe_elevenlabs_voice(voice_id: str) -> tuple[bool | None, str]:
    """(available, evidence) from GET /v1/voices/{id}: free, needs only voices_read."""
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return False, "ELEVENLABS_API_KEY not set"
    url = f"https://api.elevenlabs.io/v1/voices/{voice_id}"
    try:
        r = httpx.get(url, headers={"xi-api-key": key}, timeout=HTTP_TIMEOUT)
    except httpx.TimeoutException:
        return None, f"GET {url} -> timeout"
    except Exception as e:
        return None, f"GET {url} -> {type(e).__name__}"
    if r.status_code == 200:
        return True, f"GET {url} -> 200 ({r.json().get('name')})"
    if r.status_code in (401, 403, 404):
        return False, f"GET {url} -> {r.status_code}: {r.text[:160]}"
    return None, f"GET {url} -> {r.status_code}"


def check_elevenlabs() -> dict:
    from pipeline.tts.voices import NARRATORS
    name = "ElevenLabs"
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key:
        return _check("elevenlabs", name, "down", "ELEVENLABS_API_KEY is not set", "environment")
    headers = {"xi-api-key": key}
    evidence = []
    try:
        r = httpx.get("https://api.elevenlabs.io/v1/models", headers=headers, timeout=HTTP_TIMEOUT)
        evidence.append(f"GET /v1/models -> {r.status_code}")
        models_ok = r.status_code == 200
        model_ids = [m.get("model_id") for m in r.json()] if models_ok else []
    except httpx.TimeoutException:
        return _check("elevenlabs", name, "unknown", "probe timed out; cannot tell", "GET /v1/models -> timeout")
    except Exception as e:
        return _check("elevenlabs", name, "down", "cannot reach api.elevenlabs.io", f"GET /v1/models -> {type(e).__name__}")
    if r.status_code in (401, 403):
        return _check("elevenlabs", name, "down", "API key rejected", f"GET /v1/models -> {r.status_code}: {r.text[:160]}")
    voices = {}
    for nid, spec in NARRATORS.items():
        if spec["backend"] != "elevenlabs":
            continue
        ok, ev = probe_elevenlabs_voice(spec["voice"])
        voices[nid] = ok
        evidence.append(ev)
        if spec["model"] and model_ids and spec["model"] not in model_ids:
            voices[nid] = False
            evidence.append(f"model {spec['model']} not in /v1/models")
    # Plan usage needs the user_read permission; ask, and report why if refused.
    usage = None
    usage_reason = None
    try:
        s = httpx.get("https://api.elevenlabs.io/v1/user/subscription", headers=headers, timeout=HTTP_TIMEOUT)
        evidence.append(f"GET /v1/user/subscription -> {s.status_code}")
        if s.status_code == 200:
            sd = s.json()
            usage = {"character_count": sd.get("character_count"), "character_limit": sd.get("character_limit"),
                     "tier": sd.get("tier"), "next_reset_unix": sd.get("next_character_count_reset_unix")}
        else:
            try:
                usage_reason = s.json().get("detail", {}).get("message")
            except Exception:
                usage_reason = None
            usage_reason = usage_reason or f"HTTP {s.status_code}"
    except Exception as e:
        usage_reason = f"probe failed: {type(e).__name__}"
    states = list(voices.values())
    if models_ok and states and all(v is True for v in states):
        state, summary = "ok", "key accepted; narrator voices found"
    elif any(v is False for v in states):
        state, summary = "warn", "a narrator voice is not available to this key"
    else:
        state, summary = "unknown", "could not confirm the narrator voices"
    return _check("elevenlabs", name, state, summary, "; ".join(evidence),
                  {"voices": voices, "plan_usage": usage, "plan_usage_reason": usage_reason,
                   "note": "character credit cannot be checked without user_read; synthesis may still fail on quota"})


def check_openai(output_dir: Path) -> dict:
    name = "OpenAI TTS"
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return _check("openai", name, "down", "OPENAI_API_KEY is not set", "environment")
    try:
        r = httpx.get("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=HTTP_TIMEOUT)
        ev = f"GET /v1/models -> {r.status_code}"
    except httpx.TimeoutException:
        return _check("openai", name, "unknown", "probe timed out; cannot tell", "GET /v1/models -> timeout")
    except Exception as e:
        return _check("openai", name, "unknown", "probe failed", f"GET /v1/models -> {type(e).__name__}")
    if r.status_code in (401, 403):
        return _check("openai", name, "down", "API key rejected", ev)
    last = last_attempt(output_dir, "alloy")
    if last and not last.get("ok"):
        return _check("openai", name, "down", "last real synthesis failed",
                      f"{ev}; voice sample attempt at {last.get('at')} failed: {str(last.get('error'))[:200]}",
                      {"last_attempt": last})
    if last and last.get("ok"):
        return _check("openai", name, "ok", "key accepted; last real synthesis succeeded",
                      f"{ev}; voice sample attempt at {last.get('at')} succeeded", {"last_attempt": last})
    return _check("openai", name, "unknown",
                  "key accepted, but credit cannot be checked without spending",
                  f"{ev}; billing is not readable with an API key and no synthesis has been attempted")


def check_kokoro() -> dict:
    name = "Kokoro (local TTS)"
    if importlib.util.find_spec("kokoro") is None:
        return _check("kokoro", name, "down", "kokoro is not installed", "importlib.util.find_spec('kokoro') -> None")
    detail: dict = {}
    evidence = ["kokoro importable"]
    try:
        from pipeline.tts import kokoro_tts
        loaded = kokoro_tts._pipeline.cache_info().currsize > 0
        detail["model_loaded_in_server"] = loaded
        evidence.append(f"model {'loaded' if loaded else 'not yet loaded'} in this server process")
    except Exception as e:
        evidence.append(f"model state unknown ({type(e).__name__})")
    hub = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub" / "models--hexgrad--Kokoro-82M"
    detail["weights_cached"] = hub.exists()
    evidence.append(f"{hub} {'exists' if hub.exists() else 'missing'}")
    try:
        import torch
        cuda = bool(torch.cuda.is_available())
        detail["cuda"] = cuda
        evidence.append(f"torch.cuda.is_available() -> {cuda}")
    except Exception as e:
        detail["cuda"] = None
        evidence.append(f"torch check failed: {type(e).__name__}")
    if not hub.exists():
        return _check("kokoro", name, "warn", "weights not downloaded yet (first use downloads them)", "; ".join(evidence), detail)
    return _check("kokoro", name, "ok",
                  "installed, weights cached" + (", CUDA available" if detail.get("cuda") else ""),
                  "; ".join(evidence), detail)


# ── Renderer, GPU, disk, jobs ─────────────────────────────────────────────────

def check_renderer() -> dict:
    from pipeline import render as render_backend
    name = "Renderer"
    backend = render_backend.backend()
    detail: dict = {"backend": backend}
    evidence = [f"render.backend() -> {backend}"]
    missing = []
    if backend == "local":
        try:
            from importlib.metadata import version
            detail["manim"] = version("manim")
            evidence.append(f"manim {detail['manim']}")
        except Exception:
            missing.append("manim")
        tools = {"latex": ["latex", "--version"], "dvisvgm": ["dvisvgm", "--version"], "ffmpeg": ["ffmpeg", "-version"]}
    else:
        tools = {"docker": ["docker", "--version"], "ffmpeg": ["ffmpeg", "-version"]}
    for tool, cmd in tools.items():
        if shutil.which(tool) is None:
            missing.append(tool)
            evidence.append(f"{tool}: not on PATH")
            continue
        rc, out = _run(cmd)
        first = out.splitlines()[0] if out else ""
        detail[tool] = first[:120]
        evidence.append(f"{' '.join(cmd)} -> {first[:80]}" if rc == 0 else f"{' '.join(cmd)} -> {out[:80]}")
        if rc != 0:
            missing.append(tool)
    if missing:
        return _check("renderer", name, "down", f"missing or broken: {', '.join(missing)}", "; ".join(evidence), detail)
    return _check("renderer", name, "ok", f"{backend} backend, tools found", "; ".join(evidence), detail)


def check_gpu() -> dict:
    name = "GPU"
    if shutil.which("nvidia-smi") is None:
        return _check("gpu", name, "unknown", "nvidia-smi not found; GPU state unknown", "shutil.which('nvidia-smi') -> None")
    cmd = ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
           "--format=csv,noheader,nounits"]
    rc, out = _run(cmd)
    if rc != 0 or not out:
        return _check("gpu", name, "unknown", "nvidia-smi failed", f"{' '.join(cmd[:2])} -> {out[:120]}")
    gpus = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 5:
            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return None
            gpus.append({"name": parts[0], "memory_used_mib": num(parts[1]), "memory_total_mib": num(parts[2]),
                         "utilization_pct": num(parts[3]), "temperature_c": num(parts[4])})
    if not gpus:
        return _check("gpu", name, "unknown", "could not parse nvidia-smi output", out[:120])
    g = gpus[0]
    return _check("gpu", name, "ok",
                  f"{g['name']}: {g['memory_used_mib']:.0f}/{g['memory_total_mib']:.0f} MiB, {g['utilization_pct']:.0f}% busy",
                  "nvidia-smi --query-gpu", {"gpus": gpus})


def check_disk(output_dir: Path) -> dict:
    name = "Disk (output)"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        u = shutil.disk_usage(output_dir)
    except Exception as e:
        return _check("disk", name, "unknown", "could not read disk usage", f"shutil.disk_usage -> {type(e).__name__}")
    gb = u.free / 1e9
    state = "down" if gb < 1 else "warn" if gb < 10 else "ok"
    return _check("disk", name, state, f"{gb:.1f} GB free of {u.total / 1e9:.0f} GB",
                  f"shutil.disk_usage({output_dir})",
                  {"free_bytes": u.free, "total_bytes": u.total, "used_bytes": u.used,
                   "thresholds": "down < 1 GB, warn < 10 GB"})


def check_jobs(store) -> dict:
    import os as _os
    jobs = store.list()
    running = sum(1 for j in jobs if j.status == "running")
    pending = sum(1 for j in jobs if j.status == "pending")
    slots = int(_os.getenv("MAX_CONCURRENT_JOBS", "3"))
    return _check("jobs", "Jobs", "ok", f"{running} running, {pending} queued ({slots} slots)",
                  "in-memory job store of this server process",
                  {"running": running, "queued": pending, "slots": slots})


# ── Aggregate ─────────────────────────────────────────────────────────────────

CRITICAL = ("claude", "renderer", "disk")


def overall(checks: list[dict]) -> str:
    """down if a critical check is down; degraded if anything is down/warn;
    unknown if a critical check is unknown; else ok. Non-critical unknowns
    (e.g. a narrator whose credit cannot be read) do not make the whole
    service unknown; they stay visible on their own row."""
    by = {c["id"]: c["state"] for c in checks}
    if any(by.get(k) == "down" for k in CRITICAL):
        return "down"
    if any(s in ("down", "warn") for s in by.values()):
        return "degraded"
    if any(by.get(k) == "unknown" for k in CRITICAL):
        return "unknown"
    return "ok"


async def status(store, output_dir: Path, refresh: bool = False) -> dict:
    if not refresh and _cache["value"] and time.monotonic() - _cache["at"] < CACHE_TTL:
        cached = dict(_cache["value"])
        # Job counts are in-process and free to read: always current.
        cached["checks"] = [check_jobs(store) if c["id"] == "jobs" else c for c in cached["checks"]]
        cached["cached"] = True
        return cached
    probes = [
        asyncio.to_thread(check_claude),
        asyncio.to_thread(check_claude_status_page),
        asyncio.to_thread(check_elevenlabs),
        asyncio.to_thread(check_openai, output_dir),
        asyncio.to_thread(check_kokoro),
        asyncio.to_thread(check_renderer),
        asyncio.to_thread(check_gpu),
        asyncio.to_thread(check_disk, output_dir),
    ]
    results = await asyncio.gather(*probes, return_exceptions=True)
    ids = ["claude", "claude_status", "elevenlabs", "openai", "kokoro", "renderer", "gpu", "disk"]
    checks = []
    for cid, r in zip(ids, results):
        if isinstance(r, Exception):
            checks.append(_check(cid, cid, "unknown", "probe crashed", f"{type(r).__name__}: {str(r)[:160]}"))
        else:
            checks.append(r)
    checks.append(check_jobs(store))
    value = {"checked_at": _now(), "overall": overall(checks), "checks": checks, "cache_ttl_s": CACHE_TTL}
    _cache.update(at=time.monotonic(), value=value)
    return {**value, "cached": False}
