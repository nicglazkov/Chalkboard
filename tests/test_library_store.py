import asyncio
import pytest
from server.library import VideoMeta, LibraryStore, SQLiteLibraryStore


# ── Task 2 tests ──────────────────────────────────────────────────────────────

def test_video_meta_instantiation():
    meta = VideoMeta(
        run_id="abc-123",
        topic="explain recursion",
        created_at="2026-04-07T10:00:00Z",
    )
    assert meta.run_id == "abc-123"
    assert meta.duration_sec == 0.0
    assert meta.quality == "medium"
    assert meta.thumb_path is None
    assert meta.output_files == []


def test_library_store_is_abstract():
    with pytest.raises(TypeError):
        LibraryStore()


# ── Task 3 tests ──────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    s = SQLiteLibraryStore(str(tmp_path / "test_library.db"))
    asyncio.run(s.init())
    return s


def _make_meta(**kwargs) -> VideoMeta:
    defaults = dict(
        run_id="run-001",
        topic="explain recursion",
        created_at="2026-04-07T10:00:00Z",
        duration_sec=192.5,
        quality="medium",
        script="Recursion is when a function calls itself.",
    )
    defaults.update(kwargs)
    return VideoMeta(**defaults)


def test_add_and_get_video(store):
    meta = _make_meta()
    asyncio.run(store.add_video(meta))
    result = asyncio.run(store.get_video("run-001"))
    assert result is not None
    assert result.run_id == "run-001"
    assert result.topic == "explain recursion"
    assert result.duration_sec == pytest.approx(192.5)


def test_get_missing_video_returns_none(store):
    result = asyncio.run(store.get_video("does-not-exist"))
    assert result is None


def test_add_video_is_idempotent(store):
    meta = _make_meta(topic="original")
    asyncio.run(store.add_video(meta))
    updated = _make_meta(topic="updated")
    asyncio.run(store.add_video(updated))
    result = asyncio.run(store.get_video("run-001"))
    assert result.topic == "updated"


# ── Task 4 tests ──────────────────────────────────────────────────────────────

def test_list_videos_returns_all(store):
    asyncio.run(store.add_video(_make_meta(run_id="a", topic="Alpha", created_at="2026-01-01T00:00:00Z", duration_sec=60)))
    asyncio.run(store.add_video(_make_meta(run_id="b", topic="Beta",  created_at="2026-01-02T00:00:00Z", duration_sec=120)))
    videos, total = asyncio.run(store.list_videos())
    assert total == 2
    assert len(videos) == 2


def test_list_videos_search_by_topic(store):
    asyncio.run(store.add_video(_make_meta(run_id="a", topic="explain recursion", script="A function calls itself.")))
    asyncio.run(store.add_video(_make_meta(run_id="b", topic="sorting algorithms", script="Bubble sort compares adjacent elements.")))
    videos, total = asyncio.run(store.list_videos(query="recursion"))
    assert total == 1
    assert videos[0].run_id == "a"


def test_list_videos_search_by_script(store):
    asyncio.run(store.add_video(_make_meta(run_id="a", script="Big O notation measures complexity")))
    asyncio.run(store.add_video(_make_meta(run_id="b", script="Recursion calls itself")))
    videos, total = asyncio.run(store.list_videos(query="Big O"))
    assert total == 1
    assert videos[0].run_id == "a"


def test_list_videos_sort_newest_first(store):
    asyncio.run(store.add_video(_make_meta(run_id="old", created_at="2026-01-01T00:00:00Z")))
    asyncio.run(store.add_video(_make_meta(run_id="new", created_at="2026-06-01T00:00:00Z")))
    videos, _ = asyncio.run(store.list_videos(sort="newest"))
    assert videos[0].run_id == "new"


def test_list_videos_sort_longest_first(store):
    asyncio.run(store.add_video(_make_meta(run_id="short", duration_sec=60)))
    asyncio.run(store.add_video(_make_meta(run_id="long",  duration_sec=600)))
    videos, _ = asyncio.run(store.list_videos(sort="longest"))
    assert videos[0].run_id == "long"


def test_list_videos_sort_oldest_first(store):
    asyncio.run(store.add_video(_make_meta(run_id="old", created_at="2026-01-01T00:00:00Z")))
    asyncio.run(store.add_video(_make_meta(run_id="new", created_at="2026-06-01T00:00:00Z")))
    videos, _ = asyncio.run(store.list_videos(sort="oldest"))
    assert videos[0].run_id == "old"


def test_list_videos_sort_shortest_first(store):
    asyncio.run(store.add_video(_make_meta(run_id="short", duration_sec=60)))
    asyncio.run(store.add_video(_make_meta(run_id="long",  duration_sec=600)))
    videos, _ = asyncio.run(store.list_videos(sort="shortest"))
    assert videos[0].run_id == "short"


def test_list_videos_pagination(store):
    for i in range(5):
        asyncio.run(store.add_video(_make_meta(run_id=f"run-{i}", topic=f"topic {i}")))
    videos, total = asyncio.run(store.list_videos(limit=2, offset=0))
    assert total == 5
    assert len(videos) == 2
    page2, _ = asyncio.run(store.list_videos(limit=2, offset=2))
    assert len(page2) == 2
    assert {v.run_id for v in videos}.isdisjoint({v.run_id for v in page2})


# ── Task 5 tests ──────────────────────────────────────────────────────────────

def test_delete_video(store):
    asyncio.run(store.add_video(_make_meta()))
    asyncio.run(store.delete_video("run-001"))
    assert asyncio.run(store.get_video("run-001")) is None


def test_delete_missing_video_is_noop(store):
    asyncio.run(store.delete_video("does-not-exist"))  # should not raise
