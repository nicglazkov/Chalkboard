"""Exception hierarchy for the Chalkboard SDK.

Every error is a subclass of `ChalkboardError`. Subclasses are picked
based on the HTTP status the API returned so callers can `except` on
specific failure modes (auth, rate limits, validation) without parsing
the `detail` string. The full response is preserved on `.response` for
deeper inspection.
"""
from __future__ import annotations
from typing import Any


class ChalkboardError(Exception):
    """Base class for every SDK error. Always carries the HTTP status,
    the parsed `detail` (or raw text on non-JSON responses), and the
    raw `httpx.Response` for the rare case a caller needs the headers."""

    def __init__(self, message: str, *, status: int | None = None,
                 detail: Any = None, response=None):
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.response = response


class ChalkboardAuthError(ChalkboardError):
    """401 / 403 — invalid or revoked API key, banned account, or trying
    to manage keys/webhooks via API-key auth (use the web UI)."""


class ChalkboardRateLimitError(ChalkboardError):
    """429 — rate limit exceeded. The `retry_after_seconds` attribute is
    parsed from the `Retry-After` response header."""

    def __init__(self, *args, retry_after_seconds: float | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class ChalkboardSpendCapError(ChalkboardError):
    """402 — daily spend cap reached. Resets in 24h. Email
    hello@chalkboard.studio if you need a higher cap."""


class ChalkboardConflictError(ChalkboardError):
    """409 — Idempotency-Key reused with a different request body, or
    retrying an in-flight job. Generate a new key for distinct requests."""


class ChalkboardNotFoundError(ChalkboardError):
    """404 — job / video / webhook doesn't exist or doesn't belong to
    the authenticated account. Deliberately ambiguous."""


class ChalkboardValidationError(ChalkboardError):
    """422 — request body failed validation. `detail` is the FastAPI
    error array (list of {`loc`, `msg`, `type`} dicts)."""


class ChalkboardServerError(ChalkboardError):
    """5xx — server-side problem. Safe to retry (with the same
    Idempotency-Key if present) — 5xx responses are not cached by the
    API's idempotency layer."""


def from_response(resp) -> ChalkboardError:
    """Translate an `httpx.Response` (status >= 400) into the right
    SDK exception subclass. Used by `client.py` on every non-2xx."""
    status = resp.status_code
    try:
        body = resp.json()
        detail = body.get("detail", body) if isinstance(body, dict) else body
    except Exception:
        detail = resp.text

    msg = f"HTTP {status}: {detail!s}"

    if status in (401, 403):
        return ChalkboardAuthError(msg, status=status, detail=detail, response=resp)
    if status == 402:
        return ChalkboardSpendCapError(msg, status=status, detail=detail, response=resp)
    if status == 404:
        return ChalkboardNotFoundError(msg, status=status, detail=detail, response=resp)
    if status == 409:
        return ChalkboardConflictError(msg, status=status, detail=detail, response=resp)
    if status == 422:
        return ChalkboardValidationError(msg, status=status, detail=detail, response=resp)
    if status == 429:
        retry_after = resp.headers.get("Retry-After")
        try:
            retry_seconds = float(retry_after) if retry_after else None
        except ValueError:
            retry_seconds = None
        return ChalkboardRateLimitError(
            msg, status=status, detail=detail, response=resp,
            retry_after_seconds=retry_seconds,
        )
    if 500 <= status < 600:
        return ChalkboardServerError(msg, status=status, detail=detail, response=resp)
    return ChalkboardError(msg, status=status, detail=detail, response=resp)
