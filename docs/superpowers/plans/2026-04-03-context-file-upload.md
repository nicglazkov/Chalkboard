# Context File Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users upload local files (code, PDFs, images, Word docs) as source material via the web UI, with per-type and total size limits enforced both client-side and server-side with clear error messages.

**Architecture:** Three layered tasks — (1) a new `server/upload.py` module handles validation and saving of `UploadFile` objects to a temp dir; (2) `server/jobs.py` loads those files into context blocks and cleans up the temp dir, and `server/routes.py` adds a `POST /api/jobs/upload` multipart endpoint; (3) the frontend adds a drag-and-drop file zone with inline per-file and total-size error display, switching the submit path to multipart when files are present. The existing `POST /api/jobs` JSON endpoint is unchanged — backward compatible.

**Tech Stack:** Python 3.10, FastAPI `UploadFile`/`Form`/`File`, Starlette multipart, `pipeline/context.py` (`load_context_blocks`, `collect_files`), `shutil.rmtree`, vanilla HTML/CSS/JS drag-and-drop API

---

## Files

| File | Change |
|------|--------|
| `server/upload.py` | Create: file validation, size-limit enforcement, temp-dir write |
| `server/jobs.py` | Add `upload_dir` to `Job` + `JobStore.create`; load file blocks + cleanup in `run_job` |
| `server/routes.py` | Add `POST /api/jobs/upload` multipart endpoint |
| `server/app.py` | Set `MultiPartParser.max_part_size = 20 MB` before app starts |
| `server/static/index.html` | Upload zone UI: CSS, HTML, JS (validation, drag-drop, FormData submit) |
| `tests/test_server_upload.py` | Create: unit tests for upload validation module |
| `tests/test_server_jobs.py` | Add: tests for upload_dir loading and cleanup |
| `tests/test_server.py` | Add: integration tests for the new route |

---

### Task 1: `server/upload.py` — validation and temp-file writing

**Files:**
- Create: `server/upload.py`
- Create: `tests/test_server_upload.py`

**Limits enforced (mirrors the design decision):**

| Category | Per-file limit |
|----------|---------------|
| text/code | 2 MB |
| image | 5 MB |
| pdf | 20 MB |
| docx | 10 MB |
| Total across all files | 24 MB |

- [ ] **Step 1: Write the failing tests**

Create `tests/test_server_upload.py`:

```python
# tests/test_server_upload.py
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from server.upload import (
    file_category,
    validate_and_save,
    FileSizeError,
    TotalSizeError,
    UnsupportedFileTypeError,
    LIMITS,
    TOTAL_LIMIT,
)


def _mock_upload(filename: str, data: bytes) -> MagicMock:
    u = MagicMock()
    u.filename = filename
    u.read = AsyncMock(return_value=data)
    return u


# ── file_category ──────────────────────────────────────────────────────────

def test_file_category_text():
    assert file_category("script.py") == "text"
    assert file_category("README.md") == "text"
    assert file_category("data.json") == "text"
    assert file_category("style.css") == "text"


def test_file_category_image():
    assert file_category("photo.png") == "image"
    assert file_category("banner.jpg") == "image"
    assert file_category("anim.gif") == "image"
    assert file_category("img.webp") == "image"


def test_file_category_pdf():
    assert file_category("paper.pdf") == "pdf"
    assert file_category("PAPER.PDF") == "pdf"


def test_file_category_docx():
    assert file_category("doc.docx") == "docx"
    assert file_category("REPORT.DOCX") == "docx"


def test_file_category_unsupported():
    assert file_category("archive.zip") == "unsupported"
    assert file_category("binary.exe") == "unsupported"
    assert file_category("video.mp4") == "unsupported"
    assert file_category("noextension") == "unsupported"


# ── validate_and_save ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_and_save_writes_file(tmp_path):
    data = b"print('hello')"
    saved = await validate_and_save([_mock_upload("hello.py", data)], tmp_path)
    assert len(saved) == 1
    assert saved[0].read_bytes() == data


@pytest.mark.asyncio
async def test_validate_and_save_deduplicates_filename(tmp_path):
    """Two uploads with the same filename must not overwrite each other."""
    saved = await validate_and_save(
        [_mock_upload("notes.md", b"first"), _mock_upload("notes.md", b"second")],
        tmp_path,
    )
    assert len(saved) == 2
    assert saved[0].read_bytes() != saved[1].read_bytes()


@pytest.mark.asyncio
async def test_validate_and_save_empty_list(tmp_path):
    saved = await validate_and_save([], tmp_path)
    assert saved == []


@pytest.mark.asyncio
async def test_validate_and_save_raises_for_unsupported(tmp_path):
    with pytest.raises(UnsupportedFileTypeError, match="archive.zip"):
        await validate_and_save([_mock_upload("archive.zip", b"data")], tmp_path)


@pytest.mark.asyncio
async def test_validate_and_save_raises_file_size_error_text(tmp_path):
    big = b"x" * (LIMITS["text"] + 1)
    with pytest.raises(FileSizeError, match="big.py"):
        await validate_and_save([_mock_upload("big.py", big)], tmp_path)


@pytest.mark.asyncio
async def test_validate_and_save_raises_file_size_error_image(tmp_path):
    big = b"x" * (LIMITS["image"] + 1)
    with pytest.raises(FileSizeError, match="photo.png"):
        await validate_and_save([_mock_upload("photo.png", big)], tmp_path)


@pytest.mark.asyncio
async def test_validate_and_save_raises_file_size_error_pdf(tmp_path):
    big = b"x" * (LIMITS["pdf"] + 1)
    with pytest.raises(FileSizeError, match="paper.pdf"):
        await validate_and_save([_mock_upload("paper.pdf", big)], tmp_path)


@pytest.mark.asyncio
async def test_validate_and_save_raises_total_size_error(tmp_path):
    # Two 13 MB PDFs: each under 20 MB limit but together exceed 24 MB total
    chunk = b"x" * (13 * 1024 * 1024)
    uploads = [_mock_upload(f"doc{i}.pdf", chunk) for i in range(2)]
    with pytest.raises(TotalSizeError):
        await validate_and_save(uploads, tmp_path)


@pytest.mark.asyncio
async def test_validate_and_save_at_exact_limit_passes(tmp_path):
    """A file exactly at its per-type limit must be accepted."""
    data = b"x" * LIMITS["pdf"]
    saved = await validate_and_save([_mock_upload("ok.pdf", data)], tmp_path)
    assert len(saved) == 1
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/nic/Documents/code/Chalkboard
pytest tests/test_server_upload.py -v
```

