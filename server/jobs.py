# server/jobs.py
from __future__ import annotations
import asyncio
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

_QADensity = Literal["zero", "normal", "high"]

from pipeline.context import fetch_url_blocks, collect_files, load_context_blocks
from server.library import VideoMeta
from main import (
    run as _pipeline_run,
    _render, RenderFailed,
    _run_qa_loop, _generate_quiz, _github_to_raw_url,
)


@dataclass
class Job:
    id: str
    topic: str
    effort: str
    audience: str
    tone: str
    theme: str
    template: str | None
    speed: float
    burn_captions: bool = False
    quiz: bool = False
    urls: list[str] = field(default_factory=list)
    github: list[str] = field(default_factory=list)
    qa_density: _QADensity = "normal"
    upload_dir: Path | None = None          # temp dir for uploaded files; deleted after run
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    events: list[dict] = field(default_factory=list)
    error: str | None = None
    output_files: list[str] = field(default_factory=list)
    _queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)

    def append_event(self, event: dict) -> None:
        self.events.append(event)
        self._queue.put_nowait(event)

    async def event_stream(self):
        """Async generator — yields events until job is terminal."""
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield event
            except asyncio.TimeoutError:
                if self.status in ("completed", "failed"):
                    break


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create(self, topic: str, effort: str, audience: str, tone: str,
               theme: str, template: str | None, speed: float,
               burn_captions: bool = False, quiz: bool = False,
               urls: list[str] | None = None, github: list[str] | None = None,
               qa_density: _QADensity = "normal",
               upload_dir: Path | None = None) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, topic=topic, effort=effort, audience=audience,
                  tone=tone, theme=theme, template=template, speed=speed,
                  burn_captions=burn_captions, quiz=quiz,
                  urls=urls or [], github=github or [],
                  qa_density=qa_density, upload_dir=upload_dir)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())


# Re-export for mocking in tests
run = _pipeline_run


async def _do_render(run_id: str, verbose: bool = False, burn_captions: bool = False) -> Path | None:
    """Run Docker render. Returns path to final.mp4 or None on failure."""
    try:
        final_mp4 = await asyncio.to_thread(_render, run_id, verbose, burn_captions)
        return final_mp4 if final_mp4.exists() else None
    except RenderFailed:
        return None


async def run_job(job: Job, output_dir: Path, library_store=None) -> None:
    """Execute the full pipeline + render for a job. Updates job.status in place."""
    job.status = "running"

    def _on_progress(event: dict) -> None:
        for node_name, updates in event.items():
            if node_name == "__end__":
                continue
            job.append_event({"node": node_name, "updates": updates})

    try:
        # Build context_blocks from uploaded files, URLs, and GitHub repos
        context_blocks = None

        if job.upload_dir is not None and job.upload_dir.exists():
            file_paths = await asyncio.to_thread(
                collect_files, [str(job.upload_dir)]
            )
            file_blocks = await asyncio.to_thread(load_context_blocks, file_paths)
            context_blocks = (context_blocks or []) + file_blocks
            print(f"  [server] loaded {len(file_blocks)} block(s) from uploaded files")

        for url in job.urls:
            print(f"  [server] fetching URL: {url}")
            blocks = await asyncio.to_thread(fetch_url_blocks, url)
            context_blocks = (context_blocks or []) + blocks
            print(f"  [server] fetched {len(blocks)} block(s) from URL")
        for repo in job.github:
            raw_url = _github_to_raw_url(repo)
            print(f"  [server] fetching GitHub repo: {repo} → {raw_url}")
            blocks = await asyncio.to_thread(fetch_url_blocks, raw_url)
            context_blocks = (context_blocks or []) + blocks
            print(f"  [server] fetched {len(blocks)} block(s) from GitHub")
        if context_blocks:
            print(f"  [server] total context: {len(context_blocks)} block(s) → passing to pipeline")
        else:
            print(f"  [server] no context blocks (urls={job.urls!r}, github={job.github!r})")

        await run(
            topic=job.topic,
            effort=job.effort,
            thread_id=job.id,
            audience=job.audience,
            tone=job.tone,
            theme=job.theme,
            speed=job.speed,
            template=job.template,
            context_blocks=context_blocks,
            on_progress=_on_progress,
            interactive=False,
        )

        # render_trigger writes manifest.json as its final step.
        # If it's absent, the pipeline ended before completing (e.g. max retries
        # hit escalate_to_user which auto-aborted in non-interactive mode).
        if not (output_dir / job.id / "manifest.json").exists():
            raise RuntimeError("pipeline did not complete — no output was written")

        final_mp4 = await _do_render(job.id, burn_captions=job.burn_captions)
        if final_mp4 is None:
            job.error = "render failed; pipeline output preserved"

        # Visual QA (runs in a thread — _run_qa_loop is a sync function)
        if final_mp4 is not None and job.qa_density != "zero":
            await asyncio.to_thread(
                _run_qa_loop,
                job.id, final_mp4,
                theme=job.theme, audience=job.audience,
                tone=job.tone, effort_level=job.effort,
                context_blocks=context_blocks,
                qa_density=job.qa_density,
            )

        # Quiz generation (sync function — run in thread).
        # No final_mp4 guard: quiz only needs script.txt, so it works even when
        # render failed or --no-render was used.
        if job.quiz:
            await asyncio.to_thread(_generate_quiz, job.id)

        # Collect output files
        run_dir = output_dir / job.id
        if run_dir.exists():
            job.output_files = [
                f.name for f in run_dir.iterdir()
                if f.is_file() and f.suffix in (".mp4", ".srt", ".json", ".txt", ".py", ".jpg")
            ]

        job.status = "completed"

        # Index completed job in library
        if library_store is not None:
            try:
                seg_path = run_dir / "segments.json"
                duration_sec = 0.0
                if seg_path.exists():
                    segs = json.loads(seg_path.read_text())
                    duration_sec = sum(s.get("actual_duration_sec", 0) for s in segs)
                manifest_path = run_dir / "manifest.json"
                quality = "medium"
                title = ""
                if manifest_path.exists():
                    manifest_data = json.loads(manifest_path.read_text())
                    quality = manifest_data.get("quality", "medium")
                    title = manifest_data.get("title", "")
                script = (run_dir / "script.txt").read_text() if (run_dir / "script.txt").exists() else ""
                thumb_path = str(run_dir / "thumb.jpg") if (run_dir / "thumb.jpg").exists() else None
                created_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                meta = VideoMeta(
                    run_id=job.id,
                    topic=job.topic,
                    title=title,
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

    except Exception as e:
        job.status = "failed"
        job.error = str(e)
    finally:
        # Delete temp upload dir regardless of outcome
        if job.upload_dir is not None and job.upload_dir.exists():
            shutil.rmtree(job.upload_dir, ignore_errors=True)
