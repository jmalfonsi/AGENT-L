// AGENT-L Studio — M5 · chronologie / journal d'exécution en direct
// Journal dense et monospace, groupé par tick (repliable), filtrable, avec
// fenêtre glissante (virtualisation) : plusieurs milliers de lignes sans
// re-rendu complet.
import { bus } from './bus.js';

// Glyphes : copie fidèle de agentl/runtime.py::Trace.GLYPH.
const GLYPH = {
  TICK: '──', OBSERVE: '👁', BELIEF: '◆', GOAL: '◎',
  PLAN: '▶', STEP: '·', TOOL: '🔧', BLOCKED: '⛔',
  APPROVAL: '🙋', VERIFY_OK: '✅', VERIFY_FAIL: '❌',
  LLM: '🧠', MEMORY: '💾', EVENT: '⚡', ASK: '❓',
  DELEGATE: '→', ERROR: '‼', INFO: '•', RETRY: '↻',
  BAYES: '∿', PLANNER: '⌘', MESSAGE: '✉', SHARED: '⇄',
};
// Ordre d'affichage des puces de filtre (les plus parlantes d'abord).
const KINDS = [
  'TOOL', 'BLOCKED', 'APPROVAL', 'VERIFY_OK', 'VERIFY_FAIL', 'ERROR', 'ASK',
  'BAYES', 'PLAN', 'PLANNER', 'STEP', 'OBSERVE', 'BELIEF', 'GOAL', 'EVENT',
  'LLM', 'MEMORY', 'DELEGATE', 'MESSAGE', 'SHARED', 'RETRY', 'INFO',
];
// Teinte sémantique par kind (variables M3 uniquement).
const TONE = {
  BLOCKED: 'bad', ERROR: 'bad', VERIFY_FAIL: 'bad',
  APPROVAL: 'warn', ASK: 'warn', RETRY: 'warn',
  VERIFY_OK: 'ok', TOOL: 'accent', PLAN: 'accent', PLANNER: 'accent',
  BAYES: 'accent',
};

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;' };
const esc = (s) => String(s == null ? '' : s).replace(/[&<>]/g, (c) => ESC[c]);

const ROW_H = 20;      // hauteur fixe d'une ligne (doit coller au CSS)
const OVER = 8;        // lignes rendues au-delà de la fenêtre visible
const MAX_EVENTS = 20000;

