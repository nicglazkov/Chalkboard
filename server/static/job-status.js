// job-status.js — persistent job status pill shown in the nav across all pages
(function () {
  const KEY = 'chalkboard_active_job';
  const COMPLETED_TTL = 5 * 60 * 1000; // hide pill 5 min after completion
  const POLL_INTERVAL_RUNNING = 2000;
  const POLL_INTERVAL_ERROR   = 6000;

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
      saveJob({ id, topic, status: 'running', startedAt: Date.now(), title: '', completedAt: null });
    },
    resolve: function (id, status, title) {
      const j = getJob();
      if (j && j.id === id) {
        saveJob({ ...j, status, title: title || j.title || '', completedAt: Date.now() });
      }
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

    // Expired completed/failed job — clean up and hide
    if (j.status !== 'running' && j.status !== 'pending') {
      if (!j.completedAt || Date.now() - j.completedAt > COMPLETED_TTL) {
        saveJob(null);
        pill.style.display = 'none';
        return;
      }
    }

    pill.style.display = 'flex';

    if (j.status === 'completed') {
      dot.className   = 'job-pill-dot done';
      label.textContent = (j.title || j.topic || 'Video') + ' — Ready';
      label.href      = `/library/${j.id}`;
    } else if (j.status === 'failed') {
      dot.className   = 'job-pill-dot failed';
      label.textContent = 'Generation failed';
      label.href      = '/';
    } else {
      dot.className   = 'job-pill-dot running';
      label.textContent = j.topic ? `Generating "${j.topic}"` : 'Generating…';
      label.href      = '/';
    }
  }

  // ── Polling ──────────────────────────────────────────────────────────────
  let _pollTimer = null;

  async function poll() {
    const j = getJob();
    if (!j || (j.status !== 'running' && j.status !== 'pending')) return;

    try {
      const r = await fetch(`/api/jobs/${j.id}`);
      if (!r.ok) {
        // Job gone from server (e.g. server restarted) — keep polling in case
        // it was just a blip, but slow down
        _pollTimer = setTimeout(poll, POLL_INTERVAL_ERROR);
        return;
      }
      const data = await r.json();
      const current = getJob();
      if (!current || current.id !== j.id) return; // dismissed while polling

      if (data.status === 'completed' || data.status === 'failed') {
        let title = current.title || '';
        if (data.status === 'completed' && !title) {
          // Try to grab the AI title from the library endpoint
          try {
            const lr = await fetch(`/api/library/${j.id}`);
            if (lr.ok) { const meta = await lr.json(); title = meta.title || ''; }
          } catch { /* ignore */ }
        }
        saveJob({ ...current, status: data.status, title, completedAt: Date.now() });
        renderPill();
        return; // stop polling
      }

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
