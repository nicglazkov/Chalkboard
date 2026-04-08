from __future__ import annotations
from abc import ABC, abstractmethod
from pydantic import BaseModel, Field

import aiosqlite


class VideoMeta(BaseModel):
    run_id: str
    topic: str
    title: str = ""                        # AI-generated title; falls back to topic if empty
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
    status       TEXT DEFAULT 'completed'
)
"""

_ROW_KEYS = (
    "run_id", "topic", "title", "duration_sec", "quality", "created_at",
    "thumb_path", "script", "effort", "audience", "tone",
    "theme", "template", "speed", "status",
)

_SORT_MAP = {
    "newest":   "created_at DESC",
    "oldest":   "created_at ASC",
    "longest":  "duration_sec DESC",
    "shortest": "duration_sec ASC",
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
            # Migrate: add title column to existing DBs that predate it
            try:
                await db.execute("ALTER TABLE videos ADD COLUMN title TEXT DEFAULT ''")
            except Exception:
                pass  # column already exists
            await db.commit()

    async def add_video(self, meta: VideoMeta) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO videos
                   (run_id, topic, title, duration_sec, quality, created_at,
                    thumb_path, script, effort, audience, tone,
                    theme, template, speed, status)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    meta.run_id, meta.topic, meta.title, meta.duration_sec, meta.quality,
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
        order = _SORT_MAP.get(sort, "created_at DESC")

        if query:
            where = "WHERE topic LIKE ? COLLATE NOCASE OR title LIKE ? COLLATE NOCASE OR script LIKE ? COLLATE NOCASE"
            params = (f"%{query}%", f"%{query}%", f"%{query}%")
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
