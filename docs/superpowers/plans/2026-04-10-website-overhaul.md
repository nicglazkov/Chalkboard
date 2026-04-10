# Website Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the docs website from CLI-centric (3 pages) to web-UI-first (4 pages), adding screenshots and rewriting content to position the web UI as the primary interface.

**Architecture:** Static HTML site, no build tools. Four pages: homepage (index.html), guide (guide.html, web UI walkthrough + shared concepts), CLI reference (cli.html, new), API reference (api.html, minor edits). Shared stylesheet (docs.css). Screenshots are PNG files in the docs/ directory.

**Tech Stack:** HTML, CSS, vanilla JavaScript. No dependencies.

**Spec:** `docs/superpowers/specs/2026-04-10-website-overhaul-design.md`

---

### Task 1: Capture screenshots

Screenshots are needed by later tasks (homepage and guide both embed them). Capture these first.

**Files:**
- Create: `docs/screenshot-form.png`
- Create: `docs/screenshot-progress.png`
- Create: `docs/screenshot-library.png`

- [ ] **Step 1: Start the Chalkboard server**

```bash
cd /Users/nic/Documents/code/Chalkboard
python run_server.py &
```

Wait for `Uvicorn running on http://0.0.0.0:8000`.

- [ ] **Step 2: Capture screenshot of the job creation form**

Open `http://localhost:8000` in a browser. Fill in a topic like "explain how B-trees work". Set effort to "medium". Expand the Advanced options section so template, theme, speed, and file upload zone are visible. Take a screenshot of the form area (no browser chrome). Save as `docs/screenshot-form.png`. Crop to roughly 1200px wide.

- [ ] **Step 3: Capture screenshot of live progress**

Submit a job (or use a previously running one). While the pipeline is in progress, capture the stage-by-stage progress view showing some stages completed and some pending. Save as `docs/screenshot-progress.png`.

- [ ] **Step 4: Capture screenshot of the video library**

Navigate to `http://localhost:8000/library`. Ensure there are at least 2-3 completed videos visible in the grid. Capture the library grid. Save as `docs/screenshot-library.png`.

- [ ] **Step 5: Stop the server and commit**

```bash
kill %1  # or however the server was backgrounded
git add docs/screenshot-form.png docs/screenshot-progress.png docs/screenshot-library.png
git commit -m "docs: add web UI screenshots for homepage and guide"
```

---

### Task 2: Add screenshot styles to docs.css

Add the CSS for displaying screenshots in a grid, matching the existing card/grid visual style.

**Files:**
- Modify: `docs/docs.css:443-484` (after `.card p` rule, before footer)

- [ ] **Step 1: Add screenshot grid styles to docs.css**

Add the following CSS before the `/* ── Footer ── */` comment at line 486 of `docs/docs.css`:

```css
/* ── Screenshot gallery ── */
.screenshots {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin-top: 8px;
}

.screenshots.triple {
  grid-template-columns: 1fr 1fr 1fr;
}

.screenshot {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 4px;
  overflow: hidden;
  transition: border-color 0.2s;
}

.screenshot:hover {
  border-color: rgba(255, 255, 255, 0.15);
}

.screenshot img {
  width: 100%;
  height: auto;
  display: block;
}

.screenshot-caption {
  padding: 14px 18px;
  font-family: 'DM Mono', monospace;
  font-size: 0.72rem;
  color: var(--muted);
  letter-spacing: 0.04em;
  line-height: 1.5;
}
```

- [ ] **Step 2: Add responsive rule for screenshots**

In the `@media (max-width: 600px)` block at the bottom of `docs/docs.css`, add:

```css
  .screenshots,
  .screenshots.triple {
    grid-template-columns: 1fr;
  }
```

- [ ] **Step 3: Verify the CSS is valid**

Open any existing docs page in a browser and confirm no styling regressions. The new classes are not yet used, so this is just a sanity check.

- [ ] **Step 4: Commit**

```bash
git add docs/docs.css
git commit -m "docs: add screenshot gallery styles to docs.css"
```

---

### Task 3: Update the homepage (index.html)

Reorder sections, add screenshots section, update nav, tweak quickstart and feature copy.

**Files:**
- Modify: `docs/index.html` (full file rewrite, preserving existing styles)

- [ ] **Step 1: Update the nav links**

In `docs/index.html`, replace the nav links div (lines 492-497):

```html
      <div class="nav-links">
        <a href="guide.html">Guide</a>
        <a href="api.html">API</a>
        <a href="#quickstart">Quickstart</a>
        <a href="https://github.com/nicglazkov/Chalkboard" target="_blank" class="nav-github">GitHub ↗</a>
      </div>
```

With:

```html
      <div class="nav-links">
        <a href="guide.html">Guide</a>
        <a href="cli.html">CLI</a>
        <a href="api.html">API</a>
        <a href="#quickstart">Quickstart</a>
        <a href="https://github.com/nicglazkov/Chalkboard" target="_blank" class="nav-github">GitHub ↗</a>
      </div>
```

- [ ] **Step 2: Tweak the hero subtitle**

Replace the hero-sub paragraph (lines 509-513):

```html
        <p class="hero-sub">
          Chalkboard is a multi-agent pipeline that writes a script, fact-checks it,
          generates Manim animation code, synthesizes a voiceover, and renders
          everything to a final <code
            style="font-family:'DM Mono',monospace;font-size:0.9em;color:var(--text)">.mp4</code>, fully automated.
        </p>
```

With:

```html
        <p class="hero-sub">
          Chalkboard is a self-hosted web app that writes a script, fact-checks it,
          generates Manim animation code, synthesizes a voiceover, and renders
          everything to a final <code
            style="font-family:'DM Mono',monospace;font-size:0.9em;color:var(--text)">.mp4</code>. Fully automated.
        </p>
```

- [ ] **Step 3: Simplify the quickstart section**

Replace the entire quickstart section (lines 567-596) with:

