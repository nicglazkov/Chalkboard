# Video Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a YouTube-style video library at `/library` that indexes all completed Chalkboard runs in SQLite, serves a 4-column grid with search/sort, and a full detail page per video with player, downloads, script, and related videos.

**Architecture:** A `LibraryStore` abstract interface backed by `SQLiteLibraryStore` (`library.db`) locally. `render_trigger.py` writes extended metadata to `manifest.json`; `run_job` calls `library_store.add_video()` on completion; a startup backfill event in `create_app` indexes all pre-existing runs. Two new static HTML pages (`library.html`, `video.html`) consume new `/api/library` endpoints.

**Tech Stack:** Python 3.10, FastAPI, aiosqlite (already in requirements.txt), Pydantic v2, vanilla HTML/CSS/JS (no build step), ffmpeg (already required).

---

## File Map

**Create:**
- `server/library.py` — `VideoMeta` Pydantic model, `LibraryStore` ABC, `SQLiteLibraryStore`
- `server/library_routes.py` — `/api/library` API endpoints + `/library` page routes
- `server/static/library.html` — 4-col grid page
- `server/static/video.html` — detail/player page
- `tests/test_library_store.py` — unit tests for `SQLiteLibraryStore`
- `tests/test_library_routes.py` — integration tests for library API endpoints

**Modify:**
- `pipeline/render_trigger.py` — add `effort`, `audience`, `tone`, `theme`, `template`, `speed` to `manifest.json`
- `main.py` — add `_extract_thumbnail(run_dir)` helper; call it from `_render_once` after ffmpeg merge
- `server/app.py` — accept `library_store` param; register startup backfill; mount library router
- `server/routes.py` — accept `library_store` param; pass to `run_job`
- `server/jobs.py` — accept `library_store` param in `run_job`; call `add_video` on completion
- `server/static/index.html` — add top nav bar (Generate active, Library link)
- `tests/test_render_trigger.py` — assert new manifest fields

---

## Task 1: Extend manifest.json with full generation params

`render_trigger.py` currently writes only `run_id`, `scene_class_name`, `quality`, `topic`.
Adding the remaining params enables the startup backfill to fully populate `VideoMeta` for all future runs.

**Files:**
- Modify: `pipeline/render_trigger.py`
- Modify: `tests/test_render_trigger.py`

- [ ] **Step 1: Add assertion to existing test for new manifest fields**

Open `tests/test_render_trigger.py`. Add these assertions at the end of `test_render_trigger_writes_all_output_files`:

```python
    assert manifest["effort"] == base_state["effort_level"]
    assert manifest["audience"] == base_state["audience"]
    assert manifest["tone"] == base_state["tone"]
    assert manifest["theme"] == base_state["theme"]
    assert manifest["template"] == base_state["template"]
    assert manifest["speed"] == base_state["speed"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_render_trigger.py::test_render_trigger_writes_all_output_files -v
```

Expected: FAIL — `KeyError: 'effort'` (fields missing from manifest)

- [ ] **Step 3: Update render_trigger.py to write new fields**

In `pipeline/render_trigger.py`, replace the `manifest.json` write block:

```python
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": run_id,
        "scene_class_name": "ChalkboardScene",
        "quality": MANIM_QUALITY,
        "topic": state["topic"],
        "effort": state.get("effort_level", "medium"),
        "audience": state.get("audience", "intermediate"),
        "tone": state.get("tone", "casual"),
        "theme": state.get("theme", "chalkboard"),
        "template": state.get("template"),
        "speed": state.get("speed", 1.0),
    }, indent=2))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_render_trigger.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pipeline/render_trigger.py tests/test_render_trigger.py
git commit -m "feat: extend manifest.json with effort/audience/tone/theme/template/speed"
```

---

## Task 2: VideoMeta model + LibraryStore ABC

**Files:**
- Create: `server/library.py`
- Create: `tests/test_library_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_library_store.py`:

```python
# tests/test_library_store.py
import pytest
from server.library import VideoMeta, LibraryStore


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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_library_store.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'server.library'`

- [ ] **Step 3: Create server/library.py with VideoMeta and LibraryStore ABC**

Create `server/library.py`:

```python
# server/library.py
from __future__ import annotations
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field


class VideoMeta(BaseModel):
    run_id: str
    topic: str
    created_at: str                        # ISO8601 UTC e.g. "2026-04-07T10:00:00Z"
    duration_sec: float = 0.0
    quality: str = "medium"               # low / medium / high
    thumb_path: str | None = None         # relative path; None = CSS fallback
    script: str = ""
    effort: str = "medium"
    audience: str = "intermediate"
    tone: str = "casual"
    theme: str = "chalkboard"
    template: str | None = None
    speed: float = 1.0
    status: str = "completed"
    output_files: list[str] = Field(default_factory=list)


class LibraryStore(ABC):
    @abstractmethod
    async def add_video(self, meta: VideoMeta) -> None: ...

    @abstractmethod
    async def list_videos(
        self,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
        sort: str = "newest",
    ) -> tuple[list[VideoMeta], int]: ...

    @abstractmethod
    async def get_video(self, run_id: str) -> VideoMeta | None: ...

    @abstractmethod
    async def delete_video(self, run_id: str) -> None: ...
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_library_store.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/library.py tests/test_library_store.py
git commit -m "feat: add VideoMeta model and LibraryStore ABC"
```

---

## Task 3: SQLiteLibraryStore — init, add_video, get_video

**Files:**
- Modify: `server/library.py`
- Modify: `tests/test_library_store.py`

- [ ] **Step 1: Write failing tests for add_video and get_video**

Append to `tests/test_library_store.py`:

```python
import asyncio
import pytest
from server.library import SQLiteLibraryStore


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_library_store.py::test_add_and_get_video -v
```

Expected: FAIL — `ImportError: cannot import name 'SQLiteLibraryStore'`

- [ ] **Step 3: Implement SQLiteLibraryStore with init, add_video, get_video**

Append to `server/library.py`:

```python
import aiosqlite

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS videos (
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

_ROW_KEYS = (
    "run_id", "topic", "duration_sec", "quality", "created_at",
    "thumb_path", "script", "effort", "audience", "tone",
    "theme", "template", "speed", "status",
)


def _row_to_meta(row: tuple) -> VideoMeta:
    return VideoMeta(**dict(zip(_ROW_KEYS, row)))


class SQLiteLibraryStore(LibraryStore):
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init(self) -> None:
        """Create the videos table if it doesn't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.commit()

    async def add_video(self, meta: VideoMeta) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO videos
                   (run_id, topic, duration_sec, quality, created_at,
                    thumb_path, script, effort, audience, tone,
                    theme, template, speed, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    meta.run_id, meta.topic, meta.duration_sec, meta.quality,
                    meta.created_at, meta.thumb_path, meta.script,
                    meta.effort, meta.audience, meta.tone, meta.theme,
                    meta.template, meta.speed, meta.status,
                ),
            )
            await db.commit()

    async def get_video(self, run_id: str) -> VideoMeta | None:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT {', '.join(_ROW_KEYS)} FROM videos WHERE run_id = ?",
                (run_id,),
            ) as cursor:
                row = await cursor.fetchone()
        return _row_to_meta(row) if row else None

    async def list_videos(
        self,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
        sort: str = "newest",
    ) -> tuple[list[VideoMeta], int]:
        raise NotImplementedError

    async def delete_video(self, run_id: str) -> None:
        raise NotImplementedError
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_library_store.py::test_add_and_get_video tests/test_library_store.py::test_get_missing_video_returns_none tests/test_library_store.py::test_add_video_is_idempotent -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/library.py tests/test_library_store.py
git commit -m "feat: SQLiteLibraryStore init, add_video, get_video"
```

---

## Task 4: SQLiteLibraryStore — list_videos

**Files:**
- Modify: `server/library.py`
- Modify: `tests/test_library_store.py`

- [ ] **Step 1: Write failing tests for list_videos**

Append to `tests/test_library_store.py`:

```python
def test_list_videos_returns_all(store):
    asyncio.run(store.add_video(_make_meta(run_id="a", topic="Alpha", created_at="2026-01-01T00:00:00Z", duration_sec=60)))
    asyncio.run(store.add_video(_make_meta(run_id="b", topic="Beta",  created_at="2026-01-02T00:00:00Z", duration_sec=120)))
    videos, total = asyncio.run(store.list_videos())
    assert total == 2
    assert len(videos) == 2


def test_list_videos_search_by_topic(store):
    asyncio.run(store.add_video(_make_meta(run_id="a", topic="explain recursion")))
    asyncio.run(store.add_video(_make_meta(run_id="b", topic="sorting algorithms")))
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


def test_list_videos_pagination(store):
    for i in range(5):
        asyncio.run(store.add_video(_make_meta(run_id=f"run-{i}", topic=f"topic {i}")))
    videos, total = asyncio.run(store.list_videos(limit=2, offset=0))
    assert total == 5
    assert len(videos) == 2
    page2, _ = asyncio.run(store.list_videos(limit=2, offset=2))
    assert len(page2) == 2
    assert {v.run_id for v in videos}.isdisjoint({v.run_id for v in page2})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_library_store.py::test_list_videos_returns_all -v
```

Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement list_videos in SQLiteLibraryStore**

Replace the `list_videos` stub in `server/library.py`:

```python
    async def list_videos(
        self,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
        sort: str = "newest",
    ) -> tuple[list[VideoMeta], int]:
        _sort_map = {
            "newest":   "created_at DESC",
            "oldest":   "created_at ASC",
            "longest":  "duration_sec DESC",
            "shortest": "duration_sec ASC",
        }
        order = _sort_map.get(sort, "created_at DESC")

        if query:
            where = "WHERE topic LIKE ? COLLATE NOCASE OR script LIKE ? COLLATE NOCASE"
            params = (f"%{query}%", f"%{query}%")
        else:
            where = ""
            params = ()

        cols = ", ".join(_ROW_KEYS)
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                f"SELECT COUNT(*) FROM videos {where}", params
            ) as cur:
                row = await cur.fetchone()
                total = row[0] if row else 0

            async with db.execute(
                f"SELECT {cols} FROM videos {where} ORDER BY {order} LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ) as cur:
                rows = await cur.fetchall()

        return [_row_to_meta(r) for r in rows], total
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_library_store.py -k "list_videos" -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/library.py tests/test_library_store.py
git commit -m "feat: SQLiteLibraryStore list_videos with search, sort, pagination"
```

---

## Task 5: SQLiteLibraryStore — delete_video

**Files:**
- Modify: `server/library.py`
- Modify: `tests/test_library_store.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_library_store.py`:

```python
def test_delete_video(store):
    asyncio.run(store.add_video(_make_meta()))
    asyncio.run(store.delete_video("run-001"))
    assert asyncio.run(store.get_video("run-001")) is None


def test_delete_missing_video_is_noop(store):
    asyncio.run(store.delete_video("does-not-exist"))  # should not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_library_store.py::test_delete_video -v
```

Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement delete_video**

Replace the `delete_video` stub in `server/library.py`:

```python
    async def delete_video(self, run_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM videos WHERE run_id = ?", (run_id,))
            await db.commit()
```

- [ ] **Step 4: Run full test suite for the store**

```bash
pytest tests/test_library_store.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/library.py tests/test_library_store.py
git commit -m "feat: SQLiteLibraryStore delete_video"
```

---

## Task 6: Thumbnail extraction

Adds `_extract_thumbnail(run_dir)` to `main.py` and calls it from `_render_once` after the ffmpeg merge succeeds. Since `server/jobs.py`'s `_do_render` calls `_render` which calls `_render_once`, thumbnails are extracted automatically for both CLI and server paths.

**Files:**
- Modify: `main.py`
- Create: `tests/test_thumbnail.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_thumbnail.py`:

```python
# tests/test_thumbnail.py
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from main import _extract_thumbnail


def test_extract_thumbnail_runs_ffmpeg(tmp_path):
    """_extract_thumbnail calls ffmpeg with correct seek time and output path."""
    run_dir = tmp_path / "run-abc"
    run_dir.mkdir()
    segments = [
        {"text": "Hello.", "actual_duration_sec": 30.0},
        {"text": "World.", "actual_duration_sec": 70.0},
    ]
    (run_dir / "segments.json").write_text(json.dumps(segments))
    (run_dir / "final.mp4").write_bytes(b"fake")

    mock_result = MagicMock()
    mock_result.returncode = 0
    with patch("main.subprocess.run", return_value=mock_result) as mock_run:
        result = _extract_thumbnail(run_dir)

    assert result == run_dir / "thumb.jpg"
    cmd = mock_run.call_args[0][0]
    # seek time should be 10% of 100s = 10.0
    assert "-ss" in cmd
    ss_idx = cmd.index("-ss")
    assert float(cmd[ss_idx + 1]) == pytest.approx(10.0, abs=0.1)
    assert str(run_dir / "thumb.jpg") in cmd


def test_extract_thumbnail_returns_none_on_ffmpeg_failure(tmp_path):
    run_dir = tmp_path / "run-fail"
    run_dir.mkdir()
    (run_dir / "segments.json").write_text(json.dumps([{"text": "Hi", "actual_duration_sec": 60.0}]))
    (run_dir / "final.mp4").write_bytes(b"fake")

    with patch("main.subprocess.run", side_effect=Exception("ffmpeg not found")):
        result = _extract_thumbnail(run_dir)

    assert result is None


def test_extract_thumbnail_returns_none_when_segments_missing(tmp_path):
    run_dir = tmp_path / "run-noseg"
    run_dir.mkdir()
    result = _extract_thumbnail(run_dir)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_thumbnail.py -v
```

