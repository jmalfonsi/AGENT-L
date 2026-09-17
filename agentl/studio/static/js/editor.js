// AGENT-L Studio — M5 · éditeur de code
// Éditeur sans dépendance : un <textarea> transparent superposé à une couche
// colorée (<pre>) synchronisée en défilement, plus une gouttière de numéros.
// La coloration dérive des jetons réels du lexer (agentl/lexer.py).
import { bus, state as busState } from './bus.js';

// --- Vocabulaire du langage --------------------------------------------------
// Copie fidèle de agentl/lexer.py::KEYWORDS (les mots réservés sont en
// MAJUSCULES uniquement : tout identifiant minuscule reste métier).
const KEYWORDS = new Set([
  'AGENT', 'VERSION', 'DESCRIPTION',
  'GOAL', 'MAINTAIN', 'ACHIEVE', 'TARGET', 'WEIGHT',
  'BELIEF', 'CONFIDENCE', 'SOURCE', 'UPDATED',
  'OBSERVE', 'EVERY', 'WHEN',
  'MEMORY', 'SHORT_TERM', 'LONG_TERM', 'KNOWLEDGE', 'WRITE', 'STORE', 'INTO',
  'TOOL', 'INPUT', 'OUTPUT', 'SIDE_EFFECT', 'RISK',
  'POLICY', 'NEVER', 'ALLOW', 'DENY', 'REQUIRE', 'APPROVAL', 'FOR', 'DEFAULT',
  'PLAN', 'STEP', 'IF', 'THEN', 'ELSE', 'SET', 'LOOP', 'UNTIL', 'MAX',
  'VERIFY', 'CONDITION', 'ON', 'FAIL', 'RETRY', 'ROLLBACK', 'ESCALATE',
  'REASON', 'TASK', 'USING', 'PRODUCE',
  'ASK', 'QUESTION', 'TIMEOUT',
  'DELEGATE', 'EXPECT',
  'MESSAGE', 'TO', 'BROADCAST', 'PAYLOAD', 'SHARED', 'RECEIVE',
  'EVENT', 'DECIDE', 'RULES',
  'UPDATE_BELIEFS', 'EVALUATE_GOALS', 'SELECT_PLAN', 'EXECUTE',
  'UPDATE_MEMORY', 'ACT',
  'HYPOTHESIS', 'PREDICT', 'EVIDENCE', 'PRIOR', 'LIKELIHOOD', 'GIVEN_NOT',
  'THRESHOLD', 'EXPLAINS', 'UPDATE_HYPOTHESES',
  'GROUP', 'MAX_EVIDENCE', 'IN',
  'FROM', 'WHERE',
  'PLANNER', 'ENABLE', 'MAX_DEPTH', 'MAX_NODES', 'APPROVAL_COST',
  'EFFECT', 'REQUIRES', 'COST', 'BIND', 'SYNTHESIZE',
  'OUTCOME', 'WITH', 'UTILITY', 'VALUE', 'TARGET_CONFIDENCE',
  'AND', 'OR', 'NOT',
]);

// Mots-clés qui ouvrent une déclaration de premier niveau : servent à la fois
// à teinter différemment et à retrouver la ligne d'un nœud du graphe.
const DECL_KW = new Set([
  'AGENT', 'GOAL', 'BELIEF', 'OBSERVE', 'MEMORY', 'TOOL', 'POLICY', 'PLAN',
  'HYPOTHESIS', 'EVENT', 'DECIDE', 'PLANNER', 'MESSAGE', 'SHARED', 'LOOP',
]);

// Correspondance lane du graphe → mots-clés de déclaration plausibles.
const LANE_KW = {
  observe: ['OBSERVE'], hypothesis: ['HYPOTHESIS'], goal: ['GOAL'],
  decide: ['DECIDE'], event: ['EVENT'], plan: ['PLAN'], tool: ['TOOL'],
  policy: ['POLICY'], memory: ['MEMORY', 'SHORT_TERM', 'LONG_TERM'],
  message: ['MESSAGE'], belief: ['BELIEF'],
};

const RE = {
  block: /\/\*[\s\S]*?(?:\*\/|$)/y,
  line: /(?:#|\/\/)[^\n]*/y,
  str: /"([^"\\]*(?:\\.[^"\\]*)*)"?/y,
  datetime: /\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2})?)?/y,
  num: /\d+(?:\.\d+)?(?:%|(?:ms|sec|min|[smhd])\b)?/y,
  ident: /[A-Za-z_][A-Za-z_0-9]*/y,
  op: /==|!=|>=|<=|->|=>|[{}()[\],:.=<>+\-*/]/y,
  ws: /[ \t]+/y,
};

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;' };
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ESC[c]);

