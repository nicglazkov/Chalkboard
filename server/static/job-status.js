// job-status.js — persistent job status pill shown in the nav across all pages
(function () {
  const KEY = 'chalkboard_active_job';
  const POLL_INTERVAL_RUNNING = 2000;
  const POLL_INTERVAL_ERROR   = 6000;

  const STAGE_LABELS = {
    init:            'Initializing',
    research_agent:  'Researching topic',
    script_agent:    'Writing script',
    fact_validator:  'Fact-checking',
    manim_agent:     'Generating animation',
    code_validator:  'Validating code',
    render_trigger:  'Synthesizing voiceover',
    render:          'Rendering',
    qa:              'Visual QA',
  };

  // ── Storage helpers ──────────────────────────────────────────────────────
  function getJob() {
    try { return JSON.parse(localStorage.getItem(KEY)); } catch { return null; }
  }
  function saveJob(data) {
    if (data === null) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, JSON.stringify(data));
  }

  // ── Public API (used by index.html) ──────────────────────────────────────
  window.jobStatus = {
    set: function (id, topic) {
      saveJob({ id, topic, status: 'running', startedAt: Date.now(), currentStage: null });
    },
    resolve: function (id, status) {
      // Called by index.html when finalizeJob runs — clear immediately
      const j = getJob();
      if (j && j.id === id) saveJob(null);
      renderPill();
    },
    clear: function () {
      saveJob(null);
      renderPill();
    },
    get: getJob,
  };

  // ── Pill rendering ───────────────────────────────────────────────────────
  function renderPill() {
    const pill  = document.getElementById('job-pill');
    if (!pill) return;

    const dot   = pill.querySelector('.job-pill-dot');
    const label = pill.querySelector('.job-pill-label');

    const j = getJob();
    if (!j) { pill.style.display = 'none'; return; }

    pill.style.display = 'flex';
    dot.className   = 'job-pill-dot running';
    label.href      = '/';

    const stageLabel = j.currentStage ? (STAGE_LABELS[j.currentStage] || j.currentStage) : null;
    label.textContent = stageLabel ? `${stageLabel}…` : 'Generating…';
  }

  // ── Polling ──────────────────────────────────────────────────────────────
  let _pollTimer = null;

  async function poll() {
    const j = getJob();
    if (!j || (j.status !== 'running' && j.status !== 'pending')) return;

    try {
      const r = await fetch(`/api/jobs/${j.id}`);
      if (!r.ok) {
        if (r.status === 404) {
          // Job gone (server restarted) — clear pill
          saveJob(null);
          renderPill();
        } else {
          _pollTimer = setTimeout(poll, POLL_INTERVAL_ERROR);
        }
        return;
      }
      const data = await r.json();
      const current = getJob();
      if (!current || current.id !== j.id) return; // dismissed while polling

      if (data.status === 'completed' || data.status === 'failed') {
        // Job is done — clear the pill immediately
        saveJob(null);
        renderPill();
        return; // stop polling
      }

      // Find the most recent active stage from events
      const events = data.events || [];
      const lastEvent = events.length ? events[events.length - 1] : null;
      const currentStage = lastEvent?.node || current.currentStage || null;

      saveJob({ ...current, status: data.status, currentStage });
      renderPill();
      _pollTimer = setTimeout(poll, POLL_INTERVAL_RUNNING);
    } catch {
      _pollTimer = setTimeout(poll, POLL_INTERVAL_ERROR);
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    const pill = document.getElementById('job-pill');
    if (!pill) return;

    pill.querySelector('.job-pill-dismiss')?.addEventListener('click', (e) => {
      e.preventDefault();
      saveJob(null);
      pill.style.display = 'none';
      clearTimeout(_pollTimer);
    });

    renderPill();

    const j = getJob();
    if (j && (j.status === 'running' || j.status === 'pending')) {
      _pollTimer = setTimeout(poll, POLL_INTERVAL_RUNNING);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