Expected: FAIL — `ImportError: cannot import name '_extract_thumbnail' from 'main'`

- [ ] **Step 3: Add _extract_thumbnail to main.py**

Find the `_render_once` function in `main.py` (around line 277). Add `_extract_thumbnail` as a new module-level function just before `_render_once`:

```python
def _extract_thumbnail(run_dir: Path) -> Path | None:
    """Extract a JPEG thumbnail from final.mp4 at 10% of video duration.
    Returns path to thumb.jpg on success, None on any failure.
    """
    try:
        seg_path = run_dir / "segments.json"
        final_mp4 = run_dir / "final.mp4"
        if not seg_path.exists() or not final_mp4.exists():
            return None
        segments = json.loads(seg_path.read_text())
        duration = sum(s.get("actual_duration_sec", 0) for s in segments)
        if duration <= 0:
            return None
        seek = round(duration * 0.1, 1)
        thumb = run_dir / "thumb.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", str(final_mp4),
             "-vframes", "1", "-q:v", "3", "-vf", "scale=640:360", str(thumb)],
            check=True, capture_output=True, timeout=15,
        )
        return thumb if thumb.exists() else None
    except Exception:
        return None
```

- [ ] **Step 4: Call _extract_thumbnail from _render_once**

In `main.py`, inside `_render_once`, add the thumbnail call right before `return final_mp4` (around line 354):

```python
    _extract_thumbnail(run_dir)
    return final_mp4
```

The `run_dir` variable is already defined earlier in `_render_once` as `output_dir / run_id`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_thumbnail.py -v
```

Expected: all PASS

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
pytest tests/ -v --tb=short -q
```

Expected: all previously passing tests still PASS

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_thumbnail.py
git commit -m "feat: extract thumbnail from final.mp4 at render time"
```

---

## Task 7: Library API endpoints

**Files:**
- Create: `server/library_routes.py`
- Create: `tests/test_library_routes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_library_routes.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_library_routes.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'server.library_routes'`

- [ ] **Step 3: Create server/library_routes.py**

Create `server/library_routes.py`:

```python
# server/library_routes.py
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from server.library import LibraryStore, VideoMeta


