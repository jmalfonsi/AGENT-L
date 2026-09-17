// AGENT-L Studio — M5 · inspecteur (panneau de droite, à onglets)
// Onglets : Nœud · État · Métriques · Approbations.
// L'onglet Approbations est le point d'interaction humaine : toute arrivée de
// message `prompt` bascule l'inspecteur dessus et met le panneau en alerte.
import { bus, state as busState } from './bus.js';

const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;' };
const esc = (s) => String(s == null ? '' : s).replace(/[&<>]/g, (c) => ESC[c]);

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

const LANE_FR = {
  observe: 'Perception', hypothesis: 'Hypothèse', goal: 'But', decide: 'Décision',
  event: 'Événement', plan: 'Plan', tool: 'Outil', policy: 'Politique',
  memory: 'Mémoire', message: 'Message', belief: 'Croyance', shared: 'Mémoire partagée',
};

const METRIC_FR = {
  ticks: 'ticks', tool_calls: 'appels d’outils', blocked: 'blocages',
  approvals: 'approbations', verify_pass: 'verify ok', verify_fail: 'verify échec',
  llm_calls: 'appels LLM', memory_writes: 'écritures mémoire',
  inferences: 'inférences', plans_synthesized: 'plans synthétisés',
  messages_sent: 'messages émis', messages_received: 'messages reçus',
  shared_writes: 'écritures partagées', shared_conflicts: 'conflits partagés',
  domain_clamps: 'écrêtages de domaine', effect_drift: 'dérive d’effets',
};
// Compteurs dont une valeur non nulle mérite l'œil.
const METRIC_TONE = {
  blocked: 'warn', verify_fail: 'bad', effect_drift: 'warn',
  shared_conflicts: 'warn', domain_clamps: 'warn', approvals: 'warn',
  verify_pass: 'ok',
};