const CSS = `
.agl-timeline{position:relative;display:flex;flex-direction:column;height:100%;min-height:0;overflow:hidden;
  background:var(--surface);color:var(--ink);
  font-family:var(--font-mono);font-size:11.5px}
.agl-timeline .agl-tl-bar{display:flex;align-items:center;gap:6px;flex:0 0 auto;padding:4px 8px;
  border-bottom:1px solid var(--hair);background:var(--surface-2)}
.agl-timeline .agl-tl-bar input{flex:0 1 220px;min-width:70px;font:inherit;padding:2px 7px;
  border-radius:5px;border:1px solid var(--hair);background:var(--paper);
  color:inherit}
.agl-timeline .agl-tl-bar input:focus{outline:none;border-color:var(--accent)}
.agl-timeline .agl-tl-bar button{font:inherit;font-size:11px;padding:2px 8px;border-radius:5px;
  cursor:pointer;border:1px solid var(--hair);background:transparent;color:inherit}
.agl-timeline .agl-tl-bar button:hover{border-color:var(--accent);color:var(--accent)}
.agl-timeline .agl-tl-bar .sp{flex:1 1 auto}
.agl-timeline .agl-count{color:var(--muted);font-size:10.5px;white-space:nowrap}
.agl-timeline .agl-prog{position:relative;flex:0 1 120px;height:5px;border-radius:99px;
  background:var(--surface-2);overflow:hidden}
.agl-timeline .agl-prog i{position:absolute;inset:0 auto 0 0;background:var(--accent);
  transition:width .18s ease}
.agl-timeline .agl-prog.run i{background:var(--ok)}

.agl-timeline .agl-chips{display:flex;flex-wrap:wrap;gap:3px;flex:0 0 auto;padding:3px 8px;
  border-bottom:1px solid var(--hair);max-height:52px;overflow:auto}
.agl-timeline .agl-chips button{font:inherit;font-size:10px;line-height:15px;padding:0 6px;
  border-radius:99px;cursor:pointer;border:1px solid var(--hair);background:transparent;
  color:var(--muted);opacity:.55;transition:opacity .15s ease,color .15s ease}
.agl-timeline .agl-chips button.on{opacity:1;color:var(--ink);border-color:var(--ink)}
.agl-timeline .agl-chips button.on.bad{color:var(--err);border-color:currentColor}
.agl-timeline .agl-chips button.on.warn{color:var(--warn);border-color:currentColor}
.agl-timeline .agl-chips button.on.ok{color:var(--ok);border-color:currentColor}
.agl-timeline .agl-chips button.on.accent{color:var(--accent);border-color:currentColor}
.agl-timeline .agl-chips .n{opacity:.6;margin-left:3px}

.agl-timeline .agl-tl-scroll{position:relative;flex:1 1 auto;min-height:0;overflow:auto;
  contain:layout paint}
.agl-timeline .agl-tl-space{position:relative;width:100%}
.agl-timeline .agl-tl-win{position:absolute;left:0;right:0;top:0;will-change:transform}
.agl-timeline .r{display:flex;align-items:center;gap:6px;height:${ROW_H}px;padding:0 8px;
  white-space:nowrap;overflow:hidden;cursor:default;border-left:2px solid transparent}
.agl-timeline .r.ev{cursor:pointer}
.agl-timeline .r.ev:hover{background:var(--surface-3)}
.agl-timeline .r.sel{background:color-mix(in srgb,var(--accent) 18%,transparent);
  border-left-color:var(--accent)}
.agl-timeline .r .g{flex:0 0 auto;width:1.6em;text-align:center}
.agl-timeline .r .ag{flex:0 0 auto;max-width:16ch;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap;color:var(--lane-society,var(--info));font-size:10px;
  font-weight:700;letter-spacing:.02em}
.agl-timeline .r .k{flex:0 0 auto;width:11ch;color:var(--muted);font-size:10px;
  overflow:hidden;text-overflow:ellipsis}
.agl-timeline .r .t{flex:1 1 auto;overflow:hidden;text-overflow:ellipsis}
.agl-timeline .r .d{flex:0 1 auto;max-width:44%;color:var(--muted);overflow:hidden;
  text-overflow:ellipsis}
.agl-timeline .r .sq{flex:0 0 auto;color:var(--muted);font-size:10px}
.agl-timeline .r.bad .g,.agl-timeline .r.bad .t{color:var(--err)}
.agl-timeline .r.warn .g{color:var(--warn)}
.agl-timeline .r.ok .g{color:var(--ok)}
.agl-timeline .r.accent .g{color:var(--accent)}
.agl-timeline .r.hd{cursor:pointer;color:var(--muted);
  background:var(--surface-2);font-weight:600}
.agl-timeline .r.hd:hover{color:var(--ink)}
.agl-timeline .r mark{background:color-mix(in srgb,var(--warn) 45%,transparent);
  color:inherit;border-radius:2px}
.agl-timeline .agl-empty{padding:10px;color:var(--muted)}
.agl-timeline .agl-follow{position:absolute;right:14px;bottom:10px;z-index:3;display:none;
  font:inherit;font-size:10.5px;padding:3px 9px;border-radius:99px;cursor:pointer;
  border:1px solid var(--accent);color:var(--accent);
  background:var(--paper)}
.agl-timeline .agl-follow.on{display:block}
`;

let cssDone = false;
function injectCss() {
  if (cssDone || document.getElementById('agl-timeline-css')) { cssDone = true; return; }
  const st = document.createElement('style');
  st.id = 'agl-timeline-css';
  st.textContent = CSS;
  document.head.appendChild(st);
  cssDone = true;
}

