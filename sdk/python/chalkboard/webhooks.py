"""Webhook signature verification — re-implements the server's
`verify_signature` helper as a stdlib-only function so users don't
have to copy-paste it from the docs.

The server-side code lives in `server/webhooks.py` and is the source
of truth; if anything changes there, mirror it here.
"""
from __future__ import annotations
import hashlib
import hmac
import time


def verify_webhook_signature(
    secret: str,
    payload_bytes: bytes,
    signature_header: str,
    *,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify the `X-Chalkboard-Signature` header against the raw POST
    body.

    Returns False on malformed header, expired timestamp (replay
    protection), or HMAC mismatch. Returns True on a fresh + valid
    signature.

    Example FastAPI receiver:

        from chalkboard import verify_webhook_signature

        @app.post("/chalkboard-webhook")
        async def receive(request: Request):
            body = await request.body()
            sig = request.headers.get("X-Chalkboard-Signature", "")
            if not verify_webhook_signature(SIGNING_SECRET, body, sig):
                raise HTTPException(401, "bad signature")
            payload = json.loads(body)
            ...
    """
    parts = dict(p.split("=", 1) for p in signature_header.split(",") if "=" in p)
    if "t" not in parts or "v1" not in parts:
        return False
    try:
        t = int(parts["t"])
    except ValueError:
        return False
    if abs(time.time() - t) > tolerance_seconds:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{t}.{payload_bytes.decode('utf-8', errors='replace')}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