```html
      <!-- Quick start -->
      <section class="quickstart" id="quickstart">
        <h2>Quick start</h2>
        <p class="quickstart-sub">Prerequisites: Python 3.10+, Docker, ffmpeg</p>

        <p class="code-label">Install</p>
        <div class="code-block">
          <pre><span class="cmd">git clone https://github.com/nicglazkov/Chalkboard.git</span>
<span class="cmd">cd Chalkboard</span>
<span class="cmd">pip install -r requirements.txt</span>
<span class="cmd">cp .env.example .env</span>  <span class="comment"># add your ANTHROPIC_API_KEY</span></pre>
        </div>

        <p class="code-label">Start the web UI</p>
        <div class="code-block">
          <pre><span class="cmd">python run_server.py</span>
<span class="comment"># Open http://localhost:8000</span></pre>
        </div>

        <p class="code-label" style="margin-top:8px;opacity:0.5">Or run from the terminal</p>
        <div class="code-block" style="opacity:0.7">
          <pre><span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="comment">"explain how B-trees work"</span> <span class="flag">--effort</span> medium</pre>
        </div>
      </section>
```

- [ ] **Step 4: Update the features section copy**

In the features grid, update the "Context injection" card description (line 549) from:

```html
            <p>Feed in local files, PDFs, URLs, or GitHub repos as source material. Chalkboard builds the animation from
              your content.</p>
```

To:

```html
            <p>Upload files, PDFs, URLs, or GitHub repos as source material through the web UI or CLI. Chalkboard builds the animation from your content.</p>
```

- [ ] **Step 5: Add the screenshots section**

After the features section closing `</section>` tag (line 564) and before the quickstart section, the features section is now followed by the quickstart (which we moved up). So instead, add the screenshots section after the features `</section>` and before the options strip section. The new section order is: hero, quickstart, features, screenshots, options. 

Insert the following after the features section's closing `</section>`:

```html
      <!-- Screenshots -->
      <section class="section">
        <p class="section-label">The web UI</p>
        <div class="screenshots triple">
          <div class="screenshot">
            <img src="screenshot-form.png" alt="Chalkboard job creation form" loading="lazy" />
            <p class="screenshot-caption">Create a video with topic, effort, audience, and advanced options.</p>
          </div>
          <div class="screenshot">
            <img src="screenshot-progress.png" alt="Chalkboard live pipeline progress" loading="lazy" />
            <p class="screenshot-caption">Watch each pipeline stage complete in real time.</p>
          </div>
          <div class="screenshot">
            <img src="screenshot-library.png" alt="Chalkboard video library" loading="lazy" />
            <p class="screenshot-caption">Browse, search, and rewatch all your generated videos.</p>
          </div>
        </div>
      </section>
```

- [ ] **Step 6: Add screenshot styles to the inline homepage CSS**

The homepage uses inline `<style>` rather than `docs.css`. Add the screenshot styles before the `/* ── Animations ── */` comment (line 446):

```css
    /* ── Screenshots ── */
    .screenshots {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 24px;
      margin-top: 8px;
    }

    .screenshots.triple {
      grid-template-columns: 1fr 1fr 1fr;
    }

    .screenshot {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 4px;
      overflow: hidden;
      transition: border-color 0.2s;
    }

    .screenshot:hover {
      border-color: rgba(255, 255, 255, 0.15);
    }

    .screenshot img {
      width: 100%;
      height: auto;
      display: block;
    }

    .screenshot-caption {
      padding: 14px 18px;
      font-family: 'DM Mono', monospace;
      font-size: 0.72rem;
      color: var(--muted);
      letter-spacing: 0.04em;
      line-height: 1.5;
    }
```

And add to the existing `@media (max-width: 600px)` block:

```css
      .screenshots,
      .screenshots.triple {
        grid-template-columns: 1fr;
      }
```

- [ ] **Step 7: Verify the homepage in a browser**

Open `docs/index.html` in a browser. Verify:
- Nav shows Guide, CLI, API, Quickstart, GitHub
- Section order: hero, quickstart, features, screenshots, options, footer
- Screenshots display in a 3-column grid
- Hero subtitle says "self-hosted web app"
- Quickstart shows web UI as primary, CLI as secondary
- No broken styles or layout issues

- [ ] **Step 8: Commit**

```bash
git add docs/index.html
git commit -m "docs: homepage — web-UI-first quickstart, screenshots section, updated nav"
```

---

### Task 4: Create the CLI reference page (cli.html)

New page that receives the CLI-specific content from the current guide.html.

**Files:**
- Create: `docs/cli.html`

- [ ] **Step 1: Create cli.html**

Create `docs/cli.html` with the following content. This is the current guide.html's CLI content (flags, context injection, env vars, resume) moved to its own page, with cross-links to guide.html for shared concepts (templates, effort, TTS).

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>CLI Reference — Chalkboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Mono:wght@300;400;500&family=Lora:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="./docs.css" />
  <link rel="icon" type="image/svg+xml" href="favicon.svg">
  <link rel="icon" type="image/png" sizes="32x32" href="favicon-32.png">
