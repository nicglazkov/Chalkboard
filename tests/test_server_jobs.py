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


@pytest.mark.asyncio
async def test_run_job_passes_burn_captions_to_render(tmp_path):
    """burn_captions=True on the job must be forwarded to _do_render."""
    import json
    from server.jobs import JobStore, run_job
    from unittest.mock import patch
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       burn_captions=True)

    render_calls = []

    async def fake_run(**kwargs):
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, burn_captions=False, **kwargs):
        render_calls.append({"burn_captions": burn_captions})
        return tmp_path / "final.mp4"

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render):
        await run_job(job, tmp_path)

    assert render_calls[0]["burn_captions"] is True


@pytest.mark.asyncio
async def test_run_job_calls_generate_quiz(tmp_path):
    """quiz=True must trigger _generate_quiz after render."""
    import json
    from server.jobs import JobStore, run_job
    from unittest.mock import patch
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       quiz=True)

    quiz_calls = []

    async def fake_run(**kwargs):
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return tmp_path / "final.mp4"

    def fake_quiz(run_id):
        quiz_calls.append(run_id)

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render), \
         patch("server.jobs._generate_quiz", new=fake_quiz):
        await run_job(job, tmp_path)

    assert quiz_calls == [job.id]


@pytest.mark.asyncio
async def test_run_job_skips_qa_when_density_zero(tmp_path):
    """qa_density='zero' must not call _run_qa_loop."""
    import json
    from server.jobs import JobStore, run_job
    from unittest.mock import patch
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       qa_density="zero")

    qa_calls = []

    async def fake_run(**kwargs):
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return tmp_path / "final.mp4"

    def fake_qa_loop(*args, **kwargs):
        qa_calls.append(True)

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render), \
         patch("server.jobs._run_qa_loop", new=fake_qa_loop):
        await run_job(job, tmp_path)

    assert qa_calls == []


@pytest.mark.asyncio
async def test_run_job_runs_qa_when_density_normal(tmp_path):
    """qa_density='normal' must call _run_qa_loop with the final mp4 path."""
    import json
    from server.jobs import JobStore, run_job
    from unittest.mock import patch
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       qa_density="normal")

    qa_calls = []
    final_mp4_path = tmp_path / "final.mp4"

    async def fake_run(**kwargs):
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return final_mp4_path

    def fake_qa_loop(run_id, final_mp4, **kwargs):
        qa_calls.append({"run_id": run_id, "mp4": final_mp4})

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render), \
         patch("server.jobs._run_qa_loop", new=fake_qa_loop):
        await run_job(job, tmp_path)

    assert len(qa_calls) == 1
    assert qa_calls[0]["run_id"] == job.id
    assert qa_calls[0]["mp4"] == final_mp4_path


@pytest.mark.asyncio
async def test_run_job_fetches_url_context(tmp_path):
    """URLs on the job must be fetched and passed as context_blocks to run()."""
    import json
    from server.jobs import JobStore, run_job
    from unittest.mock import patch
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       urls=["https://example.com/page"])

    run_kwargs = {}

    async def fake_run(**kwargs):
        run_kwargs.update(kwargs)
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return tmp_path / "final.mp4"

    def fake_fetch(url):
        return [{"type": "text", "text": f"content from {url}"}]

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render), \
         patch("server.jobs.fetch_url_blocks", new=fake_fetch):
        await run_job(job, tmp_path)

    assert run_kwargs.get("context_blocks") == [{"type": "text", "text": "content from https://example.com/page"}]


@pytest.mark.asyncio
async def test_run_job_fetches_github_context(tmp_path):
    """GitHub repos on the job must be resolved to raw URLs and fetched."""
    import json
    from server.jobs import JobStore, run_job
    from unittest.mock import patch
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       github=["owner/repo"])

    run_kwargs = {}

    async def fake_run(**kwargs):
        run_kwargs.update(kwargs)
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return tmp_path / "final.mp4"

    def fake_fetch(url):
        return [{"type": "text", "text": f"readme from {url}"}]

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render), \
         patch("server.jobs.fetch_url_blocks", new=fake_fetch):
        await run_job(job, tmp_path)

    assert run_kwargs.get("context_blocks") is not None
    assert "raw.githubusercontent.com" in run_kwargs["context_blocks"][0]["text"]
