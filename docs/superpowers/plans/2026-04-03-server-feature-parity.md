# Server Feature Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add burn-captions, quiz, URL context, GitHub context, and visual QA to the API server and web UI, plus reorganise the UI into always-visible and Advanced sections.

**Architecture:** Three layered tasks — (1) plumb new fields through the data model (models → Job → routes), (2) wire the new behaviour into `run_job`, (3) update the frontend. Each task is independently testable and committable.

**Tech Stack:** Python 3.10, FastAPI, Pydantic v2, vanilla HTML/CSS/JS, pytest, pytest-asyncio

---

## Files

| File | Change |
|------|--------|
| `server/models.py` | Add 5 new fields to `CreateJobRequest` |
| `server/jobs.py` | Add fields to `Job` + `JobStore.create`; update `_do_render`; wire features in `run_job` |
| `server/routes.py` | Pass new fields from request → `store.create` |
| `server/static/index.html` | Advanced section + 5 new controls |
| `tests/test_server_jobs.py` | New behaviour tests |
| `tests/test_server.py` | Update `test_create_job_returns_202` payload |

---

### Task 1: New fields in models, Job, JobStore, and routes

**Files:**
- Modify: `server/models.py`
- Modify: `server/jobs.py` (Job dataclass + JobStore.create only)
- Modify: `server/routes.py`
- Test: `tests/test_server_jobs.py`
- Test: `tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_server_jobs.py`:

```python
def test_job_store_create_accepts_new_fields():
    store = JobStore()
    job = store.create(
        topic="test", effort="low", audience="intermediate",
        tone="casual", theme="chalkboard", template=None, speed=1.0,
        burn_captions=True, quiz=True,
        urls=["https://example.com"], github=["owner/repo"],
        qa_density="high",
    )
    assert job.burn_captions is True
    assert job.quiz is True
    assert job.urls == ["https://example.com"]
    assert job.github == ["owner/repo"]
    assert job.qa_density == "high"


def test_job_store_create_new_fields_default():
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0)
    assert job.burn_captions is False
    assert job.quiz is False
    assert job.urls == []
    assert job.github == []
    assert job.qa_density == "normal"
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_server_jobs.py::test_job_store_create_accepts_new_fields tests/test_server_jobs.py::test_job_store_create_new_fields_default -v
```

Expected: FAIL — `store.create()` does not accept new kwargs.

- [ ] **Step 3: Add new fields to `CreateJobRequest` in `server/models.py`**

Replace the entire file content:

```python
# server/models.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class CreateJobRequest(BaseModel):
    topic: str
    effort: Literal["low", "medium", "high"] = "medium"
    audience: Literal["beginner", "intermediate", "expert"] = "intermediate"
    tone: Literal["casual", "formal", "socratic"] = "casual"
    theme: Literal["chalkboard", "light", "colorful"] = "chalkboard"
    template: str | None = None
    speed: float = 1.0
    burn_captions: bool = False
    quiz: bool = False
    urls: list[str] = []
    github: list[str] = []
    qa_density: Literal["zero", "normal", "high"] = "normal"


class JobResponse(BaseModel):
    id: str
    status: Literal["pending", "running", "completed", "failed"]
    topic: str
    events: list[dict]
    error: str | None
    output_files: list[str]
```

- [ ] **Step 4: Add new fields to `Job` dataclass and `JobStore.create` in `server/jobs.py`**

In the `Job` dataclass, add these fields after `speed: float` (before `status`):

```python
    burn_captions: bool = False
    quiz: bool = False
    urls: list[str] = field(default_factory=list)
    github: list[str] = field(default_factory=list)
    qa_density: str = "normal"
```

Update `JobStore.create` signature and body:

```python
    def create(self, topic: str, effort: str, audience: str, tone: str,
               theme: str, template: str | None, speed: float,
               burn_captions: bool = False, quiz: bool = False,
               urls: list[str] | None = None, github: list[str] | None = None,
               qa_density: str = "normal") -> Job:
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, topic=topic, effort=effort, audience=audience,
                  tone=tone, theme=theme, template=template, speed=speed,
                  burn_captions=burn_captions, quiz=quiz,
                  urls=urls or [], github=github or [],
                  qa_density=qa_density)
        self._jobs[job_id] = job
        return job
```

- [ ] **Step 5: Update `server/routes.py` to pass new fields**

Replace the `create_job` handler:

```python
    @router.post("/jobs", status_code=202, response_model=JobResponse)
    async def create_job(req: CreateJobRequest):
        job = store.create(
            topic=req.topic, effort=req.effort, audience=req.audience,
            tone=req.tone, theme=req.theme, template=req.template, speed=req.speed,
            burn_captions=req.burn_captions, quiz=req.quiz,
            urls=req.urls, github=req.github, qa_density=req.qa_density,
        )
        output_dir = Path(OUTPUT_DIR).resolve()
        asyncio.create_task(run_job(job, output_dir))
        return _job_to_response(job)
```

- [ ] **Step 6: Run failing tests — should now pass**

```
pytest tests/test_server_jobs.py::test_job_store_create_accepts_new_fields tests/test_server_jobs.py::test_job_store_create_new_fields_default -v
```

Expected: PASS.

- [ ] **Step 7: Run full suite**

```
pytest --tb=short -q
```

Expected: all passing.

- [ ] **Step 8: Commit**

```bash
git add server/models.py server/jobs.py server/routes.py tests/test_server_jobs.py
git commit -m "feat: add burn_captions, quiz, urls, github, qa_density fields to Job and API model"
```

---

### Task 2: Wire new features into run_job

**Files:**
- Modify: `server/jobs.py` — `_do_render` and `run_job`
- Test: `tests/test_server_jobs.py`

The complete updated `server/jobs.py` after this task will look like this (write incrementally, test first):

New imports needed at top of `server/jobs.py`:
```python
from pipeline.context import fetch_url_blocks
from main import (
    run as _pipeline_run,
    _render, RenderFailed,
    _run_qa_loop, _generate_quiz, _github_to_raw_url,
)
```

The re-export line becomes:
```python
run = _pipeline_run
```

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_server_jobs.py`:

```python
@pytest.mark.asyncio
async def test_run_job_passes_burn_captions_to_render(tmp_path):
    """burn_captions=True on the job must be forwarded to _do_render."""
    import json
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       burn_captions=True)

    render_calls = []

    async def fake_run(**kwargs):
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, burn_captions=False, **kwargs):
        render_calls.append({"burn_captions": burn_captions})
        return tmp_path / "final.mp4"

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render):
        await run_job(job, tmp_path)

    assert render_calls[0]["burn_captions"] is True


@pytest.mark.asyncio
async def test_run_job_calls_generate_quiz(tmp_path):
    """quiz=True must trigger _generate_quiz after render."""
    import json
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       quiz=True)

    quiz_calls = []

    async def fake_run(**kwargs):
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return tmp_path / "final.mp4"

    def fake_quiz(run_id):
        quiz_calls.append(run_id)

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render), \
         patch("server.jobs._generate_quiz", new=fake_quiz):
        await run_job(job, tmp_path)

    assert quiz_calls == [job.id]


@pytest.mark.asyncio
async def test_run_job_skips_qa_when_density_zero(tmp_path):
    """qa_density='zero' must not call _run_qa_loop."""
    import json
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       qa_density="zero")

    qa_calls = []

    async def fake_run(**kwargs):
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return tmp_path / "final.mp4"

    def fake_qa_loop(*args, **kwargs):
        qa_calls.append(True)

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render), \
         patch("server.jobs._run_qa_loop", new=fake_qa_loop):
        await run_job(job, tmp_path)

    assert qa_calls == []


@pytest.mark.asyncio
async def test_run_job_runs_qa_when_density_normal(tmp_path):
    """qa_density='normal' must call _run_qa_loop with the final mp4 path."""
    import json
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       qa_density="normal")

    qa_calls = []
    final_mp4_path = tmp_path / "final.mp4"

    async def fake_run(**kwargs):
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return final_mp4_path

    def fake_qa_loop(run_id, final_mp4, **kwargs):
        qa_calls.append({"run_id": run_id, "mp4": final_mp4})

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render), \
         patch("server.jobs._run_qa_loop", new=fake_qa_loop):
        await run_job(job, tmp_path)

    assert len(qa_calls) == 1
    assert qa_calls[0]["run_id"] == job.id
    assert qa_calls[0]["mp4"] == final_mp4_path


@pytest.mark.asyncio
async def test_run_job_fetches_url_context(tmp_path):
    """URLs on the job must be fetched and passed as context_blocks to run()."""
    import json
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       urls=["https://example.com/page"])

    run_kwargs = {}

    async def fake_run(**kwargs):
        run_kwargs.update(kwargs)
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return tmp_path / "final.mp4"

    def fake_fetch(url):
        return [{"type": "text", "text": f"content from {url}"}]

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render), \
         patch("server.jobs.fetch_url_blocks", new=fake_fetch):
        await run_job(job, tmp_path)

    assert run_kwargs.get("context_blocks") == [{"type": "text", "text": "content from https://example.com/page"}]


