# Changelog

All notable changes to Chalkboard. Newest first; the format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/). `VERSION` is the single source of
truth: every change merged to main bumps it and adds an entry here
(`python scripts/bump_version.py patch "what changed"`). A running server
reports its version at `/version`.

## [0.4.0] - 2026-10-06

- Pacing: videos are paced like a person presenting. After each scene's last word the finished picture holds still and silent for at least 2 seconds (also at the end of the video) before the next scene's transition, which now runs after the hold. Real pauses are inserted at sentence ends, after questions, at a colon before a result, and at `[[beat]]` / `[[pause]]` markers the script agent places sparingly; cue times, captions, chapters and the timeline move with every pause, so word-level sync stays exact.
- Slower, clearer default delivery: the `relaxed` pace speaks at 0.94x through each voice's native speed control (ElevenLabs `voice_settings.speed`, Kokoro, OpenAI); `--speed` multiplies it.
- Presets `relaxed` (default), `normal`, `brisk` via `--pace`, the `pace` API and SDK field, the Pace chips in the web UI and `PACE` in `.env`; `SCENE_HOLD_S` and `PACE_SPEECH_SPEED` override single values. The manifest and `run_stats.json` record the pace.
- Script and scene prompts: one idea per short sentence, questions before answers, signposts and a recap; one main reveal per cue and a still hold. The layout check flags animations that run into the hold (`hold_busy`).

## [0.3.1] - 2026-10-06

- Versioning: `VERSION` file, `GET /version` and `GET /api/version` (version, git commit, server uptime, runtime, config, latest changelog entry, useful endpoints), `version` in `/api/meta` and `/api/status`, `python main.py --version`, and `chalkboard_version` + `git_commit` in every run's `manifest.json` and `run_stats.json`.
- Web UI: the sidebar footer shows the running version and commit, linking to `/version`.
- CI: pull requests must bump `VERSION` and add a CHANGELOG entry; each new version on main is tagged `v<VERSION>` automatically. `scripts/bump_version.py` does the bump.
- Library: free-text notes per video, editable on the video page (#57).
- Uploads: one shared file-type list for the UI and server, folder drops, auto-growing topic box (#55).

## [0.3.0] - 2026-10-03

- Self-hosted revival: local Manim rendering (Docker optional), Claude Opus 5.5 by default with per-agent overrides, a design system with six scene templates, word-level narration sync, Aria and Milo on ElevenLabs plus Kokoro and OpenAI, working layout checks, a new web UI built only on live data, `run_stats.json` run records, and CI on every push. See the [release notes](https://github.com/nicglazkov/Chalkboard/releases/tag/v0.3.0).

[0.4.0]: https://github.com/nicglazkov/Chalkboard/tree/v0.4.0
[0.3.1]: https://github.com/nicglazkov/Chalkboard/tree/v0.3.1
[0.3.0]: https://github.com/nicglazkov/Chalkboard/releases/tag/v0.3.0