def make_library_router(store: LibraryStore, output_dir: Path | str | None = None) -> APIRouter:
    from config import OUTPUT_DIR
    _output_dir = Path(output_dir) if output_dir else Path(OUTPUT_DIR).resolve()

    router = APIRouter(prefix="/api")

    @router.get("/library")
    async def list_videos(
        q: str = "",
        limit: int = 50,
        offset: int = 0,
        sort: str = "newest",
    ):
        limit = min(limit, 100)
        videos, total = await store.list_videos(query=q, limit=limit, offset=offset, sort=sort)
        return {"videos": [v.model_dump() for v in videos], "total": total, "limit": limit, "offset": offset}

    @router.get("/library/{run_id}")
    async def get_video(run_id: str):
        meta = await store.get_video(run_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Video not found")
        run_dir = _output_dir / run_id
        if run_dir.exists():
            output_files = [
                f.name for f in run_dir.iterdir()
                if f.is_file() and f.suffix in (".mp4", ".srt", ".json", ".txt", ".py", ".jpg")
            ]
            meta = meta.model_copy(update={"output_files": sorted(output_files)})
        return meta.model_dump()

    @router.delete("/library/{run_id}", status_code=204)
    async def delete_video(run_id: str):
        await store.delete_video(run_id)

    return router


def make_pages_router() -> APIRouter:
    """Serves library.html and video.html for browser navigation."""
    router = APIRouter()
    static_dir = Path(__file__).parent / "static"

    @router.get("/library")
    async def library_page():
        return FileResponse(str(static_dir / "library.html"))

    @router.get("/library/{run_id}")
    async def video_page(run_id: str):
        return FileResponse(str(static_dir / "video.html"))

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_library_routes.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add server/library_routes.py tests/test_library_routes.py
git commit -m "feat: library API endpoints and page routes"
```

---

## Task 8: Wire everything into app.py and jobs.py

**Files:**
- Modify: `server/app.py`
- Modify: `server/routes.py`
- Modify: `server/jobs.py`

- [ ] **Step 1: Update server/app.py**

Replace the contents of `server/app.py`:

```python
# server/app.py
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from starlette.formparsers import MultiPartParser
MultiPartParser.max_part_size = 21 * 1024 * 1024

from config import OUTPUT_DIR
from server.jobs import JobStore
from server.library import SQLiteLibraryStore, LibraryStore, VideoMeta
from server.routes import make_router
from server.library_routes import make_library_router, make_pages_router


async def _backfill(store: LibraryStore, output_dir: Path) -> None:
    """Index any completed runs in output_dir not yet in library.db."""
    if not output_dir.exists():
        return
    for run_dir in output_dir.iterdir():
        if not run_dir.is_dir():
            continue
        manifest_path = run_dir / "manifest.json"
        final_mp4 = run_dir / "final.mp4"
        if not manifest_path.exists() or not final_mp4.exists():
            continue
        run_id = run_dir.name
        if await store.get_video(run_id) is not None:
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            seg_path = run_dir / "segments.json"
            duration_sec = 0.0
            if seg_path.exists():
                segs = json.loads(seg_path.read_text())
                duration_sec = sum(s.get("actual_duration_sec", 0) for s in segs)
            script = (run_dir / "script.txt").read_text() if (run_dir / "script.txt").exists() else ""
            thumb_path = str(run_dir / "thumb.jpg") if (run_dir / "thumb.jpg").exists() else None
            mtime = final_mp4.stat().st_mtime
            created_at = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            meta = VideoMeta(
                run_id=run_id,
                topic=manifest.get("topic", run_id),
                duration_sec=duration_sec,
                quality=manifest.get("quality", "medium"),
                created_at=created_at,
                thumb_path=thumb_path,
                script=script,
                effort=manifest.get("effort", "medium"),
                audience=manifest.get("audience", "intermediate"),
                tone=manifest.get("tone", "casual"),
                theme=manifest.get("theme", "chalkboard"),
                template=manifest.get("template"),
                speed=float(manifest.get("speed", 1.0)),
                status="completed",
            )
            await store.add_video(meta)
        except Exception as e:
            print(f"  [library] backfill skipped {run_id}: {e}")


def create_app(
    store: JobStore | None = None,
    library_store: LibraryStore | None = None,
) -> FastAPI:
    if store is None:
        store = JobStore()
    if library_store is None:
        library_store = SQLiteLibraryStore("library.db")

    app = FastAPI(title="Chalkboard API", version="0.1.0")

    @app.on_event("startup")
    async def startup():
        await library_store.init()
        output_dir = Path(OUTPUT_DIR).resolve()
        await _backfill(library_store, output_dir)

    # API routes (must come before StaticFiles mount)
    app.include_router(make_router(store, library_store))
    app.include_router(make_library_router(library_store))
    app.include_router(make_pages_router())

    # Serve frontend static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
```

- [ ] **Step 2: Update server/routes.py to accept and pass library_store**

In `server/routes.py`, change the `make_router` signature and both `create_task` calls:

```python
from server.library import LibraryStore

def make_router(store: JobStore, library_store: LibraryStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/jobs", status_code=202, response_model=JobResponse)
    async def create_job(req: CreateJobRequest):
        job = store.create(
            topic=req.topic, effort=req.effort, audience=req.audience,
            tone=req.tone, theme=req.theme, template=req.template, speed=req.speed,
            burn_captions=req.burn_captions, quiz=req.quiz,
            urls=req.urls, github=req.github, qa_density=req.qa_density,
        )
        output_dir = Path(OUTPUT_DIR).resolve()
        asyncio.create_task(run_job(job, output_dir, library_store=library_store))
        return _job_to_response(job)
```

Do the same for the `create_job_with_files` route — change its `create_task` call to:
```python
        asyncio.create_task(run_job(job, output_dir, library_store=library_store))
```

Add `from server.library import LibraryStore` to the imports at the top of `server/routes.py`.

- [ ] **Step 3: Update server/jobs.py to call add_video on completion**

At the top of `server/jobs.py`, add imports:

```python
import json
from datetime import datetime, timezone
```

And add the import for `LibraryStore` and `VideoMeta` (lazy import inside the function to avoid circular imports):

Change `run_job` signature to accept `library_store`:

```python
async def run_job(job: Job, output_dir: Path, library_store=None) -> None:
```

After `job.status = "completed"` (just before the `except Exception` block), add:

```python
        # Index in library
        if library_store is not None:
            try:
                from server.library import VideoMeta
                run_dir = output_dir / job.id
                seg_path = run_dir / "segments.json"
                duration_sec = 0.0
                if seg_path.exists():
                    segs = json.loads(seg_path.read_text())
                    duration_sec = sum(s.get("actual_duration_sec", 0) for s in segs)
                manifest_path = run_dir / "manifest.json"
                quality = "medium"
                if manifest_path.exists():
                    quality = json.loads(manifest_path.read_text()).get("quality", "medium")
                script = (run_dir / "script.txt").read_text() if (run_dir / "script.txt").exists() else ""
                thumb_path = str(run_dir / "thumb.jpg") if (run_dir / "thumb.jpg").exists() else None
                created_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                meta = VideoMeta(
                    run_id=job.id,
                    topic=job.topic,
                    duration_sec=duration_sec,
                    quality=quality,
                    created_at=created_at,
                    thumb_path=thumb_path,
                    script=script,
                    effort=job.effort,
                    audience=job.audience,
                    tone=job.tone,
                    theme=job.theme,
                    template=job.template,
                    speed=job.speed,
                    status="completed",
                )
                await library_store.add_video(meta)
            except Exception as e:
                print(f"  [library] failed to index job {job.id}: {e}")
```

- [ ] **Step 4: Run existing server tests to check for regressions**

```bash
pytest tests/test_server.py tests/test_server_jobs.py -v --tb=short
```

Expected: all PASS (library_store defaults to None, so existing paths are unchanged)

- [ ] **Step 5: Run all tests**

```bash
pytest tests/ -v --tb=short -q
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add server/app.py server/routes.py server/jobs.py
git commit -m "feat: wire LibraryStore into app startup, job completion, and backfill"
```

---

## Task 9: library.html — library grid page

**Files:**
- Create: `server/static/library.html`

- [ ] **Step 1: Create library.html**

Create `server/static/library.html` with the following content. This is the full production page — not a mockup:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Library — Chalkboard</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Serif+Display&family=Lora:ital,wght@0,400;0,500;1,400&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#111110;--surface:#1a1918;--border:rgba(255,255,255,0.07);
  --text:#e8e4db;--muted:#7a7570;--accent:#c8b97a;--chalk:#f0ebe0;
}
body{background:var(--bg);color:var(--text);font-family:'DM Mono',monospace;min-height:100vh;padding-bottom:80px}

/* Nav */
.topnav{background:var(--surface);border-bottom:1px solid var(--border);padding:0 32px;
  display:flex;align-items:center;height:52px;position:sticky;top:0;z-index:100;gap:0}
.nav-logo{font-size:13px;color:var(--accent);letter-spacing:.12em;text-transform:uppercase;
  margin-right:28px;text-decoration:none;font-weight:500}
.nav-links{display:flex;gap:4px}
.nav-link{font-size:12px;color:var(--muted);padding:6px 14px;border-radius:6px;
  text-decoration:none;letter-spacing:.04em;transition:color .15s,background .15s}
.nav-link:hover{color:var(--text);background:rgba(255,255,255,.05)}
.nav-link.active{color:var(--accent);background:rgba(200,185,122,.1)}

/* Layout */
.page{max-width:1400px;margin:0 auto;padding:36px 32px}
.page-header{display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:28px;gap:20px;flex-wrap:wrap}
.page-title{font-family:'DM Serif Display',serif;font-size:28px;color:var(--chalk)}
.page-count{font-size:12px;color:var(--muted);margin-top:6px}

/* Controls */
.controls{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.search-wrap{position:relative;flex:1;min-width:180px;max-width:380px}
.search-icon{position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:13px;pointer-events:none}
.search-input{width:100%;background:var(--surface);border:1px solid rgba(255,255,255,.1);border-radius:8px;
  padding:9px 12px 9px 34px;color:var(--text);font-family:'DM Mono',monospace;font-size:12px;outline:none;transition:border-color .2s}
.search-input:focus{border-color:rgba(200,185,122,.4)}
.search-input::placeholder{color:#5a5550}
.sort-select{background:var(--surface);border:1px solid rgba(255,255,255,.1);border-radius:8px;
  padding:9px 28px 9px 14px;color:var(--muted);font-family:'DM Mono',monospace;font-size:12px;
  outline:none;cursor:pointer;-webkit-appearance:none;appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%237a7570'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 10px center}

/* Grid */
.video-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:16px}
@media(max-width:1200px){.video-grid{grid-template-columns:repeat(3,1fr)}}
@media(max-width:768px){.video-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:480px){.video-grid{grid-template-columns:1fr}}

/* Card */
.video-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;
  overflow:hidden;cursor:pointer;transition:border-color .2s,transform .15s;text-decoration:none;display:block}
.video-card:hover{border-color:rgba(200,185,122,.25);transform:translateY(-2px)}

/* Thumbnail */
.thumb-wrap{position:relative;aspect-ratio:16/9;overflow:hidden;background:#111110}
.thumb-img{width:100%;height:100%;object-fit:cover;display:block}
.thumb-fallback{width:100%;height:100%;display:flex;flex-direction:column;justify-content:space-between;padding:10px 12px}
.tf-chalkboard{background:linear-gradient(135deg,#1b2d1b 0%,#1e3020 100%)}
.tf-light{background:linear-gradient(135deg,#2a2015 0%,#2e2418 100%)}
.tf-colorful{background:linear-gradient(135deg,#101828 0%,#141c30 100%)}
.tf-logo{font-size:9px;color:rgba(200,185,122,.45);letter-spacing:.1em;text-transform:uppercase}
.tf-topic{font-family:'Lora',serif;font-size:12px;color:rgba(240,235,224,.9);line-height:1.4;font-weight:500}
.tf-meta{font-size:9px;color:rgba(122,117,112,.7);text-transform:uppercase;letter-spacing:.06em}
.dur-pill{position:absolute;bottom:6px;right:7px;background:rgba(0,0,0,.78);color:var(--text);
  font-size:10px;font-weight:500;padding:2px 6px;border-radius:3px}
.play-overlay{position:absolute;inset:0;background:rgba(0,0,0,0);display:flex;align-items:center;
  justify-content:center;transition:background .2s}
.video-card:hover .play-overlay{background:rgba(0,0,0,.22)}
.play-icon{width:36px;height:36px;background:rgba(200,185,122,.9);border-radius:50%;display:flex;
  align-items:center;justify-content:center;color:#111110;font-size:14px;
  opacity:0;transform:scale(.8);transition:opacity .2s,transform .2s}
.video-card:hover .play-icon{opacity:1;transform:scale(1)}

/* Card body */
.card-body{padding:10px 12px 12px}
.card-title{font-family:'Lora',serif;font-size:13px;color:var(--text);line-height:1.45;margin-bottom:7px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card-meta{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.card-date{font-size:10px;color:var(--muted)}
.qb{font-size:9px;padding:1px 6px;border-radius:3px;text-transform:uppercase;letter-spacing:.06em;font-weight:500}
.qb-high{background:rgba(100,180,100,.1);color:#7abf7a}
.qb-medium{background:rgba(200,185,122,.1);color:var(--accent)}
.qb-low{background:rgba(150,150,150,.1);color:#909090}

/* Load more */
.load-more-wrap{text-align:center;margin-top:36px}
.load-more-btn{background:transparent;border:1px solid rgba(255,255,255,.12);color:var(--muted);
  font-family:'DM Mono',monospace;font-size:12px;padding:10px 28px;border-radius:8px;
  cursor:pointer;transition:border-color .2s,color .2s}
.load-more-btn:hover{border-color:rgba(200,185,122,.3);color:var(--accent)}

/* Empty */
.empty{text-align:center;padding:80px 20px;color:#5a5550}
.empty-icon{font-size:36px;margin-bottom:16px;color:#3a3530}
.empty-title{font-family:'DM Serif Display',serif;font-size:20px;color:var(--muted);margin-bottom:8px}
.empty-sub{font-size:12px;line-height:1.7}
.empty-link{color:var(--accent);text-decoration:none}

/* Spinner */
.spinner{display:flex;justify-content:center;padding:60px;color:var(--muted);font-size:12px}
</style>
</head>
<body>
<nav class="topnav">
  <a href="/" class="nav-logo">◆ CHALKBOARD</a>
  <div class="nav-links">
    <a href="/" class="nav-link">Generate</a>
    <a href="/library" class="nav-link active">Library</a>
  </div>
</nav>
<div class="page">
  <div class="page-header">
    <div>
      <div class="page-title">Library</div>
      <div class="page-count" id="count">Loading...</div>
    </div>
    <div class="controls">
      <div class="search-wrap">
        <span class="search-icon">⌕</span>
        <input class="search-input" id="search" type="text" placeholder="Search topics and scripts…" />
      </div>
      <select class="sort-select" id="sort">
        <option value="newest">Newest first</option>
        <option value="oldest">Oldest first</option>
        <option value="longest">Longest</option>
        <option value="shortest">Shortest</option>
      </select>
    </div>
  </div>
  <div id="grid" class="video-grid"></div>
  <div class="load-more-wrap" id="load-more-wrap" style="display:none">
    <button class="load-more-btn" id="load-more-btn">Load more</button>
  </div>
</div>
<script>
const LIMIT = 50;
let offset = 0, total = 0, loading = false;

function fmtDur(s) {
  const m = Math.floor(s / 60), sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2,'0')}`;
}
function relTime(iso) {
  const diff = (Date.now() - new Date(iso)) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff/86400)}d ago`;
  if (diff < 2592000) return `${Math.floor(diff/604800)}w ago`;
  return `${Math.floor(diff/2592000)}mo ago`;
}
function thumbClass(theme) {
  if (theme === 'light') return 'tf-light';
  if (theme === 'colorful') return 'tf-colorful';
  return 'tf-chalkboard';
}
function renderCard(v) {
  const hasThumb = v.thumb_path;
  const thumbHtml = hasThumb
    ? `<img class="thumb-img" src="/api/jobs/${v.run_id}/files/thumb.jpg" alt="" loading="lazy" onerror="this.parentNode.innerHTML=fallbackThumb('${v.topic.replace(/'/g,"\\'")}','${v.theme}','${v.quality}')">`
    : `<div class="thumb-fallback ${thumbClass(v.theme)}">
         <div class="tf-logo">◆ CHALKBOARD</div>
         <div class="tf-topic">${escHtml(v.topic)}</div>
         <div class="tf-meta">${v.theme} · ${v.quality}</div>
       </div>`;
  return `<a href="/library/${v.run_id}" class="video-card">
    <div class="thumb-wrap">
      ${thumbHtml}
      <div class="play-overlay"><div class="play-icon">▶</div></div>
      <div class="dur-pill">${fmtDur(v.duration_sec)}</div>
    </div>
    <div class="card-body">
      <div class="card-title">${escHtml(v.topic)}</div>
      <div class="card-meta">
        <span class="card-date">${relTime(v.created_at)}</span>
        <span class="qb qb-${v.quality}">${v.quality}</span>
      </div>
    </div>
  </a>`;
}
function fallbackThumb(topic, theme, quality) {
  return `<div class="thumb-fallback ${thumbClass(theme)}">
    <div class="tf-logo">◆ CHALKBOARD</div>
    <div class="tf-topic">${escHtml(topic)}</div>
    <div class="tf-meta">${theme} · ${quality}</div>
  </div>`;
}
function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

