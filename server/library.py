from __future__ import annotations
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field

import aiosqlite


class VideoMeta(BaseModel):
    run_id: str
    topic: str
    title: str = ""                        # AI-generated title; falls back to topic if empty
    created_at: str                        # ISO8601 UTC e.g. "2026-04-07T10:00:00Z"
    duration_sec: float | None = None     # measured narration length; None = not recorded
    quality: str | None = None            # low / medium / high / 4k; None = not recorded
    thumb_path: str | None = None         # relative path; None = CSS fallback
    script: str = ""
    effort: str | None = None             # None = not recorded (old manifests)
    audience: str | None = None
    tone: str | None = None
    theme: str | None = None
    template: str | None = None
    speed: float | None = None
    status: str = "completed"
    narrator: str | None = None           # pipeline/tts/voices.py name, or backend for legacy runs
    notes: str | None = None              # the user's free-text notes; None = no notes
    output_files: list[str] = Field(default_factory=list)


NOTES_MAX_CHARS = 10_000


def clean_notes(notes: str | None) -> str | None:
    """Trim notes; empty or whitespace-only means no notes (None)."""
    if notes is None:
        return None
    notes = notes.strip()
    return notes or None


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

    @abstractmethod
    async def set_notes(self, run_id: str, notes: str | None) -> VideoMeta | None:
        """Store notes (trimmed; empty = None). Returns the updated row, None if unknown."""


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS videos (
    run_id       TEXT PRIMARY KEY,
    topic        TEXT NOT NULL,
    title        TEXT DEFAULT '',
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
    status       TEXT DEFAULT 'completed',
    narrator     TEXT,
    notes        TEXT
)
"""

# Columns added after the first release. init() adds any that an existing
# library.db lacks (ALTER TABLE ADD COLUMN keeps every row).
_ADDED_COLUMNS = (
    ("title", "TEXT DEFAULT ''"),
    ("narrator", "TEXT"),
    ("notes", "TEXT"),
)

_ROW_KEYS = (
    "run_id", "topic", "title", "duration_sec", "quality", "created_at",
    "thumb_path", "script", "effort", "audience", "tone",
    "theme", "template", "speed", "status", "narrator", "notes",
)

# add_video writes every column except notes: re-indexing a run from its files
# (backfill, a finished job) must never wipe what the user wrote.
_INDEX_KEYS = tuple(k for k in _ROW_KEYS if k != "notes")

_SORT_MAP = {
    "newest":   "created_at DESC",
    "oldest":   "created_at ASC",
    "longest":  "duration_sec IS NULL, duration_sec DESC",  # unknown durations last
    "shortest": "duration_sec IS NULL, duration_sec ASC",
}


def _row_to_meta(row: tuple) -> VideoMeta:
    return VideoMeta(**dict(zip(_ROW_KEYS, row)))


class SQLiteLibraryStore(LibraryStore):
    def __init__(self, db_path: str):
        self.db_path = db_path

    async def init(self) -> None:
        """Create the videos table if it doesn't exist, and migrate existing DBs."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute(_CREATE_TABLE)
            # Migrate older DBs in place: add only the columns they lack.
            async with db.execute("PRAGMA table_info(videos)") as cur:
                have = {row[1] for row in await cur.fetchall()}
            for name, decl in _ADDED_COLUMNS:
                if name not in have:
                    await db.execute(f"ALTER TABLE videos ADD COLUMN {name} {decl}")
            await db.commit()

    async def add_video(self, meta: VideoMeta) -> None:
        cols = ", ".join(_INDEX_KEYS)
        marks = ",".join("?" * len(_INDEX_KEYS))
        updates = ", ".join(f"{k} = excluded.{k}" for k in _INDEX_KEYS if k != "run_id")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"INSERT INTO videos ({cols}) VALUES ({marks}) "
                f"ON CONFLICT(run_id) DO UPDATE SET {updates}",
                tuple(getattr(meta, k) for k in _INDEX_KEYS),
            )
            await db.commit()

    async def set_notes(self, run_id: str, notes: str | None) -> VideoMeta | None:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "UPDATE videos SET notes = ? WHERE run_id = ?", (clean_notes(notes), run_id)
            )
            await db.commit()
            if cur.rowcount == 0:
                return None
        return await self.get_video(run_id)

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
        order = _SORT_MAP.get(sort, "created_at DESC")

        if query:
            fields = ("topic", "title", "script", "notes")
            where = "WHERE " + " OR ".join(f"{f} LIKE ? COLLATE NOCASE" for f in fields)
            params = (f"%{query}%",) * len(fields)
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

    async def delete_video(self, run_id: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM videos WHERE run_id = ?", (run_id,))
            await db.commit()