</head>
<body>

  <nav>
    <div class="container--nav">
      <a href="index.html" class="nav-logo">Chalkboard</a>
      <div class="nav-links">
        <a href="guide.html">Guide</a>
        <a href="cli.html" class="active">CLI</a>
        <a href="api.html">API</a>
        <a href="index.html#quickstart">Quickstart</a>
        <a href="https://github.com/nicglazkov/Chalkboard" target="_blank" class="nav-github">GitHub ↗</a>
      </div>
    </div>
  </nav>

  <div class="docs-layout">

    <!-- Sidebar -->
    <aside class="docs-sidebar">
      <nav class="sidebar-nav" aria-label="On this page">
        <p class="sidebar-heading">On this page</p>
        <a href="#usage" class="sidebar-link">Basic usage</a>
        <a href="#flags" class="sidebar-link">CLI flags</a>
        <a href="#flags-core" class="sidebar-sublink">Core</a>
        <a href="#flags-context" class="sidebar-sublink">Context</a>
        <a href="#flags-output" class="sidebar-sublink">Output</a>
        <a href="#flags-render" class="sidebar-sublink">Render</a>
        <a href="#context" class="sidebar-link">Context injection</a>
        <a href="#config" class="sidebar-link">Environment variables</a>
        <a href="#resume" class="sidebar-link">Resuming runs</a>
      </nav>
    </aside>

    <!-- Content -->
    <main class="docs-content">

      <!-- Hero -->
      <section class="page-hero">
        <p class="page-label">Reference</p>
        <h1>CLI Reference</h1>
        <p>Run Chalkboard from the terminal for scripting, automation, and advanced workflows. For the easier path, see the <a href="guide.html" style="color:var(--accent)">web UI guide</a>.</p>
      </section>

      <!-- Basic usage -->
      <section class="section" id="usage">
        <p class="section-label">Getting started</p>
        <h2>Basic usage</h2>
        <p class="prose">After <a href="index.html#quickstart" style="color:var(--accent)">installing</a>, run a single command to generate a video:</p>

        <div class="code-block"><pre><span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"explain how B-trees work"</span> <span class="flag">--effort</span> medium</pre></div>

        <p class="prose">The pipeline runs, renders the animation in Docker, and merges the voiceover into <code>output/&lt;run-id&gt;/final.mp4</code>.</p>
        <p class="prose"><strong>First run only:</strong> Docker builds the render image automatically (~30 seconds). Subsequent runs use the cached image.</p>
      </section>

      <!-- CLI flags -->
      <section class="section" id="flags">
        <p class="section-label">Reference</p>
        <h2>CLI flags</h2>

        <h3 id="flags-core">Core</h3>
        <table class="ref-table">
          <thead>
            <tr>
              <th style="width:160px">Flag</th>
              <th style="width:110px">Default</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="flag-name">--topic</span></td>
              <td><span class="flag-required">required</span></td>
              <td>Topic to explain, e.g. <code>"how B-trees work"</code></td>
            </tr>
            <tr>
              <td><span class="flag-name">--effort</span></td>
              <td><span class="flag-default">medium</span></td>
              <td>Validation thoroughness. See <a href="guide.html#effort" style="color:var(--accent)">effort levels</a>.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--audience</span></td>
              <td><span class="flag-default">intermediate</span></td>
              <td>Target audience: <code>beginner</code>, <code>intermediate</code>, <code>expert</code></td>
            </tr>
            <tr>
              <td><span class="flag-name">--tone</span></td>
              <td><span class="flag-default">casual</span></td>
              <td>Narration tone: <code>casual</code>, <code>formal</code>, <code>socratic</code></td>
            </tr>
            <tr>
              <td><span class="flag-name">--theme</span></td>
              <td><span class="flag-default">chalkboard</span></td>
              <td>Visual color theme. See <a href="guide.html#themes" style="color:var(--accent)">themes</a>.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--template</span></td>
              <td><span class="flag-default">—</span></td>
              <td>Animation template. See <a href="guide.html#templates" style="color:var(--accent)">templates</a>.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--speed</span></td>
              <td><span class="flag-default">1.0</span></td>
              <td>Narration speed multiplier. OpenAI: native 0.25–4.0. Kokoro/ElevenLabs: ffmpeg atempo.</td>
            </tr>
          </tbody>
        </table>

        <h3 id="flags-context">Context</h3>
        <table class="ref-table">
          <thead>
            <tr>
              <th style="width:160px">Flag</th>
              <th style="width:110px">Default</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="flag-name">--context</span></td>
              <td><span class="flag-default">—</span></td>
              <td>File or directory to use as source material. Repeatable.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--context-ignore</span></td>
              <td><span class="flag-default">—</span></td>
              <td>Glob pattern to exclude from context directories. Repeatable.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--url</span></td>
              <td><span class="flag-default">—</span></td>
              <td>URL to fetch as source material (HTML stripped to text). Repeatable.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--github</span></td>
              <td><span class="flag-default">—</span></td>
              <td>GitHub repo (<code>owner/repo</code> or full URL); fetches its README as context. Repeatable.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--yes</span></td>
              <td><span class="flag-default">off</span></td>
              <td>Skip the large-context confirmation prompt. Useful for scripted runs.</td>
            </tr>
          </tbody>
        </table>

        <h3 id="flags-output">Output</h3>
        <table class="ref-table">
          <thead>
            <tr>
              <th style="width:160px">Flag</th>
              <th style="width:110px">Default</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="flag-name">--quiz</span></td>
              <td><span class="flag-default">off</span></td>
              <td>Generate comprehension questions as <code>quiz.json</code> after the pipeline.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--burn-captions</span></td>
              <td><span class="flag-default">off</span></td>
              <td>Burn subtitles into the video (re-encodes). <code>captions.srt</code> is always written regardless.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--run-id</span></td>
              <td><span class="flag-default">auto</span></td>
              <td>Resume a previous run using its ID.</td>
            </tr>
          </tbody>
        </table>

        <h3 id="flags-render">Render</h3>
        <table class="ref-table">
          <thead>
            <tr>
              <th style="width:160px">Flag</th>
              <th style="width:110px">Default</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="flag-name">--preview</span></td>
              <td><span class="flag-default">off</span></td>
              <td>Render a fast low-quality preview (480p15) to <code>preview.mp4</code>.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--no-render</span></td>
              <td><span class="flag-default">off</span></td>
              <td>Run the AI pipeline only, skipping Docker render and ffmpeg merge.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--verbose</span></td>
              <td><span class="flag-default">off</span></td>
              <td>Stream raw Docker/Manim output to the terminal while rendering. Cannot be combined with <code>--preview</code>.</td>
            </tr>
            <tr>
              <td><span class="flag-name">--qa-density</span></td>
              <td><span class="flag-default">normal</span></td>
              <td>Visual QA frame sampling: <code>zero</code> (skip), <code>normal</code> (1 frame/30s, up to 10), <code>high</code> (1 frame/15s, up to 20).</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- Context injection -->
      <section class="section" id="context">
        <p class="section-label">Source material</p>
        <h2>Context injection</h2>
        <p class="prose">Pass local files, URLs, or GitHub repos as source material. The pipeline builds the animation from your content rather than Claude's training data alone. For the web UI equivalent, see <a href="guide.html#uploads" style="color:var(--accent)">file uploads</a>.</p>

        <p class="code-label">Local files and directories</p>
        <div class="code-block"><pre><span class="comment"># Explain a codebase</span>
