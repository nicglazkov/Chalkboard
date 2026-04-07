# Video Library — Design Spec

**Date:** 2026-04-07  
**Status:** Approved  

---

## Overview

A YouTube-style video library for browsing, searching, and watching all previously generated Chalkboard videos. Accessible at `/library` as a separate page from the generator. Built for localhost first, designed to migrate to GCP (Cloud SQL + Cloud Storage) with minimal code changes.

---

## User experience

### Navigation

A persistent top nav bar appears on all three pages (`index.html`, `library.html`, `video.html`):

```
◆ CHALKBOARD    Generate    Library
```

"Generate" links to `/` (existing `index.html`). "Library" links to `/library`. The active page is highlighted in gold.

### Library page (`/library`)

- **URL:** `/library` — served as a static HTML page, data fetched from `/api/library`
- **Header:** "Library" title (DM Serif Display), video count below it
- **Controls:** search input (left) + sort dropdown (right) — "Newest first / Oldest first / Longest / Shortest"
- **Grid:** 4-column dense grid, responsive — 3-col below 1200px, 2-col below 768px, 1-col below 480px
- **Pagination:** "Load more (N remaining)" button at bottom — appends next page to grid

**Each card shows:**
- Thumbnail (extracted frame or CSS fallback — see Thumbnails section)
- Duration pill (bottom-right of thumbnail, e.g. "3:12")
- Play icon overlay on hover
- Topic title (2-line clamp, Lora serif)
- Date (relative: "2 days ago") + quality badge (green=high, gold=medium, grey=low)

**Empty state:** shown when no videos exist yet, with a link to Generate.

**Search:** live-filters as the user types (debounced 300ms) — hits `GET /api/library?q=<term>`. Searches topic + full script text via SQLite `LIKE`. No results state shown when query matches nothing.

### Detail page (`/library/{run_id}`)

- **URL:** `/library/{run_id}` — static `video.html` that reads `run_id` from URL, fetches `/api/library/{run_id}`
- **Breadcrumb** in nav: `Library / <topic title>` (truncated)
- **Two-column layout:** main content left (1fr), sidebar right (340px fixed)

**Left column (top to bottom):**
1. Video player — native `<video>` element, 16:9 aspect ratio, `controls` attribute, serves `/api/jobs/{run_id}/files/final.mp4`
2. Title (DM Serif Display, 22px)
3. Meta row: duration · date · quality badge
4. **Downloads section** — buttons for each available file: `final.mp4`, `script.txt`, `captions.srt`, `quiz.json` (only shown if file exists in `output_files`)
5. **Script section** — full narration text in a scrollable box (max-height 220px, Lora serif)

**Right column (top to bottom):**
1. **Generation settings card** — compact 2-col grid showing: effort, audience, tone, theme, speed, template
2. **More videos** — 4 most recent other videos as compact thumbnail+title rows
3. **Re-generate with same settings** button — navigates to `/?prefill={run_id}`, pre-populating the form with identical settings

---

## Architecture

### New files

```
server/
  library.py          — VideoMeta dataclass, LibraryStore ABC, SQLiteLibraryStore
  library_routes.py   — /api/library endpoints
  static/
    library.html      — library grid page
    video.html        — video detail page
```

### Modified files

```
server/app.py           — mount library router; register startup backfill event
server/jobs.py          — call library_store.add_video() when job completes
server/static/index.html — add top nav (Generate active, Library link)
main.py                 — extract thumb.jpg after final.mp4 is created
```

---

## Data model

### `VideoMeta` dataclass (`server/library.py`)

```python
@dataclass
class VideoMeta:
    run_id: str
    topic: str
    duration_sec: float       # sum of segments[].actual_duration_sec
    quality: str              # low / medium / high
    created_at: str           # ISO8601 UTC
    thumb_path: str | None    # relative path "output/{run_id}/thumb.jpg"; None = CSS fallback
    script: str               # full narration text (for search)
    effort: str               # low / medium / high
    audience: str             # beginner / intermediate / expert
    tone: str                 # casual / formal / socratic
    theme: str                # chalkboard / light / colorful
    template: str | None      # algorithm / code / compare / None
    speed: float              # TTS speed multiplier
    status: str               # completed / failed
    output_files: list[str]   # filenames present on disk e.g. ["final.mp4","script.txt","quiz.json"]
```

### SQLite schema (`library.db`)

```sql
CREATE TABLE IF NOT EXISTS videos (
    run_id       TEXT PRIMARY KEY,
    topic        TEXT NOT NULL,
    duration_sec REAL DEFAULT 0,
    quality      TEXT DEFAULT 'medium',
    created_at   TEXT NOT NULL,
    thumb_path   TEXT,
    script       TEXT DEFAULT '',
    effort       TEXT DEFAULT 'medium',
    audience     TEXT DEFAULT 'intermediate',
    tone         TEXT DEFAULT 'casual',
    theme        TEXT DEFAULT 'chalkboard',
    template     TEXT,
    speed        REAL DEFAULT 1.0,
    status       TEXT DEFAULT 'completed'
);
```

---

## `LibraryStore` interface

```python
class LibraryStore(ABC):
    @abstractmethod
    async def add_video(self, meta: VideoMeta) -> None: ...

    @abstractmethod
    async def list_videos(
        self,
        query: str = '',
        limit: int = 50,
        offset: int = 0,
        sort: str = 'newest',     # newest | oldest | longest | shortest
    ) -> tuple[list[VideoMeta], int]: ...   # (results, total_count)

    @abstractmethod
    async def get_video(self, run_id: str) -> VideoMeta | None: ...

    @abstractmethod
    async def delete_video(self, run_id: str) -> None: ...
```

