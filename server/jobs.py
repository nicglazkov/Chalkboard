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

import threading

from pipeline import run_stats, telemetry
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
    quality: str | None = None
    narrator: str | None = None
    pace: str | None = None
    upload_dir: Path | None = None          # temp dir for uploaded files; deleted after run
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    events: list[dict] = field(default_factory=list)
    error: str | None = None
    output_files: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    _subscribers: list = field(default_factory=list, repr=False)

    def append_event(self, event: dict) -> None:
        # Server-side timestamp so a page reopened mid-job can show step times.
        # Telemetry events arrive already stamped with the time they happened.
        event = {**event, "ts": event.get("ts") or datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")}
        if event.get("node") == "peek":
            # Each peek carries the full text so far: keep only the latest per
            # stage in the stored list (SSE subscribers still get every one).
            stage = (event.get("updates") or {}).get("stage")
            self.events = [e for e in self.events
                           if not (e.get("node") == "peek" and (e.get("updates") or {}).get("stage") == stage)]
        self.events.append(event)
        for q in list(self._subscribers):
            q.put_nowait(event)

    async def event_stream(self, replay: bool = True):
        """Async generator: (optionally) the stored events, then live ones
        until the job is terminal. Each subscriber gets its own queue."""
        q: asyncio.Queue = asyncio.Queue()
        backlog = list(self.events) if replay else []
        self._subscribers.append(q)
        try:
            for event in backlog:
                yield event
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield event
                except asyncio.TimeoutError:
                    if self.status in ("completed", "failed") and q.empty():
                        break
        finally:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def totals(self) -> dict:
        t = run_stats.totals(self.events)
        return {k: t[k] for k in ("input_tokens", "output_tokens", "web_searches", "cost_usd", "tts_chars")} | {
            "claude_calls": t["calls"]}


class JobStore:
    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def create(self, topic: str, effort: str, audience: str, tone: str,
               theme: str, template: str | None, speed: float,
               burn_captions: bool = False, quiz: bool = False,
               urls: list[str] | None = None, github: list[str] | None = None,
               qa_density: _QADensity = "normal",
               upload_dir: Path | None = None,
               quality: str | None = None,
               narrator: str | None = None,
               pace: str | None = None) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, topic=topic, effort=effort, audience=audience,
                  tone=tone, theme=theme, template=template, speed=speed,
                  burn_captions=burn_captions, quiz=quiz,
                  urls=urls or [], github=github or [],
                  qa_density=qa_density, upload_dir=upload_dir, quality=quality, narrator=narrator,
                  pace=pace)
        self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return list(self._jobs.values())


# Re-export for mocking in tests
run = _pipeline_run


async def _do_render(run_id: str, verbose: bool = False, burn_captions: bool = False) -> Path | None:
    """Render (local or Docker backend) and merge audio. Returns final.mp4, or None on failure."""
    try:
        final_mp4 = await asyncio.to_thread(_render, run_id, verbose, burn_captions)
        return final_mp4 if final_mp4.exists() else None
    except RenderFailed:
        return None


# Jobs beyond this many wait their turn (renders are CPU-heavy; TTS shares one GPU).
_job_slots = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT_JOBS", "3")))


async def run_job(job: Job, output_dir: Path, library_store=None) -> None:
    """Execute the full pipeline + render for a job. Updates job.status in place."""
    async with _job_slots:
        await _run_job(job, output_dir, library_store)


def _job_sink(job: Job) -> telemetry.Sink:
    """Telemetry sink for a job: events from worker threads (call_json, Kokoro,
    the render reader) are handed to the event loop; events emitted on the
    loop thread are appended directly so ordering with node events holds."""
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()

    def _deliver(event: dict) -> None:
        if threading.get_ident() == loop_thread:
            job.append_event(event)
        else:
            try:
                loop.call_soon_threadsafe(job.append_event, event)
            except RuntimeError:
                pass  # loop closed (server shutting down)

    return telemetry.Sink(_deliver, peek=True)


def _video_meta(job: Job, run_dir: Path) -> VideoMeta:
    """Library row for a finished job, from the files it wrote (None = not recorded)."""
    duration_sec = None
    seg_path = run_dir / "segments.json"
    if seg_path.exists():
        segs = json.loads(seg_path.read_text())
        durs = [s.get("actual_duration_sec") for s in segs]
        if durs and all(isinstance(d, (int, float)) for d in durs):
            duration_sec = float(sum(durs))
    manifest_data = {}
    manifest_path = run_dir / "manifest.json"
    if manifest_path.exists():
        manifest_data = json.loads(manifest_path.read_text())
    script = (run_dir / "script.txt").read_text() if (run_dir / "script.txt").exists() else ""
    thumb_path = str(run_dir / "thumb.jpg") if (run_dir / "thumb.jpg").exists() else None
    return VideoMeta(
        run_id=job.id,
        topic=job.topic,
        title=manifest_data.get("title", ""),
        duration_sec=duration_sec,
        quality=manifest_data.get("quality"),
        created_at=datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        thumb_path=thumb_path,
        script=script,
        effort=job.effort,
        audience=job.audience,
        tone=job.tone,
        theme=job.theme,
        template=job.template,
        speed=job.speed,
        status="completed",
        narrator=manifest_data.get("narrator"),
    )