<span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"explain this codebase"</span> <span class="flag">--context</span> ./src <span class="flag">--context</span> ./docs

<span class="comment"># Turn a paper into an animation</span>
<span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"summarize this paper"</span> <span class="flag">--context</span> paper.pdf

<span class="comment"># Exclude lock files and build output</span>
<span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"visualize this"</span> <span class="flag">--context</span> ./repo <span class="flag">--context-ignore</span> <span class="str">"*.lock"</span> <span class="flag">--context-ignore</span> <span class="str">"dist/"</span>

<span class="comment"># Obsidian vault page</span>
<span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"visualize my notes"</span> <span class="flag">--context</span> ~/Documents/vault/page.md</pre></div>

        <p class="code-label">URLs and GitHub repos</p>
        <div class="code-block"><pre><span class="comment"># Web article</span>
<span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"explain this concept"</span> <span class="flag">--url</span> https://en.wikipedia.org/wiki/Binary_search_tree

<span class="comment"># GitHub repo README</span>
<span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"explain this project"</span> <span class="flag">--github</span> nicglazkov/Chalkboard

<span class="comment"># Combine files and URLs</span>
<span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"explain my project"</span> <span class="flag">--context</span> ./README.md <span class="flag">--url</span> https://example.com/blog-post</pre></div>

        <h3>Supported file types</h3>
        <p class="prose">Text and code files (<code>.py</code>, <code>.js</code>, <code>.md</code>, <code>.yaml</code>, <code>.ps1</code>, <code>.bat</code>, and many more), images (<code>.png</code>, <code>.jpg</code>, <code>.webp</code>, <code>.gif</code>), PDFs, and Word docs (<code>.docx</code>). URLs are fetched with HTML stripped to plain text, truncated at 100k characters.</p>

        <h3>Token reporting</h3>
        <p class="prose">Before the pipeline starts, Chalkboard reports how much of the context window your source material uses. If context exceeds 10k tokens you'll be prompted to confirm. Pass <code>--yes</code> to skip this prompt for scripted runs. If context exceeds 90% of the model window, Chalkboard aborts.</p>

        <h3>Resuming with context</h3>
        <p class="prose"><code>--context</code>, <code>--url</code>, and <code>--github</code> are not stored in the checkpoint. Pass them again on resume:</p>
        <div class="code-block"><pre><span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"..."</span> <span class="flag">--run-id</span> &lt;id&gt; <span class="flag">--context</span> ./src</pre></div>
      </section>

      <!-- Configuration -->
      <section class="section" id="config">
        <p class="section-label">Configuration</p>
        <h2>Environment variables</h2>
        <p class="prose">All settings can be overridden via <code>.env</code> or environment variables. Copy <code>.env.example</code> to get started.</p>

        <table class="ref-table">
          <thead>
            <tr>
              <th style="width:180px">Variable</th>
              <th style="width:130px">Default</th>
              <th>Options</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="flag-name">TTS_BACKEND</span></td>
              <td><span class="flag-default">kokoro</span></td>
              <td><code>kokoro</code>, <code>openai</code>, <code>elevenlabs</code>. See <a href="guide.html#tts" style="color:var(--accent)">TTS backends</a>.</td>
            </tr>
            <tr>
              <td><span class="flag-name">ANTHROPIC_API_KEY</span></td>
              <td><span class="flag-default">—</span></td>
              <td>Required. Get yours at <a href="https://console.anthropic.com" target="_blank">console.anthropic.com</a></td>
            </tr>
            <tr>
              <td><span class="flag-name">MANIM_QUALITY</span></td>
              <td><span class="flag-default">medium</span></td>
              <td><code>low</code>, <code>medium</code>, <code>high</code> (1080p60)</td>
            </tr>
            <tr>
              <td><span class="flag-name">DEFAULT_EFFORT</span></td>
              <td><span class="flag-default">medium</span></td>
              <td><code>low</code>, <code>medium</code>, <code>high</code></td>
            </tr>
            <tr>
              <td><span class="flag-name">DEFAULT_AUDIENCE</span></td>
              <td><span class="flag-default">intermediate</span></td>
              <td><code>beginner</code>, <code>intermediate</code>, <code>expert</code></td>
            </tr>
            <tr>
              <td><span class="flag-name">DEFAULT_TONE</span></td>
              <td><span class="flag-default">casual</span></td>
              <td><code>casual</code>, <code>formal</code>, <code>socratic</code></td>
            </tr>
            <tr>
              <td><span class="flag-name">DEFAULT_THEME</span></td>
              <td><span class="flag-default">chalkboard</span></td>
              <td><code>chalkboard</code>, <code>light</code>, <code>colorful</code></td>
            </tr>
            <tr>
              <td><span class="flag-name">OUTPUT_DIR</span></td>
              <td><span class="flag-default">./output</span></td>
              <td>Any path</td>
            </tr>
            <tr>
              <td><span class="flag-name">CHECKPOINT_DB</span></td>
              <td><span class="flag-default">pipeline_state.db</span></td>
              <td>Any path</td>
            </tr>
            <tr>
              <td><span class="flag-name">SERVER_PORT</span></td>
              <td><span class="flag-default">8000</span></td>
              <td>Overridden by <code>--port</code> at runtime</td>
            </tr>
          </tbody>
        </table>
      </section>

      <!-- Resume -->
      <section class="section" id="resume">
        <p class="section-label">Checkpointing</p>
        <h2>Resuming a crashed run</h2>
        <p class="prose">Every run is checkpointed after each pipeline stage. If it crashes or you abort, resume with the same run ID:</p>

        <div class="code-block"><pre><span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"..."</span> <span class="flag">--run-id</span> &lt;previous-run-id&gt;</pre></div>

        <h3>Preview to full render workflow</h3>
        <p class="prose">Run <code>--preview</code> first to quickly check the visuals at low quality, then do the full HD render. The pipeline result is already checkpointed, so it won't re-run:</p>

        <div class="code-block"><pre><span class="comment"># Step 1: generate script + animation, render preview</span>
<span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"how B-trees work"</span> <span class="flag">--preview</span>
<span class="comment"># → output/&lt;run-id&gt;/preview.mp4 (480p, fast)</span>

