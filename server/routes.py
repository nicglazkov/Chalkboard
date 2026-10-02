# server/routes.py
from __future__ import annotations
import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse
from config import OUTPUT_DIR
from server.jobs import JobStore, run_job, Job
from server.library import LibraryStore
from server.models import CreateJobRequest, JobResponse
from server.upload import (
    validate_and_save,
    FileSizeError, TotalSizeError, UnsupportedFileTypeError,
)
from server import insights, run_data, status as status_mod, voices as voices_mod


def _job_to_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status,
        topic=job.topic,
        events=job.events,
        error=job.error,
        output_files=job.output_files,
        effort=job.effort, quality=job.quality, narrator=job.narrator,
        qa_density=job.qa_density, quiz=job.quiz, burn_captions=job.burn_captions,
        template=job.template,
        audience=job.audience, tone=job.tone, theme=job.theme, speed=job.speed,
        totals=job.totals(),
        started_at=job.started_at, finished_at=job.finished_at,
    )


# Strong references to running job tasks: the event loop only keeps weak ones,
# so an unreferenced task can be garbage-collected mid-run.
_running_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _running_tasks.add(task)
    task.add_done_callback(_running_tasks.discard)
    return task


def make_router(store: JobStore, library_store: LibraryStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/jobs", status_code=202, response_model=JobResponse)
    async def create_job(req: CreateJobRequest):
        job = store.create(
            topic=req.topic, effort=req.effort, audience=req.audience,
            tone=req.tone, theme=req.theme, template=req.template, speed=req.speed,
            burn_captions=req.burn_captions, quiz=req.quiz,
            urls=req.urls, github=req.github, qa_density=req.qa_density,
            quality=req.quality, narrator=req.narrator,
        )
        output_dir = Path(OUTPUT_DIR).resolve()
        _spawn(run_job(job, output_dir, library_store=library_store))
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
        quality: str = Form(""),
        narrator: str = Form(""),
        urls: list[str] = Form(default=[]),
        github: list[str] = Form(default=[]),
        files: list[UploadFile] = File(default=[]),
    ):
        """Create a job from multipart form data, optionally with file uploads."""
        # Same validation as the JSON route (enum fields, quality, narrator), and
        # before any upload is written, so a typo fails fast instead of mid-run.
        try:
            req = CreateJobRequest(
                topic=topic, effort=effort, audience=audience, tone=tone, theme=theme,
                template=template or None, speed=speed, burn_captions=burn_captions,
                quiz=quiz, urls=urls, github=github, qa_density=qa_density,
                quality=quality or None, narrator=narrator or None,
            )
        except ValidationError as e:
            raise RequestValidationError(e.errors())

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
            topic=req.topic, effort=req.effort, audience=req.audience,
            tone=req.tone, theme=req.theme, template=req.template, speed=req.speed,
            burn_captions=req.burn_captions, quiz=req.quiz,
            urls=req.urls, github=req.github, qa_density=req.qa_density,
            upload_dir=upload_dir, quality=req.quality, narrator=req.narrator,
        )
        output_dir = Path(OUTPUT_DIR).resolve()
        _spawn(run_job(job, output_dir, library_store=library_store))
        return _job_to_response(job)

    @router.get("/meta")
    async def meta():
        import os
        import config as _cfg
        from pipeline import render as _render_backend
        from pipeline.tts.voices import NARRATORS
        keys = {"elevenlabs": "ELEVENLABS_API_KEY", "openai": "OPENAI_API_KEY"}
        # `configured` = the backend's API key is set (Kokoro needs none). It is
        # not a claim that the voice works: /api/voices probes availability.
        narrators = [
            {"id": nid, "label": spec["label"], "tagline": spec["tagline"], "backend": spec["backend"],
             "model": spec["model"],
             "configured": spec["backend"] == "kokoro" or bool(os.getenv(keys.get(spec["backend"], ""), ""))}
            for nid, spec in NARRATORS.items()
        ]
        return {
            "defaults": {
                "quality": _cfg.MANIM_QUALITY,
                "narrator": _cfg.NARRATOR or None,
                "tts_backend": _cfg.TTS_BACKEND,
                "effort": _cfg.DEFAULT_EFFORT,
                "audience": _cfg.DEFAULT_AUDIENCE,
                "tone": _cfg.DEFAULT_TONE,
                "theme": _cfg.DEFAULT_THEME,
            },
            "narrators": narrators,
            # backend() may import manim the first time; keep the event loop free.
            "render_backend": await asyncio.to_thread(_render_backend.backend),
            "model": _cfg.CLAUDE_MODEL,
            "running_jobs": sum(1 for j in store.list() if j.status in ("pending", "running")),
        }

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
    async def job_events(job_id: str, replay: bool = True):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        async def generator():
            async for event in job.event_stream(replay=replay):
                yield {"data": json.dumps(event)}
            yield {"data": json.dumps({"done": True})}

        return EventSourceResponse(generator())

    @router.get("/claude-status")
    async def claude_status():
        """Anthropic's status page, read from its JSON API (no keyword guessing)."""
        c = await asyncio.to_thread(status_mod.check_claude_status_page)
        label = {"ok": "operational", "warn": "degraded", "down": "outage"}.get(c["state"], "unknown")
        return {"status": label, "summary": c["summary"], "evidence": c["evidence"],
                "incidents": c["detail"].get("open_incidents", []), "checked_at": c["checked_at"],
                "url": "https://status.claude.com"}

    @router.get("/jobs/{job_id}/timeline")
    async def job_timeline(job_id: str):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        run_dir = Path(OUTPUT_DIR).resolve() / job.id
        return await asyncio.to_thread(run_data.job_timeline, run_dir, list(job.events))

    @router.get("/status")
    async def service_status():
        return await status_mod.status(store, Path(OUTPUT_DIR).resolve())

    @router.get("/voices")
    async def list_voices():
        return await voices_mod.list_voices(Path(OUTPUT_DIR).resolve())

    @router.get("/voices/{narrator}/sample")
    async def voice_sample(narrator: str):
        from pipeline.tts.voices import NARRATORS
        if narrator not in NARRATORS:
            raise HTTPException(status_code=404, detail="Unknown narrator")
        try:
            path = await voices_mod.get_sample(Path(OUTPUT_DIR).resolve(), narrator)
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=f"sample synthesis failed: {e}")
        return FileResponse(str(path), media_type="audio/wav")

    @router.get("/stats")
    async def stats():
        if library_store is None:
            raise HTTPException(status_code=503, detail="library not configured")
        videos, _ = await library_store.list_videos(limit=100000)
        return await asyncio.to_thread(insights.stats, videos, Path(OUTPUT_DIR).resolve())

    @router.get("/estimate")
    async def estimate(effort: str | None = None, quality: str | None = None,
                       narrator: str | None = None, research: bool | None = None):
        out = Path(OUTPUT_DIR).resolve()

        def _compute():
            run_ids = [p.name for p in out.iterdir() if (p / "run_stats.json").is_file()] if out.is_dir() else []
            return insights.estimate(out, run_ids, effort=effort or None, quality=quality or None,
                                     narrator=narrator or None, research=research)
        return await asyncio.to_thread(_compute)

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
