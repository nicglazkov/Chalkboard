// app.js: shared helpers, the sidebar (nav counts + render box card from /api/status),
// the workbench station model built from real job events, and the timeline renderer
// used by the progress and video pages. Plain script, no build step. Loaded in <head>
// so inline page scripts can use window.CB.
//
// Honesty rule: every number on screen comes from an API response. Missing values render
// as "Unknown" (with the reason on hover), never as a guess.
(function () {
  'use strict';

  // ── Formatting ─────────────────────────────────────────────────────────
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function fmtClock(sec) {
    if (sec == null || !isFinite(sec)) return '';
    sec = Math.max(0, sec);
    const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
  }
  function fmtDuration(sec) { return sec && sec >= 1 ? fmtClock(sec) : ''; }
  // "6m 40s" style for typical times
  function fmtSpan(sec) {
    if (sec == null || !isFinite(sec)) return '';
    sec = Math.round(sec);
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60), s = sec % 60;
    if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
    return `${Math.floor(m / 60)}h ${m % 60}m`;
  }
  function parseTs(iso) { const t = Date.parse(iso); return isNaN(t) ? null : t; }
  function fmtAgo(ms) {
    if (ms == null) return '';
    const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
    if (sec < 45) return `${sec}s ago`;
    const min = Math.round(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.round(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const d = Math.round(hr / 24);
    if (d < 14) return `${d}d ago`;
    return new Date(ms).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: new Date(ms).getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
  }
  function fmtStamp(iso) {
    const t = parseTs(iso);
    if (t == null) return '';
    const d = new Date(t);
    const today = new Date().toDateString() === d.toDateString();
    const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    return today ? `today ${time}` : `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })} ${time}`;
  }
  function fmtBytes(n) {
    if (n == null || !isFinite(n)) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1024 ** 3) return (n / (1024 * 1024)).toFixed(1) + ' MB';
    return (n / 1024 ** 3).toFixed(1) + ' GB';
  }
  function fmtInt(n) { return n == null ? '' : Number(n).toLocaleString(); }
  function fmtUsd(n) { return n == null ? '' : n < 0.01 && n > 0 ? '<$0.01' : '$' + Number(n).toFixed(2); }
  function fmtSec(n, digits) { return n == null ? '' : `${Number(n).toFixed(digits == null ? 2 : digits)} s`; }
  const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1) : '');
  function unknownHtml(why, text) { return `<span class="unknown" title="${esc(why)}">${esc(text || 'Unknown')}</span>`; }

  const RES = { low: '480p', medium: '720p', high: '1080p', '4k': '4K' };
  const RES_FULL = { low: '480p15', medium: '720p30', high: '1080p60', '4k': '4K60' };
  function resLabel(q) { return RES[q] || (q ? String(q) : ''); }
  function resFull(q) { return RES_FULL[q] || resLabel(q); }

  function fileUrl(runId, name) {
    return `/api/jobs/${encodeURIComponent(runId)}/files/${encodeURIComponent(name)}`;
  }

  // GET JSON with the status kept, so callers can tell "endpoint missing" from "value null".
  async function getJSON(url, opts) {
    try {
      const r = await fetch(url, opts);
      let data = null;
      const text = await r.text();
      try { data = text ? JSON.parse(text) : null; } catch { data = null; }
      if (!r.ok) {
        const detail = data && data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : text.slice(0, 200);
        return { ok: false, status: r.status, data, error: detail || `HTTP ${r.status}` };
      }
      return { ok: true, status: r.status, data };
    } catch (e) {
      return { ok: false, status: 0, data: null, error: 'The server did not respond.' };
    }
  }
  function missingWhy(res, what) {
    if (!res) return `${what} has not loaded.`;
    if (res.status === 404) return `${what} is not available on this server yet.`;
    if (res.status === 0) return `Could not reach the server for ${what}.`;
    return `${what} failed: ${res.error || 'HTTP ' + res.status}`;
  }

  // ── Thumbnails ─────────────────────────────────────────────────────────
  function boardHtml(text) { return `<div class="thumb-board"><span>${esc(text)}</span></div>`; }
  function thumbInner(v) {
    const label = v.title || v.topic || '';
    if (!v.thumb_path) return boardHtml(label);
    return `<img src="${fileUrl(v.run_id, 'thumb.jpg')}" alt="" loading="lazy" decoding="async" data-fallback="${esc(label)}">`;
  }
  document.addEventListener('error', (e) => {
    const img = e.target;
    if (img && img.tagName === 'IMG' && img.dataset.fallback != null) img.outerHTML = boardHtml(img.dataset.fallback);
  }, true);

  // ── Math: $...$ typeset with KaTeX from cdnjs, loaded only when needed ─
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
  function hasMath(text) { MATH_RE.lastIndex = 0; return MATH_RE.test(String(text || '')); }
  function mathHtml(text) {
    const katex = window.katex;
    const src = String(text || '');
    if (!katex) return esc(src);
    let out = '', last = 0, m;
    MATH_RE.lastIndex = 0;
    while ((m = MATH_RE.exec(src))) {
      out += esc(src.slice(last, m.index));
      const display = m[1] != null;
      try { out += katex.renderToString(display ? m[1] : m[2], { displayMode: display, throwOnError: false, output: 'html' }); }
      catch { out += esc(m[0]); }
      last = m.index + m[0].length;
    }
    return out + esc(src.slice(last));
  }
  function setMathText(el, text) {
    el.textContent = text;
    if (!hasMath(text)) return;
    loadKatex().then(() => { el.innerHTML = mathHtml(text); }).catch(() => {});
  }

  // ── Errors: a short human line first, the raw detail on request ─────────
  function explainError(raw) {
    const s = String(raw || '');
    const low = s.toLowerCase();
    const has = (...xs) => xs.some((x) => low.includes(x));
    if (!s.trim()) return { title: 'The job failed', body: 'No error message was recorded. The server log has the details.' };
    if (has('cancel')) return { title: 'The run was cancelled', body: 'Nothing else will run for this job.' };
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
  function errorCard(raw, fallbackTitle) {
    const info = explainError(raw);
    const showRaw = raw && String(raw).trim() && String(raw).trim() !== info.body;
    return `<div class="err" role="alert"><div class="err-title">${esc(info.title || fallbackTitle || 'Something failed')}</div>
      <p class="muted">${esc(info.body)}</p>
      ${showRaw ? `<details><summary>Show the error</summary><pre>${esc(String(raw).trim())}</pre></details>` : ''}</div>`;
  }

  // ── Narrators ──────────────────────────────────────────────────────────
  let voicesPromise = null;
  // Voices from /api/voices, else the narrator list in /api/meta (no samples there).
  function getVoices() {
    if (!voicesPromise) {
      voicesPromise = (async () => {
        const r = await getJSON('/api/voices');
        if (r.ok && Array.isArray(r.data)) return { source: 'voices', list: r.data };
        const m = await getMeta();
        if (m && Array.isArray(m.narrators)) return { source: 'meta', list: m.narrators.map((n) => ({ ...n, available: n.available != null ? n.available : (n.configured === false ? false : null), availability_reason: n.configured === false ? 'No API key configured on the server' : null, sample_url: null })), why: missingWhy(r, 'Voice samples') };
        return { source: 'none', list: [], why: missingWhy(r, 'The voice list') };
      })();
    }
    return voicesPromise;
  }
  let metaPromise = null;
  function getMeta() {
    if (!metaPromise) metaPromise = getJSON('/api/meta').then((r) => (r.ok ? r.data : null));
    return metaPromise;
  }
  function narratorName(id, list) {
    if (!id) return '';
    const n = (list || []).find((x) => x.id === id);
    if (n) return n.label;
    const s = String(id);
    return s === 'elevenlabs' ? 'ElevenLabs' : s === 'openai' ? 'OpenAI' : cap(s);
  }

  // ── Workbench stations, built only from real job events ────────────────
  // Pipeline nodes emit one event when they FINISH. render / visual_qa / quiz are
  // reported as pseudo-nodes with {status: running|done|failed}. tts / render progress,
  // usage and peek events carry data but do not move the pipeline on their own.
  const STATIONS = [
    { id: 'research', name: 'Research', nodes: ['research_agent'] },
    { id: 'script', name: 'Script', nodes: ['script_agent'] },
    { id: 'fact', name: 'Fact-check', nodes: ['fact_validator'] },
    { id: 'anim', name: 'Animator', nodes: ['manim_agent', 'code_validator'] },
    { id: 'layout', name: 'Layout dry-run', nodes: ['layout_checker'] },
    { id: 'render', name: 'Narrate & render', nodes: ['render_trigger', 'render'] },
    { id: 'qa', name: 'Visual QA', nodes: ['visual_qa'] },
    { id: 'quiz', name: 'Quiz', nodes: ['quiz'] },
  ];
  const NODE_STATION = {};
  STATIONS.forEach((s) => s.nodes.forEach((n) => { NODE_STATION[n] = s.id; }));
  const PSEUDO = new Set(['render', 'visual_qa', 'quiz']);
  const AGENT_STATION = [
    [/research/i, 'research'], [/fact/i, 'fact'], [/script/i, 'script'], [/manim|code|anim/i, 'anim'],
    [/layout/i, 'layout'], [/visual|qa/i, 'qa'], [/quiz/i, 'quiz'],
  ];
  function stationForAgent(agent) {
    for (const [re, id] of AGENT_STATION) if (re.test(agent || '')) return id;
    return null;
  }
  function shortModel(m) { return String(m || '').replace(/^claude-/, '').replace(/-\d{8}$/, '').replace(/-(\d+)-(\d+)$/, ' $1.$2').replace(/-/g, ' '); }

  function nextNode(node, u, job) {
    switch (node) {
      case 'init': return u.effort_level === 'high' || (job && job.effort === 'high') ? 'research_agent' : 'script_agent';
      case 'research_agent': return 'script_agent';
      case 'script_agent': return 'fact_validator';
      case 'fact_validator': return u.fact_feedback ? 'script_agent' : 'manim_agent';
      case 'manim_agent': return 'code_validator';
      case 'code_validator': return u.code_feedback ? 'manim_agent' : 'layout_checker';
      case 'layout_checker': return u.code_feedback ? 'manim_agent' : 'render_trigger';
      case 'render_trigger': return 'render';
      default: return null;
    }
  }

  // job: {status, events, effort, qa_density, quiz, narrator, error}
  // Returns {stations:[{id,name,state,seconds,detail,badge}], active, startTs, lastTs, peek, usage, render, tts}
  function buildWorkbench(job, now) {
    now = now || Date.now();
    const events = (job.events || []).filter((e) => e && !e.done);
    const st = {};
    STATIONS.forEach((s) => { st[s.id] = { ...s, state: 'queued', seconds: 0, detail: '', badge: '', seen: false, runs: 0 }; });
    const usage = { calls: 0, input_tokens: 0, output_tokens: 0, web_searches: 0, cost_usd: 0, costKnown: true, models: {} };
    let cur = null, curStart = null, startTs = null, lastTs = null, peek = null, render = null, tts = null, failedNode = null, qaRender = null;
    const add = (id, a, b) => { if (id && a != null && b != null && b > a) st[id].seconds += (b - a) / 1000; };

    for (const ev of events) {
      const node = ev.node;
      const u = ev.updates || {};
      const ts = parseTs(ev.ts);
      if (ts != null) { if (startTs == null) startTs = ts; lastTs = ts; }
      if (node === 'peek') { peek = { stage: u.stage, text: u.text || '', done: !!u.done, ts }; continue; }
      if (node === 'usage') {
        usage.calls++;
        usage.input_tokens += u.input_tokens || 0;
        usage.output_tokens += u.output_tokens || 0;
        usage.web_searches += u.web_searches || 0;
        if (u.cost_usd == null) usage.costKnown = false; else usage.cost_usd += u.cost_usd;
        const sid = stationForAgent(u.agent);
        if (sid && u.model) {
          const m = (usage.models[sid] = usage.models[sid] || {});
          m[u.model] = (m[u.model] || 0) + 1;
        }
        continue;
      }
      if (node === 'tts') { tts = { ...u, ts }; continue; }
      // Visual QA re-renders report render events inside the QA phase: detail only.
      if (node === 'render' && cur === 'visual_qa') { qaRender = { ...u, ts }; continue; }
      if (node === 'render' && u.status === 'running' && (u.segment != null || u.animation != null || u.segments != null)) {
        render = { ...u, ts };
        if (cur !== 'render') { add(NODE_STATION[cur], curStart, ts); cur = 'render'; curStart = ts; }
        continue;
      }
      if (node === 'init') { cur = nextNode('init', u, job); curStart = ts; continue; }
      if (node === 'escalate_to_user') { failedNode = cur || 'manim_agent'; add(NODE_STATION[cur], curStart, ts); cur = null; continue; }
      const sid = NODE_STATION[node];
      if (!sid) continue;
      const s = st[sid];
      s.seen = true;
      if (PSEUDO.has(node)) {
        if (u.status === 'running') {
          if (cur && cur !== node) add(NODE_STATION[cur], curStart, ts);
          if (cur !== node) { cur = node; curStart = ts; }
          s.runs++;
          continue;
        }
        add(sid, curStart != null && cur === node ? curStart : null, ts);
        if (u.status === 'failed') { failedNode = node; }
        else s.state = 'done';
        cur = null; curStart = ts;
        continue;
      }
      // A pipeline node finished: the time since the previous transition was this node's.
      add(sid, curStart, ts);
      s.runs++;
      s.state = 'done';
      describe(s, node, u);
      cur = nextNode(node, u, job);
      curStart = ts;
    }

    const running = job.status === 'running' || job.status === 'pending';
    if (job.status === 'failed' && !failedNode) failedNode = cur;
    if (running && cur && NODE_STATION[cur]) {
      const s = st[NODE_STATION[cur]];
      s.state = 'active';
      s.seen = true;
      if (curStart != null) s.seconds += (now - curStart) / 1000;
    }
    if (failedNode && NODE_STATION[failedNode]) st[NODE_STATION[failedNode]].state = 'failed';

    // Live details for the render station
    const rs = st.render;
    const parts = [];
    if (tts) {
      if (tts.status === 'failed') parts.push('narration failed');
      else if (tts.status === 'done') parts.push(`${tts.segments != null ? tts.segments + ' segments narrated' : 'narrated'}`);
      else if (tts.segments_done != null && tts.segments) parts.push(`narrating ${tts.segments_done} of ${tts.segments}`);
    }
    if (render && rs.state !== 'done') {
      if (render.segment != null && render.segments) parts.push(`scene ${render.segment + 1} of ${render.segments}`);
      else if (render.animation != null) parts.push(`animation ${render.animation}${render.animations ? ' of ' + render.animations : ''}`);
    }
    if (rs.state === 'done') parts.push('rendered');
    if (job.quality) parts.push(resFull(job.quality));
    rs.detail = parts.join(' · ');
    if (job.narrator) rs.badge = String(job.narrator);
    if (st.qa.state === 'done') st.qa.detail = 'Frame check finished';
    else if (st.qa.state === 'active') st.qa.detail = qaRender && qaRender.status === 'running' && qaRender.segment != null && qaRender.segments
      ? `Re-rendering after a fix · scene ${qaRender.segment + 1} of ${qaRender.segments}` : (qaRender ? 'Re-rendering after a fix' : 'Checking rendered frames');
    if (st.quiz.state === 'active') st.quiz.detail = 'Writing questions';

    // Model badges from usage events
    for (const [sid, models] of Object.entries(usage.models)) {
      const names = Object.keys(models);
      const calls = names.reduce((a, k) => a + models[k], 0);
      st[sid].badge = names.map(shortModel).join(', ') + (calls > 1 ? ` · ${calls} calls` : '');
    }

    // Which stations belong to this run
    const include = (s) => {
      if (s.seen) return true;
      if (s.id === 'research') return job.effort === 'high';
      if (s.id === 'qa') return job.qa_density ? job.qa_density !== 'zero' : false;
      if (s.id === 'quiz') return !!job.quiz;
      return true;
    };
    let stations = STATIONS.map((s) => st[s.id]).filter(include);
    if (!running) stations = stations.map((s) => (s.state === 'queued' ? { ...s, state: job.status === 'completed' ? 'skipped' : 'queued' } : s));
    const active = stations.find((s) => s.state === 'active') || null;
    return { stations, active, startTs, lastTs, peek, usage, render, tts };
  }
  function describe(s, node, u) {
    if (node === 'research_agent') {
      const n = Array.isArray(u.research_sources) ? u.research_sources.length : null;
      s.detail = n != null ? `${n} source${n === 1 ? '' : 's'} read` : 'Brief written';
    } else if (node === 'script_agent') {
      const segs = Array.isArray(u.script_segments) ? u.script_segments : null;
      if (segs) {
        const cues = segs.reduce((a, x) => a + ((x && (x.cues || x.cue_count)) ? (Array.isArray(x.cues) ? x.cues.length : x.cue_count) : 0), 0);
        s.detail = `${segs.length} segments${cues ? `, ${cues} sync cues` : ''}`;
      } else s.detail = 'Script written';
      if (s.runs > 1) s.detail += ` · draft ${s.runs}`;
    } else if (node === 'fact_validator') {
      s.detail = u.fact_feedback ? `Sent back for changes (round ${s.runs})` : 'Approved';
    } else if (node === 'manim_agent') {
      s.detail = `Scene code written · try ${s.runs}`;
    } else if (node === 'code_validator') {
      s.detail = u.code_feedback ? 'Code failed a check, rewriting' : 'Code passed its checks';
    } else if (node === 'layout_checker') {
      s.detail = u.code_feedback ? 'Layout problem found, rewriting' : 'Layout checked';
    } else if (node === 'render_trigger') {
      s.detail = 'Voiceover recorded';
    }
  }

  // ── Timeline ───────────────────────────────────────────────────────────
  // tl: {duration_s, segments:[{index,start_s,duration_s,label,cues}], waveform, rendered_segments}
  // opts: {mode: 'progress'|'video', renderedSegments, currentSegment, renderDone, lateCues:Set("seg:cue"), onSeek(sec)}
  function renderTimeline(host, tl, opts) {
    opts = opts || {};
    const segs = (tl && Array.isArray(tl.segments)) ? tl.segments : [];
    if (!segs.length) {
      host.innerHTML = `<div class="tl-note">${esc(opts.emptyText || 'No segments yet.')}</div>`;
      return null;
    }
    const timed = segs.every((s) => s.duration_s != null && s.start_s != null);
    const total = tl.duration_s != null ? tl.duration_s : (timed ? segs.reduce((a, s) => Math.max(a, s.start_s + s.duration_s), 0) : null);
    const rendered = opts.renderDone ? segs.length : (opts.renderedSegments != null ? opts.renderedSegments : (tl.rendered_segments != null ? tl.rendered_segments : 0));
    const curIdx = opts.currentSegment;
    const sceneName = opts.mode === 'video' ? 'Chapters' : 'Scenes';
    const blocks = segs.map((s, i) => {
      const w = timed && s.duration_s > 0 ? s.duration_s : 1;
      let cls = 'tl-seg';
      if (opts.mode === 'progress') {
        if (i < rendered) cls += ' done';
        else if (curIdx != null && i === curIdx) cls += ' now';
      }
      const label = `${i + 1}${s.label ? ' · ' + s.label : ''}`;
      const tip = `${label}${timed ? ` · ${fmtClock(s.start_s)} to ${fmtClock(s.start_s + s.duration_s)}` : ''}`;
      return `<div class="${cls}" data-i="${i}" style="flex:${w}" title="${esc(tip)}">${esc(label)}</div>`;
    }).join('');
    let wave;
    if (Array.isArray(tl.waveform) && tl.waveform.length) {
      const n = tl.waveform.length;
      const bars = tl.waveform.map((v, i) => {
        const h = Math.max(2, Math.min(100, (Number(v) || 0) * 100));
        return `<rect x="${i}" y="${((100 - h) / 2).toFixed(1)}" width="0.72" height="${h.toFixed(1)}"/>`;
      }).join('');
      wave = `<svg class="tl-wave" viewBox="0 0 ${n} 100" preserveAspectRatio="none" aria-hidden="true">${bars}</svg>`;
    } else {
      wave = `<span class="tl-none">${opts.mode === 'progress' ? 'Waveform appears after narration' : 'No waveform recorded'}</span>`;
    }
    let cues = '';
    let cueCount = 0;
    if (timed && total) {
      for (const s of segs) {
        (s.cues || []).forEach((c, k) => {
          if (c == null) return;
          cueCount++;
          const at = ((s.start_s + c) / total) * 100;
          const late = opts.lateCues && opts.lateCues.has(`${s.index}:${k}`);
          cues += `<span class="tl-cue${late ? ' late' : ''}" style="left:${at.toFixed(3)}%" title="Segment ${s.index != null ? s.index + 1 : ''} cue ${k + 1} at ${fmtClock(s.start_s + c)}"></span>`;
        });
      }
    } else {
      cueCount = segs.reduce((a, s) => a + ((s.cues || []).length), 0);
    }
    const cuesLane = timed && total ? cues : `<span class="tl-none">${opts.mode === 'progress' ? 'Cue times come from narration' : 'Not recorded'}</span>`;
    host.classList.toggle('seekable', !!opts.onSeek && timed && !!total);
    host.innerHTML = `
      <div class="tl-track"><span class="tl-name">${sceneName}</span><div class="tl-lane">${blocks}</div></div>
      <div class="tl-track"><span class="tl-name">${opts.mode === 'video' ? 'Voice' : 'Narration'}</span><div class="tl-lane wave-lane">${wave}</div></div>
      <div class="tl-track"><span class="tl-name">Cues</span><div class="tl-lane cues">${cuesLane}</div></div>
      ${opts.onSeek && total ? '<div class="tl-headwrap"><div class="tl-head" style="left:0%"></div></div>' : ''}`;
    if (opts.onSeek && timed && total) {
      host.querySelectorAll('.tl-lane').forEach((lane) => lane.addEventListener('click', (e) => {
        const r = lane.getBoundingClientRect();
        opts.onSeek(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * total);
      }));
    }
    const head = host.querySelector('.tl-head');
    return {
      total, timed, segments: segs.length, cues: cueCount,
      setTime(t) {
        if (head && total) head.style.left = `${Math.max(0, Math.min(100, (t / total) * 100))}%`;
        if (opts.mode === 'video' && timed) {
          host.querySelectorAll('.tl-seg').forEach((el, i) => {
            const s = segs[i];
            el.classList.toggle('cur', t >= s.start_s && t < s.start_s + s.duration_s);
          });
        }
      },
    };
  }

  // ── Sidebar ────────────────────────────────────────────────────────────
  // One markup for three shapes (full sidebar, icon rail, top bar + menu); CSS picks
  // the shape. The script only opens and closes the narrow-screen menu.
  const STATE_WORD = { ok: 'ok', warn: 'warning', down: 'down', unknown: 'unknown', degraded: 'degraded' };
  const ICON = {
    generate: '<path d="M12 5v14M5 12h14"/>',
    library: '<rect x="3.5" y="4.5" width="7" height="6" rx="1"/><rect x="13.5" y="4.5" width="7" height="6" rx="1"/><rect x="3.5" y="13.5" width="7" height="6" rx="1"/><rect x="13.5" y="13.5" width="7" height="6" rx="1"/>',
    progress: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
    status: '<path d="M3 12h4l2.5-6 5 12 2.5-6H21"/>',
    bars: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    x: '<path d="M6 6l12 12M18 6L6 18"/>',
  };
  const icon = (k, cls) => `<svg class="ico${cls ? ' ' + cls : ''}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ICON[k]}</svg>`;
  function renderShell() {
    const side = document.querySelector('nav.side');
    if (!side) return;
    const page = side.dataset.page;
    const cur = (k) => (page === k ? ' aria-current="page"' : '');
    side.innerHTML = `
      <div class="side-top">
        <a class="brand" href="/library" aria-label="Chalkboard library"><span class="brand-mark" aria-hidden="true">C</span><span class="brand-name">Chalkboard</span></a>
        <a class="btn-gen" href="/" title="Generate video"${cur('generate')}>${icon('generate')}<span class="nl">Generate video</span></a>
        <button type="button" class="menu-btn" aria-expanded="false" aria-controls="side-menu" aria-label="Menu">${icon('bars', 'ico-bars')}${icon('x', 'ico-x')}</button>
      </div>
      <div class="side-menu" id="side-menu">
        <div class="nav-links">
          <a class="nav-link" href="/library" title="Library"${cur('library')}>${icon('library')}<span class="nl">Library</span><span class="count" id="nav-lib"></span></a>
          <a class="nav-link" href="/progress/" title="In progress"${cur('progress')}>${icon('progress')}<span class="nl">In progress</span><span class="count" id="nav-run"></span></a>
          <a class="nav-link" href="/status/" title="Status"${cur('status')}>${icon('status')}<span class="nl">Status</span><span class="count" id="nav-status"></span></a>
        </div>
        <a class="box-card" href="/status/" id="box-card" aria-label="Render box status">
          <span class="label">Render box</span>
          <span class="box-line"><span class="dot unknown"></span><span class="faint">Checking</span></span>
        </a>
      </div>`;
    wireMenu(side);
    refreshCounts();
    refreshBox();
    setInterval(refreshCounts, 15000);
    setInterval(refreshBox, 60000);
  }
  function wireMenu(side) {
    const btn = side.querySelector('.menu-btn');
    const menu = side.querySelector('.side-menu');
    const narrow = window.matchMedia('(max-width: 860px)');
    const isOpen = () => side.classList.contains('open');
    function setOpen(open, focusBack) {
      side.classList.toggle('open', open);
      btn.setAttribute('aria-expanded', String(open));
      if (open) { const first = menu.querySelector('a'); if (first) first.focus(); }
      else if (focusBack) btn.focus();
    }
    btn.addEventListener('click', () => setOpen(!isOpen(), false));
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && isOpen()) { e.preventDefault(); setOpen(false, true); }
    });
    document.addEventListener('pointerdown', (e) => { if (isOpen() && !side.contains(e.target)) setOpen(false, false); });
    side.addEventListener('focusout', (e) => { if (isOpen() && e.relatedTarget && !side.contains(e.relatedTarget)) setOpen(false, false); });
    const onChange = () => { if (!narrow.matches && isOpen()) setOpen(false, false); };
    if (narrow.addEventListener) narrow.addEventListener('change', onChange); else narrow.addListener(onChange);
  }
  let jobsCache = null;
  async function getJobs(force) {
    if (!force && jobsCache && Date.now() - jobsCache.at < 3000) return jobsCache.res;
    const res = await getJSON('/api/jobs');
    jobsCache = { at: Date.now(), res };
    return res;
  }
  async function refreshCounts() {
    const [lib, jobs] = await Promise.all([getJSON('/api/library?limit=1'), getJobs(true)]);
    const l = document.getElementById('nav-lib');
    if (l) { l.textContent = lib.ok && lib.data ? fmtInt(lib.data.total) : '?'; l.title = lib.ok ? 'Videos in the library' : missingWhy(lib, 'The library count'); }
    const r = document.getElementById('nav-run');
    if (r) {
      if (jobs.ok && Array.isArray(jobs.data)) {
        const n = jobs.data.filter((j) => j.status === 'running' || j.status === 'pending').length;
        r.textContent = n ? String(n) : '';
        r.classList.toggle('live', n > 0);
        r.title = `${n} running or queued`;
      } else { r.textContent = '?'; r.title = missingWhy(jobs, 'The job list'); }
    }
  }
  let statusCache = null;
  async function getStatus(force) {
    if (!force && statusCache && Date.now() - statusCache.at < 10000) return statusCache.res;
    const res = await getJSON('/api/status');
    statusCache = { at: Date.now(), res };
    return res;
  }
  function pickCheck(checks, ...keys) {
    for (const k of keys) {
      const c = checks.find((x) => x.id === k) || checks.find((x) => new RegExp(k, 'i').test(x.id || '') || new RegExp(k, 'i').test(x.name || ''));
      if (c) return c;
    }
    return null;
  }
  async function refreshBox() {
    const card = document.getElementById('box-card');
    if (!card) return;
    const res = await getStatus(true);
    const ns = document.getElementById('nav-status');
    if (!res.ok || !res.data) {
      card.innerHTML = `<span class="label">Render box</span>
        <span class="box-line"><span class="dot unknown"></span>Status unknown</span>
        <span class="box-sub">${esc(missingWhy(res, 'The status check'))}</span>`;
      if (ns) ns.textContent = '';
      return;
    }
    const d = res.data;
    const checks = Array.isArray(d.checks) ? d.checks : [];
    const gpu = pickCheck(checks, 'gpu', 'renderer', 'render');
    const voice = pickCheck(checks, 'elevenlabs');
    const jobs = pickCheck(checks, 'jobs');
    const overall = STATE_WORD[d.overall] ? d.overall : 'unknown';
    const lineState = gpu ? (STATE_WORD[gpu.state] ? gpu.state : 'unknown') : overall;
    const line = gpu ? (gpu.summary || gpu.name) : `Overall ${STATE_WORD[overall]}`;
    const subs = [jobs, voice].filter(Boolean).map((c) => `<span class="box-sub">${esc(c.name)} · ${esc(c.summary || STATE_WORD[c.state] || 'unknown')}</span>`).join('');
    const checked = parseTs(d.checked_at);
    card.innerHTML = `<span class="label">Render box</span>
      <span class="box-line"><span class="dot ${lineState}"></span><span>${esc(line)}</span></span>
      ${subs}
      <span class="box-sub faint">${checked ? 'checked ' + esc(fmtAgo(checked)) : 'check time unknown'}</span>`;
    card.title = `Overall: ${STATE_WORD[overall]}`;
    if (ns) {
      const bad = checks.filter((c) => c.state === 'warn' || c.state === 'down').length;
      ns.textContent = bad ? String(bad) : '';
      ns.title = bad ? `${bad} check${bad > 1 ? 's' : ''} need attention` : '';
    }
  }

  window.CB = {
    esc, cap, fmtClock, fmtDuration, fmtSpan, fmtAgo, fmtStamp, fmtBytes, fmtInt, fmtUsd, fmtSec, parseTs, unknownHtml,
    resLabel, resFull, fileUrl, getJSON, missingWhy, thumbInner, setMathText, hasMath, loadKatex, mathHtml,
    explainError, errorCard, getVoices, getMeta, narratorName, buildWorkbench, renderTimeline, shortModel,
    getJobs, getStatus, STATE_WORD, refreshCounts,
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', renderShell);
  else renderShell();
})();