async function load(reset = false) {
  if (loading) return;
  loading = true;
  if (reset) { offset = 0; document.getElementById('grid').innerHTML = '<div class="spinner">Loading…</div>'; }
  const q = document.getElementById('search').value.trim();
  const sort = document.getElementById('sort').value;
  const url = `/api/library?q=${encodeURIComponent(q)}&limit=${LIMIT}&offset=${offset}&sort=${sort}`;
  try {
    const data = await fetch(url).then(r => r.json());
    total = data.total;
    const grid = document.getElementById('grid');
    if (reset) grid.innerHTML = '';
    if (data.videos.length === 0 && offset === 0) {
      grid.innerHTML = `<div class="empty" style="grid-column:1/-1">
        <div class="empty-icon">◆</div>
        <div class="empty-title">${q ? 'No results' : 'No videos yet'}</div>
        <div class="empty-sub">${q ? `No videos match "${escHtml(q)}"` : 'Generate your first video to get started.'}<br>
          <a href="/" class="empty-link">Go to Generate →</a></div>
      </div>`;
    } else {
      data.videos.forEach(v => { grid.insertAdjacentHTML('beforeend', renderCard(v)); });
    }
    offset += data.videos.length;
    document.getElementById('count').textContent = `${total} video${total !== 1 ? 's' : ''}`;
    const remaining = total - offset;
    const wrap = document.getElementById('load-more-wrap');
    const btn = document.getElementById('load-more-btn');
    if (remaining > 0) {
      wrap.style.display = 'block';
      btn.textContent = `Load more (${remaining} remaining)`;
    } else {
      wrap.style.display = 'none';
    }
  } catch(e) {
    document.getElementById('grid').innerHTML = '<div class="spinner">Failed to load library.</div>';
  }
  loading = false;
}