`SQLiteLibraryStore` implements this against `library.db` using `aiosqlite` (new dependency — add to `requirements.txt`).

**SaaS migration:** implement `PostgresLibraryStore` against Cloud SQL (PostgreSQL) using the same interface. `thumb_path` becomes a Cloud Storage URL. No other code changes needed.

---

## API endpoints

All endpoints live in `server/library_routes.py`, registered via `make_library_router(store)`.

### `GET /api/library`

Query params: `q` (search string), `limit` (default 50, max 100), `offset` (default 0), `sort` (newest|oldest|longest|shortest).

Response:
```json
{
  "videos": [ { ...VideoMeta fields... } ],
  "total": 142,
  "limit": 50,
  "offset": 0
}
```

Search: `WHERE topic LIKE '%q%' OR script LIKE '%q%'` — case-insensitive via `COLLATE NOCASE`.

### `GET /api/library/{run_id}`

Returns a single `VideoMeta` as JSON. 404 if not found.

### `DELETE /api/library/{run_id}`

Removes the entry from `library.db`. Does **not** delete files from disk (non-destructive by design — files can be re-indexed by the startup backfill). Returns 204.

---

## Thumbnails

### Extraction (new runs)

After `final.mp4` is created in `main.py` (post-ffmpeg merge) and in `run_job` in `server/jobs.py`:

```bash
ffmpeg -ss {duration_sec * 0.1:.1f} -i output/{run_id}/final.mp4 \
       -vframes 1 -q:v 3 -vf scale=640:360 \
       output/{run_id}/thumb.jpg
```

Samples at 10% of video duration — past the opening title card, into the first content frame. Stored as `thumb.jpg` in the run directory. Served via the existing `/api/jobs/{run_id}/files/thumb.jpg` route.

If extraction fails for any reason, `thumb_path` is set to `None` and the frontend renders a CSS fallback.

### CSS fallback (old/failed runs)

A styled card generated in the browser from `VideoMeta` fields:
- Background gradient keyed to `theme` (dark green = chalkboard, warm brown = light, dark blue = colorful)
- `◆ CHALKBOARD` logo top-left
- Topic text in Lora serif, centered
- Theme + quality bottom-left

---

## Startup backfill

`create_app()` in `server/app.py` registers a FastAPI `startup` event:

1. Open (or create) `library.db`
2. Scan all subdirectories of `output/`
3. For each directory containing both `manifest.json` and `final.mp4`:
   - Skip if `run_id` already in `library.db`
   - Read `manifest.json` → `run_id`, `topic`, `quality`
   - Read `segments.json` → sum `actual_duration_sec` for `duration_sec`
   - Read `script.txt` → `script`
   - `created_at` = filesystem mtime of `final.mp4` (ISO8601 UTC)
   - `thumb_path` = `output/{run_id}/thumb.jpg` if file exists, else `None`
   - `output_files` = list of filenames that exist on disk in `output/{run_id}/`
   - Remaining fields (`effort`, `audience`, `tone`, `theme`, `template`, `speed`) default to their schema defaults — manifest doesn't currently store them
   - Call `library_store.add_video(meta)`

This runs once at startup and is idempotent — safe to restart the server repeatedly. Handles all 180+ existing runs automatically on first launch.

**Future:** add `effort`, `audience`, `tone`, `theme`, `template`, `speed` to `manifest.json` in `render_trigger.py` so backfill and future entries are fully populated.

---

## Re-generate prefill

The "Re-generate with same settings" button on the detail page navigates to `/?prefill={run_id}`. `index.html` checks for this query param on load, fetches `/api/library/{run_id}`, and pre-fills the form fields (topic, effort, audience, tone, theme, template, speed). This reuses the existing form without any new endpoints.

---

## Frontend routing

All three pages are static HTML files served by FastAPI's `StaticFiles` mount. Client-side routing is handled by reading `window.location`:

- `library.html` — fetches `/api/library` on load; re-fetches on search/sort change
- `video.html` — reads `run_id` from the URL path (`/library/{run_id}`), fetches `/api/library/{run_id}`

FastAPI needs a catch-all route to serve `video.html` for any `/library/{run_id}` path. Add to `server/routes.py`:

```python
@router.get("/library/{run_id}")
async def serve_video_page():
    return FileResponse("server/static/video.html")

@router.get("/library")
async def serve_library_page():
    return FileResponse("server/static/library.html")
```

---

## SaaS migration path (GCP)

| Local | GCP SaaS |
|-------|----------|
| `SQLiteLibraryStore` | `PostgresLibraryStore` (Cloud SQL) |
| `output/{run_id}/thumb.jpg` | Cloud Storage object, `thumb_url` field |
| File serving via `/api/jobs/{id}/files/` | Redirect to signed GCS URL |
| Startup filesystem scan | Not needed — DB is source of truth |
| `library.db` file | Cloud SQL instance |

The `LibraryStore` interface is the only boundary that needs implementing. All frontend code, API route shapes, and `VideoMeta` field names stay the same.

---

## What's out of scope

- Video deletion from disk (delete only removes from index)
- Tagging or categorisation (future)
- Comments or annotations (future)
- User accounts / per-user libraries (SaaS phase)
- Transcript search with ranking / FTS5 (LIKE is sufficient at this scale)
- Video editing or re-rendering from the library UI