/** Découpe une source AGENT-L en segments colorés, par ligne.
 *  Retourne un tableau de lignes, chacune un tableau {cls, text}. */
function tokenizeLines(src) {
  const lines = src.split('\n');
  const out = lines.map(() => []);
  let inBlock = false;

  for (let li = 0; li < lines.length; li += 1) {
    const text = lines[li];
    const row = out[li];
    let i = 0;
    let firstWord = true;

    if (inBlock) {
      const end = text.indexOf('*/');
      if (end === -1) { row.push({ cls: 'c', text }); continue; }
      row.push({ cls: 'c', text: text.slice(0, end + 2) });
      i = end + 2;
      inBlock = false;
    }

    while (i < text.length) {
      let m;
      RE.ws.lastIndex = i;
      m = RE.ws.exec(text);
      if (m) { row.push({ cls: '', text: m[0] }); i = RE.ws.lastIndex; continue; }

      if (text.startsWith('/*', i)) {
        const end = text.indexOf('*/', i + 2);
        if (end === -1) { row.push({ cls: 'c', text: text.slice(i) }); inBlock = true; break; }
        row.push({ cls: 'c', text: text.slice(i, end + 2) });
        i = end + 2;
        continue;
      }

      RE.line.lastIndex = i;
      m = RE.line.exec(text);
      if (m) { row.push({ cls: 'c', text: m[0] }); i = RE.line.lastIndex; continue; }

      RE.str.lastIndex = i;
      if (text[i] === '"') {
        m = RE.str.exec(text);
        if (m) { row.push({ cls: 's', text: m[0] }); i = RE.str.lastIndex; continue; }
      }

      RE.datetime.lastIndex = i;
      m = RE.datetime.exec(text);
      if (m) { row.push({ cls: 'n', text: m[0] }); i = RE.datetime.lastIndex; continue; }

      RE.num.lastIndex = i;
      m = RE.num.exec(text);
      if (m) { row.push({ cls: 'n', text: m[0] }); i = RE.num.lastIndex; continue; }

      RE.ident.lastIndex = i;
      m = RE.ident.exec(text);
      if (m) {
        const w = m[0];
        let cls = 'i';
        if (KEYWORDS.has(w)) cls = DECL_KW.has(w) && firstWord ? 'kd' : 'k';
        else if (firstWord) cls = 'i';
        row.push({ cls, text: w });
        firstWord = false;
        i = RE.ident.lastIndex;
        continue;
      }

      RE.op.lastIndex = i;
      m = RE.op.exec(text);
      if (m) { row.push({ cls: 'o', text: m[0] }); i = RE.op.lastIndex; continue; }

      // Caractère hors grammaire : signalé visuellement, jamais fatal.
      row.push({ cls: 'x', text: text[i] });
      i += 1;
    }
  }
  return out;
}

