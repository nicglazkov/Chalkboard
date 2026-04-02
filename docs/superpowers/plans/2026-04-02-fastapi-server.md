# FastAPI Job Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap the Chalkboard pipeline in an HTTP API so it can be driven by a frontend: create a job, stream live progress via SSE, and fetch output files when done.

**Architecture:** A `server/` FastAPI app manages an in-memory `JobStore`. `POST /api/jobs` starts the pipeline as a background asyncio task using `run()` with `interactive=False` and an `on_progress` callback that writes events to a per-job asyncio.Queue. `GET /api/jobs/{id}/events` drains that queue as Server-Sent Events. Output files are served from `output/<run_id>/` via `GET /api/jobs/{id}/files/{filename}`. The pipeline's Docker render is run as a subprocess after the graph completes (same as CLI flow, but triggered by the server).

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, sse-starlette, pytest (httpx for API tests)

**Prerequisites:** `pip install fastapi uvicorn sse-starlette httpx`

---

## Files

- **Create:** `server/__init__.py`
- **Create:** `server/app.py` — FastAPI app factory + lifespan
- **Create:** `server/jobs.py` — JobStore, Job dataclass, background task runner
- **Create:** `server/models.py` — Pydantic request/response models
- **Create:** `server/routes.py` — all API routes
- **Create:** `tests/test_server.py` — API tests using TestClient
- **Create:** `run_server.py` — entrypoint: `uvicorn server.app:app`

---

### Task 1: Models and JobStore

**Files:**
- Create: `server/__init__.py`
- Create: `server/models.py`
- Create: `server/jobs.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_server_jobs.py`:

```python
# tests/test_server_jobs.py
import asyncio
import pytest
from server.jobs import JobStore, Job


def test_create_job_returns_id():
    store = JobStore()
    job = store.create(topic="B-trees", effort="medium", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)
    assert job.id
    assert job.status == "pending"


def test_get_job_by_id():
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)
    fetched = store.get(job.id)
    assert fetched is job


def test_get_missing_job_returns_none():
    store = JobStore()
    assert store.get("nonexistent") is None


def test_list_jobs_returns_all():
    store = JobStore()
    store.create(topic="A", effort="low", audience="intermediate",
                 tone="casual", theme="chalkboard", template=None, speed=1.0)
    store.create(topic="B", effort="low", audience="intermediate",
                 tone="casual", theme="chalkboard", template=None, speed=1.0)
    assert len(store.list()) == 2


def test_job_append_event():
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)
    job.append_event({"node": "script_agent", "status": "done"})
    assert len(job.events) == 1
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_server_jobs.py -v
```
Expected: ImportError.

- [ ] **Step 3: Create server/__init__.py**

```python
# server/__init__.py
```
(empty file)

- [ ] **Step 4: Create server/models.py**

```python
# server/models.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class CreateJobRequest(BaseModel):
    topic: str
    effort: Literal["low", "medium", "high"] = "medium"
    audience: Literal["beginner", "intermediate", "expert"] = "intermediate"
    tone: Literal["casual", "formal", "socratic"] = "casual"
    theme: Literal["chalkboard", "light", "colorful"] = "chalkboard"
    template: str | None = None
    speed: float = 1.0


class JobResponse(BaseModel):
    id: str
    status: Literal["pending", "running", "completed", "failed"]
    topic: str
    events: list[dict]
    error: str | None
    output_files: list[str]   # filenames available under /api/jobs/{id}/files/
```

- [ ] **Step 5: Create server/jobs.py**

```python
# server/jobs.py
from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class Job:
    id: str
    topic: str
    effort: str
    audience: str
    tone: str
    theme: str
    template: str | None
    speed: float
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    events: list[dict] = field(default_factory=list)
    error: str | None = None
    output_files: list[str] = field(default_factory=list)
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)

    def append_event(self, event: dict) -> None:
        self.events.append(event)
        self._queue.put_nowait(event)

    async def event_stream(self):
        """Async generator — yields events until job is terminal."""
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield event
            except asyncio.TimeoutError:
                if self.status in ("completed", "failed"):
                    break


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create(self, topic: str, effort: str, audience: str, tone: str,
               theme: str, template: str | None, speed: float) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, topic=topic, effort=effort, audience=audience,
                  tone=tone, theme=theme, template=template, speed=speed)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())
```

- [ ] **Step 6: Run tests to verify they pass**

