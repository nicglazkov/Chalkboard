# server/app.py
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# Raise per-part upload limit from the 1 MB default to 20 MB so the upload
# endpoint can accept PDF and image files up to their per-type size limits.
from starlette.formparsers import MultiPartParser
MultiPartParser.max_part_size = 20 * 1024 * 1024

from server.jobs import JobStore
from server.routes import make_router


def create_app(store: JobStore | None = None) -> FastAPI:
    if store is None:
        store = JobStore()

    app = FastAPI(title="Chalkboard API", version="0.1.0")
    app.include_router(make_router(store))

    # Serve frontend static files if the directory exists
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app


app = create_app()
