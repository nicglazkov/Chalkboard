# Web UI data contract (Pro UI, 2026-10)

Rule zero: **the UI never shows a value it cannot back with a real source.** Every field below
is either measured from the pipeline/OS/vendor at request time (with a timestamp), computed from
files the pipeline wrote, or `null`. `null` renders as "Unknown" (or "Not recorded" for old runs),
never as a guess, a placeholder number, or a default. Status checks that cannot be verified
right now return `state: "unknown"`. No animation may imply progress that did not happen.

## Live job events (`GET /api/jobs/{id}/events`, SSE; also in `GET /api/jobs/{id}.events`)
Every event carries `ts` (server ISO time). Existing pipeline node events stay as they are. New:

- `{"node": "peek", "updates": {"stage": "script"|"fact_check"|"scene_code", "text": "<full text so far>", "done": bool}}`
  Text is Claude's output **as it streams** (throttled to at most ~4 events/s; final event `done: true`).
  For structured-output calls the server extracts the growing value of the relevant JSON string field
  (`script`, `feedback`, `manim_code`) from the partial stream. Never synthesized, never replayed from a file.
- `{"node": "usage", "updates": {"agent": "...", "model": "...", "input_tokens": int, "output_tokens": int,
   "web_searches": int, "cost_usd": float|null}}` one per Claude call, from `response.usage`.
   `cost_usd` uses the price table in `pipeline/pricing.py`; unknown model => `null`.
- `{"node": "render", "updates": {"status": "running", "segment": int|null, "segments": int|null,
   "animation": int|null, "animations": int|null}}` parsed live from the renderer's output
   (Manim "Animation N" lines and a `CB_SEGMENT n` line ChalkboardSceneBase prints at each segment start).
   Also `{"status": "done"|"failed"}` at the end.
- `{"node": "tts", "updates": {"status": "running"|"done"|"failed", "segments_done": int, "segments": int, "chars": int}}`

## Job resource (`GET /api/jobs/{id}`)
Adds `totals: {"input_tokens", "output_tokens", "web_searches", "cost_usd"|null, "tts_chars"|null}`
summed from usage events (cost `null` if any call had an unknown price), and the job settings.

## Per-run record (`output/<run_id>/run_stats.json`, written by CLI and server runs)
`{"started_at", "finished_at", "stage_seconds": {node: secs}, "input_tokens", "output_tokens",
"web_searches", "cost_usd"|null, "tts_chars"|null, "narrator", "quality", "effort", "result": "done"|"failed"}`.
Older runs have no file; anything derived from it is then `null`.

## Timeline (`GET /api/jobs/{id}/timeline`, `GET /api/library/{run_id}/timeline`)
`{"duration_s": float|null, "segments": [{"index", "start_s", "duration_s", "label", "cues": [secs from segment start]}],
"waveform": [floats 0..1, ~400 buckets] | null, "rendered_segments": int|null}`
Built from `segments.json` (measured TTS durations and cue times) and `voiceover.wav` (real peaks).
Before TTS has run, durations are absent (`null`), not estimated.

## Quality (`GET /api/library/{run_id}/quality`)
`{"sync": {"cues": [{"segment", "cue", "lag_s"}], "median_lag_s", "worst_lag_s", "source": "render"}|null,
"layout": {"passed": bool, "violations": [...]}|null, "visual_qa": {"passed": bool, "issues": [...], "checked_at"}|null}`
From the real render's `cue_log`/`layout_report.json` and a persisted `qa_report.json`. Missing => `null`.

## Stats (`GET /api/stats`)
`{"videos_total", "videos_this_week", "median_sync_lag_s"|null, "sync_runs": int, "qa_pass_rate"|null,
"qa_runs": int, "median_run_seconds"|null, "timed_runs": int, "computed_at"}` from library + run records only.

## Status (`GET /api/status`)
`{"checked_at", "overall": "ok"|"degraded"|"down"|"unknown", "checks": [{"id", "name", "detail", "state": "ok"|"warn"|"down"|"unknown", "summary", "evidence", "checked_at"}]}`
Each check actually probes something (cached <= 60 s) and `evidence` says what was probed. Examples:
Claude (models endpoint reachable with our key), ElevenLabs (a no-cost authenticated endpoint; plan usage
only if the key can read it, else `null`), Kokoro (model importable/loaded, CUDA present), renderer
(manim + latex + dvisvgm + ffmpeg found and versions), GPU (nvidia-smi or torch), disk (free bytes for
output/), jobs (running/queued counts). Anything that cannot be probed without spending money or that
errors ambiguously => `unknown` with the reason.

## Voices (`GET /api/voices`, `GET /api/voices/{id}/sample`)
`[{"id", "label", "tagline", "backend", "model", "available": bool|null, "sample_url": str|null}]`.
Samples are real TTS output of one fixed sentence, generated on first request and cached under
`output/_voice_samples/`; failure => 503 with the reason, never a stand-in file.

## Estimate (`GET /api/estimate?effort=&quality=&narrator=&research=`)
`{"based_on_runs": int, "median_seconds"|null, "p25_seconds"|null, "p75_seconds"|null, "median_cost_usd"|null}`
from finished runs with matching settings (fall back to same quality only); fewer than 3 runs => nulls.

## Backend notes (implemented 2026-10-02; additive unless marked CHANGED)
Fields beyond the shapes above are extra and safe to ignore.

