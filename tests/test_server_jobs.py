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