const CSS = `
.agl-editor{display:flex;flex-direction:column;height:100%;min-height:0;
  background:var(--surface);color:var(--ink);
  font-family:var(--font-mono);overflow:hidden}
.agl-editor .agl-ed-bar{display:flex;align-items:center;gap:8px;padding:4px 8px;
  border-bottom:1px solid var(--hair);font-size:11px;flex:0 0 auto;
  background:var(--surface-2)}
.agl-editor .agl-ed-bar .sp{flex:1}
.agl-editor .agl-ed-file{font-weight:600;letter-spacing:.02em;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap;max-width:50%}
.agl-editor .agl-chip{padding:1px 6px;border-radius:99px;border:1px solid var(--hair);
  color:var(--muted);white-space:nowrap}
.agl-editor .agl-chip.ok{color:var(--ok);border-color:currentColor}
.agl-editor .agl-chip.wait{color:var(--warn);border-color:currentColor}
.agl-editor .agl-chip.bad{color:var(--err);border-color:currentColor}
.agl-editor button{font:inherit;font-size:11px;cursor:pointer;border-radius:5px;
  border:1px solid var(--hair);background:transparent;color:inherit;padding:2px 8px}
.agl-editor button:hover{border-color:var(--accent);color:var(--accent)}
.agl-editor button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}

.agl-editor .agl-conflict{display:none;gap:8px;align-items:center;flex:0 0 auto;
  padding:6px 8px;font-size:11px;border-bottom:1px solid var(--warn);
  background:color-mix(in srgb,var(--warn) 14%,transparent)}
.agl-editor .agl-conflict.on{display:flex}

.agl-editor .agl-ed-body{position:relative;flex:1 1 auto;min-height:0;display:flex;overflow:hidden}
.agl-editor .agl-gutter{flex:0 0 auto;overflow:hidden;padding:8px 6px 8px 10px;
  text-align:right;color:var(--muted);font-size:12px;line-height:19px;
  border-right:1px solid var(--hair);user-select:none;background:var(--surface-2)}
.agl-editor .agl-gutter b{display:block;font-weight:400}
.agl-editor .agl-gutter b.err{color:var(--err);font-weight:700}
.agl-editor .agl-gutter b.warn{color:var(--warn);font-weight:700}
.agl-editor .agl-gutter b.cur{color:var(--ink)}
.agl-editor .agl-scroll{position:relative;flex:1 1 auto;min-width:0;overflow:hidden}
.agl-editor .agl-hl,.agl-editor .agl-ta{position:absolute;inset:0;margin:0;padding:8px 12px;
  font:inherit;font-size:12px;line-height:19px;white-space:pre;overflow:auto;
  tab-size:2;-moz-tab-size:2;border:0;box-sizing:border-box}
.agl-editor .agl-hl{pointer-events:none;overflow:hidden;z-index:0}
.agl-editor .agl-ta{z-index:1;background:transparent;color:transparent;caret-color:var(--ink);
  resize:none;outline:none;overflow:auto;white-space:pre;overflow-wrap:normal}
.agl-editor .agl-ta::selection{background:color-mix(in srgb,var(--accent) 34%,transparent)}
.agl-editor .agl-hl .ln{display:block;min-height:19px}
.agl-editor .agl-hl .ln.sel{background:color-mix(in srgb,var(--accent) 16%,transparent);
  box-shadow:inset 2px 0 0 var(--accent)}
.agl-editor .agl-hl .k{color:var(--accent)}
.agl-editor .agl-hl .kd{color:var(--accent);font-weight:700}
.agl-editor .agl-hl .s{color:var(--ok)}
.agl-editor .agl-hl .n{color:var(--info)}
.agl-editor .agl-hl .c{color:var(--muted);font-style:italic}
.agl-editor .agl-hl .o{color:var(--muted)}
.agl-editor .agl-hl .i{color:var(--ink)}
.agl-editor .agl-hl .x{color:var(--err);text-decoration:underline wavy}
.agl-editor .agl-hl .d-error{text-decoration:underline wavy var(--err);text-underline-offset:3px}
.agl-editor .agl-hl .d-warning{text-decoration:underline wavy var(--warn);text-underline-offset:3px}

.agl-editor .agl-tip{position:absolute;z-index:5;max-width:min(46ch,90%);display:none;
  padding:5px 8px;font-size:11px;line-height:1.4;border-radius:6px;pointer-events:none;
  border:1px solid var(--hair);background:var(--paper);color:var(--ink);
  box-shadow:0 6px 18px rgba(0,0,0,.45)}
.agl-editor .agl-tip.on{display:block}
.agl-editor .agl-tip code{color:var(--muted)}

.agl-editor .agl-msgs{flex:0 0 auto;max-height:30%;overflow:auto;font-size:11px;
  border-top:1px solid var(--hair)}
.agl-editor .agl-msgs .hd{position:sticky;top:0;padding:3px 8px;color:var(--muted);
  background:var(--surface-2);border-bottom:1px solid var(--hair)}
.agl-editor .agl-msgs .row{display:flex;gap:8px;align-items:baseline;padding:3px 8px;
  cursor:pointer;border-left:2px solid transparent}
.agl-editor .agl-msgs .row:hover{background:var(--surface-3)}
.agl-editor .agl-msgs .row.error{border-left-color:var(--err)}
.agl-editor .agl-msgs .row.warning{border-left-color:var(--warn)}
.agl-editor .agl-msgs .row .cd{color:var(--muted);flex:0 0 auto}
.agl-editor .agl-msgs .row .li{color:var(--muted);flex:0 0 auto;min-width:5ch}
.agl-editor .agl-msgs .row .mg{flex:1 1 auto}
.agl-editor .agl-msgs .empty{padding:6px 8px;color:var(--muted)}
`;

const INDENT = '  ';
let cssDone = false;