let debounceTimer;
document.getElementById('search').addEventListener('input', () => {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => load(true), 300);
});
document.getElementById('sort').addEventListener('change', () => load(true));
document.getElementById('load-more-btn').addEventListener('click', () => load(false));

load(true);
</script>
</body>
</html>
```

- [ ] **Step 2: Verify library page renders correctly**

Start the server: `python run_server.py`

Open `http://localhost:8071/library` and verify:
- Top nav shows "Generate" and "Library" (Library active/gold)
- If runs exist in `output/`, cards appear after startup backfill
- Search box filters results as you type
- Sort dropdown changes order
- CSS fallback thumbnails show topic + theme + quality
- Any runs with `thumb.jpg` show the real frame

- [ ] **Step 3: Commit**

```bash
git add server/static/library.html
git commit -m "feat: library grid page with search, sort, and load-more pagination"
```

---

## Task 10: video.html — detail page

**Files:**
- Create: `server/static/video.html`

- [ ] **Step 1: Create video.html**

Create `server/static/video.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Video — Chalkboard</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=DM+Serif+Display&family=Lora:ital,wght@0,400;0,500;1,400&display=swap');
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#111110;--surface:#1a1918;--border:rgba(255,255,255,0.07);
  --text:#e8e4db;--muted:#7a7570;--accent:#c8b97a;--chalk:#f0ebe0;
}
body{background:var(--bg);color:var(--text);font-family:'DM Mono',monospace;min-height:100vh;padding-bottom:80px}

/* Nav */
.topnav{background:var(--surface);border-bottom:1px solid var(--border);padding:0 32px;
  display:flex;align-items:center;height:52px;position:sticky;top:0;z-index:100;gap:0;overflow:hidden}
.nav-logo{font-size:13px;color:var(--accent);letter-spacing:.12em;text-transform:uppercase;
  margin-right:28px;text-decoration:none;font-weight:500;white-space:nowrap}
.nav-links{display:flex;gap:4px}
.nav-link{font-size:12px;color:var(--muted);padding:6px 14px;border-radius:6px;
  text-decoration:none;letter-spacing:.04em;white-space:nowrap}
.nav-link:hover{color:var(--text);background:rgba(255,255,255,.05)}
.nav-link.active{color:var(--accent);background:rgba(200,185,122,.1)}
.nav-sep{color:#3a3530;margin:0 6px;font-size:14px}
.nav-crumb{font-size:11px;color:#5a5550;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}

/* Layout */
.page{max-width:1400px;margin:0 auto;padding:36px 32px}
.layout{display:grid;grid-template-columns:1fr 340px;gap:32px;align-items:start}
@media(max-width:960px){.layout{grid-template-columns:1fr}}

/* Video player */
.video-wrap{aspect-ratio:16/9;background:#000;border-radius:10px;overflow:hidden;
  border:1px solid var(--border)}
video{width:100%;height:100%;display:block}

/* Video info */
.video-info{margin-top:20px}
.video-title{font-family:'DM Serif Display',serif;font-size:22px;color:var(--chalk);
  line-height:1.3;margin-bottom:12px}
.meta-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:18px;
  padding-bottom:18px;border-bottom:1px solid var(--border)}
.meta-chip{font-size:11px;color:var(--muted)}
.dot{color:#3a3530}
.qb{font-size:9px;padding:2px 7px;border-radius:3px;text-transform:uppercase;letter-spacing:.06em;font-weight:500}
.qb-high{background:rgba(100,180,100,.1);color:#7abf7a}
.qb-medium{background:rgba(200,185,122,.1);color:var(--accent)}
.qb-low{background:rgba(150,150,150,.1);color:#909090}

/* Section label */
.sec-label{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.1em;margin-bottom:10px}

/* Downloads */
.downloads{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}
.dl-btn{display:flex;align-items:center;gap:7px;background:var(--surface);border:1px solid rgba(255,255,255,.1);
  border-radius:7px;padding:8px 14px;color:var(--text);font-family:'DM Mono',monospace;
  font-size:11px;cursor:pointer;text-decoration:none;transition:border-color .2s,color .2s}
.dl-btn:hover{border-color:rgba(200,185,122,.3);color:var(--accent)}
.dl-size{font-size:9px;color:#5a5550;margin-top:1px}

/* Script */
.script-box{background:#151413;border:1px solid var(--border);border-radius:8px;padding:18px 20px;
  font-family:'Lora',serif;font-size:13px;color:#c8c4bb;line-height:1.8;
  max-height:220px;overflow-y:auto}
.script-box::-webkit-scrollbar{width:4px}
.script-box::-webkit-scrollbar-track{background:transparent}
.script-box::-webkit-scrollbar-thumb{background:#3a3530;border-radius:2px}

/* Params card */
.params-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;
  padding:16px 18px;margin-bottom:20px}
.params-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px 16px}
.param-label{font-size:9px;color:#5a5550;text-transform:uppercase;letter-spacing:.1em;margin-bottom:3px}
.param-value{font-size:12px;color:var(--accent);text-transform:capitalize}

/* Related */
.related-list{display:flex;flex-direction:column;gap:10px}
.related-card{display:flex;gap:10px;align-items:flex-start;cursor:pointer;text-decoration:none;
  padding:8px;border-radius:7px;border:1px solid transparent;transition:border-color .2s}
.related-card:hover{border-color:rgba(200,185,122,.2);background:rgba(255,255,255,.02)}
.rt-wrap{width:88px;min-width:88px;aspect-ratio:16/9;border-radius:5px;overflow:hidden;position:relative}
.rt-fallback{width:100%;height:100%;display:flex;flex-direction:column;justify-content:space-between;padding:5px 7px}
.rt-chalkboard{background:linear-gradient(135deg,#1b2d1b,#1e3020)}
.rt-light{background:linear-gradient(135deg,#2a2015,#2e2418)}
.rt-colorful{background:linear-gradient(135deg,#101828,#141c30)}
.rt-logo{font-size:6px;color:rgba(200,185,122,.4);letter-spacing:.1em}
.rt-topic{font-family:'Lora',serif;font-size:8px;color:rgba(240,235,224,.85);line-height:1.3}
.rt-dur{position:absolute;bottom:3px;right:4px;background:rgba(0,0,0,.75);color:var(--text);
  font-size:8px;font-weight:500;padding:1px 4px;border-radius:2px}
.rt-info{flex:1;padding-top:1px}
.rt-title{font-family:'Lora',serif;font-size:12px;color:var(--text);line-height:1.4;margin-bottom:4px;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.rt-meta{font-size:10px;color:var(--muted)}

/* Re-generate */
.regen-btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;
  background:transparent;border:1px solid rgba(200,185,122,.2);border-radius:8px;padding:10px;
  color:var(--accent);font-family:'DM Mono',monospace;font-size:11px;cursor:pointer;margin-top:20px;
  transition:background .2s,border-color .2s;letter-spacing:.04em}
.regen-btn:hover{background:rgba(200,185,122,.07);border-color:rgba(200,185,122,.4)}

/* Loading/error */
.status-msg{padding:80px;text-align:center;color:var(--muted);font-size:13px}
</style>
</head>
<body>
<nav class="topnav">
  <a href="/" class="nav-logo">◆ CHALKBOARD</a>
  <div class="nav-links">
    <a href="/" class="nav-link">Generate</a>
    <a href="/library" class="nav-link active">Library</a>
  </div>
  <span class="nav-sep">/</span>
  <span class="nav-crumb" id="nav-crumb"></span>
</nav>
<div class="page" id="page" style="display:none">
  <div class="layout">
    <div class="left-col">
      <div class="video-wrap">
        <video id="player" controls preload="metadata"></video>
      </div>
      <div class="video-info">
        <div class="video-title" id="title"></div>
        <div class="meta-row" id="meta-row"></div>
        <div class="sec-label">Downloads</div>
        <div class="downloads" id="downloads"></div>
        <div class="sec-label" style="margin-top:4px">Script</div>
        <div class="script-box" id="script"></div>
      </div>
    </div>
    <div class="right-col">
      <div class="params-card">
        <div class="sec-label" style="margin-bottom:12px">Generation settings</div>
        <div class="params-grid" id="params-grid"></div>
      </div>
      <div class="sec-label">More videos</div>
      <div class="related-list" id="related"></div>
      <button class="regen-btn" id="regen-btn">↺ &nbsp;Re-generate with same settings</button>
    </div>
  </div>
</div>
<div class="status-msg" id="status-msg">Loading…</div>

<script>
function fmtDur(s) {
  const m = Math.floor(s/60), sec = Math.floor(s%60);
  return `${m}:${sec.toString().padStart(2,'0')}`;
}
function relTime(iso) {
  const diff = (Date.now() - new Date(iso)) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff/86400)}d ago`;
  if (diff < 2592000) return `${Math.floor(diff/604800)}w ago`;
  return `${Math.floor(diff/2592000)}mo ago`;
}
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function rtThumbClass(theme) {
  if (theme==='light') return 'rt-light';
  if (theme==='colorful') return 'rt-colorful';
  return 'rt-chalkboard';
}
const DOWNLOAD_LABELS = {
  'final.mp4':'final.mp4','script.txt':'script.txt',
  'captions.srt':'captions.srt','quiz.json':'quiz.json',
};

