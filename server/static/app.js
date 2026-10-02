// app.js: shared helpers, the nav (tabs, Claude API status, jobs menu,
// theme toggle) and the window.jobStatus store used by the generate page.
// Plain script, no build step. Loaded (not deferred) in <head> of every page
// so inline page scripts can use window.CB; the nav renders on DOMContentLoaded.
(function () {
  'use strict';

  // ── Helpers ────────────────────────────────────────────────────────────
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function fmtDuration(sec) {
    if (!sec || sec < 1) return '';
    const total = Math.round(sec);
    const m = Math.floor(total / 60);
    const s = total % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function fmtClock(sec) {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function fmtDate(iso, long) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const opts = { month: long ? 'long' : 'short', day: 'numeric' };
    if (long || d.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
    return d.toLocaleDateString(undefined, opts);
  }

  // Short date for lists: Today, Yesterday, then "Oct 2".
  function fmtWhen(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const diff = Math.round((day(new Date()) - day(d)) / 86400000);
    if (diff === 0) return 'Today';
    if (diff === 1) return 'Yesterday';
    return fmtDate(iso);
  }

  function fmtAgo(ts) {
    const sec = Math.floor((Date.now() - ts) / 1000);
    if (sec < 60) return 'just now';
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min} min ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr} h ago`;
    return `${Math.floor(hr / 24)} d ago`;
  }

  function fmtBytes(n) {
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    return (n / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function fileUrl(runId, name) {
    return `/api/jobs/${encodeURIComponent(runId)}/files/${encodeURIComponent(name)}`;
  }

  // Thumbnail markup for a library video. Falls back to the topic written on
  // a small board when there is no thumb.jpg (or it fails to load).
  function boardHtml(text, theme) {
    return `<div class="thumb-board" data-theme="${esc(theme || 'chalkboard')}"><span>${esc(text)}</span></div>`;
  }
  function thumbInner(v) {
    const label = v.title || v.topic;
    if (!v.thumb_path) return boardHtml(label, v.theme);
    return `<img src="${fileUrl(v.run_id, 'thumb.jpg')}" alt="" loading="lazy" decoding="async"
      data-fallback="${esc(label)}" data-theme="${esc(v.theme || 'chalkboard')}">`;
  }
  // One capturing listener handles every broken thumbnail on the page.
  document.addEventListener('error', (e) => {
    const img = e.target;
    if (img && img.tagName === 'IMG' && img.dataset.fallback != null) {
      img.outerHTML = boardHtml(img.dataset.fallback, img.dataset.theme);
    }
  }, true);

  const ICONS = {
    sun: '<svg class="i-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    moon: '<svg class="i-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>',
    jobs: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    mark: '<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="7" fill="#1a1918"/><rect x="0.5" y="0.5" width="31" height="31" rx="6.5" fill="none" stroke="rgba(242,237,226,0.14)"/><polygon points="11,9 11,23 24,16" fill="#c8b97a"/></svg>',
  };

  // ── Math: $...$ rendered with KaTeX, loaded from cdnjs only when needed ─
  const MATH_RE = /\$\$([^$]+?)\$\$|\$(?=\S)([^$\n]*?\S)\$(?!\d)/g;
  const KATEX = 'https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.11/';
  let katexPromise = null;

  function loadKatex() {
    if (window.katex) return Promise.resolve(window.katex);
    if (katexPromise) return katexPromise;
    katexPromise = new Promise((resolve, reject) => {
      const css = document.createElement('link');
      css.rel = 'stylesheet';
      css.href = KATEX + 'katex.min.css';
      css.integrity = 'sha384-nB0miv6/jRmo5UMMR1wu3Gz6NLsoTkbqJghGIsx//Rlm+ZU03BU6SQNC66uf4l5+';
      css.crossOrigin = 'anonymous';
      document.head.appendChild(css);
      const js = document.createElement('script');
      js.src = KATEX + 'katex.min.js';
      js.integrity = 'sha384-7zkQWkzuo3B5mTepMUcHkMB5jZaolc2xDwL6VFqjFALcbeS9Ggm/Yr2r3Dy4lfFg';
      js.crossOrigin = 'anonymous';
      js.onload = () => resolve(window.katex);
      js.onerror = reject;
      document.head.appendChild(js);
    });
    return katexPromise;
  }

  function hasMath(text) {
    MATH_RE.lastIndex = 0;
    return MATH_RE.test(String(text || ''));
  }

  function mathHtml(text) {
    const katex = window.katex;
    const src = String(text || '');
    if (!katex) return esc(src);
    let out = '';
    let last = 0;
    MATH_RE.lastIndex = 0;
    let m;
    while ((m = MATH_RE.exec(src))) {
      out += esc(src.slice(last, m.index));
      const display = m[1] != null;
      try {
        out += katex.renderToString(display ? m[1] : m[2], { displayMode: display, throwOnError: false, output: 'html' });
      } catch {
        out += esc(m[0]);
      }
      last = m.index + m[0].length;
    }
    return out + esc(src.slice(last));
  }

  // Put text in an element; upgrade any $...$ to typeset math once KaTeX loads.
  function setMathText(el, text) {
    el.textContent = text;
    if (!hasMath(text)) return;
    loadKatex().then(() => { el.innerHTML = mathHtml(text); }).catch(() => {});
  }

  // Render quality preset -> what it means on screen
  const RES = { low: '480p', medium: '720p', high: '1080p', '4k': '4K' };
  function resLabel(q) { return RES[q] || (q ? String(q) : ''); }

  // ── Pipeline stages ────────────────────────────────────────────────────
  // Pipeline nodes emit one event when they FINISH, so the step for node X is
  // marked done when its event lands and the next node is predicted from the
  // update. After the pipeline, the server reports render / visual_qa / quiz
  // as pseudo-nodes with {status: running|done|failed}.
  const STAGE_LABELS = {
    init: 'Preparing',
    research_agent: 'Researching the topic',
    script_agent: 'Writing the script',
    fact_validator: 'Checking facts',
    manim_agent: 'Writing the animation code',
    code_validator: 'Checking the code',
    layout_checker: 'Checking the layout',
    render_trigger: 'Recording the voiceover',
    escalate_to_user: 'Stopped after repeated retries',
    render: 'Rendering the video',
    visual_qa: 'Checking the rendered frames',
    quiz: 'Writing the quiz',
  };
  const PSEUDO = new Set(['render', 'visual_qa', 'quiz']);

  function stageLabel(node) {
    return STAGE_LABELS[node] || String(node || '').replace(/_/g, ' ');
  }

  // opts (optional, known only for jobs started in this browser):
  // {effort, qa: bool, quiz: bool}
  function afterRender(opts) {
    if (!opts) return null;
    if (opts.qa) return 'visual_qa';
    return opts.quiz ? 'quiz' : null;
  }
  function nextStage(node, updates, opts) {
    const u = updates || {};
    switch (node) {
      case undefined: case null: return 'init';
      case 'init': return u.effort_level === 'high' ? 'research_agent' : 'script_agent';
      case 'research_agent': return 'script_agent';
      case 'script_agent': return 'fact_validator';
      case 'fact_validator': return u.fact_feedback ? 'script_agent' : 'manim_agent';
      case 'manim_agent': return 'code_validator';
      case 'code_validator': return u.code_feedback ? 'manim_agent' : 'layout_checker';
      case 'layout_checker': return u.code_feedback ? 'manim_agent' : 'render_trigger';
      case 'render_trigger': return 'render';
      case 'render': return u.status === 'done' ? afterRender(opts) : null;
      case 'visual_qa': return u.status === 'done' && opts && opts.quiz ? 'quiz' : null;
      default: return null;
    }
  }

  // The happy path still ahead after `node`, shown as faded upcoming steps.
  function upcoming(node, opts) {
    const out = [];
    const o = opts || {};
    let n = node;
    for (let guard = 0; guard < 12; guard++) {
      let next;
      if (n == null) next = 'init';
      else if (n === 'init') next = o.effort === 'high' ? 'research_agent' : 'script_agent';
      else if (PSEUDO.has(n)) next = nextStage(n, { status: 'done' }, o);
      else next = nextStage(n, {}, o);
      if (!next) break;
      out.push(next);
      n = next;
    }
    return out;
  }

  // Turn a job's event list into display steps:
  // [{node, status: 'done'|'running'|'failed', attempt}]
  function buildSteps(events, opts) {
    const steps = [];
    const counts = {};
    const push = (node, status) => {
      counts[node] = (counts[node] || 0) + 1;
      const s = { node, status, attempt: counts[node] };
      steps.push(s);
      return s;
    };
    const last = () => steps[steps.length - 1];
    // Re-point a predicted running step at the node that actually ran.
    const claim = (node, status) => {
      const l = last();
      if (l && l.status === 'running') {
        if (l.node !== node) {
          counts[l.node]--;
          steps.pop();
          return push(node, status);
        }
        l.status = status;
        return l;
      }
      return push(node, status);
    };
    for (const ev of events || []) {
      if (!ev || ev.done || !ev.node) continue;
      const node = ev.node;
      const u = ev.updates || {};
      if (PSEUDO.has(node)) {
        if (u.status === 'running') { claim(node, 'running'); continue; }
        claim(node, u.status === 'failed' ? 'failed' : 'done');
      } else {
        claim(node, 'done');
      }
      const next = nextStage(node, u, opts);
      if (next) push(next, 'running');
    }
    if (!steps.length) push('init', 'running');
    return steps;
  }

  function currentStep(events, opts) {
    const steps = buildSteps(events, opts);
    const run = steps.filter((s) => s.status === 'running').pop();
    return run ? run.node : null;
  }

  // ── Errors: a short human line first, the raw detail on request ─────────
  function explainError(raw) {
    const s = String(raw || '');
    const low = s.toLowerCase();
    const has = (...xs) => xs.some((x) => low.includes(x));
    if (!s.trim()) return { title: 'The job failed', body: 'No error message was recorded. The server log has the details.' };
    if (has('elevenlabs', 'quota_exceeded')) return { title: 'The voiceover failed', body: 'ElevenLabs refused the request. Try again, or pick the Kokoro narrator.' };
    if (has('error code: 529', 'overloaded')) return { title: 'Claude was overloaded', body: 'Anthropic is busy right now. Try again in a minute.' };
    if (has('rate_limit', 'rate limit', 'error code: 429')) return { title: 'Hit the Claude rate limit', body: 'Too many requests in a short time. Wait a minute, then try again.' };
    if (has('credit balance', 'billing')) return { title: 'The Anthropic account is out of credits', body: 'Add credits in the Anthropic console, then try again.' };
    if (has('authentication_error', 'invalid x-api-key', 'error code: 401')) return { title: 'Claude rejected the API key', body: 'Check ANTHROPIC_API_KEY in the server .env, then restart the server.' };
    if (has('timed out', 'timeout', 'connection error', 'connecterror', 'name resolution')) return { title: 'Could not reach Claude', body: 'The request timed out or the network dropped. Try again.' };
    if (has('api_error', 'internal server error', 'error code: 500')) return { title: 'Claude had an internal error', body: 'This is usually temporary. Try again in a minute.' };
    if (has('render failed')) return { title: 'The video did not render', body: 'The script, animation code and voiceover were saved. The server log has the Manim error.' };
    if (has('code_validator failed', 'layout_checker failed')) return { title: 'The animation code kept failing its checks', body: 'Try again, or reword the topic to be more specific.' };
    if (has('fact_validator failed')) return { title: 'The script kept failing the fact check', body: 'Try again, or reword the topic to be more specific.' };
    if (has('pipeline did not complete')) return { title: 'The pipeline stopped before rendering', body: 'It gave up after repeated retries. Try again, or reword the topic.' };
    if (has('job not found', 'job is gone', 'server may have restarted')) return { title: 'Lost track of the job', body: 'The server may have restarted. Check the library in case it finished.' };
    const first = s.split('\n')[0];
    return { title: 'The job failed', body: first.length > 160 ? first.slice(0, 157) + '...' : first };
  }

  // ── Server metadata (defaults, narrators) ──────────────────────────────
  let metaPromise = null;
  function getMeta() {
    if (!metaPromise) {
      metaPromise = fetch('/api/meta')
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);
    }
    return metaPromise;
  }
  function narratorLabel(id, meta) {
    if (!id) return '';
    const n = meta && (meta.narrators || []).find((x) => x.id === id);
    if (n) return n.label;
    const s = String(id);
    return s === 'elevenlabs' ? 'ElevenLabs' : s === 'openai' ? 'OpenAI' : s.charAt(0).toUpperCase() + s.slice(1);
  }

  // ── Theme ──────────────────────────────────────────────────────────────
  const THEME_KEY = 'cb-theme';
  function currentTheme() {
    return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
  }
  function setTheme(t) {
    if (t === 'light') document.documentElement.dataset.theme = 'light';
    else delete document.documentElement.dataset.theme;
    try { localStorage.setItem(THEME_KEY, t); } catch {}
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.setAttribute('aria-label', t === 'light' ? 'Switch to dark theme' : 'Switch to light theme');
  }

  // ── Job store (localStorage) ───────────────────────────────────────────
  const STORAGE_KEY = 'chalkboard_jobs';
  const MAX_FINISHED = 10;
  const POLL_MS = 2500;
  const isActive = (j) => j.status === 'running' || j.status === 'pending';

  function getJobs() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || []; } catch { return []; }
  }
  function saveJobs(jobs) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(jobs)); } catch {}
  }
  function updateJob(id, patch) {
    const jobs = getJobs();
    const j = jobs.find((x) => x.id === id);
    if (!j) return;
    Object.assign(j, patch);
    saveJobs(jobs);
  }
  function removeJob(id) { saveJobs(getJobs().filter((j) => j.id !== id)); }
  function addJob(job) {
    const jobs = getJobs().filter((j) => j.id !== job.id);
    jobs.unshift(job);
    saveJobs([...jobs.filter(isActive), ...jobs.filter((j) => !isActive(j)).slice(0, MAX_FINISHED)]);
  }
  function activeJobs() { return getJobs().filter(isActive); }

  window.jobStatus = {
    set(id, topic, opts) {
      addJob({ id, topic, opts: opts || null, status: 'running', startedAt: Date.now(), currentStage: null });
      renderJobs();
      startPolling();
    },
    resolve(id, status) {
      updateJob(id, { status, completedAt: Date.now(), currentStage: null });
      renderJobs();
    },
    get() { return activeJobs()[0] || null; },
    clear() { saveJobs([]); renderJobs(); },
    // stage = the label key of the step that is in progress now
    updateStage(id, stage) { updateJob(id, { currentStage: stage }); renderJobs(); },
  };

  // ── Nav ────────────────────────────────────────────────────────────────
  function renderNav() {
    const host = document.querySelector('header.nav');
    if (!host) return;
    const page = host.dataset.page;
    const tab = (href, name, key) =>
      `<a class="nav-tab" href="${href}"${page === key ? ' aria-current="page"' : ''}>${name}</a>`;
    host.innerHTML = `
      <div class="wrap nav-inner">
        <a class="brand" href="/" aria-label="Chalkboard home">${ICONS.mark}<span class="brand-name">Chalkboard</span></a>
        <nav class="nav-tabs" aria-label="Main">
          ${tab('/', 'Generate', 'generate')}
          ${tab('/library', 'Library', 'library')}
        </nav>
        <div class="nav-spacer"></div>
        <div class="nav-actions">
          <a class="status-chip" id="claude-status" href="https://status.claude.com" target="_blank" rel="noopener"
             title="Checking Claude API status">
            <span class="dot unknown" aria-hidden="true"></span><span class="status-label">Claude API</span>
          </a>
          <button class="icon-btn theme-toggle" id="theme-toggle" type="button">${ICONS.sun}${ICONS.moon}</button>
          <button class="icon-btn" id="jobs-btn" type="button" aria-haspopup="true" aria-expanded="false"
                  aria-controls="jobs-pop" aria-label="Recent jobs">${ICONS.jobs}<span class="jobs-badge" hidden></span></button>
          <div class="popover" id="jobs-pop" hidden></div>
        </div>
      </div>`;

    setTheme(currentTheme());
    document.getElementById('theme-toggle').addEventListener('click', () =>
      setTheme(currentTheme() === 'light' ? 'dark' : 'light'));

    const btn = document.getElementById('jobs-btn');
    const pop = document.getElementById('jobs-pop');
    const close = () => { pop.hidden = true; btn.setAttribute('aria-expanded', 'false'); };
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = pop.hidden;
      pop.hidden = !open;
      btn.setAttribute('aria-expanded', String(open));
    });
    pop.addEventListener('click', (e) => {
      const dismiss = e.target.closest('[data-dismiss]');
      if (dismiss) {
        e.preventDefault();
        e.stopPropagation();
        removeJob(dismiss.dataset.dismiss);
        renderJobs();
        return;
      }
      const row = e.target.closest('a.job-row');
      if (row && row.dataset.resume) {
        // Move the job to the front so the generate page reconnects to it.
        const jobs = getJobs();
        const i = jobs.findIndex((j) => j.id === row.dataset.resume);
        if (i > 0) { jobs.unshift(jobs.splice(i, 1)[0]); saveJobs(jobs); }
      }
    });
    document.addEventListener('click', (e) => { if (!pop.hidden && !pop.contains(e.target)) close(); });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !pop.hidden) { close(); btn.focus(); }
    });
  }

  function renderJobs() {
    const pop = document.getElementById('jobs-pop');
    const badge = document.querySelector('#jobs-btn .jobs-badge');
    if (!pop) return;
    const jobs = getJobs();
    const running = jobs.filter(isActive);
    const finished = jobs.filter((j) => !isActive(j));

    let state = '';
    if (running.length) state = 'running';
    else if (finished.some((j) => j.status === 'failed')) state = 'failed';
    else if (finished.length) state = 'completed';
    badge.hidden = !state;
    badge.className = `jobs-badge dot ${state}`;

    let html = '';
    if (running.length) {
      html += '<div class="popover-head">In progress</div>';
      for (const j of running) {
        const stage = j.currentStage ? stageLabel(j.currentStage) : 'Starting';
        html += `<a class="job-row" href="/" data-resume="${esc(j.id)}" title="${esc(j.topic)}">
          <span class="dot running" aria-hidden="true"></span>
          <span class="job-row-main"><span class="job-row-topic">${esc(j.topic)}</span>
          <span class="job-row-sub">${esc(stage)}</span></span></a>`;
      }
    }
    if (finished.length) {
      html += '<div class="popover-head">Recent</div>';
      for (const j of finished) {
        const ok = j.status === 'completed';
        const when = j.completedAt ? ', ' + fmtAgo(j.completedAt) : '';
        html += `<a class="job-row" href="${ok ? '/library/' + encodeURIComponent(j.id) : '/'}"${ok ? '' : ` data-resume="${esc(j.id)}"`} title="${esc(j.topic)}">
          <span class="dot ${ok ? 'completed' : 'failed'}" aria-hidden="true"></span>
          <span class="job-row-main"><span class="job-row-topic">${esc(j.topic)}</span>
          <span class="job-row-sub">${ok ? 'Finished' : 'Failed'}${esc(when)}</span></span>
          <button class="icon-btn" type="button" data-dismiss="${esc(j.id)}" aria-label="Remove from list">${ICONS.close}</button></a>`;
      }
    }
    pop.innerHTML = html || '<div class="popover-empty">No recent jobs. Videos you generate show up here.</div>';
  }

  // Poll running jobs so the menu stays current on every page.
  let pollTimer = null;
  function startPolling() {
    if (!pollTimer) pollTimer = setTimeout(poll, POLL_MS);
  }
  async function poll() {
    pollTimer = null;
    for (const j of activeJobs()) {
      try {
        const r = await fetch(`/api/jobs/${encodeURIComponent(j.id)}`);
        if (!r.ok) {
          if (r.status === 404) { removeJob(j.id); renderJobs(); }
          continue;
        }
        const data = await r.json();
        if (data.status === 'completed' || data.status === 'failed') {
          updateJob(j.id, { status: data.status, completedAt: Date.now(), currentStage: null });
          renderJobs();
          continue;
        }
        const stage = currentStep(data.events, j.opts) || j.currentStage;
        if (stage !== j.currentStage) { updateJob(j.id, { currentStage: stage }); renderJobs(); }
      } catch { /* network blip: retry next tick */ }
    }
    if (activeJobs().length) pollTimer = setTimeout(poll, POLL_MS);
  }

  // ── Claude API status chip ─────────────────────────────────────────────
  const STATUS_LABELS = {
    operational: 'Claude API operational',
    degraded: 'Claude API degraded',
    outage: 'Claude API outage',
    unknown: 'Claude API status unknown',
  };
  const STATUS_SHORT = { operational: 'Claude API', degraded: 'Claude API degraded', outage: 'Claude API outage', unknown: 'Claude API' };

  async function fetchClaudeStatus() {
    const el = document.getElementById('claude-status');
    if (!el) return;
    try {
      const r = await fetch('/api/claude-status');
      if (!r.ok) throw new Error();
      const data = await r.json();
      const status = STATUS_LABELS[data.status] ? data.status : 'unknown';
      el.querySelector('.dot').className = `dot ${status}`;
      el.querySelector('.status-label').textContent = STATUS_SHORT[status];
      const active = (data.incidents || []).filter((i) => i.status !== 'Resolved');
      const title = active.length
        ? STATUS_LABELS[status] + '\n' + active.map((i) => `${i.status}: ${i.title}`).join('\n')
        : STATUS_LABELS[status];
      el.title = title;
      el.setAttribute('aria-label', title + '. Opens status.claude.com');
    } catch {
      el.title = STATUS_LABELS.unknown;
    }
  }

  // ── Init ───────────────────────────────────────────────────────────────
  function init() {
    renderNav();
    renderJobs();
    fetchClaudeStatus();
    setInterval(fetchClaudeStatus, 300000);
    if (activeJobs().length) startPolling();
  }

  window.CB = {
    esc, fmtDuration, fmtClock, fmtDate, fmtWhen, fmtAgo, fmtBytes, fileUrl, thumbInner,
    setMathText, hasMath, loadKatex, mathHtml,
    stageLabel, nextStage, buildSteps, upcoming, currentStep, explainError,
    getMeta, narratorLabel, resLabel, ICONS,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