const CSS = `
.agl-inspector{display:flex;flex-direction:column;height:100%;min-height:0;overflow:hidden;
  background:var(--surface);color:var(--ink);
  font-family:var(--font-ui);font-size:12px}
.agl-inspector .agl-tabs{display:flex;flex:0 0 auto;border-bottom:1px solid var(--hair);
  background:var(--surface-2)}
.agl-inspector .agl-tabs button{flex:1 1 auto;font:inherit;font-size:11px;padding:6px 4px;
  background:transparent;border:0;border-bottom:2px solid transparent;color:var(--muted);
  cursor:pointer;transition:color .15s ease,border-color .15s ease;position:relative}
.agl-inspector .agl-tabs button:hover{color:var(--ink)}
.agl-inspector .agl-tabs button[aria-selected="true"]{color:var(--accent);
  border-bottom-color:var(--accent)}
.agl-inspector .agl-tabs button:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.agl-inspector .agl-tabs .bdg{display:inline-block;min-width:16px;margin-left:5px;padding:0 4px;
  border-radius:99px;font-size:10px;line-height:15px;background:var(--err);color:#fff}
.agl-inspector .agl-tabs .bdg[hidden]{display:none}
.agl-inspector .agl-pane{display:none;flex:1 1 auto;min-height:0;overflow:auto;padding:10px 12px}
.agl-inspector .agl-pane.on{display:block}
.agl-inspector h4{margin:0 0 6px;font-size:10px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);font-weight:600}
.agl-inspector section{margin-bottom:14px}
.agl-inspector .empty{color:var(--muted);padding:8px 0}
.agl-inspector code,.agl-inspector .mono{font-family:var(--font-mono)}

.agl-inspector .kv{display:grid;grid-template-columns:minmax(6ch,40%) 1fr;gap:3px 10px}
.agl-inspector .kv dt{color:var(--muted);overflow-wrap:anywhere}
.agl-inspector .kv dd{margin:0;overflow-wrap:anywhere;
  font-family:var(--font-mono)}
.agl-inspector .tag{display:inline-block;padding:1px 6px;border-radius:99px;font-size:10px;
  border:1px solid var(--hair);color:var(--muted)}
.agl-inspector .tag.risk-high,.agl-inspector .tag.bad{color:var(--err);border-color:currentColor}
.agl-inspector .tag.risk-medium,.agl-inspector .tag.warn{color:var(--warn);border-color:currentColor}
.agl-inspector .tag.risk-low,.agl-inspector .tag.ok{color:var(--ok);border-color:currentColor}
.agl-inspector .nid{font-size:10px;color:var(--muted);overflow-wrap:anywhere;
  font-family:var(--font-mono)}
.agl-inspector .lst{margin:0;padding-left:16px}
.agl-inspector .lst li{margin:2px 0;overflow-wrap:anywhere}
.agl-inspector .lnk{color:var(--accent);cursor:pointer;text-decoration:none}
.agl-inspector .lnk:hover{text-decoration:underline}

.agl-inspector .hyp{margin-bottom:9px}
.agl-inspector .hyp .top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.agl-inspector .hyp .nm{overflow-wrap:anywhere}
.agl-inspector .hyp .pv{font-family:var(--font-mono);
  font-variant-numeric:tabular-nums}
.agl-inspector .bar{position:relative;height:7px;margin-top:3px;border-radius:99px;overflow:hidden;
  background:var(--surface-2)}
.agl-inspector .bar i{position:absolute;inset:0 auto 0 0;display:block;border-radius:99px;
  background:var(--accent);transition:width .18s ease}
.agl-inspector .bar i.sup{background:var(--ok)}
.agl-inspector .bar u{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--warn);
  opacity:.9}
.agl-inspector .hyp .sub{margin-top:3px;font-size:10.5px;color:var(--muted);
  font-family:var(--font-mono);overflow-wrap:anywhere}
.agl-inspector .hyp .ev{margin:2px 0 0;padding-left:14px;font-size:10.5px;color:var(--muted)}

.agl-inspector .tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(88px,1fr));gap:6px}
.agl-inspector .tile{padding:6px 8px;border-radius:7px;border:1px solid var(--hair);
  background:var(--surface-2)}
.agl-inspector .tile b{display:block;font-size:17px;line-height:1.15;font-variant-numeric:tabular-nums;
  font-family:var(--font-mono)}
.agl-inspector .tile span{font-size:10px;color:var(--muted)}
.agl-inspector .tile.ok b{color:var(--ok)}
.agl-inspector .tile.warn b{color:var(--warn)}
.agl-inspector .tile.bad b{color:var(--err)}

.agl-inspector .goal{display:flex;justify-content:space-between;gap:8px;padding:3px 0;
  border-bottom:1px dashed var(--hair)}
.agl-inspector .goal:last-child{border-bottom:0}

.agl-inspector .ask{border:1px solid var(--warn);border-radius:9px;padding:10px;
  margin-bottom:10px;background:color-mix(in srgb,var(--warn) 10%,transparent)}
.agl-inspector .ask.fresh{animation:agl-ask-in .18s ease-out}
@keyframes agl-ask-in{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
.agl-inspector .ask .q{font-size:13px;font-weight:600;margin-bottom:5px;overflow-wrap:anywhere}
.agl-inspector .ask .why{color:var(--warn);font-size:11px;margin-bottom:4px}
.agl-inspector .ask pre{margin:6px 0 0;padding:6px 8px;max-height:150px;overflow:auto;
  border-radius:6px;background:var(--paper);font-size:10.5px;white-space:pre-wrap;
  font-family:var(--font-mono)}
.agl-inspector .ask .acts{display:flex;gap:6px;margin-top:9px;flex-wrap:wrap}
.agl-inspector .ask input{flex:1 1 12ch;min-width:0;font:inherit;padding:4px 7px;border-radius:6px;
  border:1px solid var(--hair);background:var(--paper);color:inherit}
.agl-inspector .ask button{font:inherit;font-size:11.5px;padding:4px 12px;border-radius:6px;
  cursor:pointer;border:1px solid var(--hair);background:transparent;color:inherit}
.agl-inspector .ask button.yes{border-color:var(--ok);color:var(--ok);font-weight:600}
.agl-inspector .ask button.no{border-color:var(--err);color:var(--err)}
.agl-inspector .ask button:hover{background:var(--surface-3)}
.agl-inspector .ask button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.agl-inspector .ask.done{opacity:.5;border-color:var(--hair);background:transparent}
.agl-inspector.alert{box-shadow:inset 0 0 0 2px var(--warn)}
`;