<span class="comment"># Step 2: full HD render (pipeline skipped, uses checkpoint)</span>
<span class="cmd">python main.py</span> <span class="flag">--topic</span> <span class="str">"how B-trees work"</span> <span class="flag">--run-id</span> &lt;run-id&gt;
<span class="comment"># → output/&lt;run-id&gt;/final.mp4 (full quality + visual QA)</span></pre></div>
      </section>

      <!-- Footer -->
      <footer>
        <span class="footer-left">Chalkboard — MIT License</span>
        <div class="footer-right">
          <a href="https://github.com/nicglazkov/Chalkboard" target="_blank">github.com/nicglazkov/Chalkboard ↗</a>
        </div>
      </footer>

    </main>
  </div>

  <!-- Cloudflare Web Analytics -->
  <script defer src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{"token": "a176d383a88845e4a39bccf3f7c30879"}'></script>
  <!-- End Cloudflare Web Analytics -->

  <script>
  (function () {
    var links = document.querySelectorAll('.sidebar-link, .sidebar-sublink');
    var ids = Array.from(links).map(function (l) { return l.getAttribute('href').slice(1); });
    var targets = ids.map(function (id) { return document.getElementById(id); }).filter(Boolean);
    var offsets = [];

    function computeOffsets() {
      offsets = targets.map(function (t) {
        return t.getBoundingClientRect().top + window.scrollY;
      });
    }

    function update() {
      var scrollY = window.scrollY + 96;
      var active = null;
      for (var i = 0; i < offsets.length; i++) {
        if (offsets[i] <= scrollY) active = ids[i];
      }
      links.forEach(function (l) {
        l.classList.toggle('active', l.getAttribute('href') === '#' + active);
      });
    }

    computeOffsets();
    update();
    window.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', function () { computeOffsets(); update(); });
  })();
  </script>

