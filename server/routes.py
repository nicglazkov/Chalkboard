# server/routes.py
from __future__ import annotations
import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse
from config import OUTPUT_DIR
from server.jobs import JobStore, run_job, Job
from server.library import LibraryStore
from server.models import CreateJobRequest, JobResponse
from server.upload import (
    validate_and_save,
    FileSizeError, TotalSizeError, UnsupportedFileTypeError,
)

def _job_to_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status,
        topic=job.topic,
        events=job.events,
        error=job.error,
        output_files=job.output_files,
    )


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

    @router.post("/jobs/upload", status_code=202, response_model=JobResponse)
    async def create_job_with_files(
        topic: str = Form(...),
        effort: str = Form("medium"),
        audience: str = Form("intermediate"),
        tone: str = Form("casual"),
        theme: str = Form("chalkboard"),
        template: str = Form(""),
        speed: float = Form(1.0),
        burn_captions: bool = Form(False),
        quiz: bool = Form(False),
        qa_density: str = Form("normal"),
        urls: list[str] = Form(default=[]),
        github: list[str] = Form(default=[]),
        files: list[UploadFile] = File(default=[]),
    ):
        """Create a job from multipart form data, optionally with file uploads."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="chalkboard_upload_"))
        upload_dir: Path | None = None
        try:
            saved_paths = await validate_and_save(files, tmp_dir)
            upload_dir = tmp_dir if saved_paths else None
        except FileSizeError as e:
            raise HTTPException(status_code=413, detail=str(e))
        except TotalSizeError as e:
            raise HTTPException(status_code=413, detail=str(e))
        except UnsupportedFileTypeError as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            if upload_dir is None:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        job = store.create(
            topic=topic, effort=effort, audience=audience,
            tone=tone, theme=theme, template=template or None, speed=speed,
            burn_captions=burn_captions, quiz=quiz,
            urls=urls, github=github, qa_density=qa_density,
            upload_dir=upload_dir,
        )
        output_dir = Path(OUTPUT_DIR).resolve()
        asyncio.create_task(run_job(job, output_dir, library_store=library_store))
        return _job_to_response(job)

    @router.get("/jobs", response_model=list[JobResponse])
    async def list_jobs():
        return [_job_to_response(j) for j in store.list()]

    @router.get("/jobs/{job_id}", response_model=JobResponse)
    async def get_job(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return _job_to_response(job)

    @router.get("/jobs/{job_id}/events")
    async def job_events(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        async def generator():
            async for event in job.event_stream():
                yield {"data": json.dumps(event)}
            yield {"data": json.dumps({"done": True})}

        return EventSourceResponse(generator())

    @router.get("/jobs/{job_id}/files/{filename}")
    async def get_file(job_id: str, filename: str):
        # Works for both in-session jobs and backfilled library runs
        base_dir = Path(OUTPUT_DIR).resolve() / job_id
        file_path = (base_dir / filename).resolve()
        if not file_path.is_relative_to(base_dir):
            raise HTTPException(status_code=404, detail="File not found")
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(str(file_path))

    return router