let cssDone = false;
function injectCss() {
  if (cssDone || document.getElementById('agl-inspector-css')) { cssDone = true; return; }
  const st = document.createElement('style');
  st.id = 'agl-inspector-css';
  st.textContent = CSS;
  document.head.appendChild(st);
  cssDone = true;
}

const fmtNum = (v) => (typeof v === 'number' && Number.isFinite(v)
  ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : esc(v));

/** Rend une valeur de croyance/charge utile en une chaîne courte et sûre. */
function fmtVal(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return fmtNum(v);
  if (typeof v === 'boolean') return v ? 'vrai' : 'faux';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v); } catch (err) { return String(v); }
}

/** Extrait prior/posterior/seuil/naïf d'une ligne de trace BAYES (bayes.py::render). */
function parseBayesLine(text, detail) {
  if (typeof text !== 'string') return null;
  const m = /^(.+?):\s*([0-9.]+)\s*(?:→|->)\s*([0-9.]+)\s*\((?:≥|<)\s*seuil\s*([0-9.]+)\)(?:\s*\[naïf\s*:\s*([0-9.]+)\])?/.exec(text.trim());
  if (!m) return null;
  const ev = [];
  if (typeof detail === 'string' && detail) {
    for (const part of detail.split('|')) {
      const t = part.trim();
      if (t) ev.push(t);
    }
  }
  return {
    name: m[1].trim(),
    prior: parseFloat(m[2]),
    posterior: parseFloat(m[3]),
    threshold: parseFloat(m[4]),
    naive: m[5] === undefined ? null : parseFloat(m[5]),
    evidence: ev,
  };
}