@pytest.mark.asyncio
async def test_run_job_fetches_github_context(tmp_path):
    """GitHub repos on the job must be resolved to raw URLs and fetched."""
    import json
    store = JobStore()
    job = store.create(topic="test", effort="low", audience="intermediate",
                       tone="casual", theme="chalkboard", template=None, speed=1.0,
                       github=["owner/repo"])

    run_kwargs = {}

    async def fake_run(**kwargs):
        run_kwargs.update(kwargs)
        run_dir = tmp_path / job.id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"run_id": job.id}))

    async def fake_render(run_id, **kwargs):
        return tmp_path / "final.mp4"

    def fake_fetch(url):
        return [{"type": "text", "text": f"readme from {url}"}]

    with patch("server.jobs.run", new=fake_run), \
         patch("server.jobs._do_render", new=fake_render), \
         patch("server.jobs.fetch_url_blocks", new=fake_fetch):
        await run_job(job, tmp_path)

    assert run_kwargs.get("context_blocks") is not None
    assert "raw.githubusercontent.com" in run_kwargs["context_blocks"][0]["text"]
```

- [ ] **Step 2: Run to verify they fail**

```
pytest tests/test_server_jobs.py -k "burn_captions or generate_quiz or density or url_context or github_context" -v
```

Expected: all FAIL.

- [ ] **Step 3: Update `_do_render` to accept and forward `burn_captions`**

Replace `_do_render` in `server/jobs.py`:

```python
async def _do_render(run_id: str, verbose: bool = False, burn_captions: bool = False) -> Path | None:
    """Run Docker render. Returns path to final.mp4 or None on failure."""
    try:
        final_mp4 = await asyncio.to_thread(_render, run_id, verbose, burn_captions)
        return final_mp4 if final_mp4.exists() else None
    except RenderFailed:
        return None
