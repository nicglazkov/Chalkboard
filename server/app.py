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
                title=manifest.get("title", ""),
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