Expected: `ModuleNotFoundError: No module named 'server.upload'`

- [ ] **Step 3: Create `server/upload.py`**

```python
# server/upload.py
from __future__ import annotations
from pathlib import Path
from fastapi import UploadFile
from pipeline.context import TEXT_EXTENSIONS, IMAGE_MEDIA_TYPES

# Per-file size limits (bytes) by category
LIMITS: dict[str, int] = {
    "text":  2 * 1024 * 1024,   # 2 MB
    "image": 5 * 1024 * 1024,   # 5 MB
    "pdf":  20 * 1024 * 1024,   # 20 MB
    "docx": 10 * 1024 * 1024,   # 10 MB
}
TOTAL_LIMIT: int = 24 * 1024 * 1024  # 24 MB across all files


class UploadValidationError(ValueError):
    """Base class for upload validation failures."""


class UnsupportedFileTypeError(UploadValidationError):
    """File extension not supported by the pipeline."""


class FileSizeError(UploadValidationError):
    """Single file exceeds its per-type size limit."""


class TotalSizeError(UploadValidationError):
    """Sum of all uploaded files exceeds the total size limit."""


def file_category(filename: str) -> str:
    """
    Return the category string used for limit lookup.
    Returns 'unsupported' for unrecognised extensions.
    """
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_MEDIA_TYPES:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "unsupported"


async def validate_and_save(files: list[UploadFile], tmp_dir: Path) -> list[Path]:
    """
    Validate each UploadFile against per-type and total size limits,
    then write accepted files to tmp_dir. Returns list of saved Paths.

    Raises:
        UnsupportedFileTypeError: for unrecognised file extensions
        FileSizeError: when a single file exceeds its category limit
        TotalSizeError: when the running total exceeds TOTAL_LIMIT
    """
    total = 0
    saved: list[Path] = []

    for upload in files:
        filename = upload.filename or f"file_{len(saved)}"
        category = file_category(filename)

        if category == "unsupported":
            raise UnsupportedFileTypeError(
                f"{filename}: unsupported file type. "
                f"Supported types: text/code files, .pdf, .docx, .png, .jpg, .gif, .webp"
            )

        data = await upload.read()
        size = len(data)
        limit = LIMITS[category]

        if size > limit:
            limit_mb = limit / (1024 * 1024)
            size_mb = size / (1024 * 1024)
            raise FileSizeError(
                f"{filename}: {size_mb:.1f} MB exceeds the {limit_mb:.0f} MB "
                f"limit for {category} files"
            )

        total += size
        if total > TOTAL_LIMIT:
            total_mb = TOTAL_LIMIT / (1024 * 1024)
            raise TotalSizeError(
                f"Total upload size exceeds the {total_mb:.0f} MB limit"
            )

        # Resolve filename conflicts
        dest = tmp_dir / filename
        if dest.exists():
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            counter = 1
            while dest.exists():
                dest = tmp_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        dest.write_bytes(data)
        saved.append(dest)

    return saved
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_server_upload.py -v
```

Expected: all 13 tests PASS.

- [ ] **Step 5: Run full suite to confirm nothing broken**

```bash
pytest --tb=short -q
```

Expected: all passing (170+).

- [ ] **Step 6: Commit**

```bash
git add server/upload.py tests/test_server_upload.py
git commit -m "feat: add upload validation module with per-type and total size limits"
```

---

