# server/jobs.py
from __future__ import annotations
import asyncio
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from main import run as _pipeline_run


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
               theme: str, template: str | None, speed: float) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, topic=topic, effort=effort, audience=audience,
                  tone=tone, theme=theme, template=template, speed=speed)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())


# Re-export for mocking in tests
run = _pipeline_run


async def _do_render(run_id: str, verbose: bool = False) -> Path | None:
    """Run Docker render. Returns path to final.mp4 or None on failure."""
    from main import _render, RenderFailed
    try:
        final_mp4 = await asyncio.to_thread(_render, run_id, verbose)
        return final_mp4 if final_mp4.exists() else None
    except RenderFailed:
        return None


async def run_job(job: Job, output_dir: Path) -> None:
    """Execute the full pipeline + render for a job. Updates job.status in place."""
    job.status = "running"

    def _on_progress(event: dict) -> None:
        for node_name, updates in event.items():
            if node_name == "__end__":
                continue
            job.append_event({"node": node_name, "updates": updates})

    try:
        await run(
            topic=job.topic,
            effort=job.effort,
            thread_id=job.id,
            audience=job.audience,
            tone=job.tone,
            theme=job.theme,
            speed=job.speed,
            template=job.template,
            on_progress=_on_progress,
            interactive=False,
        )

        final_mp4 = await _do_render(job.id)
        if final_mp4 is None:
            job.error = "render failed; pipeline output preserved"

        # Collect output files
        run_dir = output_dir / job.id
        if run_dir.exists():
            job.output_files = [
                f.name for f in run_dir.iterdir()
                if f.is_file() and f.suffix in (".mp4", ".srt", ".json", ".txt", ".py")
            ]

        job.status = "completed"
    except Exception as e:
        job.status = "failed"
        job.error = str(e)