const runId = location.pathname.split('/').filter(Boolean).pop();

async function init() {
  try {
    const v = await fetch(`/api/library/${runId}`).then(r => {
      if (!r.ok) throw new Error(r.status);
      return r.json();
    });

    // Nav crumb
    document.getElementById('nav-crumb').textContent = v.topic;
    document.title = `${v.topic} — Chalkboard`;

    // Player
    document.getElementById('player').src = `/api/jobs/${v.run_id}/files/final.mp4`;

    // Title
    document.getElementById('title').textContent = v.topic;

    // Meta row
    const qbClass = `qb-${v.quality}`;
    document.getElementById('meta-row').innerHTML =
      `<span class="meta-chip">${fmtDur(v.duration_sec)}</span>
       <span class="dot">·</span>
       <span class="meta-chip">${relTime(v.created_at)}</span>
       <span class="dot">·</span>
       <span class="qb ${qbClass}">${v.quality}</span>`;

    // Downloads
    const dlWanted = ['final.mp4','script.txt','captions.srt','quiz.json'];
    const dlHtml = dlWanted
      .filter(f => v.output_files.includes(f))
      .map(f => `<a href="/api/jobs/${v.run_id}/files/${f}" download class="dl-btn">
        <span>↓</span><div><div>${escHtml(DOWNLOAD_LABELS[f]||f)}</div></div>
      </a>`).join('');
    document.getElementById('downloads').innerHTML = dlHtml || '<span style="color:#5a5550;font-size:11px">No files available</span>';

    // Script
    document.getElementById('script').textContent = v.script || '(no script)';

    // Params
    const params = [
      ['Effort', v.effort], ['Audience', v.audience],
      ['Tone', v.tone], ['Theme', v.theme],
      ['Speed', `${v.speed}×`], ['Template', v.template || 'none'],
    ];
    document.getElementById('params-grid').innerHTML = params.map(([l,val]) =>
      `<div><div class="param-label">${l}</div><div class="param-value">${escHtml(String(val))}</div></div>`
    ).join('');

    // Related videos
    const related = await fetch(`/api/library?limit=5&sort=newest`)
      .then(r => r.json())
      .then(d => d.videos.filter(x => x.run_id !== v.run_id).slice(0,4));
    document.getElementById('related').innerHTML = related.map(r =>
      `<a href="/library/${r.run_id}" class="related-card">
        <div class="rt-wrap">
          ${r.thumb_path
            ? `<img style="width:100%;height:100%;object-fit:cover" src="/api/jobs/${r.run_id}/files/thumb.jpg" alt="" loading="lazy">`
            : `<div class="rt-fallback ${rtThumbClass(r.theme)}">
                 <div class="rt-logo">◆ CB</div>
                 <div class="rt-topic">${escHtml(r.topic)}</div>
               </div>`}
          <div class="rt-dur">${fmtDur(r.duration_sec)}</div>
        </div>
        <div class="rt-info">
          <div class="rt-title">${escHtml(r.topic)}</div>
          <div class="rt-meta">${relTime(r.created_at)} · ${r.quality}</div>
        </div>
      </a>`
    ).join('');

    // Re-generate
    document.getElementById('regen-btn').addEventListener('click', () => {
      const p = new URLSearchParams({ prefill: v.run_id });
      location.href = `/?' + p.toString();
    });

    document.getElementById('page').style.display = 'block';
    document.getElementById('status-msg').style.display = 'none';
  } catch(e) {
    document.getElementById('status-msg').textContent =
      e.message === '404' ? 'Video not found.' : 'Failed to load video.';
  }
}
init();
</script>
</body>
</html>
```

- [ ] **Step 2: Verify detail page renders correctly**

With the server running, navigate to any video card in `/library` and click it. Verify:
- URL changes to `/library/{run_id}`
- Breadcrumb in nav shows the topic title
- Video player loads and plays `final.mp4`
- Downloads section shows only files that exist (final.mp4 always, quiz.json only if generated)
- Script appears in the scrollable box
- Generation settings show the correct values
- "More videos" sidebar shows other library entries
- "Re-generate" button navigates to `/?prefill={run_id}`

- [ ] **Step 3: Commit**

```bash
git add server/static/video.html
git commit -m "feat: video detail page with player, downloads, script, and related videos"
```

---

## Task 11: Add top nav to index.html

**Files:**
- Modify: `server/static/index.html`

- [ ] **Step 1: Add nav bar to index.html**

In `server/static/index.html`, find the opening `<body>` tag (or the first element inside body). Insert the following nav bar as the first element inside `<body>`:

```html
<nav class="topnav">
  <a href="/" class="nav-logo">◆ CHALKBOARD</a>
  <div class="nav-links">
    <a href="/" class="nav-link active">Generate</a>
    <a href="/library" class="nav-link">Library</a>
  </div>
</nav>
```

Then add the nav CSS to the `<style>` block in `index.html`. Find the existing `:root` block and add after it:

```css
.topnav {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 0 32px;
  display: flex;
  align-items: center;
  height: 52px;
  position: sticky;
  top: 0;
  z-index: 100;
  gap: 0;
}
.nav-logo {
  font-size: 13px;
  color: var(--accent);
  letter-spacing: .12em;
  text-transform: uppercase;
  margin-right: 28px;
  text-decoration: none;
  font-weight: 500;
}
.nav-links { display: flex; gap: 4px; }
.nav-link {
  font-size: 12px;
  color: var(--muted);
  padding: 6px 14px;
  border-radius: 6px;
  text-decoration: none;
  letter-spacing: .04em;
  transition: color .15s, background .15s;
}
.nav-link:hover { color: var(--text); background: rgba(255,255,255,.05); }
.nav-link.active { color: var(--accent); background: rgba(200,185,122,.1); }
```

- [ ] **Step 2: Handle prefill query param in index.html**

In `index.html`'s existing JavaScript (at the bottom of the `<script>` block), add prefill handling. Find the DOMContentLoaded or initialization code and add:

```javascript
// Pre-fill form from library "Re-generate" link
const urlParams = new URLSearchParams(window.location.search);
const prefillId = urlParams.get('prefill');
if (prefillId) {
  fetch(`/api/library/${prefillId}`)
    .then(r => r.ok ? r.json() : null)
    .then(v => {
      if (!v) return;
      const set = (id, val) => { const el = document.getElementById(id); if (el && val != null) el.value = val; };
      set('topic', v.topic);
      set('effort', v.effort);
      set('audience', v.audience);
      set('tone', v.tone);
      set('theme', v.theme);
      set('speed', v.speed);
      if (v.template) set('template', v.template);
    })
    .catch(() => {});
}
```

Note: this assumes the form fields have `id="topic"`, `id="effort"`, `id="audience"`, `id="tone"`, `id="theme"`, `id="speed"`, `id="template"`. Verify these IDs exist in `index.html` before adding — check the existing form markup and adjust `id` references to match the actual IDs used.

- [ ] **Step 3: Verify the nav bar**

With the server running, open `http://localhost:8071`:
- Nav bar appears at top with "◆ CHALKBOARD", "Generate" (active/gold), "Library"
- Clicking "Library" navigates to `/library`
- From library, clicking "Generate" returns to `/`
- "Re-generate" button on a detail page pre-fills the form correctly

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -v --tb=short -q
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add server/static/index.html
git commit -m "feat: add top nav to index.html with Library link and prefill support"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| SQLiteLibraryStore with LibraryStore ABC | Tasks 2–5 |
| VideoMeta with all fields | Task 2 |
| SQLite schema | Task 3 |
| `add_video`, `get_video`, `list_videos`, `delete_video` | Tasks 3–5 |
| Search by topic + script (LIKE) | Task 4 |
| Sort: newest/oldest/longest/shortest | Task 4 |
| Pagination (limit/offset) | Task 4 |
| GET/DELETE /api/library endpoints | Task 7 |
| output_files populated from disk in get_video | Task 7 |
| Thumbnail extraction (ffmpeg, 10% seek) | Task 6 |
| CSS fallback thumbnail | Tasks 9, 10 |
| Startup backfill of existing runs | Task 8 |
| manifest.json with full params | Task 1 |
| library_store.add_video on job completion | Task 8 |
| /library page route | Task 7 |
| /library/{run_id} page route | Task 7 |
| library.html 4-col grid + search + sort + load more | Task 9 |
| video.html player + downloads + script + params + related | Task 10 |
| Top nav on all three pages | Tasks 9, 10, 11 |
| Re-generate prefill | Tasks 10, 11 |
| aiosqlite already in requirements.txt | ✓ confirmed |

**No gaps found.**

**Type consistency check:** `VideoMeta`, `LibraryStore`, `SQLiteLibraryStore`, `make_library_router`, `make_pages_router` — all names are consistent across Tasks 2–11. `_ROW_KEYS` tuple in Task 3 matches the 14 DB columns exactly. `_row_to_meta` in Task 3 uses `zip(_ROW_KEYS, row)` — row must return columns in the same order as `_ROW_KEYS` — verified by `SELECT {', '.join(_ROW_KEYS)} FROM videos` in Task 3/4.

**Placeholder scan:** No TBDs found. All code blocks are complete.
