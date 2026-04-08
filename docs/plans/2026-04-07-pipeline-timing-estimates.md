# Plan: Per-step pipeline timing estimates

**Approach:** Hybrid — formulas for TTS + render (accurate from run 1), historical averages for LLM steps (kicks in after ≥3 runs per effort level). Conditioned on `effort_level`.

**Overnight prep:** After building, run 20–30 jobs at `effort=medium` via the API to seed the LLM step averages. A few `low` and `high` runs too. By morning, all steps will show real estimates.

---

## Step 1 — Instrument node timing in graph.py (~30 min)

Wrap each LangGraph node with `time.perf_counter()`. Add a thin decorator or wrapper that records wall-clock duration per node and attaches it to the LangGraph state or passes it via the `on_progress` callback.

Nodes to time:
- `research_agent` (effort=high only)
- `script_agent`
- `fact_validator`
- `manim_agent`
- `code_validator`
- `render_trigger` (TTS step, not Docker render)

The Docker render timing is already measured by `subprocess_with_timeout` in `main.py` — capture its return and emit it as a timing event too.

---

## Step 2 — Store timings in library.db (~1 hour)

Add a `run_timings` table to `SQLiteLibraryStore`:

```sql
CREATE TABLE IF NOT EXISTS run_timings (
    run_id TEXT NOT NULL,
    node   TEXT NOT NULL,
    effort TEXT NOT NULL,
    duration_sec REAL NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (run_id, node)
)
```

Add `record_timing(run_id, node, effort, duration_sec)` method to `LibraryStore` ABC and `SQLiteLibraryStore`.

Call it from `run_job()` in `server/jobs.py` — receive timings via the `on_progress` callback (add a `timing` key alongside `node` and `updates`) and write each one as it arrives.

Also record render duration (Docker step) in `_do_render()` / `run_job()` under node name `"render"`.

---

## Step 3 — Compute averages API endpoint (~30 min)

Add to `library_routes.py`:

```
GET /api/library/timings?effort=medium
```

Returns per-node averages over the last N runs for that effort level:

```json
{
  "effort": "medium",
  "sample_size": 14,
  "estimates": {
    "script_agent":   { "avg_sec": 28.4, "min_sec": 18.1, "max_sec": 44.2 },
    "manim_agent":    { "avg_sec": 94.1, "min_sec": 61.0, "max_sec": 148.7 },
    "fact_validator": { "avg_sec": 9.2,  "min_sec": 6.1,  "max_sec": 14.8 },
    "code_validator": { "avg_sec": 11.3, "min_sec": 7.0,  "max_sec": 18.2 },
    "render_trigger": { "avg_sec": 22.5, "min_sec": 15.0, "max_sec": 35.0 },
    "render":         { "avg_sec": 187.3,"min_sec": 90.0, "max_sec": 310.0 }
  }
}
```

Use last 50 runs max to keep estimates current (discard old outliers). Only include nodes with ≥3 data points — return `null` for nodes with insufficient data.

**Note:** TTS (`render_trigger`) and render can also be augmented with formula-based estimates as a prior, so they show something useful from run 1 even before historical data accumulates:
- TTS: `total_audio_duration_sec / speed × 1.4` (1.4× overhead factor — calibrate after first few runs)
- Render: existing `_compute_render_timeout()` formula ÷ quality_mult (it's already a good predictor, just divide back out)

---

## Step 4 — Frontend: show ETAs in the progress panel (~1–2 hours)

### On job submit
Fetch `/api/library/timings?effort={effort}` immediately after form submit (parallel with job creation). Store as `stepEstimates`.

### In `ensureStage(node)`
When a stage is first created, if `stepEstimates[node]` exists, show the estimate next to the stage name:

```
● Script agent          ~28s
```

Format: `< 1m` → show seconds; `≥ 1m` → show `~Xm Ys`.
Show `—` if no data yet (fewer than 3 historical runs).

### When a stage goes `done`
Replace the estimate with the actual elapsed time:
```
✓ Script agent          31s  (est. ~28s)
```

### Stage name element change
Currently `.stage-name` is a single `<span>`. Change to:
```html
<span class="stage-name">Script agent</span>
<span class="stage-est" id="stage-est-{node}">~28s</span>
```

Add CSS:
```css
.stage-est {
  font-family: 'DM Mono', monospace;
  font-size: 0.7rem;
  color: var(--muted);
  margin-left: auto;
}
.stage-item.done .stage-est { color: var(--accent); opacity: 0.7; }
```

---

## Overnight seeding script

After the feature is built, run this to seed the DB before sleeping:

```bash
for i in $(seq 1 25); do
  curl -s -X POST http://localhost:8071/api/jobs \
    -H "Content-Type: application/json" \
    -d "{\"topic\": \"$(python3 -c "import random; topics=['How binary search works','What is recursion','Explain TCP/IP','How hash tables work','What is Big O notation','How merge sort works','Explain the OSI model','What is a linked list','How DNS works','How public key cryptography works']; print(random.choice(topics))")\", \"effort\": \"medium\"}" \
    > /dev/null
  sleep 30  # space runs so Docker doesn't overlap
done
```

Run a few `low` and `high` effort runs too:
```bash
curl -s -X POST http://localhost:8071/api/jobs -H "Content-Type: application/json" \
  -d '{"topic": "How quicksort works", "effort": "low"}' > /dev/null
curl -s -X POST http://localhost:8071/api/jobs -H "Content-Type: application/json" \
  -d '{"topic": "Explain neural networks", "effort": "high"}' > /dev/null
```

---

## Files to change

| File | Change |
|------|--------|
| `pipeline/graph.py` | Timing wrapper around each node, emit via `on_progress` |
| `server/library.py` | `run_timings` table, `record_timing()` method |
| `server/library_routes.py` | `GET /api/library/timings` endpoint |
| `server/jobs.py` | Receive timing events, call `record_timing()` |
| `main.py` | Capture Docker render wall-clock time, emit as timing event |
| `server/static/index.html` | Fetch estimates on submit, display alongside stages, show actuals on completion |

## Tests to write

- `test_library_store.py`: `test_record_timing`, `test_get_estimates_filters_by_effort`, `test_get_estimates_requires_min_3_samples`, `test_get_estimates_caps_at_50_runs`
- `test_library_routes.py`: `test_timings_endpoint_returns_estimates`, `test_timings_endpoint_unknown_effort_returns_empty`
- `test_graph.py`: timing events emitted via `on_progress` with correct node names