function injectCss() {
  if (cssDone || document.getElementById('agl-editor-css')) { cssDone = true; return; }
  const st = document.createElement('style');
  st.id = 'agl-editor-css';
  st.textContent = CSS;
  document.head.appendChild(st);
  cssDone = true;
}

/** Décompose un nodeId (`agent::kind::key` du contrat, ou `agent|lane:key` de viz). */
function parseNodeId(id) {
  if (typeof id !== 'string' || !id) return null;
  if (id.includes('::')) {
    const p = id.split('::');
    return { agent: p[0] || '', kind: p[1] || '', key: p.slice(2).join('::') };
  }
  const bar = id.indexOf('|');
  const agent = bar >= 0 ? id.slice(0, bar) : '';
  const rest = bar >= 0 ? id.slice(bar + 1) : id;
  const col = rest.indexOf(':');
  if (col < 0) return { agent, kind: '', key: rest };
  return { agent, kind: rest.slice(0, col), key: rest.slice(col + 1) };
}

export function mountEditor(el) {
  if (!el) throw new Error('mountEditor : élément hôte manquant');
  injectCss();
  el.classList.add('agl-editor');
  el.innerHTML = `
    <div class="agl-ed-bar">
      <span class="agl-ed-file" data-f>—</span>
      <span class="agl-chip" data-rev>rev —</span>
      <span class="agl-chip" data-sync>synchronisé</span>
      <span class="sp"></span>
      <span class="agl-chip" data-diagchip>0 message</span>
      <button type="button" data-save title="Sauvegarder (⌘/Ctrl+S)">Sauvegarder</button>
    </div>
    <div class="agl-conflict" data-conflict>
      <span data-conflict-msg>Conflit de révision : le serveur a une version plus récente.</span>
      <span class="sp"></span>
      <button type="button" data-keep>Garder mon texte</button>
      <button type="button" data-take>Prendre celle du serveur</button>
    </div>
    <div class="agl-ed-body">
      <div class="agl-gutter" data-gutter></div>
      <div class="agl-scroll">
        <pre class="agl-hl" data-hl aria-hidden="true"></pre>
        <textarea class="agl-ta" data-ta spellcheck="false" wrap="off"
          autocomplete="off" autocapitalize="off" autocorrect="off"
          aria-label="Source AGENT-L"></textarea>
        <div class="agl-tip" data-tip role="tooltip"></div>
      </div>
    </div>
    <div class="agl-msgs" data-msgs>
      <div class="hd">Messages</div>
      <div class="empty">Aucun diagnostic.</div>
    </div>`;

  const $ = (s) => el.querySelector(s);
  const ta = $('[data-ta]');
  const hl = $('[data-hl]');
  const gutter = $('[data-gutter]');
  const tip = $('[data-tip]');
  const msgs = $('[data-msgs]');
  const chipRev = $('[data-rev]');
  const chipSync = $('[data-sync]');
  const chipDiag = $('[data-diagchip]');
  const fileLbl = $('[data-f]');
  const conflict = $('[data-conflict]');
  const conflictMsg = $('[data-conflict-msg]');

  const st = {
    rev: 0,             // révision confirmée par le serveur
    pending: false,     // une édition est en vol / en attente d'envoi
    dirty: false,       // texte local différent de la dernière diffusion serveur
    diags: [],
    byLine: new Map(),
    selLine: 0,
    curLine: 1,
    serverText: null,   // texte serveur retenu pendant un conflit
    lineCount: 1,
    timer: 0,
  };

  // ---------------------------------------------------------------- rendu
  function renderHighlight() {
    const src = ta.value;
    const rows = tokenizeLines(src);
    const parts = new Array(rows.length);
    for (let i = 0; i < rows.length; i += 1) {
      const d = st.byLine.get(i + 1);
      const cls = ['ln'];
      if (d) cls.push(`d-${d.severity === 'error' ? 'error' : 'warning'}`);
      if (st.selLine === i + 1) cls.push('sel');
      let inner = '';
      for (const t of rows[i]) {
        inner += t.cls ? `<span class="${t.cls}">${esc(t.text)}</span>` : esc(t.text);
      }
      parts[i] = `<span class="${cls.join(' ')}">${inner || ' '}</span>`;
    }
    // ⚠ Jointure SANS séparateur : `.ln` est un bloc, il produit déjà son
    // propre saut de ligne. Un `\n` littéral entre deux blocs, dans un
    // conteneur en `white-space:pre`, ajoutait une ligne vide après CHAQUE
    // ligne — la couche colorée faisait le double de la hauteur du texte, se
    // décalait des numéros de ligne et du curseur, et l'édition devenait
    // impraticable au-delà des premières lignes.
    hl.innerHTML = parts.join('');
    if (rows.length !== st.lineCount) { st.lineCount = rows.length; renderGutter(); }
    else renderGutter();
    syncScroll();
  }

  function renderGutter() {
    let out = '';
    for (let i = 1; i <= st.lineCount; i += 1) {
      const d = st.byLine.get(i);
      const cls = d ? (d.severity === 'error' ? 'err' : 'warn') : (i === st.curLine ? 'cur' : '');
      out += `<b class="${cls}">${i}</b>`;
    }
    gutter.innerHTML = out;
    gutter.scrollTop = ta.scrollTop;
  }

  function syncScroll() {
    hl.scrollTop = ta.scrollTop;
    hl.scrollLeft = ta.scrollLeft;
    gutter.scrollTop = ta.scrollTop;
  }

  function setSync(kind, label) {
    chipSync.className = `agl-chip ${kind}`;
    chipSync.textContent = label;
  }

  // ---------------------------------------------------- diagnostics + panneau
  function applyDiags(list) {
    st.diags = Array.isArray(list) ? list.slice() : [];
    st.byLine = new Map();
    for (const d of st.diags) {
      const ln = Number(d.line) || 0;
      if (!ln) continue;
      const prev = st.byLine.get(ln);
      // L'erreur prime sur l'avertissement pour le soulignement de la ligne.
      if (!prev || (prev.severity !== 'error' && d.severity === 'error')) st.byLine.set(ln, d);
    }
    const errs = st.diags.filter((d) => d.severity === 'error').length;
    const warns = st.diags.length - errs;
    chipDiag.className = `agl-chip ${errs ? 'bad' : (warns ? 'wait' : 'ok')}`;
    chipDiag.textContent = errs || warns
      ? `${errs} erreur${errs > 1 ? 's' : ''} · ${warns} avert.`
      : 'aucun diagnostic';
    renderMessages();
    renderHighlight();
  }

  function renderMessages() {
    if (!st.diags.length) {
      msgs.innerHTML = '<div class="hd">Messages</div><div class="empty">Aucun diagnostic.</div>';
      return;
    }
    const sorted = st.diags.slice().sort((a, b) => (a.line || 0) - (b.line || 0));
    let out = `<div class="hd">Messages — ${sorted.length}</div>`;
    for (let i = 0; i < sorted.length; i += 1) {
      const d = sorted[i];
      const sev = d.severity === 'error' ? 'error' : 'warning';
      out += `<div class="row ${sev}" data-i="${i}" tabindex="0" role="button">`
        + `<span class="cd">${esc(d.code || (sev === 'error' ? 'E' : 'W'))}</span>`
        + `<span class="li">${d.line ? `L${d.line}` : '—'}</span>`
        + `<span class="mg">${esc(d.message || '')}</span></div>`;
    }
    msgs.innerHTML = out;
    msgs._sorted = sorted;
  }

  msgs.addEventListener('click', (ev) => {
    const row = ev.target.closest('.row');
    if (!row || !msgs._sorted) return;
    const d = msgs._sorted[Number(row.dataset.i)];
    if (d) gotoLine(d.line, d.col);
  });
  msgs.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const row = ev.target.closest('.row');
    if (!row || !msgs._sorted) return;
    ev.preventDefault();
    const d = msgs._sorted[Number(row.dataset.i)];
    if (d) gotoLine(d.line, d.col);
  });

  // ------------------------------------------------------------ navigation
  function offsetOfLine(line, col) {
    const lines = ta.value.split('\n');
    const idx = Math.max(0, Math.min(lines.length - 1, (Number(line) || 1) - 1));
    let off = 0;
    for (let i = 0; i < idx; i += 1) off += lines[i].length + 1;
    const c = Math.max(0, Math.min(lines[idx].length, (Number(col) || 1) - 1));
    return { start: off, end: off + lines[idx].length, caret: off + c };
  }

  function gotoLine(line, col) {
    if (!line) return;
    const o = offsetOfLine(line, col);
    ta.focus();
    ta.setSelectionRange(o.caret, o.caret);
    const lh = 19;
    const target = (line - 1) * lh - ta.clientHeight / 2 + lh;
    ta.scrollTop = Math.max(0, target);
    syncScroll();
    st.curLine = line;
    renderGutter();
  }

  /** Retrouve la ligne de déclaration d'un nœud du graphe (heuristique lexicale). */
  function lineOfNode(nodeId) {
    const p = parseNodeId(nodeId);
    if (!p || !p.key) return 0;
    const kws = LANE_KW[p.kind] || [];
    const key = p.key;
    const tail = key.includes('.') ? key.split('.').pop() : key;
    const lines = ta.value.split('\n');
    let fallback = 0;
    for (let i = 0; i < lines.length; i += 1) {
      const raw = lines[i];
      const t = raw.trim();
      if (!t || t.startsWith('#') || t.startsWith('//')) continue;
      const m = /^([A-Z_]+)\b\s*(.*)$/.exec(t);
      if (!m) continue;
      const kw = m[1];
      const rest = m[2];
      const named = new RegExp(`(^|[^A-Za-z_0-9])${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^A-Za-z_0-9]|$)`);
      const namedTail = new RegExp(`(^|[^A-Za-z_0-9])${tail.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}([^A-Za-z_0-9]|$)`);
      if (kws.length && kws.includes(kw) && (named.test(rest) || namedTail.test(rest))) return i + 1;
      if (!fallback && DECL_KW.has(kw) && named.test(rest)) fallback = i + 1;
    }
    return fallback;
  }

  function selectNode(nodeId) {
    const line = lineOfNode(nodeId);
    st.selLine = line;
    renderHighlight();
    if (line) {
      // Rendre visible sans voler le focus si l'utilisateur est en train de taper.
      const lh = 19;
      const top = (line - 1) * lh;
      if (top < ta.scrollTop || top > ta.scrollTop + ta.clientHeight - lh * 2) {
        ta.scrollTop = Math.max(0, top - ta.clientHeight / 2);
        syncScroll();
      }
    }
  }

  // ------------------------------------------------------------- info-bulle
  function lineFromPointer(ev) {
    const r = ta.getBoundingClientRect();
    const y = ev.clientY - r.top + ta.scrollTop - 8; // 8 = padding haut
    return Math.max(1, Math.min(st.lineCount, Math.floor(y / 19) + 1));
  }

  ta.addEventListener('mousemove', (ev) => {
    const line = lineFromPointer(ev);
    const here = st.diags.filter((d) => Number(d.line) === line);
    if (!here.length) { tip.classList.remove('on'); return; }
    tip.innerHTML = here.map((d) => `<div><code>${esc(d.severity === 'error' ? 'erreur' : 'avert.')} `
      + `${esc(d.code || '')}</code> ${esc(d.message || '')}</div>`).join('');
    const r = ta.getBoundingClientRect();
    const y = (line - 1) * 19 - ta.scrollTop + 8 + 22;
    tip.style.left = `${Math.max(4, Math.min(r.width - 20, ev.clientX - r.left + 8))}px`;
    tip.style.top = `${Math.max(4, y)}px`;
    tip.classList.add('on');
  });
  ta.addEventListener('mouseleave', () => tip.classList.remove('on'));
  ta.addEventListener('scroll', () => { tip.classList.remove('on'); syncScroll(); });

  // ---------------------------------------------------------- édition clavier
  function indentOf(line) { const m = /^[ \t]*/.exec(line); return m ? m[0] : ''; }

  function replaceRange(start, end, text, caret) {
    const v = ta.value;
    ta.value = v.slice(0, start) + text + v.slice(end);
    const c = caret === undefined ? start + text.length : caret;
    ta.setSelectionRange(c, c);
  }

  function selectedLineRange() {
    const v = ta.value;
    const s = v.lastIndexOf('\n', ta.selectionStart - 1) + 1;
    let e = v.indexOf('\n', ta.selectionEnd);
    if (e === -1) e = v.length;
    return { s, e };
  }

  ta.addEventListener('keydown', (ev) => {
    if (ev.key === 'Tab') {
      ev.preventDefault();
      const multi = ta.value.slice(ta.selectionStart, ta.selectionEnd).includes('\n');
      if (multi) {
        const { s, e } = selectedLineRange();
        const block = ta.value.slice(s, e);
        const next = ev.shiftKey
          ? block.replace(/^(\t| {1,2})/gm, '')
          : block.replace(/^/gm, INDENT);
        const v = ta.value;
        ta.value = v.slice(0, s) + next + v.slice(e);
        ta.setSelectionRange(s, s + next.length);
      } else if (ev.shiftKey) {
        const { s } = selectedLineRange();
        const line = ta.value.slice(s, ta.selectionStart);
        const m = /^(\t| {1,2})/.exec(line);
        if (m) {
          const caret = ta.selectionStart - m[0].length;
          replaceRange(s, s + m[0].length, '', caret);
        }
      } else {
        replaceRange(ta.selectionStart, ta.selectionEnd, INDENT);
      }
      onInput();
      return;
    }

    if (ev.key === 'Enter') {
      const v = ta.value;
      const s = v.lastIndexOf('\n', ta.selectionStart - 1) + 1;
      const before = v.slice(s, ta.selectionStart);
      const after = v.slice(ta.selectionEnd);
      let ind = indentOf(before);
      const opens = /\{\s*$/.test(before);
      if (opens) ind += INDENT;
      ev.preventDefault();
      // `{` suivi de `}` : on ouvre un bloc aéré et on place le curseur dedans.
      if (opens && /^\s*\}/.test(after)) {
        const ins = `\n${ind}\n${indentOf(before)}`;
        replaceRange(ta.selectionStart, ta.selectionEnd, ins, ta.selectionStart + 1 + ind.length);
      } else {
        replaceRange(ta.selectionStart, ta.selectionEnd, `\n${ind}`);
      }
      onInput();
      return;
    }

    if (ev.key === '}') {
      const v = ta.value;
      const s = v.lastIndexOf('\n', ta.selectionStart - 1) + 1;
      const before = v.slice(s, ta.selectionStart);
      if (/^[ \t]+$/.test(before) && before.length >= INDENT.length) {
        ev.preventDefault();
        replaceRange(s, ta.selectionStart, before.slice(0, before.length - INDENT.length) + '}');
        onInput();
      }
      return;
    }

    if (ev.key === '{') {
      // Fermeture automatique uniquement en fin de ligne, jamais au milieu.
      const rest = ta.value.slice(ta.selectionEnd);
      if (ta.selectionStart === ta.selectionEnd && /^\s*(\n|$)/.test(rest)) {
        ev.preventDefault();
        const c = ta.selectionStart + 1;
        replaceRange(ta.selectionStart, ta.selectionEnd, '{}', c);
        onInput();
      }
    }
  });

  // ------------------------------------------------------------- envoi serveur
  function scheduleSend() {
    if (!st.dirty) bus.emit('ui.dirty', { dirty: true });  // pastille « non enregistré » (M3)
    st.dirty = true;
    setSync('wait', 'édition…');
    clearTimeout(st.timer);
    st.timer = setTimeout(flush, 350);
  }

  function flush() {
    clearTimeout(st.timer);
    if (!st.dirty) return;
    st.pending = true;
    setSync('wait', 'envoi…');
    bus.send({ type: 'edit', text: ta.value, rev: st.rev });
  }

  function onInput() {
    const v = ta.value;
    const upto = v.slice(0, ta.selectionStart);
    st.curLine = upto.split('\n').length;
    renderHighlight();
    scheduleSend();
  }

  ta.addEventListener('input', onInput);
  ta.addEventListener('click', () => {
    st.curLine = ta.value.slice(0, ta.selectionStart).split('\n').length;
    renderGutter();
  });
  ta.addEventListener('keyup', (ev) => {
    if (ev.key.startsWith('Arrow') || ev.key === 'Home' || ev.key === 'End') {
      st.curLine = ta.value.slice(0, ta.selectionStart).split('\n').length;
      renderGutter();
    }
  });

  function save() {
    flush();
    // Deux voies volontairement redondantes et idempotentes : le canal temps
    // réel (§2 du contrat, tel que spécifié à M5) et l'événement local que M3
    // convertit en POST /api/save (c'est lui qui possède la route).
    bus.emit('ui.save', {});
    bus.send({ type: 'save' });
    setSync('ok', 'sauvegarde…');
  }
  el.querySelector('[data-save]').addEventListener('click', save);
  // Raccourci local (le raccourci global appartient à M3 ; les deux sont idempotents).
  ta.addEventListener('keydown', (ev) => {
    if ((ev.metaKey || ev.ctrlKey) && (ev.key === 's' || ev.key === 'S')) {
      ev.preventDefault();
      save();
    }
  });

  // --------------------------------------------------------------- conflits
  function showConflict(serverText, serverRev, message) {
    st.serverText = typeof serverText === 'string' ? serverText : null;
    conflictMsg.textContent = message
      || `Conflit de révision (serveur rev ${serverRev}). Votre texte est conservé.`;
    conflict.classList.add('on');
    setSync('bad', 'conflit');
  }
  function hideConflict() { conflict.classList.remove('on'); st.serverText = null; }

  el.querySelector('[data-keep]').addEventListener('click', () => {
    // On adopte la révision serveur comme base, mais on renvoie NOTRE texte.
    hideConflict();
    st.dirty = true;
    flush();
  });
  el.querySelector('[data-take]').addEventListener('click', () => {
    if (st.serverText !== null) setText(st.serverText, st.rev);
    hideConflict();
    st.dirty = false;
    st.pending = false;
    setSync('ok', 'synchronisé');
  });

  function setText(text, rev) {
    const keepTop = ta.scrollTop;
    const caret = ta.selectionStart;
    ta.value = typeof text === 'string' ? text : '';
    ta.setSelectionRange(Math.min(caret, ta.value.length), Math.min(caret, ta.value.length));
    if (typeof rev === 'number') { st.rev = rev; chipRev.textContent = `rev ${rev}`; }
    renderHighlight();
    ta.scrollTop = keepTop;
    syncScroll();
  }

  // ------------------------------------------------------------- bus (entrée)
  const offs = [];
  const on = (t, fn) => { bus.on(t, fn); offs.push([t, fn]); };

  on('hello', (m) => {
    const s = (m && m.session) || {};
    if (s.file) fileLbl.textContent = s.file;
    if (typeof s.rev === 'number') { st.rev = s.rev; chipRev.textContent = `rev ${s.rev}`; }
  });

  on('session', (m) => {
    // Certains hôtes rediffusent la session complète (cf. /api/session).
    if (!m) return;
    if (m.file) fileLbl.textContent = m.file;
    if (typeof m.source === 'string') setText(m.source, m.rev);
    if (m.diags) applyDiags(m.diags);
    st.dirty = false;
    st.pending = false;
    setSync('ok', 'synchronisé');
  });

  on('source', (m) => {
    if (!m || typeof m.text !== 'string') return;
    const mine = ta.value;
    if (m.text === mine) {
      if (typeof m.rev === 'number') { st.rev = m.rev; chipRev.textContent = `rev ${m.rev}`; }
      st.dirty = false;
      st.pending = false;
      hideConflict();
      setSync('ok', 'synchronisé');
      return;
    }
    if (st.dirty || st.pending) {
      // Édition concurrente : on ne touche JAMAIS au texte de l'utilisateur.
      if (typeof m.rev === 'number') { st.rev = m.rev; chipRev.textContent = `rev ${m.rev}`; }
      showConflict(m.text, m.rev, `Le serveur a diffusé une autre version (rev ${m.rev}). `
        + 'Votre texte est conservé.');
      return;
    }
    setText(m.text, m.rev);
    setSync('ok', 'synchronisé');
  });

  on('diagnostics', (m) => { if (m && Array.isArray(m.diags)) applyDiags(m.diags); });

  // M3 confirme l'écriture disque (POST /api/save) : on éteint l'indicateur.
  on('ui.saved', () => {
    st.dirty = false;
    setSync('ok', 'enregistré');
    bus.emit('ui.dirty', { dirty: false });
  });

  on('error', (m) => {
    const msg = (m && m.message) || '';
    if (/rev|409|conflit|périm/i.test(msg)) showConflict(null, st.rev, `${msg} — votre texte est conservé.`);
    else setSync('bad', 'erreur serveur');
  });

  on('ui.select', (p) => { if (p && p.nodeId) selectNode(p.nodeId); });

  // État déjà connu du bus au moment du montage.
  try {
    const s = busState && busState.session;
    if (s) {
      if (s.file) fileLbl.textContent = s.file;
      if (typeof s.source === 'string') setText(s.source, s.rev);
    }
    if (busState && Array.isArray(busState.diags)) applyDiags(busState.diags);
  } catch (err) {
    // Le bus peut ne pas être encore initialisé : ce n'est pas une erreur fatale.
    setSync('wait', 'en attente du bus');
  }

  renderHighlight();

  return {
    el,
    focus: () => ta.focus(),
    getText: () => ta.value,
    setText,
    gotoLine,
    selectNode,
    applyDiags,
    save,
    destroy() {
      clearTimeout(st.timer);
      for (const [t, fn] of offs) if (fn) bus.off(t, fn);
      el.innerHTML = '';
      el.classList.remove('agl-editor');
    },
  };
}

export default mountEditor;
