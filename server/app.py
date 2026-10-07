# server/app.py
from __future__ import annotations
import json
from contextlib import asynccontextmanager
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
from server.version_routes import make_version_router
from pipeline.version import __version__ as CHALKBOARD_VERSION, git as _git_info


def _measured_duration(run_dir: Path) -> float | None:
    """Narration length from segments.json (measured TTS durations); None if
    the file is missing or any segment has no measured duration."""
    try:
        segs = json.loads((run_dir / "segments.json").read_text())
    except Exception:
        return None
    durs = [s.get("actual_duration_sec") for s in segs] if isinstance(segs, list) else []
    if not durs or not all(isinstance(d, (int, float)) for d in durs):
        return None
    return float(sum(durs))


def _disk_fields(run_dir: Path, manifest: dict) -> dict:
    """Library fields read from the run's own files. Missing => None, never a default."""
    speed = manifest.get("speed")
    return {
        "title": manifest.get("title", ""),
        "duration_sec": _measured_duration(run_dir),
        "quality": manifest.get("quality"),
        "effort": manifest.get("effort"),
        "audience": manifest.get("audience"),
        "tone": manifest.get("tone"),
        "theme": manifest.get("theme"),
        "template": manifest.get("template"),
        "speed": float(speed) if isinstance(speed, (int, float)) else None,
        "narrator": manifest.get("narrator"),
        "thumb_path": str(run_dir / "thumb.jpg") if (run_dir / "thumb.jpg").exists() else None,
    }


async def _backfill(store: LibraryStore, output_dir: Path, refresh: bool = False) -> None:
    """Index any completed runs in output_dir not yet in library.db.

    refresh=True also re-reads the files of runs already indexed and corrects
    rows that hold values the files do not support (older code filled gaps
    with defaults such as duration 0 or quality "medium")."""
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
        existing = await store.get_video(run_id)
        if existing is not None and not refresh:
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            fields = _disk_fields(run_dir, manifest)
            if existing is not None:
                update = {k: v for k, v in fields.items() if getattr(existing, k) != v}
                if update:
                    await store.add_video(existing.model_copy(update=update))
                continue
            script = (run_dir / "script.txt").read_text() if (run_dir / "script.txt").exists() else ""
            mtime = final_mp4.stat().st_mtime
            created_at = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            meta = VideoMeta(
                run_id=run_id,
                topic=manifest.get("topic", run_id),
                created_at=created_at,
                script=script,
                status="completed",
                **fields,
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

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await library_store.init()
        await _backfill(library_store, Path(OUTPUT_DIR).resolve(), refresh=True)
        yield

    app = FastAPI(title="Chalkboard API", version=CHALKBOARD_VERSION or "unknown", lifespan=lifespan)

    # API routes (must come before StaticFiles mount)
    app.include_router(make_router(store, library_store))
    app.include_router(make_library_router(library_store))
    app.include_router(make_pages_router())
    app.include_router(make_version_router())
    _git_info()  # capture git facts at startup

    # Serve frontend static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