### Task 2: Backend wiring — Job, run_job, new route, app config

**Files:**
- Modify: `server/jobs.py`
- Modify: `server/routes.py`
- Modify: `server/app.py`
- Test: `tests/test_server_jobs.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_server_jobs.py` (below the existing tests):

```python
# ── upload_dir tests ────────────────────────────────────────────────────────

def test_job_store_create_accepts_upload_dir(tmp_path):
    store = JobStore()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    job = store.create(
        topic="test", effort="low", audience="intermediate",
        tone="casual", theme="chalkboard", template=None, speed=1.0,
        upload_dir=upload_dir,
    )
    assert job.upload_dir == upload_dir


def test_job_store_create_upload_dir_defaults_none():
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)
    assert job.upload_dir is None


@pytest.mark.asyncio
async def test_run_job_loads_file_context(tmp_path):
    """Files in upload_dir must be loaded into context_blocks passed to run()."""
    import json
    store = JobStore()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "notes.txt").write_text("important context")

    job = store.create(
        topic="test", effort="low", audience="intermediate",
        tone="casual", theme="chalkboard", template=None, speed=1.0,
        upload_dir=upload_dir,
    )
    run_kwargs = {}

    async def fake_run(**kwargs):
        run_kwargs.update(kwargs)
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return tmp_path / "final.mp4"

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render):
        await run_job(job, tmp_path)

    blocks = run_kwargs.get("context_blocks") or []
    text_values = [b["text"] for b in blocks if b.get("type") == "text"]
    assert any("important context" in t for t in text_values)


@pytest.mark.asyncio
async def test_run_job_cleans_up_upload_dir_on_success(tmp_path):
    """upload_dir must be deleted after a successful run."""
    import json
    store = JobStore()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "notes.txt").write_text("context")

    job = store.create(
        topic="test", effort="low", audience="intermediate",
        tone="casual", theme="chalkboard", template=None, speed=1.0,
        upload_dir=upload_dir,
    )

    async def fake_run(**kwargs):
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return tmp_path / "final.mp4"

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render):
        await run_job(job, tmp_path)

    assert not upload_dir.exists()


@pytest.mark.asyncio
async def test_run_job_cleans_up_upload_dir_on_failure(tmp_path):
    """upload_dir must be deleted even when the pipeline raises."""
    store = JobStore()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "notes.txt").write_text("context")

    job = store.create(
        topic="test", effort="low", audience="intermediate",
        tone="casual", theme="chalkboard", template=None, speed=1.0,
        upload_dir=upload_dir,
    )

    async def fake_run(**kwargs):
        raise RuntimeError("pipeline blew up")

    with patch("server.jobs.run", new=fake_run):
        await run_job(job, tmp_path)

    assert job.status == "failed"
    assert not upload_dir.exists()
```

Add to `tests/test_server.py` (below the existing tests):

```python
def test_upload_endpoint_returns_202(client, tmp_path):
    """POST /api/jobs/upload with a valid file must return 202."""
    tc, store = client
    from server.upload import validate_and_save

    saved_path = tmp_path / "script.py"
    saved_path.write_bytes(b"print('hello')")

    async def fake_validate(files, tmp_dir):
        return [saved_path]

    with patch("server.routes.asyncio.create_task"), \
         patch("server.routes.validate_and_save", new=fake_validate):
        resp = tc.post(
            "/api/jobs/upload",
            data={"topic": "explain B-trees", "effort": "low"},
            files={"files": ("script.py", b"print('hello')", "text/plain")},
        )

    assert resp.status_code == 202
    body = resp.json()
    assert "id" in body
    assert body["status"] == "pending"
    assert body["topic"] == "explain B-trees"


def test_upload_endpoint_returns_413_on_file_size_error(client):
    """POST /api/jobs/upload must return 413 when a file exceeds its size limit."""
    tc, store = client
    from server.upload import FileSizeError

    async def fake_validate(files, tmp_dir):
        raise FileSizeError("big.pdf: 21.0 MB exceeds the 20 MB limit for pdf files")

    with patch("server.routes.validate_and_save", new=fake_validate):
        resp = tc.post(
            "/api/jobs/upload",
            data={"topic": "test"},
            files={"files": ("big.pdf", b"x" * 100, "application/pdf")},
        )

    assert resp.status_code == 413
    assert "big.pdf" in resp.json()["detail"]


def test_upload_endpoint_returns_400_on_unsupported_type(client):
    """POST /api/jobs/upload must return 400 for unsupported file types."""
    tc, store = client
    from server.upload import UnsupportedFileTypeError

    async def fake_validate(files, tmp_dir):
        raise UnsupportedFileTypeError("archive.zip: unsupported file type")

    with patch("server.routes.validate_and_save", new=fake_validate):
        resp = tc.post(
            "/api/jobs/upload",
            data={"topic": "test"},
            files={"files": ("archive.zip", b"data", "application/zip")},
        )

    assert resp.status_code == 400
    assert "archive.zip" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_server_jobs.py -k "upload_dir" -v
pytest tests/test_server.py -k "upload_endpoint" -v
```