/** Surligne les occurrences du filtre dans un texte déjà échappé. */
function mark(text, needle) {
  const safe = esc(text);
  if (!needle) return safe;
  const n = esc(needle);
  const re = new RegExp(n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
  return safe.replace(re, (m) => `<mark>${m}</mark>`);
}

export function mountTimeline(el) {
  if (!el) throw new Error('mountTimeline : élément hôte manquant');
  injectCss();
  el.classList.add('agl-timeline');
  el.innerHTML = `
    <div class="agl-tl-bar">
      <input type="search" data-q placeholder="filtrer  ( / )" aria-label="Filtrer le journal"/>
      <span class="agl-count" data-count>0</span>
      <span class="sp"></span>
      <span class="agl-count" data-tick>tick —</span>
      <span class="agl-prog" data-prog><i style="width:0"></i></span>
      <button type="button" data-all title="Tout afficher / tout masquer">kinds</button>
      <button type="button" data-fold title="Replier / déplier tous les ticks">plier</button>
      <button type="button" data-export title="Exporter le journal en JSON">export</button>
      <button type="button" data-clear title="Vider le journal affiché">vider</button>
    </div>
    <div class="agl-chips" data-chips></div>
    <div class="agl-tl-scroll" data-scroll tabindex="0">
      <div class="agl-tl-space" data-space><div class="agl-tl-win" data-win></div></div>
      <div class="agl-empty" data-empty>Journal vide — lancez un run.</div>
    </div>
    <button type="button" class="agl-follow" data-follow>↓ suivre</button>`;

  const $ = (s) => el.querySelector(s);
  const scroller = $('[data-scroll]');
  const space = $('[data-space]');
  const win = $('[data-win]');
  const chipsEl = $('[data-chips]');
  const qInput = $('[data-q]');
  const countEl = $('[data-count]');
  const tickEl = $('[data-tick]');
  const prog = $('[data-prog]');
  const progBar = prog.firstElementChild;
  const emptyEl = $('[data-empty]');
  const followBtn = $('[data-follow]');

  const st = {
    events: [],
    seen: new Set(),
    rows: [],
    counts: new Map(),
    hidden: new Set(),      // kinds masqués
    collapsed: new Set(),   // ticks repliés
    q: '',
    follow: true,
    selected: null,
    maxTicks: 0,
    tick: 0,
    running: false,
    raf: 0,
    firstIdx: -1,
    lastIdx: -1,
  };

  // ------------------------------------------------------------------ puces
  function renderChips() {
    let html = '';
    for (const k of KINDS) {
      const n = st.counts.get(k) || 0;
      const on = !st.hidden.has(k);
      html += `<button type="button" data-kind="${k}" class="${on ? 'on' : ''} ${TONE[k] || ''}" `
        + `aria-pressed="${on}" title="${k}">${GLYPH[k] || '•'} ${k.toLowerCase()}`
        + (n ? `<span class="n">${n}</span>` : '') + '</button>';
    }
    chipsEl.innerHTML = html;
  }

  chipsEl.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-kind]');
    if (!b) return;
    const k = b.dataset.kind;
    if (ev.altKey) {
      // Alt-clic : isoler ce kind.
      st.hidden = new Set(KINDS.filter((x) => x !== k));
    } else if (st.hidden.has(k)) st.hidden.delete(k);
    else st.hidden.add(k);
    renderChips();
    rebuild();
  });

  $('[data-all]').addEventListener('click', () => {
    st.hidden = st.hidden.size ? new Set() : new Set(KINDS);
    renderChips();
    rebuild();
  });

  // -------------------------------------------------------------- lignes
  function passes(e) {
    if (st.hidden.has(e.kind)) return false;
    if (!st.q) return true;
    const q = st.q.toLowerCase();
    return (e.text && e.text.toLowerCase().includes(q))
      || (e.detail && String(e.detail).toLowerCase().includes(q))
      || (e.kind && e.kind.toLowerCase().includes(q))
      || (e.nodeId && String(e.nodeId).toLowerCase().includes(q));
  }

  /** Reconstruit la liste plate des lignes (entêtes de tick + événements). */
  function rebuild(keepScroll) {
    const prevTop = scroller.scrollTop;
    const rows = [];
    let curTick = null;
    let head = null;
    for (const e of st.events) {
      if (!passes(e)) continue;
      const t = Number.isFinite(e.tick) ? e.tick : 0;
      if (curTick === null || t !== curTick) {
        curTick = t;
        head = { kind: 'head', tick: t, n: 0 };
        rows.push(head);
      }
      head.n += 1;
      if (!st.collapsed.has(curTick)) rows.push({ kind: 'ev', e });
    }
    st.rows = rows;
    space.style.height = `${rows.length * ROW_H}px`;
    countEl.textContent = `${rows.filter((r) => r.kind === 'ev').length} / ${st.events.length}`;
    emptyEl.style.display = st.events.length ? 'none' : '';
    st.firstIdx = -1;
    if (keepScroll) scroller.scrollTop = prevTop;
    if (st.follow) scroller.scrollTop = scroller.scrollHeight;
    draw();
  }

  function rowHtml(r, idx) {
    if (r.kind === 'head') {
      const folded = st.collapsed.has(r.tick);
      return `<div class="r hd" data-idx="${idx}" data-tick="${r.tick}" `
        + `style="transform:translateY(${idx * ROW_H}px);position:absolute;left:0;right:0">`
        + `<span class="g">${folded ? '▸' : '▾'}</span>`
        + `<span class="t">tick ${r.tick} — ${r.n} événement${r.n > 1 ? 's' : ''}</span></div>`;
    }
    const e = r.e;
    const tone = TONE[e.kind] || '';
    const sel = st.selected && e.nodeId === st.selected ? ' sel' : '';
    return `<div class="r ev ${tone}${sel}" data-idx="${idx}" `
      + `style="transform:translateY(${idx * ROW_H}px);position:absolute;left:0;right:0" `
      + `title="${esc(e.detail || e.text || '')}">`
      + `<span class="g">${GLYPH[e.kind] || '•'}</span>`
      + `<span class="k">${esc(e.kind || '')}</span>`
      + (e.agent ? `<span class="ag">${esc(e.agent)}</span>` : '')
      + `<span class="t">${mark(e.text || '', st.q)}</span>`
      + (e.detail ? `<span class="d">${mark(e.detail, st.q)}</span>` : '')
      + `<span class="sq">#${esc(e.seq == null ? '' : e.seq)}</span></div>`;
  }

  /** Fenêtre glissante : ne rend que les lignes visibles (± OVER). */
  function draw() {
    st.raf = 0;
    const top = scroller.scrollTop;
    const h = scroller.clientHeight || 200;
    let first = Math.max(0, Math.floor(top / ROW_H) - OVER);
    let last = Math.min(st.rows.length, Math.ceil((top + h) / ROW_H) + OVER);
    if (first === st.firstIdx && last === st.lastIdx) return;
    st.firstIdx = first;
    st.lastIdx = last;
    let html = '';
    for (let i = first; i < last; i += 1) html += rowHtml(st.rows[i], i);
    win.innerHTML = html;
  }

  function schedule() {
    if (st.raf) return;
    st.raf = requestAnimationFrame(draw);
  }

  scroller.addEventListener('scroll', () => {
    const atBottom = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < ROW_H * 2;
    if (!atBottom && st.follow) {
      // L'utilisateur remonte : on coupe le défilement automatique.
      st.follow = false;
      followBtn.classList.add('on');
    } else if (atBottom && !st.follow) {
      st.follow = true;
      followBtn.classList.remove('on');
    }
    schedule();
  }, { passive: true });

  followBtn.addEventListener('click', () => {
    st.follow = true;
    followBtn.classList.remove('on');
    scroller.scrollTop = scroller.scrollHeight;
    schedule();
  });

  win.addEventListener('click', (ev) => {
    const row = ev.target.closest('[data-idx]');
    if (!row) return;
    const r = st.rows[Number(row.dataset.idx)];
    if (!r) return;
    if (r.kind === 'head') {
      if (st.collapsed.has(r.tick)) st.collapsed.delete(r.tick);
      else st.collapsed.add(r.tick);
      rebuild(true);
      return;
    }
    if (r.e.nodeId) {
      st.selected = r.e.nodeId;
      st.firstIdx = -1;
      draw();
      bus.emit('ui.select', { nodeId: r.e.nodeId });
    }
  });

  // ------------------------------------------------------------------ filtre
  let qTimer = 0;
  qInput.addEventListener('input', () => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => { st.q = qInput.value.trim(); rebuild(); }, 90);
  });
  qInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { qInput.value = ''; st.q = ''; rebuild(); qInput.blur(); }
  });

  function focusFilter() { qInput.focus(); qInput.select(); }
  const onKey = (ev) => {
    if (ev.key !== '/' || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    const t = ev.target;
    const tag = t && t.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || (t && t.isContentEditable)) return;
    ev.preventDefault();
    focusFilter();
  };
  document.addEventListener('keydown', onKey);

  // ------------------------------------------------------------- pliage/export
  $('[data-fold]').addEventListener('click', () => {
    const ticks = new Set(st.events.map((e) => (Number.isFinite(e.tick) ? e.tick : 0)));
    if (st.collapsed.size) st.collapsed.clear();
    else st.collapsed = ticks;
    rebuild(true);
  });

  $('[data-clear]').addEventListener('click', () => {
    st.events = [];
    st.seen.clear();
    st.counts.clear();
    st.collapsed.clear();
    renderChips();
    rebuild();
  });

  $('[data-export]').addEventListener('click', () => {
    const payload = {
      exportedAt: new Date().toISOString(),
      tick: st.tick,
      maxTicks: st.maxTicks,
      count: st.events.length,
      events: st.events,
    };
    let url = '';
    try {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `agentl-trace-${Date.now()}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (err) {
      bus.emit('ui.toast', { level: 'error', message: `Export impossible : ${err && err.message}` });
    } finally {
      if (url) setTimeout(() => URL.revokeObjectURL(url), 2000);
    }
  });

  // ------------------------------------------------------------- bus (entrée)
  function push(e) {
    // `seq` est monotone par run : il sert à dédupliquer une reconnexion.
    const key = e.seq == null ? null : `${e.runId || ''}#${e.seq}`;
    if (key !== null) {
      if (st.seen.has(key)) return false;
      st.seen.add(key);
    }
    st.events.push(e);
    st.counts.set(e.kind, (st.counts.get(e.kind) || 0) + 1);
    if (st.events.length > MAX_EVENTS) {
      const drop = st.events.splice(0, st.events.length - MAX_EVENTS);
      for (const d of drop) {
        if (d.seq != null) st.seen.delete(`${d.runId || ''}#${d.seq}`);
      }
    }
    return true;
  }

  let pending = 0;
  function flushSoon() {
    if (pending) return;
    pending = requestAnimationFrame(() => { pending = 0; renderChips(); rebuild(); });
  }

  const offs = [];
  const on = (t, fn) => { bus.on(t, fn); offs.push([t, fn]); };

  on('trace', (m) => {
    if (!m || typeof m !== 'object') return;
    if (push({
      seq: m.seq, runId: m.runId, tick: m.tick, kind: m.kind || 'INFO',
      text: m.text || '', detail: m.detail || '', nodeId: m.nodeId || null, ts: m.ts,
      // Présent uniquement en société : c'est là qu'il faut savoir qui parle.
      agent: m.agent || null,
    })) flushSoon();
  });

  on('tick', (m) => {
    if (!m) return;
    st.tick = Number(m.tick) || 0;
    tickEl.textContent = `tick ${st.tick}${st.maxTicks ? `/${st.maxTicks}` : ''}`;
    progBar.style.width = st.maxTicks
      ? `${Math.min(100, (st.tick / st.maxTicks) * 100).toFixed(1)}%` : '0';
  });

  on('run.started', (m) => {
    st.maxTicks = Number(m && m.maxTicks) || 0;
    st.tick = 0;
    st.running = true;
    st.follow = true;
    followBtn.classList.remove('on');
    prog.classList.add('run');
    progBar.style.width = '0';
    tickEl.textContent = `tick 0${st.maxTicks ? `/${st.maxTicks}` : ''}`;
  });

  on('run.finished', (m) => {
    st.running = false;
    prog.classList.remove('run');
    progBar.style.width = '100%';
    const status = (m && m.status) || 'done';
    push({ seq: null, tick: st.tick, kind: status === 'error' ? 'ERROR' : 'INFO',
      text: `run terminé — ${status}${m && m.error ? ` : ${m.error}` : ''}`,
      detail: '', nodeId: null });
    flushSoon();
  });

  on('run.paused', (m) => {
    push({ seq: null, tick: (m && m.tick) || st.tick, kind: 'INFO', text: 'run en pause', detail: '' });
    flushSoon();
  });
  on('run.resumed', (m) => {
    push({ seq: null, tick: (m && m.tick) || st.tick, kind: 'INFO', text: 'run repris', detail: '' });
    flushSoon();
  });
  on('error', (m) => {
    push({ seq: null, tick: st.tick, kind: 'ERROR', text: (m && m.message) || 'erreur', detail: '' });
    flushSoon();
  });

  // M3 possède le raccourci global « / » et le relaie par cet événement.
  on('ui.focusFilter', () => focusFilter());

  on('ui.select', (p) => {
    if (!p || !p.nodeId) return;
    st.selected = p.nodeId;
    st.firstIdx = -1;
    draw();
  });

  renderChips();
  rebuild();
  const ro = typeof ResizeObserver === 'function'
    ? new ResizeObserver(() => { st.firstIdx = -1; schedule(); }) : null;
  if (ro) ro.observe(scroller);

  return {
    el,
    focusFilter,
    push: (e) => { if (push(e)) flushSoon(); },
    count: () => st.events.length,
    destroy() {
      for (const [t, fn] of offs) bus.off(t, fn);
      document.removeEventListener('keydown', onKey);
      if (ro) ro.disconnect();
      if (st.raf) cancelAnimationFrame(st.raf);
      el.innerHTML = '';
      el.classList.remove('agl-timeline');
    },
  };
}

export default mountTimeline;