**Events.** SSE now fans out to every subscriber and first replays the stored events
(`?replay=0` skips the replay). `job.events` keeps only the latest `peek` per stage (each carries
the full text); the SSE stream delivers every one. Between `peek`s with growing text there can be a
long silence while Claude thinks; a retried call starts its text from empty again.
`usage` may add `cache_tokens` (then `cost_usd` is `null`: no cache rates assumed). Calls that fail
before returning a response are not counted (their usage is never reported).
`render`: `segment` = 0-based index of the segment being rendered (`CB_SEGMENT`), `segments` =
`len(segments.json)`, `animation` = Manim's current animation number (1-based); `animations` is
always `null` (the total is not known until the render ends: loops, templates and waits add
animations). The first `render` event of a job has all four `null`. QA re-renders emit more
`render` running events inside the `visual_qa` phase. `tts.chars` = characters of narration text
synthesized so far.

**Job resource** adds `totals.claude_calls`, `audience`, `tone`, `theme`, `speed`, `started_at`,
`finished_at`. With no Claude call yet, tokens are 0 and `cost_usd` 0 (nothing spent).

**run_stats.json** also has `run_id`, `total_seconds`, `claude_calls`, `research` (bool; `null` on
a resumed run), `source` ("cli"|"server"), `resumed` (CLI `--run-id`: stats cover that invocation
only; if the run already has a record, the resumed one is appended to its `later_invocations`
instead of replacing it; resumed records are excluded from estimates and timings). `stage_seconds` keys are graph node names
plus `render`, `visual_qa`, `quiz`.

**Timeline**: `label` = segment text (first ~80 chars). `waveform` peaks are normalized to the
loudest peak in the file (1.0 = loudest moment); cached as `waveform.json` in the run dir.
`rendered_segments` = all segments once `final.mp4` exists; for a live job, the index of the
segment being rendered (segments before it are done); `null` when unknown.

**Quality**: `sync` only from a `layout_report.json` written by a real render (`mode: "render"`;
older reports are attributed by file times, else `sync` is `null`). `lag_s` = renderer time when
the cued animation started minus the word's TTS time (>= 0; the scene waits for early cues), not a
pixel measurement. Cue entries add `spoken_at_s`, `visual_at_s`; `sync.evidence` explains the
source. `layout` adds `source` ("render"|"dry_run"|null) and `evidence`. `visual_qa` adds `attempts`.
`GET /api/library/{run_id}/stats` returns `{"run_stats": {...}|null}`.

**Stats**: `videos_this_week` = rolling 7 days by `created_at`; adds `sync_cues` (cue count behind
the median). `median_run_seconds` uses successful, non-resumed runs with run_stats only.

**Status**: each check has `detail` (object). Overall: `down` if claude/renderer/disk is down,
`degraded` if any check is down/warn, `unknown` if claude/renderer/disk is unknown, else `ok`.
Extra check `claude_status` (status.claude.com JSON API, API component). OpenAI credit cannot be
read with an API key: `unknown` until a real synthesis (voice sample) has been attempted, then
`ok`/`down` from that attempt. Response adds `cached` and `cache_ttl_s`.

**Voices** add `voice`, `availability_reason`, `sample_cached`. `available` is `true`/`false` only
on evidence (ElevenLabs: `GET /v1/voices/{id}`; OpenAI: last real synthesis) else `null`.
`sample_url` is `null` only when the narrator is known unavailable.

**Estimate** adds `basis` ("exact"|"same_quality"|null) and `cost_runs`; `median_cost_usd` needs
3 runs with a known cost. Only successful, non-resumed runs with run_stats count.

**Library list items** (`GET /api/library` `videos[]`, also `GET /api/library/{id}`): the stored
row (`run_id, topic, title, created_at, duration_sec, quality, thumb_path, script, effort,
audience, tone, theme, template, speed, status, narrator, notes`) plus `has_run_stats`, `has_final`,
`thumb_url`|null, `video_url`|null, `run_seconds`|null, `cost_usd`|null, `run_result`|null.
CHANGED: `duration_sec`, `quality`, `effort`, `audience`, `tone`, `theme`, `speed` are `null` when
the run's files do not record them (were defaulted to 0 / "medium" / ...). `duration_sec` = measured
narration length. `created_at` = job completion (server) or `final.mp4` mtime (indexed from disk).
`status` is always "completed" (only runs with `final.mp4` are indexed). `GET /api/library/{id}`
also includes `run_stats`.
`notes` = the user's free-text notes (`null` = none; never written by the pipeline, and
re-indexing a run keeps them). `GET /api/library?q=` also matches `notes`.

**Notes** (`PATCH /api/library/{run_id}`, body `{"notes": str|null}`): trimmed; empty or
whitespace-only is stored as `null`; more than 10,000 characters (after trimming), a non-string,
or a body without `notes` => 422; unknown run => 404. Returns
`{"run_id", "notes": str|null, "saved_at"}` where `saved_at` is the server's UTC time after the
write committed. The video page shows "Saved" only from this response, else "Not saved: <reason>".

**CHANGED `/api/meta`**: narrators have `configured` (API key set; Kokoro always) instead of
`available`; use `/api/voices` for availability. **`/api/claude-status`** now reads the status
page JSON API: `{"status": "operational"|"degraded"|"outage"|"unknown", "summary", "evidence",
"incidents": [{name, status, impact, url}], "checked_at", "url"}` (no keyword guessing).