Expected: `AttributeError` on `Job.upload_dir` and `404` for the missing route.

- [ ] **Step 3: Update `server/jobs.py`**

Make these changes:

**3a. Add imports** — replace the existing import block at the top with:

```python
# server/jobs.py
from __future__ import annotations
import asyncio
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

_QADensity = Literal["zero", "normal", "high"]

from pipeline.context import fetch_url_blocks, collect_files, load_context_blocks
from main import (
    run as _pipeline_run,
    _render, RenderFailed,
    _run_qa_loop, _generate_quiz, _github_to_raw_url,
)
```

(Added `shutil`, removed `os`, added `collect_files` and `load_context_blocks` to the pipeline.context import.)

**3b. Add `upload_dir` field to `Job`** — add after `qa_density`:

```python
    qa_density: _QADensity = "normal"
    upload_dir: Path | None = None          # temp dir for uploaded files; deleted after run
    status: Literal["pending", "running", "completed", "failed"] = "pending"
```

**3c. Update `JobStore.create`** — add `upload_dir` parameter:

```python
    def create(self, topic: str, effort: str, audience: str, tone: str,
               theme: str, template: str | None, speed: float,
               burn_captions: bool = False, quiz: bool = False,
               urls: list[str] | None = None, github: list[str] | None = None,
               qa_density: _QADensity = "normal",
               upload_dir: Path | None = None) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, topic=topic, effort=effort, audience=audience,
                  tone=tone, theme=theme, template=template, speed=speed,
                  burn_captions=burn_captions, quiz=quiz,
                  urls=urls or [], github=github or [],
                  qa_density=qa_density, upload_dir=upload_dir)
        self._jobs[job_id] = job
        return job
```

**3d. Update `run_job`** — add file block loading and upload_dir cleanup. Replace the entire `run_job` function:

```python
async def run_job(job: Job, output_dir: Path) -> None:
    """Execute the full pipeline + render for a job. Updates job.status in place."""
    job.status = "running"

    def _on_progress(event: dict) -> None:
        for node_name, updates in event.items():
            if node_name == "__end__":
                continue
            job.append_event({"node": node_name, "updates": updates})

    try:
        # Build context_blocks from uploaded files, URLs, and GitHub repos
        context_blocks = None

        if job.upload_dir is not None and job.upload_dir.exists():
            print(f"  [server] loading {len(list(job.upload_dir.iterdir()))} uploaded file(s)")
            file_paths = await asyncio.to_thread(
                collect_files, [str(job.upload_dir)]
            )
            file_blocks = await asyncio.to_thread(load_context_blocks, file_paths)
            context_blocks = (context_blocks or []) + file_blocks
            print(f"  [server] loaded {len(file_blocks)} block(s) from uploaded files")

        for url in job.urls:
            print(f"  [server] fetching URL: {url}")
            blocks = await asyncio.to_thread(fetch_url_blocks, url)
            context_blocks = (context_blocks or []) + blocks
            print(f"  [server] fetched {len(blocks)} block(s) from URL")
        for repo in job.github:
            raw_url = _github_to_raw_url(repo)
            print(f"  [server] fetching GitHub repo: {repo} → {raw_url}")
            blocks = await asyncio.to_thread(fetch_url_blocks, raw_url)
            context_blocks = (context_blocks or []) + blocks
            print(f"  [server] fetched {len(blocks)} block(s) from GitHub")
        if context_blocks:
            print(f"  [server] total context: {len(context_blocks)} block(s) → passing to pipeline")
        else:
            print(f"  [server] no context blocks (urls={job.urls!r}, github={job.github!r})")

        await run(
            topic=job.topic,
            effort=job.effort,
            thread_id=job.id,
            audience=job.audience,
            tone=job.tone,
            theme=job.theme,
            speed=job.speed,
            template=job.template,
            context_blocks=context_blocks,
            on_progress=_on_progress,
            interactive=False,
        )

        # render_trigger writes manifest.json as its final step.
        # If it's absent, the pipeline ended before completing.
        if not (output_dir / job.id / "manifest.json").exists():
            raise RuntimeError("pipeline did not complete — no output was written")

        final_mp4 = await _do_render(job.id, burn_captions=job.burn_captions)
        if final_mp4 is None:
            job.error = "render failed; pipeline output preserved"

        # Visual QA (runs in a thread — _run_qa_loop is a sync function)
        if final_mp4 is not None and job.qa_density != "zero":
            await asyncio.to_thread(
                _run_qa_loop,
                job.id, final_mp4,
                theme=job.theme, audience=job.audience,
                tone=job.tone, effort_level=job.effort,
                context_blocks=context_blocks,
                qa_density=job.qa_density,
            )

        # Quiz generation (sync function — run in thread).
        # No final_mp4 guard: quiz only needs script.txt, so it works even when
        # render failed or --no-render was used.
        if job.quiz:
            await asyncio.to_thread(_generate_quiz, job.id)

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
    finally:
        # Delete temp upload dir regardless of outcome
        if job.upload_dir is not None and job.upload_dir.exists():
            shutil.rmtree(job.upload_dir, ignore_errors=True)
```

