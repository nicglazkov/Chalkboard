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


def test_delete_with_files_removes_directory(lib_client):
    tc, store, tmp_path = lib_client
    _add(store, run_id="r1")
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    (run_dir / "final.mp4").write_bytes(b"video-data")
    (run_dir / "scene.py").write_text("code")
    resp = tc.delete("/api/library/r1?files=true")
    assert resp.status_code == 204
    assert asyncio.run(store.get_video("r1")) is None
    assert not run_dir.exists()


def test_delete_without_files_preserves_directory(lib_client):
    tc, store, tmp_path = lib_client
    _add(store, run_id="r1")
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    (run_dir / "final.mp4").write_bytes(b"video-data")
    resp = tc.delete("/api/library/r1")
    assert resp.status_code == 204
    assert asyncio.run(store.get_video("r1")) is None
    assert run_dir.exists()
    assert (run_dir / "final.mp4").exists()


def test_delete_with_files_no_directory(lib_client):
    tc, store, tmp_path = lib_client
    _add(store, run_id="r1")
    run_dir = tmp_path / "r1"
    assert not run_dir.exists()
    resp = tc.delete("/api/library/r1?files=true")
    assert resp.status_code == 204
    assert asyncio.run(store.get_video("r1")) is None


def test_list_limit_capped_at_100(lib_client):
    tc, store, _ = lib_client
    resp = tc.get("/api/library?limit=999")
    assert resp.json()["limit"] == 100


def test_delete_rejects_dot_segments(tmp_path):
    """DELETE /api/library/%2e%2e?files=true must not rmtree the output dir's parent."""
    out = tmp_path / "out"
    out.mkdir()
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("x")
    store = SQLiteLibraryStore(str(tmp_path / "lib.db"))
    asyncio.run(store.init())
    app = FastAPI()
    app.include_router(make_library_router(store, output_dir=out))
    tc = TestClient(app)
    for rid in ("%2e%2e", "%2e"):
        resp = tc.delete(f"/api/library/{rid}?files=true")
        assert resp.status_code == 404
    assert sentinel.exists()
    assert out.exists()


# ── Notes (PATCH /api/library/{run_id}) ───────────────────────────────────────

def test_patch_notes_saves_and_shows_in_list_and_detail(lib_client):
    tc, store, _ = lib_client
    _add(store, run_id="r1")
    resp = tc.patch("/api/library/r1", json={"notes": "  Redo scene 3, cue late  "})
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == "r1"
    assert body["notes"] == "Redo scene 3, cue late"
    assert body["saved_at"].endswith("Z")
    assert tc.get("/api/library/r1").json()["notes"] == "Redo scene 3, cue late"
    assert tc.get("/api/library").json()["videos"][0]["notes"] == "Redo scene 3, cue late"


def test_patch_notes_empty_or_null_clears(lib_client):
    tc, store, _ = lib_client
    _add(store, run_id="r1")
    tc.patch("/api/library/r1", json={"notes": "something"})
    assert tc.patch("/api/library/r1", json={"notes": "   "}).json()["notes"] is None
    tc.patch("/api/library/r1", json={"notes": "something"})
    assert tc.patch("/api/library/r1", json={"notes": None}).json()["notes"] is None
    assert tc.get("/api/library/r1").json()["notes"] is None


def test_patch_notes_validation(lib_client):
    tc, store, _ = lib_client
    _add(store, run_id="r1")
    assert tc.patch("/api/library/r1", json={"notes": "x" * 10_001}).status_code == 422
    assert tc.patch("/api/library/r1", json={"notes": "x" * 10_000}).status_code == 200
    assert tc.patch("/api/library/r1", json={"notes": 5}).status_code == 422
    assert tc.patch("/api/library/r1", json={}).status_code == 422
    # A rejected request leaves the stored notes alone.
    assert tc.get("/api/library/r1").json()["notes"] == "x" * 10_000


def test_patch_notes_unknown_run_404(lib_client):
    tc, _, _ = lib_client
    assert tc.patch("/api/library/nope", json={"notes": "hi"}).status_code == 404


def test_list_search_matches_notes(lib_client):
    tc, store, _ = lib_client
    _add(store, run_id="r1", topic="recursion")
    _add(store, run_id="r2", topic="sorting")
    tc.patch("/api/library/r2", json={"notes": "Show this one to Dimitri"})
    resp = tc.get("/api/library?q=dimitri")
    assert resp.json()["total"] == 1
    assert resp.json()["videos"][0]["run_id"] == "r2"