</body>
</html>
```

- [ ] **Step 2: Verify cli.html in a browser**

Open `docs/cli.html`. Verify:
- Nav shows Guide, CLI (active), API, Quickstart, GitHub
- Sidebar links work and highlight on scroll
- All four flag tables render correctly
- Cross-links to guide.html#templates, guide.html#effort, guide.html#tts are present
- Context injection section has all code examples
- No em dashes in body text

- [ ] **Step 3: Commit**

```bash
git add docs/cli.html
git commit -m "docs: new CLI reference page with flags, context injection, env vars"
```

---

### Task 5: Rewrite the guide page (guide.html)

Complete rewrite to be web-UI-first. This is the largest task.

**Files:**
- Modify: `docs/guide.html` (full rewrite)

- [ ] **Step 1: Rewrite guide.html**

Replace the entire contents of `docs/guide.html` with the following web-UI-first guide:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Guide — Chalkboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Mono:wght@300;400;500&family=Lora:ital,wght@0,400;0,500;1,400&display=swap" rel="stylesheet" />
  <link rel="stylesheet" href="./docs.css" />
  <link rel="icon" type="image/svg+xml" href="favicon.svg">
  <link rel="icon" type="image/png" sizes="32x32" href="favicon-32.png">
</head>
<body>

  <nav>
    <div class="container--nav">
      <a href="index.html" class="nav-logo">Chalkboard</a>
      <div class="nav-links">
        <a href="guide.html" class="active">Guide</a>
        <a href="cli.html">CLI</a>
        <a href="api.html">API</a>
        <a href="index.html#quickstart">Quickstart</a>
        <a href="https://github.com/nicglazkov/Chalkboard" target="_blank" class="nav-github">GitHub ↗</a>
      </div>
    </div>
  </nav>

  <div class="docs-layout">

    <!-- Sidebar -->
    <aside class="docs-sidebar">
      <nav class="sidebar-nav" aria-label="On this page">
        <p class="sidebar-heading">On this page</p>
        <a href="#quickstart" class="sidebar-link">Quick start</a>
        <a href="#creating" class="sidebar-link">Creating a video</a>
        <a href="#progress" class="sidebar-link">Live progress</a>
        <a href="#library" class="sidebar-link">Video library</a>
        <a href="#uploads" class="sidebar-link">File uploads</a>
        <a href="#templates" class="sidebar-link">Templates</a>
        <a href="#effort" class="sidebar-link">Effort levels</a>
        <a href="#tts" class="sidebar-link">TTS backends</a>
        <a href="#themes" class="sidebar-link">Themes</a>
      </nav>
    </aside>

    <!-- Content -->
    <main class="docs-content">

      <!-- Hero -->
      <section class="page-hero">
        <p class="page-label">Documentation</p>
        <h1>Guide</h1>
        <p>How to use Chalkboard, from creating your first video to customizing every detail. For terminal workflows, see the <a href="cli.html" style="color:var(--accent)">CLI reference</a>.</p>
      </section>

      <!-- Quick start -->
      <section class="section" id="quickstart">
        <p class="section-label">Getting started</p>
        <h2>Quick start</h2>
        <p class="prose">Prerequisites: Python 3.10+, <a href="https://docker.com" target="_blank">Docker</a>, and <a href="https://ffmpeg.org" target="_blank">ffmpeg</a>.</p>

        <p class="code-label">Install</p>
        <div class="code-block"><pre><span class="cmd">git clone https://github.com/nicglazkov/Chalkboard.git</span>
<span class="cmd">cd Chalkboard</span>
<span class="cmd">pip install -r requirements.txt</span>
<span class="cmd">cp .env.example .env</span>  <span class="comment"># add your ANTHROPIC_API_KEY</span></pre></div>

        <p class="code-label">Start the web UI</p>
        <div class="code-block"><pre><span class="cmd">python run_server.py</span>
<span class="comment"># Open http://localhost:8000</span></pre></div>

        <p class="prose" style="margin-top:20px;">The first run builds a Docker image for rendering (~30 seconds). Subsequent runs use the cached image.</p>
        <p class="prose"><strong>API keys:</strong> You need an <code>ANTHROPIC_API_KEY</code> in your <code>.env</code> file. For TTS, the default backend (Kokoro) is free and local. Alternatively, add an <code>OPENAI_API_KEY</code> for OpenAI TTS. See <a href="#tts" style="color:var(--accent)">TTS backends</a> for all options.</p>
      </section>

      <!-- Creating a video -->
      <section class="section" id="creating">
        <p class="section-label">Web UI</p>
        <h2>Creating a video</h2>
        <p class="prose">Open <code>http://localhost:8000</code> after starting the server. The main form has three required fields:</p>
        <ul class="prose" style="margin-left:20px;margin-bottom:20px;">
          <li><strong>Topic</strong> — what to explain, e.g. "how B-trees work" or "the history of the internet"</li>
          <li><strong>Effort</strong> — how thorough the validation should be (see <a href="#effort" style="color:var(--accent)">effort levels</a>)</li>
          <li><strong>Audience</strong> — beginner, intermediate, or expert</li>
        </ul>

        <p class="prose">Click <strong>Advanced options</strong> to expand additional settings:</p>
        <ul class="prose" style="margin-left:20px;margin-bottom:20px;">
          <li><strong>Tone</strong> — casual, formal, or socratic</li>
          <li><strong>Theme</strong> — visual color palette (see <a href="#themes" style="color:var(--accent)">themes</a>)</li>
          <li><strong>Template</strong> — animation layout for specific content types (see <a href="#templates" style="color:var(--accent)">templates</a>)</li>
          <li><strong>Speed</strong> — narration speed multiplier (default 1.0)</li>
          <li><strong>URLs</strong> — web pages to use as source material</li>
          <li><strong>GitHub</strong> — repository to pull README from as context</li>
          <li><strong>File uploads</strong> — drag and drop files as source material (see <a href="#uploads" style="color:var(--accent)">file uploads</a>)</li>
          <li><strong>QA density</strong> — how many frames Claude inspects after rendering</li>
          <li><strong>Burn captions</strong> — bake subtitles into the video</li>
          <li><strong>Generate quiz</strong> — create comprehension questions from the script</li>
        </ul>

        <p class="prose">Hit <strong>Generate</strong> to start. The pipeline runs in the background and you can watch progress in real time.</p>

        <div class="screenshots" style="margin-top:16px;">
          <div class="screenshot">
            <img src="screenshot-form.png" alt="Chalkboard job creation form" loading="lazy" />
            <p class="screenshot-caption">The job creation form with advanced options expanded.</p>
          </div>
        </div>
      </section>

      <!-- Live progress -->
      <section class="section" id="progress">
        <p class="section-label">Web UI</p>
        <h2>Live progress</h2>
        <p class="prose">After submitting a job, the page switches to a live progress view. Each pipeline stage lights up as it completes:</p>
        <ol class="prose" style="margin-left:20px;margin-bottom:20px;">
          <li><strong>Research</strong> (high effort only) — web search for source material</li>
          <li><strong>Script</strong> — Claude writes the narration and segments</li>
          <li><strong>Fact check</strong> — Claude validates accuracy</li>
          <li><strong>Animation</strong> — Claude generates Manim code</li>
          <li><strong>Code review</strong> — syntax and semantic validation</li>
          <li><strong>Layout check</strong> — headless dry-run for bounding box and timing issues</li>
          <li><strong>Render</strong> — TTS audio generation, Docker render, ffmpeg merge</li>
        </ol>
        <p class="prose">If a stage fails validation, the pipeline automatically retries (up to 3 attempts per stage). When the job completes, a video player appears with download links for all output files.</p>

        <div class="screenshots" style="margin-top:16px;">
          <div class="screenshot">
            <img src="screenshot-progress.png" alt="Chalkboard live pipeline progress" loading="lazy" />
            <p class="screenshot-caption">Live pipeline progress with stages completing in real time.</p>
          </div>
        </div>
      </section>

      <!-- Video library -->
      <section class="section" id="library">
        <p class="section-label">Web UI</p>
        <h2>Video library</h2>
        <p class="prose">Navigate to <code>/library</code> (or click "Library" in the web UI header) to browse all your generated videos. The library indexes every completed run automatically.</p>
        <ul class="prose" style="margin-left:20px;margin-bottom:20px;">
          <li><strong>Grid view</strong> with thumbnails, titles, and metadata</li>
          <li><strong>Search</strong> by topic or title</li>
          <li><strong>Video detail page</strong> with playback, script, download links, and generation settings</li>
          <li><strong>Delete</strong> videos you no longer need (with optional file cleanup)</li>
        </ul>
        <p class="prose">Old runs generated before the library existed are automatically backfilled on server startup.</p>

        <div class="screenshots" style="margin-top:16px;">
          <div class="screenshot">
            <img src="screenshot-library.png" alt="Chalkboard video library" loading="lazy" />
            <p class="screenshot-caption">The video library with search and grid view.</p>
          </div>
        </div>
      </section>

      <!-- File uploads -->
      <section class="section" id="uploads">
        <p class="section-label">Source material</p>
        <h2>File uploads</h2>
        <p class="prose">In the Advanced options panel, you'll find a drag-and-drop upload zone. Drop individual files or entire folders to use as source material for your video.</p>

        <h3>Supported file types</h3>
        <p class="prose">Text and code files (<code>.py</code>, <code>.js</code>, <code>.md</code>, <code>.yaml</code>, and many more), images (<code>.png</code>, <code>.jpg</code>, <code>.webp</code>, <code>.gif</code>), PDFs, and Word docs (<code>.docx</code>).</p>

        <h3>Size limits</h3>
        <table class="ref-table">
          <thead>
            <tr>
              <th style="width:160px">Type</th>
              <th>Limit</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="flag-name">Text / code</span></td>
              <td>2 MB per file</td>
            </tr>
            <tr>
              <td><span class="flag-name">Images</span></td>
              <td>5 MB per file</td>
            </tr>
            <tr>
              <td><span class="flag-name">PDFs</span></td>
              <td>20 MB per file</td>
            </tr>
            <tr>
              <td><span class="flag-name">DOCX</span></td>
              <td>10 MB per file</td>
            </tr>
            <tr>
              <td><span class="flag-name">Total</span></td>
              <td>24 MB across all files</td>
            </tr>
          </tbody>
        </table>

        <p class="prose" style="margin-top:20px;">You can also paste URLs and GitHub repos in the Advanced options panel. For CLI-based context injection with more control (glob ignore patterns, token reporting), see the <a href="cli.html#context" style="color:var(--accent)">CLI context injection</a> docs.</p>
      </section>

      <!-- Templates -->
      <section class="section" id="templates">
        <p class="section-label">Animation</p>
        <h2>Templates</h2>
        <p class="prose">Templates inject layout and visual convention guidance into the animation generator, producing more structured animations for specific content types. Select a template in the Advanced options panel, or omit it to let Chalkboard choose a freeform layout.</p>

        <div class="cards">
          <div class="card">
            <p class="card-name">algorithm</p>
            <h4>Sorting &amp; searching</h4>
            <p>Array cells + pointer arrows + step counter + explicit swap animations. Best for sorting, searching, and graph traversal.</p>
          </div>
          <div class="card">
            <p class="card-name">code</p>
            <h4>Code walkthroughs</h4>
            <p>Manim Code object, incremental line reveal, callout annotations. Best for implementation explainers.</p>
          </div>
          <div class="card">
            <p class="card-name">compare</p>
            <h4>A vs B trade-offs</h4>
            <p>Two labeled columns, consistent color per side, summary row at end. Best for technology comparisons.</p>
          </div>
          <div class="card">
            <p class="card-name">howto</p>
            <h4>Step-by-step guides</h4>
            <p>Numbered steps revealed progressively, active step highlighted, completed steps dimmed. Best for setup guides, recipes, and processes.</p>
          </div>
          <div class="card">
            <p class="card-name">timeline</p>
            <h4>Chronological events</h4>
            <p>Horizontal axis with dated markers animated left to right. Best for history, version timelines, and biographical sequences.</p>
          </div>
        </div>
      </section>

      <!-- Effort levels -->
      <section class="section" id="effort">
        <p class="section-label">Quality</p>
        <h2>Effort levels</h2>
        <p class="prose">Effort controls how thorough the validation is and whether web search is used during script generation.</p>

        <table class="ref-table">
          <thead>
            <tr>
              <th style="width:100px">Level</th>
              <th>Fact-check</th>
              <th>Web search</th>
              <th style="width:80px">Segments</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="flag-name">low</span></td>
              <td>Light check, obvious errors only</td>
              <td>Never</td>
              <td>3–4</td>
            </tr>
            <tr>
              <td><span class="flag-name">medium</span></td>
              <td>Spot-check key claims</td>
              <td>No</td>
              <td>4–6</td>
            </tr>
            <tr>
              <td><span class="flag-name">high</span></td>
              <td>Thorough</td>
              <td>Dedicated research step before scripting</td>
              <td>5–8</td>
            </tr>
          </tbody>
        </table>

        <p class="prose" style="margin-top:20px;">With <strong>high</strong> effort, a dedicated research agent runs web searches before the script is written. If the search fails or the results are off-topic, Chalkboard prints a warning and continues on training data. The pipeline never aborts because of a search failure.</p>

        <h3>Automatic quality checks</h3>
        <p class="prose">Two quality checks run automatically on every video, regardless of effort level.</p>
        <div class="cards" style="grid-template-columns: 1fr 1fr; margin-top: 16px;">
          <div class="card">
            <p class="card-name">pre-render</p>
            <h4>Layout check</h4>
            <p>Dry-runs the scene headlessly inside Docker and validates every segment's bounding boxes (off-screen, colliding elements) and animation timing against the audio budget. Violations are fed back to the animation generator and retried automatically.</p>
          </div>
          <div class="card">
            <p class="card-name">post-render</p>
            <h4>Visual QA</h4>
            <p>Samples frames from the final video and asks Claude to flag overlapping text, off-screen content, or readability issues. Errors trigger scene regeneration and re-render (up to 2 attempts).</p>
          </div>
        </div>
      </section>

      <!-- TTS backends -->
      <section class="section" id="tts">
        <p class="section-label">Voiceover</p>
        <h2>TTS backends</h2>
        <p class="prose">Set <code>TTS_BACKEND</code> in your <code>.env</code> file. The <code>.env.example</code> ships with <code>openai</code>, which works on all platforms. The code default when unset is <code>kokoro</code>.</p>

        <table class="ref-table">
          <thead>
            <tr>
              <th style="width:110px">Backend</th>
              <th style="width:80px">Quality</th>
              <th style="width:80px">Cost</th>
              <th>Requires</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><span class="flag-name">kokoro</span></td>
              <td>Best</td>
              <td>Free</td>
              <td>PyTorch ≥ 2.4, <code>espeak-ng</code> (not available on Intel Macs)</td>
            </tr>
            <tr>
              <td><span class="flag-name">openai</span></td>
              <td>Great</td>
              <td>API</td>
              <td><code>OPENAI_API_KEY</code></td>
            </tr>
            <tr>
              <td><span class="flag-name">elevenlabs</span></td>
              <td>Great</td>
              <td>API</td>
              <td><code>pip install elevenlabs</code>, <code>ELEVENLABS_API_KEY</code></td>
            </tr>
          </tbody>
        </table>

        <p class="prose" style="margin-top:20px;"><strong>Intel Mac users:</strong> PyTorch ≥ 2.4 has no x86_64 macOS wheels. Use <code>openai</code> or <code>elevenlabs</code>.</p>
        <p class="prose">To install <code>espeak-ng</code> for Kokoro: <code>brew install espeak-ng</code> / <code>apt install espeak-ng</code></p>
      </section>

      <!-- Themes -->
      <section class="section" id="themes">
        <p class="section-label">Visual style</p>
        <h2>Themes</h2>
        <p class="prose">Themes control the color palette of the generated animation. Select a theme in the web UI form or pass <code>--theme</code> on the CLI.</p>

        <div class="cards" style="grid-template-columns: 1fr 1fr 1fr; margin-top: 16px;">
          <div class="card">
            <p class="card-name">chalkboard</p>
            <h4>Chalkboard</h4>
            <p>Dark green-gray background with chalk-white text and warm accent colors. The default theme.</p>
          </div>
          <div class="card">
            <p class="card-name">light</p>
            <h4>Light</h4>
            <p>Clean white background with dark text. Good for presentations and formal contexts.</p>
          </div>
          <div class="card">
            <p class="card-name">colorful</p>
            <h4>Colorful</h4>
            <p>Dark background with vibrant, saturated accent colors. Good for engaging, visual topics.</p>
          </div>
        </div>
      </section>

      <!-- Footer -->
      <footer>
        <span class="footer-left">Chalkboard — MIT License</span>
        <div class="footer-right">
          <a href="https://github.com/nicglazkov/Chalkboard" target="_blank">github.com/nicglazkov/Chalkboard ↗</a>
        </div>
      </footer>

    </main>
  </div>

  <!-- Cloudflare Web Analytics -->
  <script defer src='https://static.cloudflareinsights.com/beacon.min.js' data-cf-beacon='{"token": "a176d383a88845e4a39bccf3f7c30879"}'></script>
  <!-- End Cloudflare Web Analytics -->

  <script>
  (function () {
    var links = document.querySelectorAll('.sidebar-link, .sidebar-sublink');
    var ids = Array.from(links).map(function (l) { return l.getAttribute('href').slice(1); });
    var targets = ids.map(function (id) { return document.getElementById(id); }).filter(Boolean);
    var offsets = [];

    function computeOffsets() {
      offsets = targets.map(function (t) {
        return t.getBoundingClientRect().top + window.scrollY;
      });
    }

    function update() {
      var scrollY = window.scrollY + 96;
      var active = null;
      for (var i = 0; i < offsets.length; i++) {
        if (offsets[i] <= scrollY) active = ids[i];
      }
      links.forEach(function (l) {
        l.classList.toggle('active', l.getAttribute('href') === '#' + active);
      });
    }

    computeOffsets();
    update();
    window.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', function () { computeOffsets(); update(); });
  })();
  </script>

</body>
</html>
```

