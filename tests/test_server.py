# tests/test_server.py
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient
from server.app import create_app
from server.jobs import JobStore


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
