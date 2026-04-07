# tests/test_library_routes.py
import asyncio
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from fastapi import FastAPI
from server.library import SQLiteLibraryStore, VideoMeta
from server.library_routes import make_library_router


@pytest.fixture
def lib_client(tmp_path):
    store = SQLiteLibraryStore(str(tmp_path / "lib.db"))
    asyncio.run(store.init())
    app = FastAPI()
    app.include_router(make_library_router(store, output_dir=tmp_path))
    return TestClient(app), store, tmp_path


def _add(store, run_id="r1", topic="test topic", script="test script", **kw):
    meta = VideoMeta(run_id=run_id, topic=topic, script=script,
                     created_at="2026-04-07T10:00:00Z", **kw)
    asyncio.run(store.add_video(meta))
    return meta


def test_list_empty(lib_client):
    tc, store, _ = lib_client
    resp = tc.get("/api/library")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["videos"] == []


def test_list_returns_videos(lib_client):
    tc, store, _ = lib_client
    _add(store, run_id="r1", topic="recursion")
    _add(store, run_id="r2", topic="sorting")
    resp = tc.get("/api/library")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_list_search(lib_client):
    tc, store, _ = lib_client
    _add(store, run_id="r1", topic="recursion explained")
    _add(store, run_id="r2", topic="sorting algorithms")
    resp = tc.get("/api/library?q=recursion")
    assert resp.json()["total"] == 1
    assert resp.json()["videos"][0]["run_id"] == "r1"


def test_get_video(lib_client):
    tc, store, tmp_path = lib_client
    _add(store, run_id="r1")
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "final.mp4").write_bytes(b"x")
    resp = tc.get("/api/library/r1")
    assert resp.status_code == 200
    assert resp.json()["run_id"] == "r1"
    assert "final.mp4" in resp.json()["output_files"]


def test_get_missing_video_returns_404(lib_client):
    tc, store, _ = lib_client
    resp = tc.get("/api/library/does-not-exist")
    assert resp.status_code == 404


def test_delete_video(lib_client):
    tc, store, _ = lib_client
    _add(store, run_id="r1")
    resp = tc.delete("/api/library/r1")
    assert resp.status_code == 204
    assert asyncio.run(store.get_video("r1")) is None


def test_delete_missing_returns_204(lib_client):
    tc, store, _ = lib_client
    resp = tc.delete("/api/library/does-not-exist")
    assert resp.status_code == 204


def test_list_limit_capped_at_100(lib_client):
    tc, store, _ = lib_client
    resp = tc.get("/api/library?limit=999")
    assert resp.json()["limit"] == 100