def _write_run_stats(job: Job, run_dir: Path) -> None:
    try:
        settings = {"run_id": job.id, "effort": job.effort, "quality": job.quality,
                    "narrator": job.narrator, "pace": job.pace, "source": "server"}
        # The manifest holds the resolved values (server defaults applied).
        settings.update({k: v for k, v in run_stats.manifest_settings(run_dir).items() if v is not None})
        ok = job.status == "completed" and (run_dir / "final.mp4").exists()
        stats = run_stats.build(
            job.events, started_at=job.started_at, finished_at=job.finished_at,
            settings=settings, result="done" if ok else "failed",
        )
        run_stats.write(run_dir, stats)
    except Exception as e:
        print(f"  [run_stats] could not write for {job.id}: {e}")


async def _run_job(job: Job, output_dir: Path, library_store=None) -> None:
    job.status = "running"
    job.started_at = telemetry.now_iso()
    token = telemetry.set_sink(_job_sink(job))
    try:
        await _run_job_inner(job, output_dir, library_store)
    finally:
        telemetry.reset_sink(token)
        job.finished_at = telemetry.now_iso()
        _write_run_stats(job, output_dir / job.id)


async def _run_job_inner(job: Job, output_dir: Path, library_store=None) -> None:
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
            quality=job.quality,
            narrator=job.narrator,
            pace=job.pace,
        )

        # render_trigger writes manifest.json as its final step.
        # If it's absent, the pipeline ended before completing (e.g. max retries
        # hit escalate_to_user which auto-aborted in non-interactive mode).
        if not (output_dir / job.id / "manifest.json").exists():
            raise RuntimeError("pipeline did not complete — no output was written")

        # Post-pipeline phases are reported as pseudo-nodes so the progress UI
        # can show render / QA / quiz instead of going quiet for minutes.
        # Live render position (segment / animation) arrives as more `render`
        # events from main._render_once through the telemetry sink.
        job.append_event({"node": "render", "updates": {"status": "running", "segment": None,
                                                        "segments": None, "animation": None,
                                                        "animations": None}})
        final_mp4 = await _do_render(job.id, burn_captions=job.burn_captions)
        if final_mp4 is None:
            job.error = "render failed; pipeline output preserved"
        job.append_event({"node": "render", "updates": {"status": "done" if final_mp4 else "failed"}})

        # Visual QA (runs in a thread — _run_qa_loop is a sync function)
        if final_mp4 is not None and job.qa_density != "zero":
            job.append_event({"node": "visual_qa", "updates": {"status": "running"}})
            await asyncio.to_thread(
                _run_qa_loop,
                job.id, final_mp4,
                theme=job.theme, audience=job.audience,
                tone=job.tone, effort_level=job.effort,
                context_blocks=context_blocks,
                qa_density=job.qa_density,
                burn_captions=job.burn_captions,
            )
            job.append_event({"node": "visual_qa", "updates": {"status": "done"}})

        # Quiz generation (sync function — run in thread).
        # No final_mp4 guard: quiz only needs script.txt, so it works even when
        # render failed or --no-render was used.
        if job.quiz:
            job.append_event({"node": "quiz", "updates": {"status": "running"}})
            await asyncio.to_thread(_generate_quiz, job.id)
            job.append_event({"node": "quiz", "updates": {"status": "done"}})

        # Collect output files
        run_dir = output_dir / job.id
        if run_dir.exists():
            job.output_files = [
                f.name for f in run_dir.iterdir()
                if f.is_file() and f.suffix in (".mp4", ".srt", ".json", ".txt", ".py", ".jpg", ".wav")
            ]

        job.status = "completed"

        # Index completed job in library
        if library_store is not None and (run_dir / "final.mp4").exists():
            try:
                await library_store.add_video(_video_meta(job, run_dir))
            except Exception as e:
                print(f"  [library] failed to index job {job.id}: {e}")

    except Exception as e:
        job.status = "failed"
        job.error = str(e)
    finally:
        # Delete temp upload dir regardless of outcome
        if job.upload_dir is not None and job.upload_dir.exists():
            shutil.rmtree(job.upload_dir, ignore_errors=True)
