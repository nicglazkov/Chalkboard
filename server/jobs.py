# server/jobs.py
from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Literal


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
