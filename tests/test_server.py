# tests/test_server.py
import asyncio
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from server.app import create_app
from server.jobs import JobStore, Job, run_job
from pipeline.retry import TimeoutExhausted


@pytest.fixture
def client():
    store = JobStore()
    app = create_app(store)
    with TestClient(app) as tc:
        yield tc, store


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


def test_get_file_rejects_path_traversal(client, tmp_path):
    tc, store = client
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)
    with patch("server.routes.OUTPUT_DIR", str(tmp_path)):
        resp = tc.get(f"/api/jobs/{job.id}/files/../../etc/passwd")
    assert resp.status_code == 404


def test_static_index_served(client):
    """GET / must return the index.html page."""
    tc, store = client
    resp = tc.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_run_job_fails_when_pipeline_raises(tmp_path):
    """TimeoutExhausted from run() must mark the job as failed with a real error message."""
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)

    async def fake_run(**kwargs):
        raise TimeoutExhausted("kokoro_tts failed after 3 attempts: PyTorch not found")

    with patch("server.jobs.run", new=fake_run):
        await run_job(job, tmp_path)

    assert job.status == "failed"
    assert "kokoro_tts" in job.error


@pytest.mark.asyncio
async def test_run_job_fails_when_manifest_missing(tmp_path):
    """If run() returns normally but manifest.json is absent, job must fail (not attempt render)."""
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)

    async def fake_run(**kwargs):
        pass  # run() completes but writes nothing (e.g. escalate_to_user auto-aborted)

    with patch("server.jobs.run", new=fake_run):
        await run_job(job, tmp_path)

    assert job.status == "failed"
    assert "pipeline did not complete" in job.error


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


def test_upload_endpoint_cleans_up_tmp_dir_on_unexpected_error(tmp_path, monkeypatch):
    """temp dir must be deleted even when validate_and_save raises an unexpected exception."""
    from server.routes import make_router
    from server.jobs import JobStore

    created_dirs = []

    def fake_mkdtemp(prefix=""):
        d = str(tmp_path / "upload_tmp")
        import os
        os.makedirs(d, exist_ok=True)
        created_dirs.append(d)
        return d

    async def exploding_validate(files, tmp_dir):
        raise OSError("disk full")

    store = JobStore()
    app = create_app(store)
    monkeypatch.setattr("server.routes.tempfile.mkdtemp", fake_mkdtemp)
    monkeypatch.setattr("server.routes.validate_and_save", exploding_validate)

    from fastapi.testclient import TestClient
    with TestClient(app, raise_server_exceptions=False) as tc:
        resp = tc.post(
            "/api/jobs/upload",
            data={"topic": "test"},
            files={"files": ("notes.txt", b"hello", "text/plain")},
        )

    assert resp.status_code == 500
    from pathlib import Path
    for d in created_dirs:
        assert not Path(d).exists(), f"temp dir {d} was not cleaned up"
