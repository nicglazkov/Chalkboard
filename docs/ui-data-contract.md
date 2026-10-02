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
- `{"node": "tts", "updates": {"status": "running"|"done", "segments_done": int, "segments": int, "chars": int}}`

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