- [ ] **Step 4: Update `server/routes.py`** — add the upload endpoint and imports

Replace the entire file:

```python
# server/routes.py
from __future__ import annotations
import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from config import OUTPUT_DIR
from server.jobs import JobStore, run_job, Job
from server.models import CreateJobRequest, JobResponse
from server.upload import (
    validate_and_save,
    FileSizeError, TotalSizeError, UnsupportedFileTypeError,
)


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
    router = APIRouter(prefix="/api")

    @router.post("/jobs", status_code=202, response_model=JobResponse)
    async def create_job(req: CreateJobRequest):
        """Create a job from a JSON body (no file uploads)."""
        job = store.create(
            topic=req.topic, effort=req.effort, audience=req.audience,
            tone=req.tone, theme=req.theme, template=req.template, speed=req.speed,
            burn_captions=req.burn_captions, quiz=req.quiz,
            urls=req.urls, github=req.github, qa_density=req.qa_density,
        )
        output_dir = Path(OUTPUT_DIR).resolve()
        asyncio.create_task(run_job(job, output_dir))
        return _job_to_response(job)

    @router.post("/jobs/upload", status_code=202, response_model=JobResponse)
    async def create_job_with_files(
        topic: str = Form(...),
        effort: str = Form("medium"),
        audience: str = Form("intermediate"),
        tone: str = Form("casual"),
        theme: str = Form("chalkboard"),
        template: str = Form(""),
        speed: float = Form(1.0),
        burn_captions: bool = Form(False),
        quiz: bool = Form(False),
        qa_density: str = Form("normal"),
        urls: list[str] = Form(default=[]),
        github: list[str] = Form(default=[]),
        files: list[UploadFile] = File(default=[]),
    ):
        """Create a job from multipart form data, optionally with file uploads."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="chalkboard_upload_"))
        try:
            saved_paths = await validate_and_save(files, tmp_dir)
        except FileSizeError as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=413, detail=str(e))
        except TotalSizeError as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=413, detail=str(e))
        except UnsupportedFileTypeError as e:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(e))

        # Only keep upload_dir if files were actually saved
        upload_dir: Path | None = tmp_dir if saved_paths else None
        if upload_dir is None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        job = store.create(
            topic=topic, effort=effort, audience=audience,
            tone=tone, theme=theme, template=template or None, speed=speed,
            burn_captions=burn_captions, quiz=quiz,
            urls=urls, github=github, qa_density=qa_density,
            upload_dir=upload_dir,
        )
        output_dir = Path(OUTPUT_DIR).resolve()
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
                yield {"data": json.dumps(event)}
            yield {"data": json.dumps({"done": True})}

        return EventSourceResponse(generator())

    @router.get("/jobs/{job_id}/files/{filename}")
    async def get_file(job_id: str, filename: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        base_dir = Path(OUTPUT_DIR).resolve() / job_id
        file_path = (base_dir / filename).resolve()
        if not file_path.is_relative_to(base_dir):
            raise HTTPException(status_code=404, detail="File not found")
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(file_path))

    return router
```

- [ ] **Step 5: Update `server/app.py`** — raise the Starlette multipart size limit

Replace the entire file:

```python
# server/app.py
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Raise per-part upload limit from the 1 MB default to 20 MB so the upload
# endpoint can accept PDF and image files up to their per-type size limits.
from starlette.formparsers import MultiPartParser
MultiPartParser.max_part_size = 20 * 1024 * 1024

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

- [ ] **Step 6: Run the new tests**

```bash
pytest tests/test_server_jobs.py -k "upload_dir" -v
pytest tests/test_server.py -k "upload_endpoint" -v
```

Expected: all 6 new tests PASS.

- [ ] **Step 7: Run full suite**

```bash
pytest --tb=short -q
```

Expected: all passing (183+).

- [ ] **Step 8: Commit**

```bash
git add server/upload.py server/jobs.py server/routes.py server/app.py \
        tests/test_server_jobs.py tests/test_server.py
