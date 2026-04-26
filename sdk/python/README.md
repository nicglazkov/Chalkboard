# chalkboard-sdk

Python client for the [Chalkboard API](https://chalkboard.studio/docs/api).
Typed, sync, one-screen-of-code, no codegen. The full handwritten
client lives in `chalkboard/client.py` — read it end-to-end if you
want to know exactly what a method does.

## Install

The hosted Chalkboard service is in closed beta — the SDK is shipped
via GitHub Releases on this repo until the v1 surface stabilises and
we publish to PyPI.

```bash
pip install https://github.com/nicglazkov/Chalkboard/releases/download/sdk-py/v0.1.1/chalkboard_sdk-0.1.1-py3-none-any.whl
```

## Quickstart

```python
from chalkboard import ChalkboardClient

client = ChalkboardClient(api_key="chk_live_…")

# Submit a job. Idempotency-Key is optional but recommended on retries.
job = client.create_job(
    topic="How hash tables actually work",
    effort="medium",
    theme="chalkboard",
    idempotency_key=client.fresh_idempotency_key(),
)

# Block until the pipeline finishes (poll-based).
final = client.wait_for_completion(job.id, timeout=600)
print(final.status, final.output_files)

# Or stream events as they arrive.
for event in client.stream_events(job.id):
    print(event)
    if event.get("done"):
        break

# Download the final mp4.
client.download_file(final.id, "final.mp4", out_path="hash-tables.mp4")
```

## Test mode

Pass a `chk_test_…` key (creatable at <https://chalkboard.studio/account>)
to force the cheapest pipeline settings — `effort=low`, `quality=low`,
`qa_density=zero`, Haiku model. Roughly 4× cheaper. Useful for CI
smoke-tests against new features. The same code path runs; just at
the cheap end of every dial.

## Webhooks

Subscribe to `job.completed` / `job.failed` / `job.cancelled` at
[/account](https://chalkboard.studio/account) → "Webhooks". On the
receiver side, verify the signature:

```python
from chalkboard import verify_webhook_signature

SIGNING_SECRET = os.environ["CHALKBOARD_SIGNING_SECRET"]

# In your FastAPI / Flask / Django handler:
def receive_webhook(request):
    body = request.body  # raw bytes — don't json.parse before verifying
    sig = request.headers["X-Chalkboard-Signature"]
    if not verify_webhook_signature(SIGNING_SECRET, body, sig):
        return Response(status_code=401)
    event = json.loads(body)
    # ... handle event["data"] (JobResponse-shaped)
```

`verify_webhook_signature` returns `False` on a tampered body, wrong
secret, malformed header, or signature older than 5 minutes (replay
protection). Constant-time compare via `hmac.compare_digest`.

## Errors

Every non-2xx response raises a typed exception. Catch the parent
`ChalkboardError` if you don't care about the specifics:

```python
from chalkboard import (
    ChalkboardError, ChalkboardRateLimitError, ChalkboardSpendCapError,
)

try:
    client.create_job(topic="…")
except ChalkboardRateLimitError as e:
    time.sleep(e.retry_after_seconds or 5)
except ChalkboardSpendCapError:
    # Daily $10 cap reached. Resets in 24h.
    ...
except ChalkboardError as e:
    print(f"{e.status}: {e.detail}")
```

## What's NOT in this SDK (yet)

- **Async client.** Sync only for now. Add `httpx.AsyncClient` wrappers
  if you need them.
- **Generated bindings.** This is a hand-written client. The
  `/openapi.json` spec is publicly available if you want to feed it
  into `openapi-python-client` or similar.
- **Webhook management endpoints from API-key auth.** The server
  refuses `/webhooks` POST/DELETE under API-key auth as a privilege-
  escalation guard. Use the `/account` web UI to create webhooks, then
  use this client to receive them.

## Source

This SDK and the underlying multi-agent pipeline are both open source
in [github.com/nicglazkov/Chalkboard](https://github.com/nicglazkov/Chalkboard).
The pipeline can be self-hosted (clone the repo, set `ANTHROPIC_API_KEY`,
run `python run_server.py`) and the SDK works against either the hosted
endpoint at <https://chalkboard.studio> or your own deployment via the
`base_url=` kwarg.
