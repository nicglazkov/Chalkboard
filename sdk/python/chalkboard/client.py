"""Sync client for the Chalkboard public API.

Wraps `httpx.Client` with auth + idempotency + structured-error
plumbing. Only sync for now — async wrappers are easy to add (httpx
already supports both surfaces) but aren't shipped in v0.1 to keep
the SDK small.

Example:

    from chalkboard import ChalkboardClient

    client = ChalkboardClient(api_key="chk_live_…")
    job = client.create_job(topic="How hash tables work")
    final = client.wait_for_completion(job.id, timeout=600)
    client.download_file(final.id, "final.mp4", out_path="hash-tables.mp4")
"""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import httpx

from .exceptions import ChalkboardError, from_response
from .models import ApiKeyMeta, JobResponse, VideoMeta, WebhookMeta


DEFAULT_BASE_URL = "https://chalkboard.studio/api/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_POLL_INTERVAL_SECONDS = 5.0


@dataclass
class ChalkboardConfig:
    """Knobs for the client. Most users only ever touch `api_key`."""
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    user_agent: str = f"chalkboard-python/0.1.1"


class ChalkboardClient:
    """Sync HTTP client for the Chalkboard API.

    Creates one underlying `httpx.Client` per instance and reuses the
    connection pool. Safe to share across threads — `httpx.Client` is
    thread-safe.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        config: ChalkboardConfig | None = None,
    ):
        if config is None:
            if not api_key:
                raise ValueError("api_key (or config) is required")
            config = ChalkboardConfig(
                api_key=api_key, base_url=base_url, timeout=timeout,
            )
        self.config = config
        self._http = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "User-Agent": config.user_agent,
            },
        )

    # ── Lifecycle ───────────────────────────────────────────────────────────
    def close(self) -> None:
        """Release the underlying connection pool. Optional — Python's
        garbage collector calls __del__ on the http client which closes
        too. But explicit is friendlier in long-running processes."""
        self._http.close()

    def __enter__(self) -> "ChalkboardClient":
        return self

    def __exit__(self, *a) -> None:
        self.close()

    # ── Internal request helper ─────────────────────────────────────────────
    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
        idempotency_key: str | None = None,
        stream: bool = False,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        resp = self._http.request(
            method, path, json=json, params=params, headers=headers or None,
        )
        if resp.status_code >= 400:
            raise from_response(resp)
        return resp

    # ── Jobs ────────────────────────────────────────────────────────────────
    def create_job(
        self,
        *,
        topic: str,
        effort: str = "medium",
        audience: str = "intermediate",
        tone: str = "casual",
        theme: str = "chalkboard",
        template: str | None = None,
        speed: float = 1.0,
        burn_captions: bool = False,
        quiz: bool = False,
        urls: list[str] | None = None,
        github: list[str] | None = None,
        qa_density: str = "normal",
        email_on_complete: bool = True,
        quality: str = "medium",
        model: str | None = None,
        idempotency_key: str | None = None,
    ) -> JobResponse:
        """Submit a new job.

        Pass `idempotency_key` if you might retry — a UUID v4 is fine.
        Reusing the same key with the same body within 24 hours replays
        the original response (no duplicate paid job); reusing it with
        a different body raises ChalkboardConflictError.
        """
        body: dict[str, Any] = {
            "topic": topic,
            "effort": effort, "audience": audience, "tone": tone,
            "theme": theme, "speed": speed,
            "burn_captions": burn_captions, "quiz": quiz,
            "urls": list(urls or []), "github": list(github or []),
            "qa_density": qa_density,
            "email_on_complete": email_on_complete,
            "quality": quality,
        }
        if template is not None:
            body["template"] = template
        if model is not None:
            body["model"] = model
        resp = self._request("POST", "/jobs", json=body, idempotency_key=idempotency_key)
        return JobResponse.from_dict(resp.json())

    def get_job(self, job_id: str) -> JobResponse:
        resp = self._request("GET", f"/jobs/{job_id}")
        return JobResponse.from_dict(resp.json())

    def list_jobs(self) -> list[JobResponse]:
        resp = self._request("GET", "/jobs")
        return [JobResponse.from_dict(d) for d in resp.json()]

    def cancel_job(self, job_id: str) -> dict:
        """Request cancellation. Returns the API's `{"status": "cancelling"}`
        envelope or `{"status": "<terminal>"}` if the job already finished."""
        resp = self._request("DELETE", f"/jobs/{job_id}")
        return resp.json()

    def retry_job(
        self, job_id: str, *, idempotency_key: str | None = None,
    ) -> JobResponse:
        """Re-run a terminal job with the same parameters. Source must
        be in completed/failed/cancelled. Test-mode follows THIS request's
        auth, not the source's mode."""
        resp = self._request(
            "POST", f"/jobs/{job_id}/retry", idempotency_key=idempotency_key,
        )
        return JobResponse.from_dict(resp.json())

    def rerender_job(
        self, job_id: str, *, idempotency_key: str | None = None,
    ) -> JobResponse:
        """Reuse the source's script + voiceover; regenerate the Manim
        scene only. Cheaper than retry — no script/research/TTS cost."""
        resp = self._request(
            "POST", f"/jobs/{job_id}/rerender", idempotency_key=idempotency_key,
        )
        return JobResponse.from_dict(resp.json())

    def wait_for_completion(
        self,
        job_id: str,
        *,
        timeout: float = 600.0,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> JobResponse:
        """Block until the job reaches a terminal state. Returns the
        final JobResponse. Raises TimeoutError if the deadline elapses
        without the job finishing."""
        deadline = time.monotonic() + timeout
        while True:
            job = self.get_job(job_id)
            if job.status in ("completed", "failed", "cancelled"):
                return job
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Job {job_id} still {job.status} after {timeout:.0f}s"
                )
            time.sleep(poll_interval)

    def stream_events(self, job_id: str) -> Iterator[dict]:
        """SSE generator yielding pipeline events as they arrive. The
        final event is `{"done": true}`; the iterator stops there.

        Example:
            for event in client.stream_events(job.id):
                print(event)
        """
        with self._http.stream(
            "GET", f"/jobs/{job_id}/events",
        ) as resp:
            if resp.status_code >= 400:
                # `httpx.Response` from a stream needs `.read()` before .json()
                resp.read()
                raise from_response(resp)
            buffer = ""
            for line in resp.iter_lines():
                # SSE format: lines beginning with `data: <json>`
                if not line:
                    if buffer:
                        import json as _json
                        try:
                            event = _json.loads(buffer)
                            buffer = ""
                            yield event
                            if event.get("done"):
                                return
                        except _json.JSONDecodeError:
                            buffer = ""
                    continue
                if line.startswith("data: "):
                    buffer = line[len("data: "):]

    # ── Files ───────────────────────────────────────────────────────────────
    def download_file(
        self, job_id: str, filename: str, *, out_path: str | Path | None = None,
    ) -> Path:
        """Download one of the job's output files (final.mp4, thumb.jpg,
        captions.srt, …). Streams to disk so large files don't materialise
        in memory."""
        out = Path(out_path) if out_path else Path(filename)
        with self._http.stream("GET", f"/jobs/{job_id}/files/{filename}") as resp:
            if resp.status_code >= 400:
                resp.read()
                raise from_response(resp)
            with out.open("wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        return out

    # ── Library ─────────────────────────────────────────────────────────────
    def list_videos(
        self, *, q: str = "", limit: int = 50, offset: int = 0,
        sort: str = "newest", status: str | None = None,
    ) -> list[VideoMeta]:
        params: dict[str, Any] = {"limit": limit, "offset": offset, "sort": sort}
        if q:
            params["q"] = q
        if status:
            params["status"] = status
        resp = self._request("GET", "/library", params=params)
        body = resp.json()
        # The response envelope is {videos, total, limit, offset}; extract.
        videos = body.get("videos", body) if isinstance(body, dict) else body
        return [VideoMeta.from_dict(d) for d in videos]

    def get_video(self, run_id: str) -> VideoMeta:
        resp = self._request("GET", f"/library/{run_id}")
        return VideoMeta.from_dict(resp.json())

    def delete_video(self, run_id: str) -> None:
        self._request("DELETE", f"/library/{run_id}")

    # ── Webhooks ────────────────────────────────────────────────────────────
    # Webhook management is gated to ID-token / cookie auth on the server
    # side (PR 4 privilege-escalation guard). API-key callers will get a
    # 403 on the *_webhook methods below; they're included for completeness
    # so a script doing one-time setup can use them with a Firebase ID
    # token, but the typical user creates webhooks via /account.

    def list_webhooks(self) -> list[WebhookMeta]:
        resp = self._request("GET", "/webhooks")
        return [WebhookMeta.from_dict(d) for d in resp.json()]

    # ── API keys ────────────────────────────────────────────────────────────
    # Same caveat — the `/account/api-keys` endpoints refuse API-key
    # auth. Included so an admin running a setup script with a Firebase
    # ID token can list keys for audit.

    def list_api_keys(self) -> list[ApiKeyMeta]:
        # Note: this endpoint is at /api/account/api-keys, NOT
        # /api/v1/account/.... Use a distinct path because account
        # management isn't part of the v1 surface.
        with httpx.Client(
            base_url=self.config.base_url.rsplit("/v1", 1)[0],
            timeout=self.config.timeout,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "User-Agent": self.config.user_agent,
            },
        ) as alt:
            resp = alt.get("/account/api-keys")
            if resp.status_code >= 400:
                raise from_response(resp)
            return [ApiKeyMeta.from_dict(d) for d in resp.json()]

    # ── Convenience: idempotency key generator ──────────────────────────────
    @staticmethod
    def fresh_idempotency_key() -> str:
        """UUID v4 — sufficient entropy for the 24h key window. Convenience
        for callers who don't want to import `uuid` themselves."""
        return str(uuid.uuid4())