```

- [ ] **Step 4: Update the imports at the top of `server/jobs.py`**

Replace:
```python
from main import run as _pipeline_run
```

With:
```python
from pipeline.context import fetch_url_blocks
from main import (
    run as _pipeline_run,
    _render, RenderFailed,
    _run_qa_loop, _generate_quiz, _github_to_raw_url,
)
```

And keep the re-export line unchanged:
```python
run = _pipeline_run
```

Also remove the lazy imports inside `_do_render` since `_render` and `RenderFailed` are now imported at the top.

- [ ] **Step 5: Replace `run_job` with the fully-wired version**

```python
async def run_job(job: Job, output_dir: Path) -> None:
    """Execute the full pipeline + render for a job. Updates job.status in place."""
    job.status = "running"

    def _on_progress(event: dict) -> None:
        for node_name, updates in event.items():
            if node_name == "__end__":
                continue
            job.append_event({"node": node_name, "updates": updates})

    try:
        # Build context_blocks from URLs and GitHub repos
        context_blocks = None
        for url in job.urls:
            blocks = await asyncio.to_thread(fetch_url_blocks, url)
            context_blocks = (context_blocks or []) + blocks
        for repo in job.github:
            raw_url = _github_to_raw_url(repo)
            blocks = await asyncio.to_thread(fetch_url_blocks, raw_url)
            context_blocks = (context_blocks or []) + blocks

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
        # If it's absent, the pipeline ended before completing.
        if not (output_dir / job.id / "manifest.json").exists():
            raise RuntimeError("pipeline did not complete — no output was written")

        final_mp4 = await _do_render(job.id, burn_captions=job.burn_captions)
        if final_mp4 is None:
            job.error = "render failed; pipeline output preserved"

        # Visual QA (runs in a thread — _run_qa_loop uses asyncio.run internally)
        if final_mp4 is not None and job.qa_density != "zero":
            await asyncio.to_thread(
                _run_qa_loop,
                job.id, final_mp4,
                theme=job.theme, audience=job.audience,
                tone=job.tone, effort_level=job.effort,
                context_blocks=context_blocks,
                qa_density=job.qa_density,
            )

        # Quiz generation
        if job.quiz:
            await asyncio.to_thread(_generate_quiz, job.id)

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
```

- [ ] **Step 6: Run the new tests**

```
pytest tests/test_server_jobs.py -k "burn_captions or generate_quiz or density or url_context or github_context" -v
```

Expected: all PASS.

- [ ] **Step 7: Run full suite**

```
pytest --tb=short -q
```

Expected: all passing. If `test_run_job_sets_status_completed` fails because the fake_render signature no longer matches, update its `fake_render` to `async def fake_render(run_id, burn_captions=False, **kwargs)`.

- [ ] **Step 8: Commit**

```bash
git add server/jobs.py tests/test_server_jobs.py
git commit -m "feat: wire burn_captions, quiz, url/github context, and visual QA into run_job"
```

---

### Task 3: Frontend — Advanced section and new controls

**Files:**
- Modify: `server/static/index.html`

The UI reorganisation:
- **Always visible**: Topic, Effort, Audience
- **Advanced section** (collapsible `<details>`): Tone, Theme, Template, Speed, QA Density, Burn Captions (checkbox), Quiz (checkbox), URLs (repeatable), GitHub (repeatable)

No backend changes needed — the API already accepts all fields after Tasks 1 and 2.

- [ ] **Step 1: No test to write — verify the server test still passes after edits**

The only server test for the frontend is `test_static_index_served` which just checks the page returns 200 with text/html. It will continue to pass as long as `index.html` exists and is valid HTML.

- [ ] **Step 2: Add the Advanced section CSS to `<style>` in `index.html`**

Add these rules inside the `<style>` block, after the existing `.btn-submit` rules:

```css
    /* Advanced section */
    details.advanced {
      margin-top: 1.25rem;
    }

    details.advanced summary {
      font-family: 'DM Mono', monospace;
      font-size: 0.72rem;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      cursor: pointer;
      list-style: none;
      display: flex;
      align-items: center;
      gap: 0.4rem;
      user-select: none;
      padding: 0.2rem 0;
    }

    details.advanced summary::-webkit-details-marker { display: none; }

    details.advanced summary::before {
      content: '▶';
      font-size: 0.55rem;
      transition: transform 0.15s;
      display: inline-block;
    }

    details[open].advanced summary::before {
      transform: rotate(90deg);
    }

    details.advanced .advanced-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 1rem 1.25rem;
      margin-top: 1rem;
    }

    details.advanced .advanced-grid .form-group {
      margin-bottom: 0;
    }

    .checkbox-group {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding-top: 0.3rem;
    }

    .checkbox-group input[type="checkbox"] {
      width: 15px;
      height: 15px;
      accent-color: var(--accent);
      cursor: pointer;
      flex-shrink: 0;
    }

    .checkbox-group span {
      font-family: 'DM Mono', monospace;
      font-size: 0.8rem;
      color: var(--text);
    }

    /* Repeatable input rows */
    .repeatable-list {
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
      margin-bottom: 0.4rem;
    }

    .repeatable-row {
      display: flex;
      gap: 0.4rem;
      align-items: center;
    }

    .repeatable-row input {
      flex: 1;
    }

    .btn-remove {
      font-family: 'DM Mono', monospace;
      font-size: 0.75rem;
      background: none;
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: 4px;
      padding: 0.3rem 0.5rem;
      cursor: pointer;
      flex-shrink: 0;
      line-height: 1;
    }

    .btn-remove:hover { border-color: #c0392b; color: #c0392b; }

    .btn-add {
      font-family: 'DM Mono', monospace;
      font-size: 0.7rem;
      background: none;
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: 4px;
      padding: 0.3rem 0.65rem;
      cursor: pointer;
      align-self: flex-start;
    }

    .btn-add:hover { border-color: var(--accent); color: var(--accent); }

    .repeatable-full {
      grid-column: 1 / -1;
    }
```

- [ ] **Step 3: Replace the form HTML inside `<div class="form-card">`**

Replace the entire `<form id="job-form">` element with:

```html
      <form id="job-form">
        <div class="form-group">
          <label for="topic">Topic</label>
          <textarea id="topic" name="topic" placeholder="e.g. How merge sort works" required></textarea>
        </div>

        <div class="form-grid">
          <div class="form-group">
            <label for="effort">Effort</label>
            <select id="effort" name="effort">
              <option value="low">low</option>
              <option value="medium" selected>medium</option>
              <option value="high">high</option>
            </select>
          </div>

          <div class="form-group">
            <label for="audience">Audience</label>
            <select id="audience" name="audience">
              <option value="beginner">beginner</option>
              <option value="intermediate" selected>intermediate</option>
              <option value="expert">expert</option>
            </select>
          </div>
        </div>

        <details class="advanced">
          <summary>Advanced options</summary>
          <div class="advanced-grid">

            <div class="form-group">
              <label for="tone">Tone</label>
              <select id="tone" name="tone">
                <option value="casual" selected>casual</option>
                <option value="formal">formal</option>
                <option value="socratic">socratic</option>
              </select>
            </div>

            <div class="form-group">
              <label for="theme">Theme</label>
              <select id="theme" name="theme">
                <option value="chalkboard" selected>chalkboard</option>
                <option value="light">light</option>
                <option value="colorful">colorful</option>
              </select>
            </div>

            <div class="form-group">
              <label for="template">Template</label>
              <select id="template" name="template">
                <option value="">none</option>
                <option value="algorithm">algorithm</option>
                <option value="code">code</option>
                <option value="compare">compare</option>
              </select>
            </div>

            <div class="form-group">
              <label for="speed">Speed</label>
              <input type="text" id="speed" name="speed" value="1.0" />
            </div>

            <div class="form-group">
              <label for="qa-density">Visual QA</label>
              <select id="qa-density" name="qa_density">
                <option value="zero">off</option>
                <option value="normal" selected>normal</option>
                <option value="high">high</option>
              </select>
            </div>

            <div class="form-group">
              <label>&nbsp;</label>
              <div class="checkbox-group">
                <input type="checkbox" id="burn-captions" name="burn_captions" />
                <span>Burn captions</span>
              </div>
              <div class="checkbox-group" style="margin-top: 0.5rem;">
                <input type="checkbox" id="quiz" name="quiz" />
                <span>Generate quiz</span>
              </div>
            </div>

            <div class="form-group repeatable-full">
              <label>URLs (source material)</label>
              <div class="repeatable-list" id="url-list"></div>
              <button type="button" class="btn-add" id="add-url">+ Add URL</button>
            </div>

            <div class="form-group repeatable-full">
              <label>GitHub repos (owner/repo)</label>
              <div class="repeatable-list" id="github-list"></div>
              <button type="button" class="btn-add" id="add-github">+ Add repo</button>
            </div>

          </div>
        </details>

        <button type="submit" class="btn-submit" id="submit-btn">Generate Video</button>
      </form>
```

- [ ] **Step 4: Add JS helpers for repeatable inputs and update the submit handler**

After the existing `const DOWNLOADABLE = [...]` line, add:

```javascript
    function addRepeatableRow(listId, placeholder) {
      const list = document.getElementById(listId);
      const row = document.createElement('div');
      row.className = 'repeatable-row';
      row.innerHTML = `<input type="text" placeholder="${placeholder}" />
                       <button type="button" class="btn-remove">✕</button>`;
      row.querySelector('.btn-remove').addEventListener('click', () => row.remove());
      list.appendChild(row);
      row.querySelector('input').focus();
    }

    function getRepeatableValues(listId) {
      return Array.from(document.querySelectorAll(`#${listId} input`))
        .map(el => el.value.trim())
        .filter(Boolean);
    }

    document.getElementById('add-url').addEventListener('click', () =>
      addRepeatableRow('url-list', 'https://example.com/article'));

    document.getElementById('add-github').addEventListener('click', () =>
      addRepeatableRow('github-list', 'owner/repo'));
```

Then update the submit handler's `body` object (replace the existing const body = {...} block):

```javascript
      const body = {
        topic,
        effort:         document.getElementById('effort').value,
        audience:       document.getElementById('audience').value,
        tone:           document.getElementById('tone').value,
        theme:          document.getElementById('theme').value,
        template:       document.getElementById('template').value || null,
        speed:          parseFloat(document.getElementById('speed').value) || 1.0,
        burn_captions:  document.getElementById('burn-captions').checked,
        quiz:           document.getElementById('quiz').checked,
        qa_density:     document.getElementById('qa-density').value,
        urls:           getRepeatableValues('url-list'),
        github:         getRepeatableValues('github-list'),
      };
```

- [ ] **Step 5: Run the full test suite to confirm nothing broke**

```
pytest --tb=short -q
```

Expected: all passing (162+ tests).

- [ ] **Step 6: Manual smoke test**

```bash
python3 run_server.py
```

Open `http://localhost:8000`. Verify:
- Topic, Effort, Audience are always visible
- "Advanced options" is collapsed by default; clicking it expands smoothly
- All advanced controls are present with correct defaults
- Adding/removing URL and GitHub rows works
- Submitting a job with burn_captions=true sends the field (check browser Network tab)

- [ ] **Step 7: Commit**

```bash
git add server/static/index.html
git commit -m "feat: add Advanced section to UI with QA density, burn captions, quiz, URL and GitHub context"
```
