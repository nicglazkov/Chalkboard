from __future__ import annotations
import asyncio
import shutil
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pipeline import run_stats
from server import run_data
from server.library import LibraryStore, VideoMeta


def make_library_router(store: LibraryStore, output_dir: Path | str | None = None) -> APIRouter:
    from config import OUTPUT_DIR
    _output_dir = Path(output_dir) if output_dir else Path(OUTPUT_DIR).resolve()

    router = APIRouter(prefix="/api")
    last_scan = [0.0]

    async def _refresh_index() -> None:
        import time
        if time.monotonic() - last_scan[0] < 10:
            return
        last_scan[0] = time.monotonic()
        from server.app import _backfill
        await _backfill(store, _output_dir)

    @router.get("/library")
    async def list_videos(
        q: str = "",
        limit: int = 50,
        offset: int = 0,
        sort: str = "newest",
    ):
        limit = min(limit, 100)
        # Pick up videos made from the CLI since the last look (cheap, throttled).
        await _refresh_index()
        videos, total = await store.list_videos(query=q, limit=limit, offset=offset, sort=sort)
        items = await asyncio.to_thread(lambda: [_list_item(v) for v in videos])
        return {"videos": items, "total": total, "limit": limit, "offset": offset}

    def _run_dir(run_id: str) -> Path:
        run_dir = (_output_dir / run_id).resolve()
        if run_dir.parent != _output_dir.resolve():
            raise HTTPException(status_code=404, detail="Video not found")
        return run_dir

    def _list_item(v: VideoMeta) -> dict:
        """Library row plus facts read from the run's files (None = not recorded)."""
        d = v.model_dump()
        run_dir = _output_dir / v.run_id
        stats = run_stats.read(run_dir)
        d["has_run_stats"] = stats is not None
        d["has_final"] = (run_dir / "final.mp4").exists()
        d["thumb_url"] = f"/api/jobs/{v.run_id}/files/thumb.jpg" if (run_dir / "thumb.jpg").exists() else None
        d["video_url"] = f"/api/jobs/{v.run_id}/files/final.mp4" if d["has_final"] else None
        d["run_seconds"] = stats.get("total_seconds") if stats and not stats.get("resumed") else None
        d["cost_usd"] = stats.get("cost_usd") if stats and not stats.get("resumed") else None
        d["run_result"] = stats.get("result") if stats else None
        # created_at: job completion time (server runs) or final.mp4's mtime (indexed from disk).
        return d

    @router.get("/library/{run_id}/timeline")
    async def video_timeline(run_id: str):
        run_dir = _run_dir(run_id)
        tl = await asyncio.to_thread(run_data.run_timeline, run_dir)
        if tl is None:
            raise HTTPException(status_code=404, detail="No segments.json for this run")
        return tl

    @router.get("/library/{run_id}/quality")
    async def video_quality(run_id: str):
        run_dir = _run_dir(run_id)
        if not run_dir.is_dir():
            raise HTTPException(status_code=404, detail="Video not found")
        return await asyncio.to_thread(run_data.quality, run_dir)

    @router.get("/library/{run_id}/stats")
    async def video_stats(run_id: str):
        run_dir = _run_dir(run_id)
        if not run_dir.is_dir():
            raise HTTPException(status_code=404, detail="Video not found")
        return {"run_stats": run_stats.read(run_dir)}

    @router.get("/library/{run_id}")
    async def get_video(run_id: str):
        meta = await store.get_video(run_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Video not found")
        run_dir = _output_dir / run_id
        if run_dir.exists():
            output_files = [
                f.name for f in run_dir.iterdir()
                if f.is_file() and f.suffix in (".mp4", ".srt", ".json", ".txt", ".jpg")
            ]
            meta = meta.model_copy(update={"output_files": sorted(output_files)})
        item = await asyncio.to_thread(_list_item, meta)
        item["run_stats"] = run_stats.read(run_dir)
        return item

    @router.delete("/library/{run_id}", status_code=204)
    async def delete_video(run_id: str, files: bool = False):
        # run_id is a single path segment, but "." / ".." (e.g. sent as %2e%2e)
        # would make rmtree hit the output dir or its parent.
        run_dir = (_output_dir / run_id).resolve()
        if run_dir.parent != _output_dir.resolve():
            raise HTTPException(status_code=404, detail="Video not found")
        await store.delete_video(run_id)
        if files:
            if run_dir.is_dir():
                await asyncio.to_thread(shutil.rmtree, run_dir)

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
