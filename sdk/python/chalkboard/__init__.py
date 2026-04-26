"""Chalkboard Python SDK — typed wrappers around the public REST API.

Quickstart:

    from chalkboard import ChalkboardClient

    client = ChalkboardClient(api_key="chk_live_…")
    job = client.create_job(topic="How hash tables work")
    final = client.wait_for_completion(job.id)
    client.download_file(final.id, "final.mp4", out_path="hash-tables.mp4")

The handwritten client is intentionally small (~one screen of code) so
users can read it end-to-end. Generated bindings from `/openapi.json`
are easy to layer on later if anyone wants the full surface — for now,
this covers the four canonical flows: create / poll / stream / library.
"""
from .client import ChalkboardClient, ChalkboardConfig
from .exceptions import (
    ChalkboardError, ChalkboardAuthError, ChalkboardRateLimitError,
    ChalkboardSpendCapError, ChalkboardConflictError, ChalkboardNotFoundError,
    ChalkboardValidationError, ChalkboardServerError,
)
from .models import JobResponse, VideoMeta, ApiKeyMeta, WebhookMeta
from .webhooks import verify_webhook_signature

__all__ = [
    "ChalkboardClient", "ChalkboardConfig",
    "JobResponse", "VideoMeta", "ApiKeyMeta", "WebhookMeta",
    "ChalkboardError",
    "ChalkboardAuthError", "ChalkboardRateLimitError", "ChalkboardSpendCapError",
    "ChalkboardConflictError", "ChalkboardNotFoundError",
    "ChalkboardValidationError", "ChalkboardServerError",
    "verify_webhook_signature",
]

__version__ = "0.1.1"   # mirror the API/app version that this SDK pairs with