- [ ] **Step 2: Verify guide.html in a browser**

Open `docs/guide.html`. Verify:
- Nav shows Guide (active), CLI, API, Quickstart, GitHub
- Sidebar has 9 sections: Quick start, Creating a video, Live progress, Video library, File uploads, Templates, Effort levels, TTS backends, Themes
- All sidebar links scroll to correct sections and highlight on scroll
- Screenshot placeholders display (broken images are fine until screenshots exist)
- Template cards display in 3-column grid (5 cards)
- Theme cards display in 3-column grid (3 cards)
- Quality checks display in 2-column card grid
- No em dashes in body text
- Cross-links to cli.html work

- [ ] **Step 3: Commit**

```bash
git add docs/guide.html
git commit -m "docs: rewrite guide page — web-UI-first with screenshots, shared concepts"
```

---

### Task 6: Update the API reference (api.html)

Minor updates: add CLI nav link, trim the Web UI section.

**Files:**
- Modify: `docs/api.html:16-24` (nav links)
- Modify: `docs/api.html:248-267` (Web UI section)

- [ ] **Step 1: Update nav links in api.html**

Replace the nav links div (lines 17-24):

```html
      <div class="nav-links">
        <a href="guide.html">Guide</a>
        <a href="api.html" class="active">API</a>
        <a href="index.html#quickstart">Quickstart</a>
        <a href="https://github.com/nicglazkov/Chalkboard" target="_blank" class="nav-github">GitHub ↗</a>
      </div>
```

