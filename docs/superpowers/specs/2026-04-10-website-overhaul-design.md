# Website Overhaul: Web-UI-First Documentation

**Date:** 2026-04-10
**Status:** Approved

## Summary

Restructure the docs website from a CLI-centric 3-page site to a web-UI-first 4-page site. Keep the existing visual identity (dark chalkboard theme, DM Serif Display / Lora / DM Mono typography, golden accent, chalk grain texture). Add screenshots of the web UI. Reorganize content so the web UI guide is the primary documentation path and CLI becomes a secondary reference.

## Goals

1. Get self-hosters up and running quickly (install, run server, open browser)
2. Show what the web UI looks like before people install (screenshots)
3. Provide documentation depth for power users (CLI, API)
4. Preserve the current visual identity and design quality
5. Keep the site as static HTML with no build tools

## Non-goals

- Embedded example videos (can be added later without restructuring)
- Interactive demos or live playground
- SaaS-specific landing page (future work)
- Build tooling (Astro, Hugo, etc.)

## Page Structure

### 1. Homepage (index.html)

Section order, top to bottom:

1. **Hero** - keep unchanged. "Turn any topic into a narrated animation" headline, pipeline flow pills, CTA buttons. One small copy tweak: hero-sub paragraph mentions the web UI alongside the pipeline description.
2. **Quick start** - simplified to 3 steps: clone/install, run server, open browser. The CLI one-liner stays as a secondary "or run from the terminal" note. Drop the "With context" block (belongs in the guide).
3. **Features grid** - keep the 2x2 numbered card layout. Reword cards to be web-UI-aware:
   - "Multi-agent pipeline" - unchanged
   - "Context injection" - mention file upload in the web UI, not just `--context` flags
   - "Visual QA pass" - unchanged
   - "Captions & chapters" - unchanged
4. **Screenshots** - new section. 2-3 images using the existing card/grid style (1px border, `var(--surface)` background). Candidates: job creation form, live progress view, video library. Short caption below each.
5. **Configurable options** - unchanged (audience, tone, template, theme, TTS, effort tag grid).
6. **Footer** - unchanged.

**Nav update:** Links become Guide, CLI, API, Quickstart, GitHub. The current "Guide" link points to the new web-UI-first guide. New "CLI" link points to cli.html.

### 2. Guide (guide.html) - Web UI First

Complete rewrite of the current CLI guide. This becomes the primary documentation page.

**Page hero:** "Guide" title. Subtitle: "How to use Chalkboard, from creating your first video to customizing every detail."

**Sidebar + sections:**

1. **Quick start** - same 3-step install from homepage with more detail (first-run Docker build note, `.env` setup, prerequisites)
2. **Creating a video** - walk through the web UI form: topic, effort, audience, tone. Describe the advanced options panel (template, speed, theme, URLs, GitHub, file uploads, QA density, quiz, burn captions). Screenshot of the form.
3. **Live progress** - SSE-driven stage-by-stage progress view. What each pipeline stage means. Screenshot of a job mid-progress.
4. **Video library** - the `/library` grid, searching, video detail page. Screenshot of the library.
5. **File uploads** - supported file types, size limits, drag-and-drop behavior. Web UI equivalent of "Context injection" but focused on the upload zone.
6. **Templates** - keep the 5-card grid (algorithm, code, compare, howto, timeline) with descriptions. No CLI flags shown here.
7. **Effort levels** - keep the table (low/medium/high, fact-check depth, web search behavior).
8. **TTS backends** - keep the table (kokoro/openai/elevenlabs, quality, cost, requirements).
9. **Themes** - brief section covering chalkboard/light/colorful with descriptions.

**Principle:** Shared concepts (templates, effort, TTS, themes) live here as the canonical reference. The CLI page cross-links to them.

### 3. CLI Reference (cli.html) - New Page

New page for terminal power users. Receives most of the current guide.html content.

**Page hero:** "CLI Reference" title. Subtitle: "Run Chalkboard from the terminal for scripting, automation, and advanced workflows."

**Sidebar + sections:**

1. **Basic usage** - the one-liner `python main.py --topic "..." --effort medium`. Note linking to the guide for the easier web UI path.
2. **CLI flags** - the four existing reference tables (Core, Context, Output, Render) moved verbatim.
3. **Context injection** - local files, URLs, GitHub repos, `--context-ignore`, token reporting, resuming with context. Current guide content, unchanged.
4. **Environment variables** - current env var table, unchanged.
5. **Resuming runs** - `--run-id`, preview-to-full workflow, unchanged.

**Cross-links:** Templates, effort, and TTS are NOT duplicated. Flag descriptions link to the canonical sections in guide.html (e.g., the `--template` row links to `guide.html#templates`).

### 4. API Reference (api.html) - Minor Updates

Stays almost identical. Two changes:

1. Remove CLI-centric language from copy where it appears
2. Trim the "Web UI" section at the bottom to a one-liner linking to guide.html (there is now an entire page for that)

## Screenshots

Screenshots need to be captured from the running web UI and saved to `docs/`. Candidates:

- `screenshot-form.png` - the job creation form with topic filled in, advanced options expanded
- `screenshot-progress.png` - a job mid-pipeline with some stages completed
- `screenshot-library.png` - the video library grid with a few completed videos

Screenshots should be cropped to the browser content area (no browser chrome). They will be displayed in the existing card grid style with 1px borders and `var(--surface)` background, matching the site's visual language.

## Styling

No changes to the visual identity:
- Color scheme: `--bg: #111110`, `--surface: #1a1918`, `--accent: #c8b97a`, `--chalk: #f0ebe0`, `--muted: #7a7570`
- Typography: DM Serif Display (headings), Lora (body), DM Mono (code/labels)
- Chalk grain texture overlay
- 1px border grid system
- fadeUp entrance animations on homepage

New CSS needed:
- Screenshot card styles (image + caption in a grid, consistent with existing card patterns)
- Any adjustments to `docs.css` for the new cli.html page (likely zero, since it shares the same layout as guide/api)

## Copy Guidelines

- No em dashes in any normal text. Use commas, periods, or restructure sentences.
- Web UI is the default path. CLI is "advanced" or "for scripting/automation."
- Keep the terse, monospace-label style established by the current site.

## File Changes

| File | Action |
|------|--------|
| `docs/index.html` | Edit: reorder sections, add screenshots section, update nav, tweak quickstart and feature copy |
| `docs/guide.html` | Rewrite: web-UI-first content, new sidebar, new sections |
| `docs/cli.html` | Create: new page, receives current guide.html CLI content |
| `docs/api.html` | Edit: trim Web UI section, minor copy tweaks |
| `docs/docs.css` | Edit: add screenshot card styles |
| `docs/screenshot-*.png` | Create: 2-3 screenshots of the web UI (captured separately) |

## Future Work (Out of Scope)

- Embedded example videos on homepage
- Interactive pipeline visualization
- Theme color swatches in the guide
- SaaS-specific landing page or pricing
- "Chalkboard explaining Chalkboard" meta-demo video