git commit -m "feat: add POST /api/jobs/upload endpoint with per-type file size limits"
```

---

### Task 3: Frontend — file upload zone with inline error display

**Files:**
- Modify: `server/static/index.html`

No backend changes needed — the server is ready after Task 2.

- [ ] **Step 1: No test to write**

`test_static_index_served` (existing) will still pass as long as the HTML is valid. Manually verify behavior after this task using the smoke test in Step 6.

- [ ] **Step 2: Add CSS for the upload zone**

Inside the `<style>` block, after the existing `.btn-add:hover` rules, add:

```css
    /* ── File upload zone ─────────────────────────────────────── */
    .upload-zone {
      border: 1.5px dashed var(--border);
      border-radius: 6px;
      padding: 1.25rem 1rem;
      text-align: center;
      cursor: pointer;
      transition: border-color 0.15s, background 0.15s;
    }

    .upload-zone:hover,
    .upload-zone.drag-over {
      border-color: var(--accent);
      background: rgba(200, 185, 122, 0.04);
    }

    .upload-zone p {
      font-family: 'DM Mono', monospace;
      font-size: 0.75rem;
      color: var(--muted);
      margin: 0;
      pointer-events: none;
    }

    .upload-zone input[type="file"] {
      display: none;
    }

    .file-list {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
      margin-top: 0.5rem;
    }

    .file-item {
      display: grid;
      grid-template-columns: 1fr auto auto;
      align-items: center;
      gap: 0.4rem 0.5rem;
      padding: 0.35rem 0.5rem;
      border-radius: 4px;
      background: rgba(255,255,255,0.025);
      border: 1px solid var(--border);
    }

    .file-item--error {
      border-color: #c0392b;
    }

    .file-name {
      font-family: 'DM Mono', monospace;
      font-size: 0.75rem;
      color: var(--text);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }

    .file-size {
      font-family: 'DM Mono', monospace;
      font-size: 0.7rem;
      color: var(--muted);
      white-space: nowrap;
    }

    .file-error {
      grid-column: 1 / -1;
      font-family: 'DM Mono', monospace;
      font-size: 0.7rem;
      color: #e74c3c;
    }

    .upload-total {
      font-family: 'DM Mono', monospace;
      font-size: 0.7rem;
      color: var(--muted);
      margin-top: 0.4rem;
      text-align: right;
    }

    .upload-total--error {
      color: #e74c3c;
    }
```

- [ ] **Step 3: Add the upload zone HTML inside the Advanced section**

Find the closing `</div>` of the `<div class="form-group repeatable-full">` containing the GitHub repos list, and the `</div>` closing the `advanced-grid`. Add the new upload zone group between those two:

Replace:
```html
            <div class="form-group repeatable-full">
              <label>GitHub repos (owner/repo)</label>
              <div class="repeatable-list" id="github-list"></div>
              <button type="button" class="btn-add" id="add-github">+ Add repo</button>
            </div>

          </div>
        </details>
```

With:
```html
            <div class="form-group repeatable-full">
              <label>GitHub repos (owner/repo)</label>
              <div class="repeatable-list" id="github-list"></div>
              <button type="button" class="btn-add" id="add-github">+ Add repo</button>
            </div>

            <div class="form-group repeatable-full">
              <label>Local files (source material)</label>
              <div class="upload-zone" id="upload-zone">
                <p>Drop files here or <span style="color:var(--accent);text-decoration:underline;cursor:pointer;" id="upload-browse-trigger">click to browse</span></p>
                <p style="margin-top:0.3rem;font-size:0.65rem;">PDF · DOCX · images · code &amp; text files</p>
                <input type="file" id="file-input" multiple
                  accept=".txt,.md,.py,.js,.ts,.jsx,.tsx,.go,.rs,.java,.c,.cpp,.h,.hpp,.rb,.swift,.kt,.sh,.bash,.zsh,.yaml,.yml,.json,.toml,.csv,.html,.css,.scss,.xml,.ini,.env,.sql,.graphql,.proto,.tf,.hcl,.vue,.php,.scala,.dart,.elm,.png,.jpg,.jpeg,.gif,.webp,.pdf,.docx" />
              </div>
              <div class="file-list" id="file-list"></div>
              <div class="upload-total" id="upload-total" style="display:none;"></div>
            </div>

          </div>
        </details>
