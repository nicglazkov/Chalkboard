"""Minimal FastAPI webhook receiver.

    pip install chalkboard-sdk fastapi uvicorn
    export CHALKBOARD_SIGNING_SECRET=<from /account → Webhooks>
    uvicorn verify_webhook:app --port 8000

Then point a Chalkboard webhook at https://your-host/chalkboard.
"""
import json
import os

from fastapi import FastAPI, HTTPException, Request

from chalkboard import verify_webhook_signature

SIGNING_SECRET = os.environ["CHALKBOARD_SIGNING_SECRET"]

app = FastAPI()


@app.post("/chalkboard")
async def receive(request: Request):
    body = await request.body()    # raw bytes — DO NOT JSON-decode before verifying
    sig = request.headers.get("X-Chalkboard-Signature", "")
    if not verify_webhook_signature(SIGNING_SECRET, body, sig):
        raise HTTPException(status_code=401, detail="bad signature")

    event = json.loads(body)
    # event has shape: {id, event, created_at, data: { ...JobResponse... }}
    print(f"Received {event['event']} for run {event['data']['id']}")
    if event["event"] == "job.completed":
        # ... do the thing your integration cares about ...
        pass

    # Always 2xx so we don't trigger the retry path.
    return {"received": True}
