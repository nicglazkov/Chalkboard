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


def test_run_job_sets_status_completed(tmp_path):
    """run_job must mark the job completed after the pipeline finishes."""
    from server.jobs import JobStore, run_job
    from unittest.mock import patch, AsyncMock
    import json

    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)

    async def fake_run(**kwargs):
        # Simulate render_trigger writing manifest.json
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
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


def test_job_store_create_accepts_new_fields():
    store = JobStore()
    job = store.create(
        topic="test", effort="low", audience="intermediate",
        tone="casual", theme="chalkboard", template=None, speed=1.0,
        burn_captions=True, quiz=True,
        urls=["https://example.com"], github=["owner/repo"],
        qa_density="high",
    )
    assert job.burn_captions is True
    assert job.quiz is True
    assert job.urls == ["https://example.com"]
    assert job.github == ["owner/repo"]
    assert job.qa_density == "high"


def test_job_store_create_new_fields_default():
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)
    assert job.burn_captions is False
    assert job.quiz is False
    assert job.urls == []
    assert job.github == []
    assert job.qa_density == "normal"
