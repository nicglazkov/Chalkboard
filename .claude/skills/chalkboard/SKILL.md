---
name: chalkboard
description: Use when the user says "use chalkboard", "make a chalkboard video", or asks for a narrated explainer video, an animated explanation, a Manim animation, or a 3Blue1Brown-style visual walkthrough of a topic, paper, codebase, or set of notes. Runs this repo's pipeline (python main.py) to produce output/<run_id>/final.mp4, and covers choosing options, resuming, and debugging failed runs.
---

# Chalkboard: generate a narrated explainer video

Chalkboard turns a topic (plus optional source material) into a narrated Manim video: script, fact check, scene code on the built-in design system, validation, TTS, render, visual QA. Architecture details live in `CLAUDE.md`; setup in `README.md`.

## Before the first run

From the repo root, check once:

- `.env` exists with `ANTHROPIC_API_KEY` and a working `TTS_BACKEND` (`openai` needs `OPENAI_API_KEY`; `kokoro` needs PyTorch + `espeak-ng`). If `.env` is missing, tell the user to copy `.env.example` and fill in keys; never invent keys.
- A render path exists: either `manim` importable plus `latex`, `dvisvgm` and `ffmpeg` on `PATH` (local, preferred), or Docker. `RENDER_BACKEND=auto` picks local when it can. `--no-render` skips rendering entirely.

## Run it

Runs take several minutes (each Claude stage, then TTS, render and visual QA), so start it **in the background** and check on it, rather than blocking:

```bash
python main.py --topic "how B-trees keep themselves balanced" --yes [options] > chalkboard.log 2>&1
```

Always pass `--yes`. It skips confirmations and makes the run non-interactive: nothing waits on stdin, exhausted retries abort instead of prompting, and a scene with only layout warnings after its last retry is rendered anyway.

The first output line contains `run: <run_id>`; note it. When the run ends the log shows `Done → .../final.mp4`.

Several runs can go at once (each has its own run id; the checkpoint DB is shared safely). When driving a run over ssh (or `wsl.exe`), detach it and follow the log instead of streaming it through the session: `setsid -f bash -c 'python main.py ... --yes > logs/<id>.log 2>&1 < /dev/null; echo $? > logs/<id>.rc'`, then poll the log and wait for the `.rc` file (or `tail -f --pid`). Never pipe `main.py` through `tee`/`grep` held open by a remote session: if that session's relay stalls, the client waits forever even though the video is done.

## Choosing options

Pick from the request; leave the rest at defaults.

| Want | Flag |
|------|------|
| Depth of fact checking; web research before scripting | `--effort low|medium|high` (`high` adds a research step) |
| Audience | `--audience beginner|intermediate|expert` |
| Narration style | `--tone casual|formal|socratic` |
| Look | `--theme chalkboard|light|colorful` |
| Resolution | `--quality low|medium|high|4k` (480p15 / 720p30 / 1080p60 / 2160p60). Set it on the first run; it is fixed once the pipeline finishes |
| Voice | `--narrator aria|milo` (ElevenLabs `eleven_v4`, needs `ELEVENLABS_API_KEY`), `kokoro` (free, local), `alloy` (OpenAI). Default: `NARRATOR` in `.env`, else `TTS_BACKEND` |
| Scene structure | `--template algorithm` (array step-through), `code` (code walkthrough), `compare` (A vs B), `derivation` (step-by-step math), `howto` (numbered steps), `timeline` (dated events). Omit to let the agent decide |
| Source material | `--context PATH` (files/dirs, repeatable), `--context-ignore GLOB`, `--url URL`, `--github owner/repo` |
| Pace | `--speed 1.15` |
| Quick look first | `--preview` (480p `preview.mp4`, then full render via `--run-id`) |
| Extras | `--quiz` (quiz.json), `--burn-captions`, `--qa-density zero|normal|high` |

Math topics: `--template derivation` gives aligned, step-by-step equations. Code topics: `--template code` with `--context` pointing at the file.

## Where output lands

`output/<run_id>/` (or `$OUTPUT_DIR/<run_id>/`):

- `final.mp4`: the video (captions and chapter markers embedded); `preview.mp4` with `--preview`
- `captions.srt`, `chapters.txt`, `thumb.jpg`, `quiz.json` (with `--quiz`)
- `script.txt`, `segments.json` (per-segment timing), `scene.py` (generated Manim code), `manifest.json` (settings)
- `layout_report.json` (last layout dry-run), `qa_frames/` (frames reviewed by visual QA), `media/` (raw Manim output)

Report the full path of `final.mp4` to the user.

## Resume

Every pipeline stage is checkpointed in `pipeline_state.db`. To continue a run that crashed or was stopped:

```bash
python main.py --topic "<same topic>" --run-id <run_id> --yes [--context ... again]
```

It continues from the last completed stage, or goes straight to rendering if the pipeline already finished. `--context`, `--url` and `--github` are not checkpointed: pass them again.

To re-render after hand-editing `output/<run_id>/scene.py`: delete `output/<run_id>/final.mp4` and `output/<run_id>/media/`, then run the resume command above.

A run whose pipeline ended in escalation (retries exhausted, status failed) is finished as far as the checkpoint is concerned; resuming it will not retry. Start a new run instead, perhaps with a `--template` or a narrower topic.

## Which version is running

`curl <base>/version` on a server (e.g. `curl -s http://127.0.0.1:8000/version`) returns the version, git commit (`dirty: true` = uncommitted changes), uptime, runtime, config and latest changelog entry. Locally: `python main.py --version`. Each run's `manifest.json` and `run_stats.json` record `chalkboard_version` and `git_commit`. If you change this repo, bump `VERSION` (see `CLAUDE.md`, Versioning).

## Debugging

1. **Read the log.** Each node prints `[node] ... → status`; retries print `[label] failed (...) retrying`; the render prints `[render] animation N/M`; QA prints `[qa] issues found:` with each issue.
2. **Layout problems:** read `output/<run_id>/layout_report.json` (`passed`, `violations` with `type` such as `off_screen`, `overlap`, `zone_collision`, `label_on_curve`, `timing_overrun`, and a per-segment `description`).
3. **Scene crashes:** the layout feedback includes the traceback tail. Reproduce locally (local backend) with `PYTHONPATH=docker CHALKBOARD_REPORT_DIR=output/<run_id> python -m manim render -ql output/<run_id>/scene.py ChalkboardScene`. Known Manim 0.21.0 pitfalls are listed in `CLAUDE.md`.
4. **Render failures:** rerun with `--verbose` (not combinable with `--preview`) to stream Manim output.
5. **API errors:** auth, bad request and out-of-credit errors fail immediately; tell the user rather than retrying in a loop.

Do not modify pipeline code to get one video through; report recurring failure patterns so they can be fixed in the prompt or the AST guards.
