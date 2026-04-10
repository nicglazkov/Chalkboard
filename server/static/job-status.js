// job-status.js — persistent job status icon + dropdown panel across all pages
(function () {
  const STORAGE_KEY = 'chalkboard_jobs';
  const MAX_FINISHED = 10;
  const POLL_MS = 2500;
  const POLL_ERR_MS = 6000;

  const STAGE_LABELS = {
    init:              'Initializing',
    research_agent:    'Researching topic',
    script_agent:      'Writing script',
    fact_validator:    'Fact-checking',
    manim_agent:       'Generating animation code',
    code_validator:    'Validating code',
    layout_checker:    'Checking layout',
    render_trigger:    'Synthesizing voiceover',
    escalate_to_user:  'Waiting for user input',
  };

  // ── Storage ────────────────────────────────────────────────────────────────
  function getJobs() {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || []; }
    catch { return []; }
  }
  function saveJobs(jobs) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(jobs));
  }
  function findJob(id) { return getJobs().find(j => j.id === id); }
  function updateJob(id, patch) {
    const jobs = getJobs();
    const idx = jobs.findIndex(j => j.id === id);
    if (idx === -1) return;
    Object.assign(jobs[idx], patch);
    saveJobs(jobs);
  }
  function removeJob(id) {
    saveJobs(getJobs().filter(j => j.id !== id));
  }
  function addJob(job) {
    const jobs = getJobs().filter(j => j.id !== job.id);
    jobs.unshift(job);
    // Cap finished jobs
    const running = jobs.filter(j => j.status === 'running' || j.status === 'pending');
    const finished = jobs.filter(j => j.status !== 'running' && j.status !== 'pending');
    saveJobs([...running, ...finished.slice(0, MAX_FINISHED)]);
  }
  function activeJobs() {
    return getJobs().filter(j => j.status === 'running' || j.status === 'pending');
  }

  // ── Public API (used by index.html) ────────────────────────────────────────
  window.jobStatus = {
    set(id, topic) {
      addJob({ id, topic, status: 'running', startedAt: Date.now(), currentStage: null });
      render();
      startPolling();
    },
    resolve(id, status) {
      updateJob(id, { status, completedAt: Date.now(), currentStage: null });
      render();
    },
    get() {
      // Backward compat: return the first running job (used by reconnectActiveJob)
      return activeJobs()[0] || null;
    },
    clear() {
      saveJobs([]);
      render();
    },
    updateStage(id, stage) {
      updateJob(id, { currentStage: stage });
      render();
    },
  };

  // ── CSS injection (one-time) ───────────────────────────────────────────────
  function injectStyles() {
    if (document.getElementById('job-status-styles')) return;
    const style = document.createElement('style');
    style.id = 'job-status-styles';
    style.textContent = `
      /* ── Status icon ── */
      #job-status-icon {
        margin-left: auto;
        position: relative;
        width: 32px;
        height: 32px;
        border: none;
        border-radius: 6px;
        background: transparent;
        color: var(--muted);
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: color 0.15s, background 0.12s;
        flex-shrink: 0;
      }
      #job-status-icon:hover {
        color: var(--text);
        background: rgba(255,255,255,0.05);
      }
      #job-status-icon .js-badge {
        position: absolute;
        top: 4px;
        right: 4px;
        width: 8px;
        height: 8px;
        border-radius: 50%;
        background: var(--accent);
        display: none;
      }
      #job-status-icon .js-badge.active {
        display: block;
        animation: js-pulse 1.4s ease-in-out infinite;
      }
      #job-status-icon .js-badge.has-done {
        display: block;
        background: #5cba6a;
        animation: none;
      }
      #job-status-icon .js-badge.has-failed {
        display: block;
        background: #e06060;
        animation: none;
      }
      @keyframes js-pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.25; }
      }

      /* ── Dropdown ── */
      #job-status-dropdown {
        display: none;
        position: absolute;
        top: calc(100% + 6px);
        right: 0;
        width: 320px;
        max-height: 400px;
        overflow-y: auto;
        background: var(--surface, #1a1918);
        border: 1px solid var(--border, rgba(255,255,255,0.07));
        border-radius: 8px;
        box-shadow: 0 12px 32px rgba(0,0,0,0.5);
        z-index: 100;
        scrollbar-width: thin;
        scrollbar-color: var(--border, rgba(255,255,255,0.07)) transparent;
      }
      #job-status-dropdown.open { display: block; }

      .js-dropdown-header {
        font-family: 'DM Mono', monospace;
        font-size: 0.68rem;
        font-weight: 500;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: var(--muted, #7a7570);
        padding: 0.6rem 0.75rem 0.35rem;
      }

      .js-job-row {
        display: flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.5rem 0.75rem;
        cursor: pointer;
        text-decoration: none;
        color: inherit;
        transition: background 0.12s;
        border-top: 1px solid var(--border, rgba(255,255,255,0.07));
      }
      .js-job-row:first-child { border-top: none; }
      .js-job-row:hover { background: rgba(255,255,255,0.04); }

      .js-job-dot {
        width: 8px;
        height: 8px;
        border-radius: 50%;
        flex-shrink: 0;
      }
      .js-job-dot.running {
        background: var(--accent, #c8b97a);
        animation: js-pulse 1.4s ease-in-out infinite;
      }
      .js-job-dot.completed { background: #5cba6a; }
      .js-job-dot.failed { background: #e06060; }

      .js-job-info {
        flex: 1;
        min-width: 0;
      }
      .js-job-topic {
        font-family: 'Lora', Georgia, serif;
        font-size: 0.78rem;
        color: var(--chalk, #f0ebe0);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .js-job-stage {
        font-family: 'DM Mono', monospace;
        font-size: 0.65rem;
        color: var(--muted, #7a7570);
        margin-top: 0.1rem;
      }

      .js-dismiss {
        background: none;
        border: none;
        color: var(--muted, #7a7570);
        cursor: pointer;
        font-size: 1rem;
        line-height: 1;
        padding: 0.15rem;
        border-radius: 4px;
        flex-shrink: 0;
        transition: color 0.15s, background 0.12s;
      }
      .js-dismiss:hover {
        color: var(--text, #e8e4db);
        background: rgba(255,255,255,0.06);
      }

      .js-empty {
        font-family: 'DM Mono', monospace;
        font-size: 0.72rem;
        color: var(--muted, #7a7570);
        padding: 1.5rem 0.75rem;
        text-align: center;
      }
    `;
    document.head.appendChild(style);
  }

  // ── Rendering ──────────────────────────────────────────────────────────────
  let dropdownOpen = false;

  function render() {
    const container = document.getElementById('job-status');
    if (!container) return;

    const jobs = getJobs();
    const running = jobs.filter(j => j.status === 'running' || j.status === 'pending');
    const finished = jobs.filter(j => j.status !== 'running' && j.status !== 'pending');

    // Build icon + dropdown
    let badgeClass = '';
    if (running.length > 0) badgeClass = 'active';
    else if (finished.some(j => j.status === 'failed')) badgeClass = 'has-failed';
    else if (finished.length > 0) badgeClass = 'has-done';

    // SVG: simple activity/pulse icon
    const iconSvg = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"></polyline></svg>`;

    let dropdownHtml = '';

    if (running.length > 0) {
      dropdownHtml += `<div class="js-dropdown-header">In progress</div>`;
      running.forEach(j => {
        const stage = j.currentStage ? (STAGE_LABELS[j.currentStage] || j.currentStage) : 'Starting…';
        dropdownHtml += `
          <div class="js-job-row" onclick="window.jobStatus._navigate('${j.id}', 'running')" title="${esc(j.topic)}">
            <span class="js-job-dot running"></span>
            <div class="js-job-info">
              <div class="js-job-topic">${esc(j.topic)}</div>
              <div class="js-job-stage">${esc(stage)}</div>
            </div>
          </div>`;
      });
    }

    if (finished.length > 0) {
      dropdownHtml += `<div class="js-dropdown-header">Recent</div>`;
      finished.forEach(j => {
        const label = j.status === 'completed' ? 'Completed' : 'Failed';
        const timeStr = j.completedAt ? formatTimeAgo(j.completedAt) : '';
        dropdownHtml += `
          <div class="js-job-row" onclick="window.jobStatus._navigate('${j.id}', '${j.status}')" title="${esc(j.topic)}">
            <span class="js-job-dot ${j.status}"></span>
            <div class="js-job-info">
              <div class="js-job-topic">${esc(j.topic)}</div>
              <div class="js-job-stage">${esc(label)}${timeStr ? ' · ' + esc(timeStr) : ''}</div>
            </div>
            <button class="js-dismiss" onclick="window.jobStatus._dismiss(event, '${j.id}')" aria-label="Dismiss">×</button>
          </div>`;
      });
    }

    if (jobs.length === 0) {
      dropdownHtml = `<div class="js-empty">No recent jobs</div>`;
    }

    container.innerHTML = `
      <button id="job-status-icon" aria-label="Job status">
        ${iconSvg}
        <span class="js-badge ${badgeClass}"></span>
      </button>
      <div id="job-status-dropdown" class="${dropdownOpen ? 'open' : ''}">
        ${dropdownHtml}
      </div>`;

    // Bind icon click
    const icon = document.getElementById('job-status-icon');
    icon.addEventListener('click', (e) => {
      e.stopPropagation();
      dropdownOpen = !dropdownOpen;
      document.getElementById('job-status-dropdown').classList.toggle('open', dropdownOpen);
    });
  }

  // ── Navigation ─────────────────────────────────────────────────────────────
  window.jobStatus._navigate = function (id, status) {
    dropdownOpen = false;
    if (status === 'completed') {
      window.location.href = `/library/${id}`;
    } else {
      // running or failed — go to generate page, which will reconnect via reconnectActiveJob
      // Make sure this job is the "first" in the list so .get() returns it
      const jobs = getJobs();
      const idx = jobs.findIndex(j => j.id === id);
      if (idx > 0) {
        const [job] = jobs.splice(idx, 1);
        jobs.unshift(job);
        saveJobs(jobs);
      }
      window.location.href = '/';
    }
  };

  window.jobStatus._dismiss = function (e, id) {
    e.stopPropagation();
    removeJob(id);
    render();
  };

  // ── Helpers ────────────────────────────────────────────────────────────────
  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatTimeAgo(ts) {
    const sec = Math.floor((Date.now() - ts) / 1000);
    if (sec < 60) return 'just now';
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const d = Math.floor(hr / 24);
    return `${d}d ago`;
  }

  // ── Polling ────────────────────────────────────────────────────────────────
  let pollTimer = null;

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setTimeout(poll, POLL_MS);
  }

  async function poll() {
    pollTimer = null;
    const running = activeJobs();
    if (running.length === 0) return;

    for (const j of running) {
      try {
        const r = await fetch(`/api/jobs/${j.id}`);
        if (!r.ok) {
          if (r.status === 404) { removeJob(j.id); render(); }
          continue;
        }
        const data = await r.json();

        if (data.status === 'completed' || data.status === 'failed') {
          updateJob(j.id, { status: data.status, completedAt: Date.now(), currentStage: null });
          render();
          continue;
        }

        // Update current stage from latest event
        const events = data.events || [];
        const last = events.length ? events[events.length - 1] : null;
        const stage = last?.node || j.currentStage;
        if (stage !== j.currentStage) {
          updateJob(j.id, { currentStage: stage });
          render();
        }
      } catch {
        // network error — will retry next poll
      }
    }

    if (activeJobs().length > 0) {
      pollTimer = setTimeout(poll, POLL_MS);
    }
  }

  // ── Close dropdown on outside click ────────────────────────────────────────
  document.addEventListener('click', () => {
    if (dropdownOpen) {
      dropdownOpen = false;
      const dd = document.getElementById('job-status-dropdown');
      if (dd) dd.classList.remove('open');
    }
  });

  // ── Init ───────────────────────────────────────────────────────────────────
  function init() {
    injectStyles();
    render();
    if (activeJobs().length > 0) startPolling();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
