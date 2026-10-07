# server/version_routes.py
"""GET /version (pretty, for browsers) and GET /api/version (compact): what is
running here. Everything comes from a live source; unknown values are null.
No secrets or key values are ever included."""
from __future__ import annotations
import asyncio
import functools
import json
import platform
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from fastapi import APIRouter, Request
from fastapi.responses import Response

from pipeline import version as _ver

STARTED_AT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
_STARTED_MONO = time.monotonic()

ENDPOINTS = [
    {"path": "/version", "about": "this document (pretty JSON); /api/version is the same, compact"},
    {"path": "/api/meta", "about": "server defaults, narrators, render backend, model, upload rules"},
    {"path": "/api/status", "about": "live health checks (Claude, voices, renderer, GPU, disk, jobs)"},
    {"path": "/api/library", "about": "finished videos (paged)"},
    {"path": "/api/jobs", "about": "jobs in this server process; POST to start one"},
    {"path": "/openapi.json", "about": "machine-readable API schema"},
    {"path": "/docs", "about": "interactive API docs"},
]


def _manim_version() -> str | None:
    from importlib import metadata
    try:
        return metadata.version("manim")
    except metadata.PackageNotFoundError:
        return None


@functools.lru_cache(maxsize=1)
def _ffmpeg_version() -> str | None:
    if shutil.which("ffmpeg") is None:
        return None
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"ffmpeg version (\S+)", r.stdout or "")
    return m.group(1) if r.returncode == 0 and m else None


def _config() -> dict:
    import config as _cfg
    from pipeline import render as _render_backend
    try:
        backend = _render_backend.backend()
    except Exception:
        backend = None
    pace = next((getattr(_cfg, k) for k in ("DEFAULT_PACE", "PACE") if hasattr(_cfg, k)), None)
    return {
        "model": getattr(_cfg, "CLAUDE_MODEL", None),
        "render_backend": backend,
        "narrator": getattr(_cfg, "NARRATOR", "") or None,
        "pace": pace if pace not in ("",) else None,
    }


def version_info(request: Request | None = None) -> dict:
    server = request.scope.get("server") if request is not None else None
    host, port = (server[0], server[1]) if server else (None, None)
    return {
        "name": "chalkboard",
        "version": _ver.__version__,
        "git": _ver.git(),
        "server": {
            "started_at": STARTED_AT,
            "uptime_s": round(time.monotonic() - _STARTED_MONO, 1),
            "host": host,
            "hostname": socket.gethostname() or None,
            "port": port,
        },
        "changelog": _ver.latest_changelog(),
        "runtime": {
            "python": platform.python_version(),
            "manim": _manim_version(),
            "ffmpeg": _ffmpeg_version(),
        },
        "config": _config(),
        "endpoints": ENDPOINTS,
    }


def make_version_router() -> APIRouter:
    router = APIRouter()
    headers = {"Cache-Control": "no-store"}

    @router.get("/version")
    async def version_pretty(request: Request):
        data = await asyncio.to_thread(version_info, request)
        return Response(json.dumps(data, indent=2) + "\n", media_type="application/json", headers=headers)

    @router.get("/api/version")
    async def version_api(request: Request):
        data = await asyncio.to_thread(version_info, request)
        return Response(json.dumps(data), media_type="application/json", headers=headers)

    return router
