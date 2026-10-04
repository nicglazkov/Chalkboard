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
    # Unrecorded values stay unknown rather than defaulting to a plausible guess.
    assert meta.duration_sec is None
    assert meta.quality is None
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


# ── Notes ─────────────────────────────────────────────────────────────────────

import sqlite3

_OLD_SCHEMA = """
CREATE TABLE videos (
    run_id       TEXT PRIMARY KEY,
    topic        TEXT NOT NULL,
    duration_sec REAL DEFAULT 0,
    quality      TEXT DEFAULT 'medium',
    created_at   TEXT NOT NULL,
    thumb_path   TEXT,
    script       TEXT DEFAULT '',
    effort       TEXT DEFAULT 'medium',
    audience     TEXT DEFAULT 'intermediate',
    tone         TEXT DEFAULT 'casual',
    theme        TEXT DEFAULT 'chalkboard',
    template     TEXT,
    speed        REAL DEFAULT 1.0,
    status       TEXT DEFAULT 'completed'
)
"""


def test_init_migrates_old_schema_in_place(tmp_path):
    """A library.db from before title/narrator/notes keeps every row and gains the columns."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(_OLD_SCHEMA)
    con.executemany(
        "INSERT INTO videos (run_id, topic, created_at, script) VALUES (?,?,?,?)",
        [("a", "alpha", "2026-01-01T00:00:00Z", "s1"), ("b", "beta", "2026-01-02T00:00:00Z", "s2")],
    )
    con.commit()
    con.close()

    s = SQLiteLibraryStore(str(path))
    asyncio.run(s.init())
    asyncio.run(s.init())  # idempotent
    cols = {r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(videos)")}
    assert {"title", "narrator", "notes"} <= cols
    videos, total = asyncio.run(s.list_videos())
    assert total == 2
    assert {v.run_id for v in videos} == {"a", "b"}
    assert all(v.notes is None for v in videos)
    assert asyncio.run(s.set_notes("a", "kept")).notes == "kept"


def test_set_notes_trims_and_empty_is_none(store):
    asyncio.run(store.add_video(_make_meta()))
    assert asyncio.run(store.set_notes("run-001", "  check the  sync  \n")).notes == "check the  sync"
    assert asyncio.run(store.set_notes("run-001", "   ")).notes is None
    assert asyncio.run(store.set_notes("run-001", "x")).notes == "x"
    assert asyncio.run(store.set_notes("run-001", None)).notes is None


def test_set_notes_unknown_run_returns_none(store):
    assert asyncio.run(store.set_notes("nope", "hi")) is None


def test_reindexing_keeps_notes(store):
    """add_video (backfill / finished job) must not wipe the user's notes."""
    asyncio.run(store.add_video(_make_meta(topic="original")))
    asyncio.run(store.set_notes("run-001", "my note"))
    asyncio.run(store.add_video(_make_meta(topic="updated")))
    result = asyncio.run(store.get_video("run-001"))
    assert result.topic == "updated"
    assert result.notes == "my note"


def test_list_videos_search_by_notes(store):
    asyncio.run(store.add_video(_make_meta(run_id="a", topic="Alpha")))
    asyncio.run(store.add_video(_make_meta(run_id="b", topic="Beta")))
    asyncio.run(store.set_notes("b", "Follow up on Fourier transforms"))
    videos, total = asyncio.run(store.list_videos(query="fourier"))
    assert total == 1
    assert videos[0].run_id == "b"
