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
                if f.is_file() and f.suffix in (".mp4", ".srt", ".json", ".txt", ".jpg")
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
