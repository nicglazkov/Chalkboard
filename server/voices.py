# server/voices.py
"""Narrator list and real voice samples (/api/voices, /api/voices/{id}/sample).

A sample is real TTS output of SAMPLE_TEXT from the narrator's own backend,
made on first request and cached under output/_voice_samples/. Every attempt
(success or failure) is recorded in <id>.attempt.json so /api/status can
report what actually happened. A failed attempt returns the reason; there is
never a stand-in file.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from server import status as status_mod

SAMPLE_TEXT = ("Hi, I'm your narrator. Let's see why the derivative of e to the x "
               "is e to the x, one step at a time.")
SAMPLES_DIR = status_mod.VOICE_SAMPLES_DIR

_locks: dict[str, asyncio.Lock] = {}


def _spec_key(spec: dict) -> str:
    raw = json.dumps([spec["backend"], spec["voice"], spec["model"], SAMPLE_TEXT])
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


def sample_path(output_dir: Path, narrator: str, spec: dict) -> Path:
    return output_dir / SAMPLES_DIR / f"{narrator}-{_spec_key(spec)}.wav"


def _availability(narrator: str, spec: dict, output_dir: Path) -> tuple[bool | None, str]:
    """(available, reason). True/False only on evidence; None when unknown."""
    backend = spec["backend"]
    last = status_mod.last_attempt(output_dir, narrator)
    if backend == "kokoro":
        if importlib.util.find_spec("kokoro") is None:
            return False, "kokoro package not installed"
        if last and not last.get("ok"):
            return False, f"last synthesis failed at {last.get('at')}: {str(last.get('error'))[:160]}"
        return True, "local model installed"
    if backend == "elevenlabs":
        ok, ev = status_mod.probe_elevenlabs_voice(spec["voice"])
        if ok is True and last and not last.get("ok") and last.get("key") == _spec_key(spec):
            return None, f"{ev}; but last synthesis failed at {last.get('at')}: {str(last.get('error'))[:160]}"
        return ok, ev
    if backend == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            return False, "OPENAI_API_KEY not set"
        if last:
            if last.get("ok"):
                return True, f"last synthesis succeeded at {last.get('at')}"
            return False, f"last synthesis failed at {last.get('at')}: {str(last.get('error'))[:160]}"
        return None, "key set; credit cannot be checked without spending"
    return None, f"unknown backend {backend!r}"


async def list_voices(output_dir: Path) -> list[dict]:
    from pipeline.tts.voices import NARRATORS

    async def one(nid: str, spec: dict) -> dict:
        available, reason = await asyncio.to_thread(_availability, nid, spec, output_dir)
        cached = sample_path(output_dir, nid, spec).exists()
        return {
            "id": nid, "label": spec["label"], "tagline": spec["tagline"],
            "backend": spec["backend"], "model": spec["model"] or None, "voice": spec["voice"],
            "available": available, "availability_reason": reason,
            "sample_cached": cached,
            # The sample endpoint synthesizes on first use; it is offered unless
            # the narrator is known to be unavailable.
            "sample_url": f"/api/voices/{nid}/sample" if (cached or available is not False) else None,
        }

    return list(await asyncio.gather(*(one(n, s) for n, s in NARRATORS.items())))


def _record_attempt(output_dir: Path, narrator: str, spec: dict, ok: bool, error: str | None) -> None:
    d = output_dir / SAMPLES_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{narrator}.attempt.json").write_text(json.dumps({
        "ok": ok, "error": error, "key": _spec_key(spec),
        "at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "backend": spec["backend"], "voice": spec["voice"], "model": spec["model"] or None,
    }))


async def get_sample(output_dir: Path, narrator: str) -> Path:
    """Path of the cached sample, synthesizing it first if needed.
    Raises KeyError for an unknown narrator, RuntimeError(reason) on failure."""
    from pipeline.tts.base import get_backend
    from pipeline.tts.voices import NARRATORS
    spec = NARRATORS[narrator]
    path = sample_path(output_dir, narrator, spec)
    if path.exists():
        return path
    lock = _locks.setdefault(narrator, asyncio.Lock())
    async with lock:
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp.wav")
        try:
            generate = get_backend(spec["backend"])
            await generate([{"text": SAMPLE_TEXT, "estimated_duration_sec": 6.0}], tmp,
                           voice=spec["voice"], model=spec["model"] or None)
            if not tmp.exists() or tmp.stat().st_size < 1000:
                raise RuntimeError("backend returned no audio")
            tmp.replace(path)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            reason = f"{type(e).__name__}: {e}"
            await asyncio.to_thread(_record_attempt, output_dir, narrator, spec, False, reason[:500])
            raise RuntimeError(reason[:500]) from e
        await asyncio.to_thread(_record_attempt, output_dir, narrator, spec, True, None)
        return path