With:

```html
      <div class="nav-links">
        <a href="guide.html">Guide</a>
        <a href="cli.html">CLI</a>
        <a href="api.html" class="active">API</a>
        <a href="index.html#quickstart">Quickstart</a>
        <a href="https://github.com/nicglazkov/Chalkboard" target="_blank" class="nav-github">GitHub ↗</a>
      </div>
```

- [ ] **Step 2: Simplify the Web UI section**

Replace the entire Web UI section (lines 249-267):

```html
      <!-- Web UI -->
      <section class="section" id="webui">
        <p class="section-label">Interface</p>
        <h2>Web UI</h2>
        <p class="prose">The server includes a built-in web interface at <code>http://localhost:8000</code> with a job creation form, live progress view, and video library. No build step required. See the <a href="guide.html" style="color:var(--accent)">Guide</a> for a full walkthrough.</p>
      </section>
```

- [ ] **Step 3: Verify api.html in a browser**

Open `docs/api.html`. Verify:
- Nav shows Guide, CLI, API (active), Quickstart, GitHub
- Web UI section is a single paragraph with a link to the guide
- Everything else is unchanged

- [ ] **Step 4: Commit**

```bash
git add docs/api.html
git commit -m "docs: api page — add CLI nav link, simplify Web UI section"
```

---

### Task 7: Final review and cleanup

Cross-page consistency check across all four pages.

**Files:**
- Possibly modify: any of the 4 HTML files if issues found

- [ ] **Step 1: Verify nav consistency**

Open each page and confirm the nav links are identical (except for which one has `class="active"`):
- `index.html`: no active class on Guide/CLI/API
- `guide.html`: Guide is active
- `cli.html`: CLI is active
- `api.html`: API is active

- [ ] **Step 2: Verify cross-links work**

Click through these links and confirm they land on the right section:
- `cli.html` → `guide.html#templates` (templates section)
- `cli.html` → `guide.html#effort` (effort levels section)
- `cli.html` → `guide.html#tts` (TTS backends section)
- `cli.html` → `guide.html#themes` (themes section)
- `cli.html` → `guide.html#uploads` (file uploads section)
- `guide.html` → `cli.html` (hero subtitle link)
- `guide.html` → `cli.html#context` (file uploads section link)
- `api.html` → `guide.html` (Web UI section link)

- [ ] **Step 3: Check for em dashes**

Search all four HTML files for the `—` character and the `&mdash;` entity. Replace any found in body text with commas, periods, or restructured sentences. The `—` in the CLI flags table default column (meaning "none") is fine to keep.

- [ ] **Step 4: Responsive check**

Open each page at 600px viewport width. Confirm:
- Screenshots stack to single column
- Template/theme cards stack to single column
- Tables are scrollable, not clipped
- Sidebar is hidden on guide/cli/api pages

- [ ] **Step 5: Commit any fixes**

```bash
git add docs/
git commit -m "docs: cross-page consistency fixes"
```