export function mountInspector(el) {
  if (!el) throw new Error('mountInspector : élément hôte manquant');
  injectCss();
  el.classList.add('agl-inspector');
  el.innerHTML = `
    <div class="agl-tabs" role="tablist">
      <button type="button" role="tab" data-tab="node" aria-selected="true">Nœud</button>
      <button type="button" role="tab" data-tab="state" aria-selected="false">État</button>
      <button type="button" role="tab" data-tab="metrics" aria-selected="false">Métriques</button>
      <button type="button" role="tab" data-tab="asks" aria-selected="false">Approbations<span class="bdg" data-bdg hidden>0</span></button>
    </div>
    <div class="agl-pane on" data-pane="node" role="tabpanel"></div>
    <div class="agl-pane" data-pane="state" role="tabpanel"></div>
    <div class="agl-pane" data-pane="metrics" role="tabpanel"></div>
    <div class="agl-pane" data-pane="asks" role="tabpanel"></div>`;

  const panes = {};
  for (const p of el.querySelectorAll('.agl-pane')) panes[p.dataset.pane] = p;
  const tabs = [...el.querySelectorAll('[data-tab]')];
  const badge = el.querySelector('[data-bdg]');

  const st = {
    graph: null,
    nodesById: new Map(),
    edges: [],
    selected: null,
    last: null,          // dernier message `state`
    bayes: new Map(),    // nom d'hypothèse → détail bayésien analysé
    prompts: [],         // {msg, answered, value}
    tab: 'node',
  };

  function show(name) {
    if (!panes[name]) return;
    st.tab = name;
    for (const t of tabs) t.setAttribute('aria-selected', String(t.dataset.tab === name));
    for (const k of Object.keys(panes)) panes[k].classList.toggle('on', k === name);
  }
  for (const t of tabs) t.addEventListener('click', () => show(t.dataset.tab));

  // ------------------------------------------------------------------ graphe
  function indexGraph(g) {
    st.graph = g || null;
    st.nodesById = new Map();
    st.edges = (g && Array.isArray(g.edges)) ? g.edges : [];
    const nodes = (g && Array.isArray(g.nodes)) ? g.nodes : [];
    for (const n of nodes) if (n && n.id) st.nodesById.set(n.id, n);
    renderNode();
  }

  function detailLines(n) {
    const d = n && n.detail;
    if (!d) return [];
    if (Array.isArray(d)) return d.filter((x) => x !== null && x !== undefined).map(String);
    return String(d).split('\n').filter(Boolean);
  }

  /** Politiques qui s'appliquent : arêtes reliant le nœud à un nœud de lane `policy`. */
  function policiesFor(id) {
    const out = [];
    for (const e of st.edges) {
      if (!e) continue;
      const other = e.from === id ? e.to : (e.to === id ? e.from : null);
      if (!other) continue;
      const n = st.nodesById.get(other);
      if (n && (n.lane === 'policy' || n.kind === 'policy')) out.push({ node: n, edge: e });
    }
    return out;
  }

  function neighbours(id) {
    const inc = []; const outg = [];
    for (const e of st.edges) {
      if (!e) continue;
      if (e.to === id && st.nodesById.has(e.from)) inc.push({ n: st.nodesById.get(e.from), e });
      if (e.from === id && st.nodesById.has(e.to)) outg.push({ n: st.nodesById.get(e.to), e });
    }
    return { inc, outg };
  }

  function nodeLink(n) {
    return `<a class="lnk" data-goto="${esc(n.id)}">${esc(n.label || n.id)}</a>`;
  }

  function renderNode() {
    const pane = panes.node;
    const id = st.selected;
    if (!id) {
      pane.innerHTML = '<div class="empty">Aucun nœud sélectionné. '
        + 'Cliquez un nœud du canevas ou une ligne de la chronologie.</div>';
      return;
    }
    const n = st.nodesById.get(id);
    const p = parseNodeId(id);
    const lane = (n && (n.lane || n.kind)) || (p && p.kind) || '';
    const label = (n && n.label) || (p && p.key) || id;
    const lines = detailLines(n);

    // Les détails de viz sont des phrases ; on met en avant celles qui portent
    // le contrat de l'outil, le risque, l'effet ou la garde.
    const pick = (re) => lines.filter((l) => re.test(l));
    const risk = pick(/risque|RISK|SIDE_EFFECT|effet de bord/i);
    const eff = pick(/EFFECT|effet|REQUIRES|COST|OUTCOME|UTILITY/i);
    const guard = pick(/quand|WHEN|si |IF |garde|condition/i);
    const contract = lines.filter((l) => !risk.includes(l) && !eff.includes(l) && !guard.includes(l));

    const pol = policiesFor(id);
    const { inc, outg } = neighbours(id);

    const sect = (title, html) => (html ? `<section><h4>${title}</h4>${html}</section>` : '');
    const list = (arr) => (arr.length
      ? `<ul class="lst">${arr.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : '');

    const riskTone = /high|élev/i.test(risk.join(' ')) ? 'risk-high'
      : (/medium|moyen/i.test(risk.join(' ')) ? 'risk-medium'
        : (risk.length ? 'risk-low' : ''));

    pane.innerHTML = `
      <section>
        <h4>${esc(LANE_FR[lane] || lane || 'Nœud')}</h4>
        <div style="font-size:14px;font-weight:600;overflow-wrap:anywhere">${esc(label)}</div>
        <div class="nid">${esc(id)}</div>
        ${n && n.agent ? `<div style="margin-top:4px"><span class="tag">agent ${esc(n.agent)}</span></div>` : ''}
        ${risk.length ? `<div style="margin-top:5px"><span class="tag ${riskTone}">${esc(risk[0])}</span></div>` : ''}
      </section>
      ${sect('Contrat', list(contract) || (n ? '' : '<div class="empty">Nœud absent du graphe courant.</div>'))}
      ${sect('Risque / effet de bord', list(risk.slice(1)))}
      ${sect('Effet (planificateur)', list(eff))}
      ${sect('Garde', list(guard))}
      ${sect('Politiques applicables', pol.length
    ? `<ul class="lst">${pol.map((x) => `<li>${nodeLink(x.node)}`
        + `${x.edge.label ? ` <span class="tag warn">${esc(x.edge.label)}</span>` : ''}`
        + `${detailLines(x.node).length ? `<div class="nid">${esc(detailLines(x.node).join(' · '))}</div>` : ''}</li>`).join('')}</ul>`
    : '<div class="empty">Aucune politique ne cible ce nœud.</div>')}
      ${sect('Entrées', inc.length ? `<ul class="lst">${inc.map((x) => `<li>${nodeLink(x.n)}`
    + `${x.e.label ? ` <span class="nid">${esc(x.e.label)}</span>` : ''}</li>`).join('')}</ul>` : '')}
      ${sect('Sorties', outg.length ? `<ul class="lst">${outg.map((x) => `<li>${nodeLink(x.n)}`
    + `${x.e.label ? ` <span class="nid">${esc(x.e.label)}</span>` : ''}</li>`).join('')}</ul>` : '')}`;
  }

  panes.node.addEventListener('click', (ev) => {
    const a = ev.target.closest('[data-goto]');
    if (!a) return;
    bus.emit('ui.select', { nodeId: a.dataset.goto });
  });

  // -------------------------------------------------------------------- état
  function renderState() {
    const pane = panes.state;
    const m = st.last;
    if (!m) { pane.innerHTML = '<div class="empty">Aucun état reçu. Lancez un run.</div>'; return; }

    const beliefs = m.beliefs && typeof m.beliefs === 'object' ? m.beliefs : {};
    const hyps = m.hypotheses && typeof m.hypotheses === 'object' ? m.hypotheses : {};
    const goals = m.goals && typeof m.goals === 'object' ? m.goals : {};

    const bKeys = Object.keys(beliefs).sort();
    const hKeys = Object.keys(hyps).sort((a, b) => (hyps[b] || 0) - (hyps[a] || 0));
    const gKeys = Object.keys(goals).sort();

    let html = '<section><h4>Hypothèses</h4>';
    if (!hKeys.length) html += '<div class="empty">Aucune hypothèse.</div>';
    for (const k of hKeys) {
      const p = Number(hyps[k]);
      const info = st.bayes.get(k);
      const th = info && Number.isFinite(info.threshold) ? info.threshold : null;
      const pct = Math.max(0, Math.min(1, Number.isFinite(p) ? p : 0)) * 100;
      const sup = th !== null && p >= th;
      html += `<div class="hyp"><div class="top"><span class="nm">${esc(k)}</span>`
        + `<span class="pv">${Number.isFinite(p) ? p.toFixed(3) : '—'}</span></div>`
        + `<div class="bar"><i class="${sup ? 'sup' : ''}" style="width:${pct.toFixed(1)}%"></i>`
        + (th !== null ? `<u style="left:${(th * 100).toFixed(1)}%" title="seuil ${th}"></u>` : '')
        + '</div>';
      if (info) {
        const bits = [`a priori ${info.prior.toFixed(2)}`];
        if (th !== null) bits.push(`seuil ${th.toFixed(2)} ${sup ? '✓' : '✗'}`);
        if (info.naive !== null && Number.isFinite(info.naive)) {
          bits.push(`naïf ${info.naive.toFixed(3)} → calibré ${info.posterior.toFixed(3)}`);
        }
        html += `<div class="sub">${esc(bits.join(' · '))}</div>`;
        if (info.evidence.length) {
          html += `<ul class="ev">${info.evidence.slice(0, 8)
            .map((e) => `<li>${esc(e)}</li>`).join('')}</ul>`;
        }
      }
      html += '</div>';
    }
    html += '</section>';

    html += '<section><h4>Buts</h4>';
    if (!gKeys.length) html += '<div class="empty">Aucun but.</div>';
    else {
      for (const k of gKeys) {
        const v = String(goals[k]);
        const tone = /satisf|done|ok|atteint/i.test(v) ? 'ok'
          : (/fail|échec|violat|breach/i.test(v) ? 'bad' : 'warn');
        html += `<div class="goal"><span>${esc(k)}</span>`
          + `<span class="tag ${tone}">${esc(v)}</span></div>`;
      }
    }
    html += '</section>';

    html += '<section><h4>Croyances</h4>';
    if (!bKeys.length) html += '<div class="empty">Aucune croyance.</div>';
    else {
      html += `<dl class="kv">${bKeys.map((k) => `<dt>${esc(k)}</dt>`
        + `<dd>${esc(fmtVal(beliefs[k]))}</dd>`).join('')}</dl>`;
    }
    html += '</section>';

    pane.innerHTML = html;
  }

  // ---------------------------------------------------------------- métriques
  function renderMetrics() {
    const pane = panes.metrics;
    const m = (st.last && st.last.metrics) || (busState && busState.lastState
      && busState.lastState.metrics) || null;
    if (!m || typeof m !== 'object' || !Object.keys(m).length) {
      pane.innerHTML = '<div class="empty">Aucune métrique. Lancez un run.</div>';
      return;
    }
    const keys = Object.keys(m);
    const known = Object.keys(METRIC_FR).filter((k) => keys.includes(k));
    const rest = keys.filter((k) => !METRIC_FR[k]).sort();
    const tile = (k) => {
      const v = m[k];
      const tone = (Number(v) > 0 && METRIC_TONE[k]) ? METRIC_TONE[k] : '';
      return `<div class="tile ${tone}"><b>${esc(fmtVal(v))}</b>`
        + `<span>${esc(METRIC_FR[k] || k)}</span></div>`;
    };
    pane.innerHTML = `<section><h4>Compteurs du runtime</h4>`
      + `<div class="tiles">${known.concat(rest).map(tile).join('')}</div></section>`;
  }

  // -------------------------------------------------------------- approbations
  function pendingCount() { return st.prompts.filter((p) => !p.answered).length; }

  function refreshBadge() {
    const n = pendingCount();
    badge.hidden = n === 0;
    badge.textContent = String(n);
    el.classList.toggle('alert', n > 0);
  }

  function renderAsks(freshId) {
    const pane = panes.asks;
    if (!st.prompts.length) {
      pane.innerHTML = '<div class="empty">Aucune demande. Les approbations et questions '
        + 'du runtime apparaîtront ici.</div>';
      refreshBadge();
      return;
    }
    const cards = st.prompts.slice().reverse().map((p) => {
      const m = p.msg;
      const ask = m.mode === 'ask';
      let payload = '';
      if (m.payload !== undefined && m.payload !== null) {
        let txt;
        try { txt = JSON.stringify(m.payload, null, 2); } catch (err) { txt = String(m.payload); }
        payload = `<pre>${esc(txt)}</pre>`;
      }
      const acts = p.answered
        ? `<div class="acts"><span class="tag ok">répondu : ${esc(fmtVal(p.value))}</span></div>`
        : (ask
          ? `<div class="acts"><input type="text" data-in="${esc(m.promptId)}" `
            + `placeholder="votre réponse…" aria-label="Réponse"/>`
            + `<button type="button" class="yes" data-send="${esc(m.promptId)}">Répondre</button></div>`
          : `<div class="acts">`
            + `<button type="button" class="yes" data-yes="${esc(m.promptId)}">Approuver</button>`
            + `<button type="button" class="no" data-no="${esc(m.promptId)}">Refuser</button></div>`);
      return `<div class="ask ${p.answered ? 'done' : ''} ${freshId === m.promptId ? 'fresh' : ''}" `
        + `data-card="${esc(m.promptId)}">`
        + `<div class="q">${esc(m.question || (ask ? 'Question' : 'Approbation requise'))}</div>`
        + (m.reason ? `<div class="why">${esc(m.reason)}</div>` : '')
        + (m.nodeId ? `<div class="nid"><a class="lnk" data-goto="${esc(m.nodeId)}">${esc(m.nodeId)}</a></div>` : '')
        + payload + acts + '</div>';
    }).join('');
    pane.innerHTML = cards;
    refreshBadge();
  }

  function answer(promptId, value) {
    const p = st.prompts.find((x) => x.msg.promptId === promptId);
    if (!p || p.answered) return;
    p.answered = true;
    p.value = value;
    bus.send({ type: 'reply', promptId, value });
    renderAsks();
  }

  panes.asks.addEventListener('click', (ev) => {
    const a = ev.target.closest('[data-goto]');
    if (a) { bus.emit('ui.select', { nodeId: a.dataset.goto }); return; }
    const yes = ev.target.closest('[data-yes]');
    if (yes) { answer(yes.dataset.yes, true); return; }
    const no = ev.target.closest('[data-no]');
    if (no) { answer(no.dataset.no, false); return; }
    const send = ev.target.closest('[data-send]');
    if (send) {
      const card = send.closest('[data-card]');
      const input = card && card.querySelector('[data-in]');
      answer(send.dataset.send, input ? input.value : '');
    }
  });
  panes.asks.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Enter') return;
    const input = ev.target.closest('[data-in]');
    if (!input) return;
    ev.preventDefault();
    answer(input.dataset.in, input.value);
  });

  // ------------------------------------------------------------- bus (entrée)
  const offs = [];
  const on = (t, fn) => { bus.on(t, fn); offs.push([t, fn]); };

  on('graph', (m) => indexGraph(m && m.graph));
  on('session', (m) => { if (m && m.graph) indexGraph(m.graph); });

  on('state', (m) => {
    if (!m) return;
    st.last = m;
    renderState();
    renderMetrics();
  });

  on('run.finished', (m) => {
    if (m && m.metrics) {
      st.last = Object.assign({}, st.last || {}, { metrics: m.metrics });
      renderMetrics();
    }
  });

  on('trace', (m) => {
    if (!m || m.kind !== 'BAYES') return;
    const info = parseBayesLine(m.text, m.detail);
    if (!info) return;
    st.bayes.set(info.name, info);
    if (st.tab === 'state') renderState();
  });

  on('prompt', (m) => {
    if (!m || !m.promptId) return;
    if (st.prompts.some((p) => p.msg.promptId === m.promptId)) return;
    st.prompts.push({ msg: m, answered: false, value: null });
    show('asks');
    renderAsks(m.promptId);
    // Focus clavier : la demande doit être actionnable sans souris.
    const card = panes.asks.querySelector(`[data-card]`);
    const first = card && card.querySelector('input, button');
    if (first) {
      try { first.focus({ preventScroll: false }); } catch (err) { first.focus(); }
    }
  });

  on('run.started', () => { st.bayes.clear(); });

  on('ui.select', (p) => {
    if (!p || !p.nodeId) return;
    st.selected = p.nodeId;
    renderNode();
    if (st.tab !== 'asks' || !pendingCount()) show('node');
  });

  // Amorçage sur l'état déjà connu du bus.
  try {
    if (busState) {
      if (busState.graph) indexGraph(busState.graph);
      if (busState.lastState) { st.last = busState.lastState; renderState(); renderMetrics(); }
    }
  } catch (err) {
    // Bus non initialisé : les panneaux resteront vides jusqu'au premier message.
  }
  renderNode();
  if (!st.last) { renderState(); renderMetrics(); }
  renderAsks();

  return {
    el,
    show,
    select: (nodeId) => { st.selected = nodeId; renderNode(); },
    pending: pendingCount,
    destroy() {
      for (const [t, fn] of offs) bus.off(t, fn);
      el.innerHTML = '';
      el.classList.remove('agl-inspector', 'alert');
    },
  };
}

export default mountInspector;