```

- [ ] **Step 4: Add JS for file management and drag-and-drop**

Find the existing line:
```javascript
    document.getElementById('add-url').addEventListener('click', () =>
```

Before that line, insert the complete file upload JS block:

```javascript
    // ── File upload ──────────────────────────────────────────────────────────

    const FILE_LIMITS = { text: 2*1024*1024, image: 5*1024*1024, pdf: 20*1024*1024, docx: 10*1024*1024 };
    const TOTAL_LIMIT = 24 * 1024 * 1024;
    const TEXT_EXTS = new Set(['.txt','.md','.py','.js','.ts','.jsx','.tsx','.go','.rs',
      '.java','.c','.cpp','.h','.hpp','.rb','.swift','.kt','.sh','.bash','.zsh',
      '.yaml','.yml','.json','.toml','.csv','.html','.css','.scss','.xml','.ini',
      '.env','.sql','.graphql','.proto','.tf','.hcl','.vue','.php','.scala','.dart','.elm']);
    const IMAGE_EXTS = new Set(['.png','.jpg','.jpeg','.gif','.webp']);

    let selectedFiles = []; // [{file: File, error: string|null}]

    function getFileCategory(file) {
      const ext = '.' + (file.name.split('.').pop() || '').toLowerCase();
      if (IMAGE_EXTS.has(ext)) return 'image';
      if (ext === '.pdf') return 'pdf';
      if (ext === '.docx') return 'docx';
      if (TEXT_EXTS.has(ext)) return 'text';
      return 'unsupported';
    }

    function formatBytes(n) {
      if (n < 1024) return n + ' B';
      if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
      return (n / (1024 * 1024)).toFixed(1) + ' MB';
    }

    function validateFile(file) {
      const cat = getFileCategory(file);
      if (cat === 'unsupported') return `Unsupported type — use PDF, DOCX, images, or text/code files`;
      const limit = FILE_LIMITS[cat];
      if (file.size > limit) {
        return `${formatBytes(file.size)} exceeds the ${formatBytes(limit)} limit for ${cat} files`;
      }
      return null;
    }

    function renderFileList() {
      const listEl = document.getElementById('file-list');
      const totalEl = document.getElementById('upload-total');
      listEl.innerHTML = '';

      selectedFiles.forEach(({file, error}, i) => {
        const item = document.createElement('div');
        item.className = 'file-item' + (error ? ' file-item--error' : '');

        const nameEl = document.createElement('span');
        nameEl.className = 'file-name';
        nameEl.textContent = file.name;

        const sizeEl = document.createElement('span');
        sizeEl.className = 'file-size';
        sizeEl.textContent = formatBytes(file.size);

        const removeBtn = document.createElement('button');
        removeBtn.type = 'button';
        removeBtn.className = 'btn-remove';
        removeBtn.textContent = '✕';
        removeBtn.addEventListener('click', () => {
          selectedFiles.splice(i, 1);
          renderFileList();
        });

        item.appendChild(nameEl);
        item.appendChild(sizeEl);
        item.appendChild(removeBtn);

        if (error) {
          const errEl = document.createElement('span');
          errEl.className = 'file-error';
          errEl.textContent = error;
          item.appendChild(errEl);
        }

        listEl.appendChild(item);
      });

      // Total size counter
      const total = selectedFiles.reduce((s, {file}) => s + file.size, 0);
      const overTotal = total > TOTAL_LIMIT;
      if (selectedFiles.length > 0) {
        totalEl.style.display = '';
        totalEl.textContent = `Total: ${formatBytes(total)} / ${formatBytes(TOTAL_LIMIT)}`;
        totalEl.className = 'upload-total' + (overTotal ? ' upload-total--error' : '');
      } else {
        totalEl.style.display = 'none';
      }

      // Disable submit if any per-file error or total exceeded
      const hasErrors = overTotal || selectedFiles.some(({error}) => error);
      submitBtn.disabled = hasErrors;
    }

    function addFiles(newFiles) {
      for (const file of newFiles) {
        // Skip duplicates (same name + size)
        const isDupe = selectedFiles.some(
          ({file: f}) => f.name === file.name && f.size === file.size
        );
        if (isDupe) continue;
        selectedFiles.push({file, error: validateFile(file)});
      }
      renderFileList();
    }

    const uploadZone = document.getElementById('upload-zone');
    const fileInput  = document.getElementById('file-input');

    document.getElementById('upload-browse-trigger').addEventListener('click', (e) => {
      e.stopPropagation();
      fileInput.click();
    });
    uploadZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      addFiles(Array.from(fileInput.files));
      fileInput.value = ''; // allow re-selecting the same file
    });
    uploadZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      uploadZone.classList.add('drag-over');
    });
    uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag-over'));
    uploadZone.addEventListener('drop', (e) => {
      e.preventDefault();
      uploadZone.classList.remove('drag-over');
      addFiles(Array.from(e.dataTransfer.files));
    });
