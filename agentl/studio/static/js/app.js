// app.js — coque applicative du studio AGENT-L (module M3).
//
// Responsabilités, et rien d'autre (contrat §5) :
//   1. connecter le bus WebSocket ;
//   2. monter les quatre panneaux fournis par M4/M5, en tolérant leur absence ;
//   3. câbler la barre supérieure sur `bus.send` ;
//   4. thème, raccourcis clavier (§6), tailles de panneaux persistées.
// Aucune logique métier de panneau ne vit ici.

import { bus, state, connect } from './bus.js';

// ---------------------------------------------------------------------------
// Petits utilitaires
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);

/** Clés de persistance (préfixées pour ne pas polluer le domaine). */
const LS = {
  theme: 'agentl.studio.theme',
  layout: 'agentl.studio.layout',
  ticks: 'agentl.studio.ticks',
  pace: 'agentl.studio.pace',
  editor: 'agentl.studio.editorCollapsed',
};

/** Lecture localStorage tolérante (mode privé, quota, stockage désactivé). */
function lsGet(key) {
  try { return localStorage.getItem(key); } catch (err) { return null; }
}
function lsSet(key, value) {
  try { localStorage.setItem(key, value); } catch (err) { /* stockage indisponible : on continue sans */ }
}

/** Affiche un message transitoire ; `kind` ∈ ok|warn|error|info. */
function toast(message, kind = 'info', ms = 5000) {
  const host = $('toasts');
  if (!host) return;
  const el = document.createElement('div');
  el.className = `toast toast--${kind}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => {
    el.classList.add('is-leaving');
    setTimeout(() => el.remove(), 200);
  }, ms);
}

/** `fetch` JSON avec erreurs normalisées (jamais de rejet nu à l'écran). */
async function api(path, options) {
  let res;
  try {
    res = await fetch(path, options);
  } catch (err) {
    throw new Error(`serveur injoignable (${path})`);
  }
  let data = null;
  try { data = await res.json(); } catch (err) { data = null; }
  if (!res.ok) {
    const msg = data && data.error ? data.error : `HTTP ${res.status}`;
    throw new Error(`${path} : ${msg}`);
  }
  return data;
}

async function postJSON(path, body) {
  return api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
}

// ---------------------------------------------------------------------------
// Thème : auto → clair → sombre → auto (le choix explicite gagne, §1)
// ---------------------------------------------------------------------------

const THEMES = ['auto', 'light', 'dark'];
const THEME_GLYPH = { auto: '◐', light: '☀', dark: '☾' };

function applyTheme(theme) {
  const value = THEMES.includes(theme) ? theme : 'auto';
  document.documentElement.setAttribute('data-theme', value);
  const glyph = $('theme-glyph');
  if (glyph) glyph.textContent = THEME_GLYPH[value];
  const btn = $('btn-theme');
  if (btn) btn.title = `Thème : ${value === 'auto' ? 'système' : value === 'light' ? 'clair' : 'sombre'}`;
  lsSet(LS.theme, value);
  bus.emit('ui.theme', { theme: value });
}

function initTheme() {
  applyTheme(lsGet(LS.theme) || 'auto');
  const btn = $('btn-theme');
  if (btn) {
    btn.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme') || 'auto';
      applyTheme(THEMES[(THEMES.indexOf(current) + 1) % THEMES.length]);
    });
  }
}

// ---------------------------------------------------------------------------
// Montage des panneaux (tolérant aux modules pas encore livrés)
// ---------------------------------------------------------------------------

/**
 * Importe `module` et appelle son `factory(el)`.
 * Si le fichier n'existe pas encore (développement en parallèle M4/M5) ou
 * échoue, on l'écrit dans le panneau au lieu de casser toute la coque.
 */
async function mountPanel(elementId, modulePath, factory) {
  const el = $(elementId);
  if (!el) return;
  try {
    const mod = await import(modulePath);
    const fn = mod[factory];
    if (typeof fn !== 'function') {
      throw new Error(`export « ${factory} » absent`);
    }
    fn(el);
  } catch (err) {
    const note = document.createElement('p');
    note.className = 'panel__placeholder';
    note.textContent = `${modulePath} indisponible — ${err && err.message ? err.message : err}`;
    el.appendChild(note);
  }
}

function mountPanels() {
  // En parallèle : un module lent ou absent ne retarde pas les autres.
  return Promise.all([
    mountPanel('editor', './editor.js', 'mountEditor'),
    mountPanel('canvas', './canvas.js', 'mountCanvas'),
    mountPanel('inspector', './inspector.js', 'mountInspector'),
    mountPanel('timeline', './timeline.js', 'mountTimeline'),
  ]);
}

// ---------------------------------------------------------------------------
// Grille redimensionnable, persistée
// ---------------------------------------------------------------------------

const LAYOUT_DEFAULT = { editor: 360, inspector: 340, timeline: 220 };
const LAYOUT_MIN = { editor: 160, inspector: 160, timeline: 80 };

let layout = { ...LAYOUT_DEFAULT };

function applyLayout() {
  const root = document.documentElement;
  root.style.setProperty('--w-editor', `${Math.round(layout.editor)}px`);
  root.style.setProperty('--w-inspector', `${Math.round(layout.inspector)}px`);
  root.style.setProperty('--h-timeline', `${Math.round(layout.timeline)}px`);
}

function saveLayout() {
  lsSet(LS.layout, JSON.stringify(layout));
  bus.emit('ui.resize', { ...layout });
}

function loadLayout() {
  const raw = lsGet(LS.layout);
  if (raw) {
    try {
      const parsed = JSON.parse(raw);
      for (const key of Object.keys(LAYOUT_DEFAULT)) {
        const v = Number(parsed[key]);
        if (Number.isFinite(v) && v >= 0) layout[key] = v;
      }
    } catch (err) {
      // Valeur corrompue : on repart des tailles par défaut, sans bruit.
      layout = { ...LAYOUT_DEFAULT };
    }
  }
  applyLayout();
}

/**
 * Rend une poignée glissable au pointeur et pilotable au clavier
 * (flèches = 16 px, Home = taille par défaut).
 */
function wireGutter(gutterId, key, axis, invert) {
  const g = $(gutterId);
  if (!g) return;
  const min = LAYOUT_MIN[key];

  const maxFor = () => (axis === 'x' ? window.innerWidth * 0.6 : window.innerHeight * 0.7);

  const setSize = (px) => {
    layout[key] = Math.max(min, Math.min(maxFor(), px));
    applyLayout();
  };

  g.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    g.setPointerCapture(ev.pointerId);
    g.classList.add('is-dragging');
    document.body.classList.add('is-resizing', axis === 'x' ? 'is-resizing-col' : 'is-resizing-row');

    const start = axis === 'x' ? ev.clientX : ev.clientY;
    const base = layout[key];

    const onMove = (e) => {
      const delta = (axis === 'x' ? e.clientX : e.clientY) - start;
      setSize(base + (invert ? -delta : delta));
    };
    const onUp = () => {
      g.removeEventListener('pointermove', onMove);
      g.removeEventListener('pointerup', onUp);
      g.removeEventListener('pointercancel', onUp);
      g.classList.remove('is-dragging');
      document.body.classList.remove('is-resizing', 'is-resizing-col', 'is-resizing-row');
      saveLayout();
    };
    g.addEventListener('pointermove', onMove);
    g.addEventListener('pointerup', onUp);
    g.addEventListener('pointercancel', onUp);
  });

  g.addEventListener('keydown', (ev) => {
    const step = 16;
    const less = axis === 'x' ? 'ArrowLeft' : 'ArrowUp';
    const more = axis === 'x' ? 'ArrowRight' : 'ArrowDown';
    if (ev.key === less) setSize(layout[key] + (invert ? step : -step));
    else if (ev.key === more) setSize(layout[key] + (invert ? -step : step));
    else if (ev.key === 'Home') setSize(LAYOUT_DEFAULT[key]);
    else return;
    ev.preventDefault();
    saveLayout();
  });
}

/**
 * Replie / déplie l'éditeur.
 *
 * L'écriture du programme et l'observation d'un run sont deux usages
 * distincts : pendant un run, la place vaut mieux au canevas. L'éditeur est
 * donc replié par défaut, et rouvrable d'un clic par le rail de gauche.
 */
function setEditorCollapsed(collapsed) {
  const grid = $('grid');
  const rail = $('editor-rail');
  const toggle = $('btn-editor-toggle');
  if (!grid) return;
  grid.classList.toggle('is-editor-collapsed', !!collapsed);
  if (rail) rail.hidden = !collapsed;
  if (toggle) toggle.setAttribute('aria-expanded', String(!collapsed));
  lsSet(LS.editor, collapsed ? '1' : '0');
  // Le canevas doit recalculer sa vue : la largeur utile vient de changer.
  bus.emit('ui.resize', { ...layout, editorCollapsed: !!collapsed });
}

function initLayout() {
  loadLayout();
  wireGutter('gutter-left', 'editor', 'x', false);
  wireGutter('gutter-right', 'inspector', 'x', true);
  wireGutter('gutter-bottom', 'timeline', 'y', true);
  window.addEventListener('resize', () => bus.emit('ui.resize', { ...layout }));

  const toggle = $('btn-editor-toggle');
  const rail = $('editor-rail');
  if (toggle) toggle.addEventListener('click', () => setEditorCollapsed(true));
  if (rail) rail.addEventListener('click', () => setEditorCollapsed(false));
  const saved = lsGet(LS.editor);
  setEditorCollapsed(saved === null ? true : saved === '1');
}

// ---------------------------------------------------------------------------
// Barre supérieure
// ---------------------------------------------------------------------------

/** Nombre de ticks demandé, borné et mémorisé. */
function ticksValue() {
  const input = $('ticks-input');
  const n = Math.trunc(Number(input && input.value));
  const safe = Number.isFinite(n) && n > 0 ? Math.min(n, 9999) : 12;
  if (input) input.value = String(safe);
  lsSet(LS.ticks, String(safe));
  return safe;
}

/**
 * Reflète l'hôte imposé par la norme de nommage (`X.agent` → `X.py`).
 *
 * Il n'y a plus de choix à faire : montrer un sélecteur laisserait croire le
 * contraire. On affiche le fichier attendu et le résultat de sa vérification —
 * présent ? déclare-t-il bien les capteurs du programme ?
 */
function showHost(status) {
  const badge = $('host-indicator');
  const label = $('host-label');
  if (!badge || !label) return;
  badge.classList.remove('is-ok', 'is-unsure', 'is-missing');
  if (!status || !status.path) { label.textContent = '—'; return; }
  const nom = status.path.split('/').pop();
  label.textContent = nom;
  if (!status.exists) {
    badge.classList.add('is-missing');
    badge.title = status.message || `${status.path} est introuvable`;
  } else if (status.confirmed) {
    badge.classList.add('is-ok');
    badge.title = status.message
      || `${status.path} — ${status.sensors}/${status.expectedSensors} capteurs`;
  } else {
    badge.classList.add('is-unsure');
    badge.title = status.message || `${status.path} — contrat non confirmé`;
  }
}

/**
 * Allure du mode démonstration, en secondes entre deux événements.
 *
 * À pleine vitesse un run se termine avant que l'œil n'ait vu quoi que ce
 * soit : l'allure par défaut est donc « lisible », pas « rapide ».
 */
function paceValue() {
  const sel = $('pace-select');
  const v = Number(sel && sel.value);
  const safe = Number.isFinite(v) && v >= 0 ? Math.min(v, 5) : 0;
  lsSet(LS.pace, String(safe));
  return safe;
}

function doRun(step) {
  const status = state.session && state.session.hostStatus;
  if (status && status.path && !status.exists) {
    toast(status.message || `hôte introuvable : ${status.path}`, 'error', 9000);
    return;
  }
  bus.send({ type: 'run', ticks: ticksValue(), step: !!step, pace: paceValue() });
}

function doPauseResume() {
  if (!state.running) return;
  bus.send({ type: state.paused ? 'resume' : 'pause' });
}

function doStep() {
  // Hors run, `→` démarre en mode pas à pas ; en run, avance d'un tick.
  if (state.running) bus.send({ type: 'step' });
  else doRun(true);
}

function doStop() {
  if (state.running) bus.send({ type: 'stop' });
}

async function doSave() {
  try {
    await postJSON('/api/save', {});
    markDirty(false);
    toast('Programme enregistré.', 'ok', 2500);
    bus.emit('ui.saved', {});
  } catch (err) {
    toast(err.message, 'error', 8000);
  }
}

/** Demande à la chronologie de prendre le focus de filtre (raccourci « / »). */
function focusFilter() {
  bus.emit('ui.focusFilter', {});
  const field = document.querySelector('#timeline .filter, #timeline input[type="search"], #timeline input[type="text"]');
  if (field) { field.focus(); field.select && field.select(); return true; }
  return false;
}

function markDirty(dirty) {
  const el = $('file-dirty');
  if (el) el.hidden = !dirty;
}

function initTopbar() {
  const ticks = $('ticks-input');
  if (ticks) {
    const saved = lsGet(LS.ticks);
    if (saved) ticks.value = saved;
    ticks.addEventListener('change', ticksValue);
  }

  const pace = $('pace-select');
  if (pace) {
    const saved = lsGet(LS.pace);
    if (saved !== null && saved !== '') pace.value = saved;
    // Réglable en cours de run : le serveur applique l'allure au vol.
    pace.addEventListener('change', () => {
      const value = paceValue();
      if (state.running) bus.send({ type: 'pace', value });
    });
  }

  const bind = (id, fn) => { const el = $(id); if (el) el.addEventListener('click', fn); };
  bind('btn-run', () => doRun(false));
  bind('btn-pause', doPauseResume);
  bind('btn-step', doStep);
  bind('btn-stop', doStop);
  bind('btn-save', doSave);

  const file = $('file-select');
  if (file) {
    file.addEventListener('change', async () => {
      const path = file.value;
      if (!path) return;
      try {
        const session = await postJSON('/api/open', { path });
        applySession(session);
        bus.emit('session', session);   // les panneaux se resynchronisent
        markDirty(false);
      } catch (err) {
        toast(err.message, 'error', 8000);
      }
    });
  }

  // Une édition non enregistrée allume la pastille ; M5 émet `ui.dirty`.
  bus.on('ui.dirty', (p) => markDirty(!p || p.dirty !== false));
}

// ---------------------------------------------------------------------------
// Indicateurs (connexion, run, tick)
// ---------------------------------------------------------------------------

const CONN_LABEL = {
  idle: 'hors ligne',
  connecting: 'connexion…',
  open: 'connecté',
  retrying: 'reconnexion…',
  closed: 'déconnecté',
};

function renderConnection(status) {
  const badge = $('conn-indicator');
  const label = $('conn-label');
  if (label) label.textContent = CONN_LABEL[status] || status;
  if (badge) {
    badge.className = `badge badge--conn is-${status}`;
    badge.title = status === 'retrying'
      ? `Reconnexion (tentative ${state.attempts})`
      : `Connexion temps réel : ${CONN_LABEL[status] || status}`;
  }
}

function renderRun() {
  const badge = $('run-indicator');
  const label = $('run-label');
  const tick = $('tick-label');
  const kind = !state.running ? 'idle' : state.paused ? 'paused' : 'running';
  const text = { idle: 'au repos', running: 'en cours', paused: 'en pause' }[kind];

  if (badge) badge.className = `badge badge--${kind}`;
  if (label) label.textContent = text;
  if (tick) {
    tick.hidden = !state.running && !state.tick;
    tick.textContent = state.maxTicks
      ? `t${state.tick}/${state.maxTicks}`
      : `t${state.tick}`;
  }

  const run = $('btn-run');
  const pause = $('btn-pause');
  const stop = $('btn-stop');
  if (run) run.disabled = state.running;
  if (stop) stop.disabled = !state.running;
  if (pause) {
    pause.disabled = !state.running;
    pause.textContent = state.paused ? 'Reprendre' : 'Pause';
    pause.setAttribute('aria-pressed', String(!!state.paused));
  }
}

function initIndicators() {
  renderConnection(state.connection);
  renderRun();

  bus.on('connection', (p) => renderConnection(p.status));
  for (const t of ['run.started', 'run.finished', 'run.paused', 'run.resumed', 'tick', 'state']) {
    bus.on(t, renderRun);
  }

  bus.on('run.finished', (msg) => {
    if (msg && msg.status === 'error') {
      toast(`Run interrompu : ${msg.error || 'erreur inconnue'}`, 'error', 9000);
    }
  });
  bus.on('error', (msg) => toast(msg && msg.message ? msg.message : 'erreur serveur', 'error', 8000));
  bus.on('bus.error', (p) => toast(p.message, 'warn', 6000));
  bus.on('hello', () => { if (state.session) applySession(state.session); });
}

// ---------------------------------------------------------------------------
// Session : nom de fichier, liste des fichiers, hôtes disponibles
// ---------------------------------------------------------------------------

/** Remplit un `<select>` sans perdre la valeur courante si elle existe encore. */
function fillSelect(id, values, current, emptyLabel) {
  const sel = $(id);
  if (!sel) return;
  const wanted = current != null ? current : sel.value;
  sel.textContent = '';
  const none = document.createElement('option');
  none.value = '';
  none.textContent = emptyLabel;
  sel.appendChild(none);
  for (const v of values || []) {
    const opt = document.createElement('option');
    opt.value = String(v);
    opt.textContent = String(v);
    sel.appendChild(opt);
  }
  sel.value = wanted && Array.from(sel.options).some((o) => o.value === wanted) ? wanted : '';
}

/** Reflète une session (réponse `/api/session` ou message `hello`). */
function applySession(session) {
  if (!session) return;
  const file = session.file || (session.session && session.session.file) || '';
  const sel = $('file-select');
  if (sel && file) {
    if (!Array.from(sel.options).some((o) => o.value === file)) {
      const opt = document.createElement('option');
      opt.value = file; opt.textContent = file;
      sel.appendChild(opt);
    }
    sel.value = file;
  }
  document.title = file ? `${file} — AGENT-L Studio` : 'AGENT-L Studio';
  showHost(session.hostStatus);
}

/** Charge l'état initial en HTTP (le WebSocket prend ensuite le relais). */
async function loadSession() {
  try {
    const session = await api('/api/session');
    applySession(session);
    if (session) {
      if (session.graph) { state.graph = session.graph; bus.emit('graph', { type: 'graph', graph: session.graph }); }
      if (session.diags) { state.diags = session.diags; bus.emit('diagnostics', { type: 'diagnostics', diags: session.diags }); }
      state.session = session;
      bus.emit('session', session);
    }
  } catch (err) {
    toast(err.message, 'warn', 7000);
  }

  try {
    const files = await api('/api/files');
    const list = Array.isArray(files) ? files : (files && files.files) || [];
    const current = state.session && state.session.file;
    fillSelect('file-select', list, current, '(aucun fichier)');
    if (current) applySession(state.session);
  } catch (err) {
    // Route optionnelle : l'absence de liste n'est pas bloquante.
  }
}

// ---------------------------------------------------------------------------
// Raccourcis clavier (contrat §6)
// ---------------------------------------------------------------------------

/** Vrai si la frappe vise un champ de saisie : on ne détourne alors rien. */
function isTyping(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' ||
         target.isContentEditable === true ||
         !!(target.closest && target.closest('[contenteditable="true"]'));
}

function initShortcuts() {
  window.addEventListener('keydown', (ev) => {
    const mod = ev.metaKey || ev.ctrlKey;
    const typing = isTyping(ev.target);

    // ⌘/Ctrl+Entrée — lancer (valable même depuis l'éditeur).
    if (mod && ev.key === 'Enter') { ev.preventDefault(); doRun(false); return; }

    // ⌘/Ctrl+S — enregistrer (idem).
    if (mod && (ev.key === 's' || ev.key === 'S')) { ev.preventDefault(); doSave(); return; }

    if (mod || ev.altKey) return;   // les autres raccourcis sont sans modificateur

    // Échap depuis un champ : rend le focus au document.
    if (ev.key === 'Escape' && typing && ev.target.blur) { ev.target.blur(); return; }
    if (typing) return;

    if (ev.key === ' ' || ev.key === 'Spacebar') {
      ev.preventDefault(); doPauseResume(); return;
    }
    if (ev.key === 'ArrowRight') { ev.preventDefault(); doStep(); return; }
    if (ev.key === '/') {
      if (focusFilter()) ev.preventDefault();
    }
  });
}

// ---------------------------------------------------------------------------
// Amorçage
// ---------------------------------------------------------------------------

function boot() {
  initTheme();
  initLayout();
  initTopbar();
  initIndicators();
  initShortcuts();

  // Les panneaux se montent avant l'ouverture du canal : ils ne perdent ainsi
  // aucun message (le bus met de toute façon les envois en file d'attente).
  mountPanels().then(loadSession).catch((err) => {
    toast(`amorçage incomplet : ${err && err.message ? err.message : err}`, 'error', 9000);
  });

  connect();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot, { once: true });
} else {
  boot();
}