```
pytest tests/test_server_jobs.py -v
```

- [ ] **Step 7: Commit**

```bash
git add server/__init__.py server/models.py server/jobs.py tests/test_server_jobs.py
git commit -m "feat: add JobStore, Job dataclass, and Pydantic models for FastAPI server"
```

---

### Task 2: Background job runner

**Files:**
- Modify: `server/jobs.py` — add `run_job` coroutine

- [ ] **Step 1: Write the failing test**

Add to `tests/test_server_jobs.py`:

```python
def test_run_job_sets_status_completed(tmp_path):
    """run_job must mark the job completed after the pipeline finishes."""
    from server.jobs import JobStore, run_job
    from unittest.mock import patch, AsyncMock

    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)

    async def fake_run(**kwargs):
        pass  # pipeline succeeds immediately

    async def fake_render(run_id, output_dir, **kwargs):
        return tmp_path / "final.mp4"

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render):
        asyncio.run(run_job(job, output_dir=tmp_path))

    assert job.status == "completed"


def test_run_job_sets_status_failed_on_exception(tmp_path):
    """run_job must mark the job failed when the pipeline raises."""
    from server.jobs import JobStore, run_job
    from unittest.mock import patch

    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)

    async def boom(**kwargs):
        raise RuntimeError("pipeline exploded")

    with patch("server.jobs.run", new=boom):
        asyncio.run(run_job(job, output_dir=tmp_path))

    assert job.status == "failed"
    assert "pipeline exploded" in job.error
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_server_jobs.py::test_run_job_sets_status_completed tests/test_server_jobs.py::test_run_job_sets_status_failed_on_exception -v
```

- [ ] **Step 3: Add run_job and _do_render to server/jobs.py**

Add imports at top of `server/jobs.py`:

```python
import os
from pathlib import Path
from main import run as _pipeline_run
```

Add after the `JobStore` class:

```python
# Re-export for mocking in tests
run = _pipeline_run


async def _do_render(run_id: str, output_dir: Path, verbose: bool = False,
                     qa_density: str = "normal") -> Path | None:
    """Run Docker render and QA loop. Returns path to final.mp4 or None on failure."""
    # Import here to avoid circular imports with main.py's module-level code
    from main import (
        _ensure_docker_image, _render, _run_qa_loop, OUTPUT_DIR, RenderFailed
    )
    import asyncio

    final_mp4 = output_dir / "final.mp4"
    try:
        await asyncio.to_thread(_ensure_docker_image)
        await asyncio.to_thread(_render, run_id)
        return final_mp4 if final_mp4.exists() else None
    except RenderFailed as e:
        return None


async def run_job(job: Job, output_dir: Path) -> None:
    """Execute the full pipeline + render for a job. Updates job.status in place."""
    job.status = "running"

    def _on_progress(event: dict) -> None:
        for node_name, updates in event.items():
            if node_name == "__end__":
                continue
            job.append_event({"node": node_name, "updates": updates})

    try:
        await run(
            topic=job.topic,
            effort=job.effort,
            thread_id=job.id,
            audience=job.audience,
            tone=job.tone,
            theme=job.theme,
            speed=job.speed,
            template=job.template,
            on_progress=_on_progress,
            interactive=False,
        )

        final_mp4 = await _do_render(job.id, output_dir / job.id)

        # Collect output files
        run_dir = output_dir / job.id
        if run_dir.exists():
            job.output_files = [
                f.name for f in run_dir.iterdir()
                if f.is_file() and f.suffix in (".mp4", ".srt", ".json", ".txt", ".py")
            ]

        job.status = "completed"
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_server_jobs.py -v
```

- [ ] **Step 5: Commit**

```bash
git add server/jobs.py tests/test_server_jobs.py
git commit -m "feat: add run_job background coroutine to drive pipeline + render"
```

---

### Task 3: FastAPI routes and app

**Files:**
- Create: `server/routes.py`
- Create: `server/app.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server.py`:

```python
# tests/test_server.py
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from server.app import create_app
from server.jobs import JobStore


@pytest.fixture
def client():
    store = JobStore()
    app = create_app(store)
    return TestClient(app), store


def test_create_job_returns_202(client):
    tc, store = client
    with patch("server.routes.asyncio.create_task"):
        resp = tc.post("/api/jobs", json={"topic": "explain B-trees", "effort": "low"})
    assert resp.status_code == 202
    body = resp.json()
    assert "id" in body
    assert body["status"] == "pending"
    assert body["topic"] == "explain B-trees"


def test_get_job_returns_job(client):
    tc, store = client
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)
    resp = tc.get(f"/api/jobs/{job.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job.id


def test_get_missing_job_returns_404(client):
    tc, store = client
    resp = tc.get("/api/jobs/does-not-exist")
    assert resp.status_code == 404


def test_list_jobs(client):
    tc, store = client
    store.create(topic="A", effort="low", audience="intermediate",
                 tone="casual", theme="chalkboard", template=None, speed=1.0)
    store.create(topic="B", effort="low", audience="intermediate",
                 tone="casual", theme="chalkboard", template=None, speed=1.0)
    resp = tc.get("/api/jobs")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_get_file_returns_404_for_missing_file(client, tmp_path):
    tc, store = client
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)
    with patch("server.routes.OUTPUT_DIR", str(tmp_path)):
        resp = tc.get(f"/api/jobs/{job.id}/files/final.mp4")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_server.py -v
```
Expected: ImportError.

- [ ] **Step 3: Create server/routes.py**

```python
# server/routes.py
from __future__ import annotations
import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from config import OUTPUT_DIR
from server.jobs import JobStore, run_job, Job
from server.models import CreateJobRequest, JobResponse

router = APIRouter(prefix="/api")


def _job_to_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status,
        topic=job.topic,
        events=job.events,
        error=job.error,
        output_files=job.output_files,
    )


def make_router(store: JobStore) -> APIRouter:
    output_dir = Path(OUTPUT_DIR).resolve()

    @router.post("/jobs", status_code=202, response_model=JobResponse)
    async def create_job(req: CreateJobRequest):
        job = store.create(
            topic=req.topic, effort=req.effort, audience=req.audience,
            tone=req.tone, theme=req.theme, template=req.template, speed=req.speed,
        )
        asyncio.create_task(run_job(job, output_dir))
        return _job_to_response(job)

    @router.get("/jobs", response_model=list[JobResponse])
    async def list_jobs():
        return [_job_to_response(j) for j in store.list()]

    @router.get("/jobs/{job_id}", response_model=JobResponse)
    async def get_job(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return _job_to_response(job)

    @router.get("/jobs/{job_id}/events")
    async def job_events(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        async def generator():
            async for event in job.event_stream():
                import json
                yield {"data": json.dumps(event)}
            yield {"data": '{"done": true}'}

        return EventSourceResponse(generator())

    @router.get("/jobs/{job_id}/files/{filename}")
    async def get_file(job_id: str, filename: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        file_path = output_dir / job_id / filename
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(file_path))

    return router
```

- [ ] **Step 4: Create server/app.py**

```python
# server/app.py
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from server.jobs import JobStore
from server.routes import make_router


def create_app(store: JobStore | None = None) -> FastAPI:
    if store is None:
        store = JobStore()

    app = FastAPI(title="Chalkboard API", version="0.1.0")
    app.include_router(make_router(store))

    # Serve frontend static files if the directory exists
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_server.py -v
```

- [ ] **Step 6: Commit**

```bash
git add server/routes.py server/app.py tests/test_server.py
git commit -m "feat: add FastAPI routes for job CRUD, SSE events, and file serving"
```

---

### Task 4: Server entrypoint and requirements

**Files:**
- Create: `run_server.py`
- Modify: `requirements.txt`

- [ ] **Step 1: Add server dependencies to requirements.txt**

Add to `requirements.txt`:

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
sse-starlette>=1.8.0
httpx>=0.27.0
```

- [ ] **Step 2: Create run_server.py**

```python
#!/usr/bin/env python3
"""Start the Chalkboard API server."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("server.app:app", host="0.0.0.0", port=8000, reload=True)
```

- [ ] **Step 3: Install new dependencies**

```
pip install fastapi uvicorn[standard] sse-starlette httpx
```

- [ ] **Step 4: Run full test suite**

```
pytest --tb=short -q
```
Expected: all tests pass.

- [ ] **Step 5: Smoke test the server manually**

```bash
python run_server.py &
curl -s -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{"topic": "explain recursion", "effort": "low"}' | python3 -m json.tool
# should return {"id": "...", "status": "pending", ...}
kill %1
```

- [ ] **Step 6: Commit**

```bash
git add run_server.py requirements.txt
git commit -m "feat: add uvicorn server entrypoint and FastAPI dependencies"
```