```

- [ ] **Step 5: Update the submit handler to use FormData when files are present**

Find the existing submit handler block that starts with:
```javascript
      const body = {
        topic,
        effort:         document.getElementById('effort').value,
```

Replace the entire block from `const body = {` through the closing `}` of the `fetch` try/catch (everything up to `startSSE(job.id);`) with:

```javascript
      const hasFiles = selectedFiles.length > 0 && !selectedFiles.some(({error}) => error);
      const totalSize = selectedFiles.reduce((s, {file}) => s + file.size, 0);
      if (totalSize > TOTAL_LIMIT) {
        showError(`Total file size (${formatBytes(totalSize)}) exceeds the ${formatBytes(TOTAL_LIMIT)} limit`);
        submitBtn.disabled = false;
        return;
      }

      let resp, job;
      try {
        if (hasFiles) {
          // Multipart path: files + form fields → /api/jobs/upload
          const fd = new FormData();
          fd.append('topic',         topic);
          fd.append('effort',        document.getElementById('effort').value);
          fd.append('audience',      document.getElementById('audience').value);
          fd.append('tone',          document.getElementById('tone').value);
          fd.append('theme',         document.getElementById('theme').value);
          fd.append('template',      document.getElementById('template').value);
          fd.append('speed',         document.getElementById('speed').value);
          fd.append('burn_captions', document.getElementById('burn-captions').checked ? 'true' : 'false');
          fd.append('quiz',          document.getElementById('quiz').checked ? 'true' : 'false');
          fd.append('qa_density',    document.getElementById('qa-density').value);
          for (const url of getRepeatableValues('url-list'))    fd.append('urls', url);
          for (const repo of getRepeatableValues('github-list')) fd.append('github', repo);
          for (const {file} of selectedFiles)                   fd.append('files', file);

          resp = await fetch('/api/jobs/upload', { method: 'POST', body: fd });
        } else {
          // JSON path: no files → /api/jobs
          const body = {
            topic,
            effort:        document.getElementById('effort').value,
            audience:      document.getElementById('audience').value,
            tone:          document.getElementById('tone').value,
            theme:         document.getElementById('theme').value,
            template:      document.getElementById('template').value || null,
            speed:         parseFloat(document.getElementById('speed').value) || 1.0,
            burn_captions: document.getElementById('burn-captions').checked,
            quiz:          document.getElementById('quiz').checked,
            qa_density:    document.getElementById('qa-density').value,
            urls:          getRepeatableValues('url-list'),
            github:        getRepeatableValues('github-list'),
          };
          resp = await fetch('/api/jobs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
        }

        if (!resp.ok) {
          const text = await resp.text();
          throw new Error(`HTTP ${resp.status}: ${text}`);
        }
        job = await resp.json();
      } catch (err) {
        showError(`Failed to start job: ${err.message}`);
        submitBtn.disabled = false;
        return;
      }
```

- [ ] **Step 6: Run the full test suite**

```bash
pytest --tb=short -q
```

Expected: all passing (183+).

- [ ] **Step 7: Manual smoke test**

```bash
python3 run_server.py
```

Open `http://localhost:8071`. Verify:

1. "Local files" section appears in Advanced options (collapsed by default)
2. Clicking the zone or "click to browse" opens the file picker
3. Dragging a `.py` file onto the zone adds it to the list with correct size display
4. Dragging an unsupported file (e.g. `.zip`) shows a red inline error
5. Adding a file that would exceed its type limit shows a red inline error with the specific limit
6. Adding multiple files that together exceed 24 MB shows the total counter turn red and the submit button disable
7. Removing a file with an error re-enables the submit button if no more errors remain
8. Submitting with a valid `.py` file (no errors): check browser Network tab — request goes to `/api/jobs/upload` as `multipart/form-data`
9. Submitting with no files: request goes to `/api/jobs` as `application/json` (unchanged behaviour)
10. A job submitted with an uploaded file completes successfully (context from the file appears in the pipeline's script)

- [ ] **Step 8: Commit**

```bash
git add server/static/index.html
git commit -m "feat: add file upload zone to UI with client-side size validation and drag-and-drop"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task |
|---|---|
| Per-file limits: text 2 MB, image 5 MB, PDF 20 MB, docx 10 MB | Task 1 (`LIMITS` dict) + Task 3 (`FILE_LIMITS` JS) |
| Total upload limit: 24 MB | Task 1 (`TOTAL_LIMIT`) + Task 3 (`TOTAL_LIMIT` JS) |
| Unsupported file type rejection (400) | Task 1 (`UnsupportedFileTypeError`) + Task 2 route |
| File size error (413) | Task 1 (`FileSizeError`, `TotalSizeError`) + Task 2 route |
| Inline per-file error in UI | Task 3 (`.file-item--error` + `.file-error` span) |
| Total size counter with error state | Task 3 (`.upload-total--error`) |
| Submit disabled when errors present | Task 3 (`submitBtn.disabled = hasErrors`) |
| Drag-and-drop | Task 3 (dragover/dragleave/drop handlers) |
| Click to browse | Task 3 (click → `fileInput.click()`) |
| Duplicate file prevention | Task 3 (`isDupe` check by name+size) |
| Temp dir cleanup on success | Task 2 (`finally` block in `run_job`) |
| Temp dir cleanup on failure | Task 2 (`finally` block in `run_job`) |
| Backward compatible JSON endpoint unchanged | Task 2 (`POST /api/jobs` untouched) |
| File blocks loaded before URL blocks | Task 2 (`run_job` — file blocks first, then URLs, then GitHub) |
| Starlette 20 MB per-part limit | Task 2 (`server/app.py` MultiPartParser config) |

**Placeholder scan:** No TBDs, no "add appropriate handling" vagueness. Every step has exact code.

**Type consistency check:**
- `validate_and_save(files: list[UploadFile], tmp_dir: Path) -> list[Path]` — used in Task 1 tests and Task 2 route with identical signature ✅
- `file_category(filename: str) -> str` — returns one of `"text"`, `"image"`, `"pdf"`, `"docx"`, `"unsupported"` — matches the keys in `LIMITS` dict and in the JS `FILE_LIMITS` object ✅
- `Job.upload_dir: Path | None` — set in `JobStore.create(upload_dir=...)`, read in `run_job`, cleaned up in `finally` ✅
- `FileSizeError`, `TotalSizeError`, `UnsupportedFileTypeError` — all subclass `UploadValidationError`; imported in routes.py from `server.upload`; caught individually for different HTTP status codes ✅
