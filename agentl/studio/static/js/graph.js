/**
 * graph.js — modèle de graphe du canevas AGENT-L Studio (module M4).
 *
 * Rôle : transformer le blob produit par `agentl.viz.build_program()` (envoyé
 * par le serveur dans le message `{"type":"graph","graph":{...}}`) en un
 * modèle stable, indexé et géométriquement complet, exploitable par
 * `canvas.js` sans jamais retoucher au JSON d'origine.
 *
 * Ce fichier ne touche pas au DOM : il ne fait que des maths et des index.
 *
 * Vocabulaire (identique à viz.py) :
 *   lane   ∈ observe · hypothesis · goal · trigger · plan · tool · policy · society
 *   kind   ∈ observe · hypothesis · goal · decide · event · inbox · plan · tool ·
 *            policy · message
 *
 * ⚠ Divergence constatée avec le CONTRACT.md §3 (signalée, non corrigée) :
 *   le contrat annonce `nodeId = f"{agent}::{kind}::{key}"` alors que viz.py
 *   produit `f"{agent}|{lane}:{key}"` (le préfixe agent n'existant qu'en
 *   multi-agents). Comme M4 doit illuminer les nœuds à partir des `nodeId`
 *   émis par M1, `resolveNode()` accepte LES DEUX formes, plus quelques
 *   variantes tolérantes, afin qu'aucune trace ne se perde en route.
 */

// --------------------------------------------------------------------------
// Constantes géométriques
// --------------------------------------------------------------------------

/** Largeur par défaut d'un nœud (viz.py : NODE_W). */
export const NODE_W = 232;
/** Hauteur d'un nœud rendu par le canevas (le SVG est plus compact que viz). */
export const NODE_H = 76;
/** Hauteur d'une rangée de lane (viz.py : ROW_H). */
export const ROW_H = 158;
/** Largeur d'une colonne de lane (viz.py : LANE_W). */
export const LANE_W = 320;

/** Hauteur de l'entête teintée d'une lane (canevas : bandeau + libellé). */
export const LANE_HEAD_H = 30;
/** Marge intérieure d'une colonne : un nœud n'y colle jamais au bord. */
export const LANE_PAD = 10;

/**
 * Contraint un nœud à **rester dans sa colonne et sous l'intitulé**.
 *
 * Le rangement par lanes est la seule chose qui rende le graphe lisible : un
 * nœud déplacé dans la colonne voisine ment sur sa nature. On laisse donc le
 * déplacement vertical libre et on borne l'horizontale à la bande de la lane ;
 * on interdit par ailleurs de remonter sous l'entête, qui masquerait le
 * libellé de colonne.
 */
export function clampToLane(model, n, x, y) {
  const lane = (model && model.laneByKey && model.laneByKey.get(n.lane))
    || (model && model.lanes && model.lanes[0])
    || { x: 0, w: LANE_W };
  const w = isNum(n.w) ? n.w : NODE_W;
  const min = lane.x + LANE_PAD;
  const max = lane.x + lane.w - w - LANE_PAD;
  const cx = max < min ? lane.x + (lane.w - w) / 2 : Math.min(Math.max(x, min), max);
  const cy = Math.max(y, LANE_HEAD_H + LANE_PAD);
  return { x: cx, y: cy };
}

/** Ordre canonique des lanes (viz.py : LANES). */
export const LANES = [
  'observe', 'hypothesis', 'goal', 'trigger', 'plan', 'tool', 'policy', 'society',
];

/** Libellés de lane (repris de viz.py pour rester cohérent avec les rapports). */
export const LANE_LABEL = {
  observe: 'PERCEPTION · OBSERVE',
  hypothesis: 'INFÉRENCE · HYPOTHESIS',
  goal: 'OBJECTIFS · GOAL',
  trigger: 'DÉCLENCHEMENT · DECIDE / EVENT',
  plan: 'STRATÉGIE · PLAN',
  tool: 'ACTION · TOOL',
  policy: 'GARDE-FOUS · POLICY',
  society: 'SOCIÉTÉ · MESSAGE / DELEGATE',
};

/** kind de nœud → lane d'appartenance (utile pour les nodeId « ::kind:: »). */
export const KIND_LANE = {
  observe: 'observe',
  hypothesis: 'hypothesis',
  goal: 'goal',
  decide: 'trigger',
  event: 'trigger',
  inbox: 'trigger',
  plan: 'plan',
  tool: 'tool',
  policy: 'policy',
  message: 'society',
  memory: 'society',
};

/** Glyphe affiché dans la pastille d'un nœud, par kind. */
export const KIND_ICON = {
  observe: '◎',
  hypothesis: 'λ',
  goal: '◆',
  decide: '⑂',
  event: '⚡',
  inbox: '✉',
  plan: '❐',
  tool: '⚙',
  policy: '⛨',
  message: '➤',
  memory: '❃',
};

/** Phase du flux d'exécution (§2 du contrat) → lane à mettre en avant. */
export const PHASE_LANE = {
  OBSERVE: 'observe',
  PERCEIVE: 'observe',
  BELIEF: 'hypothesis',
  BELIEFS: 'hypothesis',
  BAYES: 'hypothesis',
  HYPOTHESIS: 'hypothesis',
  GOAL: 'goal',
  GOALS: 'goal',
  DECIDE: 'trigger',
  EVENT: 'trigger',
  TRIGGER: 'trigger',
  PLAN: 'plan',
  PLANNER: 'plan',
  STEP: 'plan',
  ACT: 'tool',
  TOOL: 'tool',
  POLICY: 'policy',
  MESSAGE: 'society',
  SHARED: 'society',
  DELEGATE: 'society',
  MEMORY: 'society',
};

/** kind de trace (§2) → lane concernée, pour retrouver un nœud sans nodeId. */
export const TRACE_LANE = {
  OBSERVE: 'observe',
  BELIEF: 'hypothesis',
  BAYES: 'hypothesis',
  GOAL: 'goal',
  PLAN: 'plan',
  STEP: 'plan',
  PLANNER: 'plan',
  TOOL: 'tool',
  BLOCKED: 'tool',
  APPROVAL: 'policy',
  EVENT: 'trigger',
  MESSAGE: 'society',
  SHARED: 'society',
  DELEGATE: 'society',
  MEMORY: 'society',
};

// --------------------------------------------------------------------------
// Utilitaires
// --------------------------------------------------------------------------

/** Vrai si `v` est un nombre exploitable comme coordonnée. */
function isNum(v) {
  return typeof v === 'number' && Number.isFinite(v);
}

/** Clamp élémentaire. */
export function clamp(v, lo, hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

/** Normalise une chaîne pour comparaison tolérante (casse / séparateurs). */
function fold(s) {
  return String(s == null ? '' : s).trim().toLowerCase();
}

// --------------------------------------------------------------------------
// Normalisation du graphe
// --------------------------------------------------------------------------

/**
 * Construit le modèle de canevas à partir du graphe serveur.
 *
 * @param {object|null} raw   graphe `build_program()` (peut être null/partiel)
 * @param {object} [opts]     { positions: {id: {x,y}} } positions utilisateur
 * @returns {object} modèle normalisé
 */
export function normalizeGraph(raw, opts = {}) {
  const g = (raw && typeof raw === 'object') ? raw : {};
  const overrides = opts.positions || {};

  const nodeW = isNum(g.node_w) ? g.node_w : NODE_W;
  const rowH = isNum(g.row_h) ? g.row_h : ROW_H;

  // -- nœuds ---------------------------------------------------------------
  const seen = new Set();
  const nodes = [];
  for (const rawNode of Array.isArray(g.nodes) ? g.nodes : []) {
    if (!rawNode || typeof rawNode !== 'object') continue;
    const id = String(rawNode.id == null ? '' : rawNode.id);
    if (!id || seen.has(id)) continue;   // ids vides ou dupliqués : ignorés
    seen.add(id);
    const kind = fold(rawNode.kind) || 'plan';
    const lane = fold(rawNode.lane) || KIND_LANE[kind] || 'plan';
    nodes.push({
      id,
      kind,
      lane,
      agent: rawNode.agent == null ? '' : String(rawNode.agent),
      label: rawNode.label == null ? id : String(rawNode.label),
      detail: (rawNode.detail && typeof rawNode.detail === 'object') ? rawNode.detail : {},
      // géométrie (complétée plus bas)
      x: isNum(rawNode.x) ? rawNode.x : null,
      y: isNum(rawNode.y) ? rawNode.y : null,
      w: nodeW,
      h: NODE_H,
      pinned: false,       // vrai si l'utilisateur a déplacé le nœud
      badges: [],          // rempli par computeBadges()
      // état d'exécution (mutable, remis à zéro par resetRuntime())
      hits: 0,
      status: '',          // '' | 'active' | 'error' | 'ok'
    });
  }

  // -- lanes ---------------------------------------------------------------
  let lanes = (Array.isArray(g.lanes) ? g.lanes : [])
    .filter((l) => l && typeof l === 'object')
    .map((l, i) => ({
      key: fold(l.key) || LANES[i] || `lane${i}`,
      label: l.label == null ? (LANE_LABEL[fold(l.key)] || fold(l.key)) : String(l.label),
      x: isNum(l.x) ? l.x : i * LANE_W,
      w: isNum(l.w) && l.w > 0 ? l.w : LANE_W,
    }));
  if (!lanes.length) {
    // Le serveur n'a pas fourni de lanes : on les déduit des nœuds présents.
    const used = LANES.filter((k) => nodes.some((n) => n.lane === k));
    lanes = used.map((k, i) => ({
      key: k, label: LANE_LABEL[k] || k, x: i * LANE_W, w: LANE_W,
    }));
  }
  const laneByKey = new Map(lanes.map((l) => [l.key, l]));

  // -- bandes d'agents -----------------------------------------------------
  const bands = (Array.isArray(g.bands) ? g.bands : [])
    .filter((b) => b && typeof b === 'object')
    .map((b) => ({
      name: b.name == null ? '' : String(b.name),
      version: b.version == null ? '' : String(b.version),
      top: isNum(b.top) ? b.top : 0,
      height: isNum(b.height) ? b.height : rowH,
    }));

  // -- arêtes --------------------------------------------------------------
  const edges = [];
  const edgeSeen = new Set();
  for (const e of Array.isArray(g.edges) ? g.edges : []) {
    if (!e || typeof e !== 'object') continue;
    const from = String(e.from == null ? '' : e.from);
    const to = String(e.to == null ? '' : e.to);
    if (!from || !to || !seen.has(from) || !seen.has(to)) continue;
    const kind = fold(e.kind);
    const key = `${from}\u0000${to}\u0000${kind}`;
    if (edgeSeen.has(key)) continue;
    edgeSeen.add(key);
    edges.push({
      id: `e${edges.length}`,
      from, to, kind,
      label: e.label == null ? '' : String(e.label),
      d: '',            // chemin SVG, calculé par routeEdges()
      mid: { x: 0, y: 0 },
    });
  }

  const model = {
    name: g.name == null ? 'agent' : String(g.name),
    version: g.version == null ? '' : String(g.version),
    description: g.description == null ? '' : String(g.description),
    multi: !!g.multi,
    lanes,
    laneByKey,
    bands,
    nodes,
    edges,
    nodeW,
    rowH,
    width: isNum(g.width) && g.width > 0 ? g.width : lanes.length * LANE_W,
    height: isNum(g.height) && g.height > 0 ? g.height : rowH * 4,
    byId: new Map(),
    alias: new Map(),
    adj: new Map(),
  };

  completeLayout(model);
  applyPositions(model, overrides);
  buildIndex(model);
  buildAdjacency(model);
  for (const n of model.nodes) { n.badges = computeBadges(n); n.llm = usesLLM(n); }
  routeEdges(model);
  recomputeBounds(model);
  return model;
}

/**
 * Complète les positions manquantes : viz.py fournit déjà `x`/`y` pour chaque
 * nœud (via `_layout`), on les respecte scrupuleusement ; mais un graphe
 * partiel, un nœud ajouté à chaud ou une lane inconnue peuvent en manquer —
 * on les empile alors au bas de leur colonne de lane.
 */
export function completeLayout(model) {
  // Prochaine rangée libre par lane, calculée à partir de l'existant.
  const nextY = new Map();
  for (const n of model.nodes) {
    if (isNum(n.y)) {
      const cur = nextY.get(n.lane);
      const cand = n.y + model.rowH;
      if (cur == null || cand > cur) nextY.set(n.lane, cand);
    }
  }
  for (const n of model.nodes) {
    const lane = model.laneByKey.get(n.lane)
      || model.lanes[0]
      || { x: 0, w: LANE_W };
    if (!isNum(n.x)) n.x = lane.x + (lane.w - n.w) / 2;
    if (!isNum(n.y)) {
      const y = nextY.get(n.lane) != null ? nextY.get(n.lane) : 12;
      n.y = y;
      nextY.set(n.lane, y + model.rowH);
    }
    // `_layout` de viz.py commence ses rangées à y=12, sous l'entête du HTML
    // qu'il produit — mais le canevas dessine, lui, un bandeau de 30 px : la
    // première rangée recouvrait donc l'intitulé de colonne.
    const c = clampToLane(model, n, n.x, n.y);
    n.x = c.x; n.y = c.y;
  }
}

/** Applique un dictionnaire `{id: {x, y}}` de positions utilisateur. */
export function applyPositions(model, positions) {
  if (!positions) return;
  for (const n of model.nodes) {
    const p = positions[n.id];
    if (p && isNum(p.x) && isNum(p.y)) {
      // Une position mémorisée avant l'introduction du confinement peut être
      // hors colonne : on la ramène plutôt que de la rejeter.
      const c = clampToLane(model, n, p.x, p.y);
      n.x = c.x;
      n.y = c.y;
      n.pinned = true;
    }
  }
}

/** Extrait les positions déplacées par l'utilisateur (pour localStorage). */
export function extractPositions(model) {
  const out = {};
  for (const n of model.nodes) {
    if (n.pinned) out[n.id] = { x: Math.round(n.x), y: Math.round(n.y) };
  }
  return out;
}

// --------------------------------------------------------------------------
// Index : nodeId (toutes formes) → nœud
// --------------------------------------------------------------------------

/**
 * Construit `byId` (exact) et `alias` (formes tolérantes). Les alias ambigus
 * (deux nœuds pour la même clé) sont volontairement retirés : mieux vaut ne
 * rien illuminer qu'illuminer le mauvais nœud.
 */
export function buildIndex(model) {
  model.byId = new Map();
  const alias = new Map();
  const ambiguous = new Set();
  const put = (key, node) => {
    if (!key) return;
    const k = fold(key);
    if (!k || model.byId.has(k)) return;
    if (alias.has(k) && alias.get(k) !== node) { ambiguous.add(k); return; }
    alias.set(k, node);
  };

  for (const n of model.nodes) {
    model.byId.set(n.id, n);
    // Décomposition de la forme viz : "agent|lane:key" ou "lane:key".
    const bar = n.id.indexOf('|');
    const rest = bar >= 0 ? n.id.slice(bar + 1) : n.id;
    const agent = bar >= 0 ? n.id.slice(0, bar) : n.agent;
    const colon = rest.indexOf(':');
    const key = colon >= 0 ? rest.slice(colon + 1) : rest;
    n.key = key;

    put(n.id, n);
    // Forme annoncée par le contrat §3 : agent::kind::key
    put(`${agent}::${n.kind}::${key}`, n);
    put(`${agent}::${n.lane}::${key}`, n);
    put(`${n.kind}::${key}`, n);
    put(`${n.lane}::${key}`, n);
    // Formes viz sans préfixe d'agent (mono-agent) et variantes courantes.
    put(`${n.lane}:${key}`, n);
    put(`${n.kind}:${key}`, n);
    put(`${agent}|${n.lane}:${key}`, n);
    put(`${agent}.${key}`, n);
    // Clé nue : seulement si elle n'entre pas en collision.
    put(key, n);
    put(n.label, n);
  }
  for (const k of ambiguous) alias.delete(k);
  model.alias = alias;
}

/**
 * Résout un `nodeId` de trace vers un nœud du modèle, en tolérant les
 * différences de format entre M1 et viz.py. Retourne `null` si introuvable.
 *
 * @param {object} model
 * @param {string|null} nodeId
 * @param {object} [hint]  { kind, agent } indices issus de la trace
 */
export function resolveNode(model, nodeId, hint = {}) {
  if (!model) return null;
  if (nodeId) {
    const raw = String(nodeId);
    const direct = model.byId.get(raw);
    if (direct) return direct;
    const f = fold(raw);
    const byAlias = model.alias.get(f);
    if (byAlias) return byAlias;
    // Dernier recours : la clé finale après le dernier séparateur.
    const tail = f.split('::').pop().split(':').pop();
    const byTail = model.alias.get(tail);
    if (byTail) return byTail;
    return null;
  }
  // Pas de nodeId : on tente une résolution par (agent, lane du kind, texte).
  const lane = TRACE_LANE[String(hint.kind || '').toUpperCase()];
  if (!lane || !hint.key) return null;
  const cand = model.alias.get(fold(`${lane}:${hint.key}`));
  return cand || null;
}

/** Construit l'adjacence nœud → arêtes entrantes / sortantes. */
export function buildAdjacency(model) {
  const adj = new Map();
  const slot = (id) => {
    let s = adj.get(id);
    if (!s) { s = { in: [], out: [] }; adj.set(id, s); }
    return s;
  };
  for (const n of model.nodes) slot(n.id);
  for (const e of model.edges) {
    slot(e.from).out.push(e);
    slot(e.to).in.push(e);
  }
  model.adj = adj;
}

/** Arêtes incidentes à un nœud (entrantes + sortantes). */
export function incidentEdges(model, nodeId) {
  const s = model.adj.get(nodeId);
  if (!s) return [];
  return s.in.concat(s.out);
}

/** Cherche l'arête reliant deux nœuds (dans un sens ou l'autre). */
export function findEdge(model, fromId, toId) {
  const s = model.adj.get(fromId);
  if (!s) return null;
  for (const e of s.out) if (e.to === toId) return e;
  for (const e of s.in) if (e.from === toId) return e;
  return null;
}

// --------------------------------------------------------------------------
// Badges lisibles sans clic
// --------------------------------------------------------------------------

/**
 * Calcule les pastilles affichées sur un nœud : risque, effets de bord, seuil
 * bayésien, cadence, destinataire… Chaque badge = {text, tone}.
 * `tone` ∈ neutral | warn | danger | info.
 */
/**
 * Ce nœud peut-il interroger le LLM ?
 *
 * Propriété **statique**, lisible dans le programme : `viz.py` note chaque
 * étape `REASON` d'un plan. C'est le seul endroit où le langage confie une
 * décision à un oracle non déterministe — donc le seul qu'un lecteur doive
 * repérer sans avoir à ouvrir le nœud.
 */
export function usesLLM(n) {
  if (!n || n.kind !== 'plan') return false;
  const steps = (n.detail && n.detail.steps) || [];
  return steps.some((st) => (st.acts || []).some((a) => /REASON/.test(String(a))));
}

export function computeBadges(n) {
  const d = n.detail || {};
  const out = [];
  const push = (text, tone) => {
    if (text) out.push({ text: String(text), tone: tone || 'neutral' });
  };

  if (n.kind === 'tool') {
    const risk = fold(d.risk);
    if (risk) {
      const tone = /high|critical|élev|eleve/.test(risk) ? 'danger'
        : (/med|moy/.test(risk) ? 'warn' : 'neutral');
      push(`RISK ${String(d.risk).toUpperCase()}`, tone);
    }
    if (Array.isArray(d.side_effects) && d.side_effects.length) {
      push(`SIDE_EFFECT ×${d.side_effects.length}`, 'warn');
    }
    if (isNum(d.cost)) push(`coût ${d.cost}`, 'neutral');
    if (d.requires) push('REQUIRES', 'info');
  } else if (n.kind === 'hypothesis') {
    if (d.threshold != null) push(`P ≥ ${d.threshold}`, 'info');
    if (d.prior != null) push(`prior ${d.prior}`, 'neutral');
    const ev = Array.isArray(d.evidence) ? d.evidence.length : 0;
    if (ev) push(`${ev} témoin${ev > 1 ? 's' : ''}`, 'neutral');
  } else if (n.kind === 'goal') {
    if (d.mode) push(String(d.mode).toUpperCase(), 'info');
    if (d.weight != null) push(`poids ${d.weight}`, 'neutral');
  } else if (n.kind === 'policy') {
    const eff = String(d.effect || '').toUpperCase();
    if (eff) {
      const tone = (eff === 'NEVER' || eff === 'DENY') ? 'danger'
        : (eff === 'REQUIRE_APPROVAL' ? 'warn' : 'info');
      push(eff, tone);
    }
    if (d.target) push(`→ ${d.target}`, 'neutral');
  } else if (n.kind === 'plan') {
    const st = Array.isArray(d.steps) ? d.steps.length : 0;
    push(`${st} étape${st > 1 ? 's' : ''}`, 'neutral');
    if (d.when) push('WHEN', 'info');
    if (usesLLM(n)) push('🧠 LLM', 'warn');
  } else if (n.kind === 'observe') {
    const lines = Array.isArray(d.lines) ? d.lines : [];
    push(lines.length ? lines[0] : 'chaque tick', 'neutral');
  } else if (n.kind === 'message') {
    push(d.delegate ? 'DELEGATE' : 'MESSAGE', 'info');
    if (d.to) push(`→ ${d.to}`, 'neutral');
  } else if (n.kind === 'inbox') {
    if (d.message) push(`✉ ${d.message}`, 'info');
    if (d.then) push(`→ ${d.then}`, 'neutral');
  } else if (n.kind === 'decide') {
    if (d.then) push(`→ ${d.then}`, 'info');
  } else if (n.kind === 'event') {
    push('EVENT', 'info');
    if (d.then) push(`→ ${d.then}`, 'neutral');
  }
  return out.slice(0, 3);
}

/** Résumé d'une ligne affiché sous le titre du nœud. */
export function nodeSubtitle(n) {
  const d = n.detail || {};
  switch (n.kind) {
    case 'goal': return String(d.condition || '');
    case 'decide': return String(d.condition || '');
    case 'event': return String(d.when || d.then || '');
    case 'policy': return String(d.guard || '(inconditionnel)');
    case 'tool': return String(d.description || (Array.isArray(d.effects) ? d.effects[0] : '') || '');
    case 'hypothesis': return String(d.description || d.explains || '');
    case 'observe': return String(d.root ? `source ${d.root}` : '');
    case 'inbox': return String(d.when || '');
    case 'message': return String(d.from ? `de ${d.from}` : '');
    case 'plan': return String(d.when || '');
    default: return '';
  }
}

// --------------------------------------------------------------------------
// Routage des arêtes (béziers avec ports d'entrée / sortie)
// --------------------------------------------------------------------------

/** Port de sortie d'un nœud (bord droit, milieu). */
export function portOut(n) {
  return { x: n.x + n.w, y: n.y + n.h / 2 };
}

/** Port d'entrée d'un nœud (bord gauche, milieu). */
export function portIn(n) {
  return { x: n.x, y: n.y + n.h / 2 };
}

/**
 * Calcule le chemin SVG d'une arête. Le sens des ports s'inverse quand la
 * cible est à gauche de la source (retour arrière), et une arête « boucle »
 * (même colonne) contourne le nœud par la droite.
 */
export function edgeGeometry(a, b) {
  let p1 = portOut(a);
  let p2 = portIn(b);
  let back = false;
  if (b.x + b.w / 2 < a.x + a.w / 2) {
    p1 = portIn(a);
    p2 = portOut(b);
    back = true;
  }
  const dx = Math.max(48, Math.abs(p2.x - p1.x) * 0.5);
  const s = back ? -1 : 1;
  const c1 = { x: p1.x + dx * s, y: p1.y };
  const c2 = { x: p2.x - dx * s, y: p2.y };
  const d = `M${p1.x.toFixed(1)},${p1.y.toFixed(1)} `
    + `C${c1.x.toFixed(1)},${c1.y.toFixed(1)} `
    + `${c2.x.toFixed(1)},${c2.y.toFixed(1)} `
    + `${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
  // point milieu approché d'une bézier cubique (t = 0.5)
  const mid = {
    x: (p1.x + 3 * c1.x + 3 * c2.x + p2.x) / 8,
    y: (p1.y + 3 * c1.y + 3 * c2.y + p2.y) / 8,
  };
  return { d, mid };
}

/** (Re)calcule le tracé de toutes les arêtes, ou seulement de `subset`. */
export function routeEdges(model, subset) {
  const list = subset || model.edges;
  for (const e of list) {
    const a = model.byId.get(e.from);
    const b = model.byId.get(e.to);
    if (!a || !b) { e.d = ''; continue; }
    const g = edgeGeometry(a, b);
    e.d = g.d;
    e.mid = g.mid;
  }
  return list;
}

/** Recalcule l'enveloppe du graphe (utile après déplacement de nœuds). */
export function recomputeBounds(model) {
  let maxX = model.width;
  let maxY = 0;
  for (const n of model.nodes) {
    if (n.x + n.w + 40 > maxX) maxX = n.x + n.w + 40;
    if (n.y + n.h + 40 > maxY) maxY = n.y + n.h + 40;
  }
  for (const b of model.bands) {
    if (b.top + b.height > maxY) maxY = b.top + b.height;
  }
  model.width = maxX;
  model.height = Math.max(maxY, model.height || 0);
  return model;
}

// --------------------------------------------------------------------------
// Diff : régénération du graphe sans perdre le travail de l'utilisateur
// --------------------------------------------------------------------------

/**
 * Fusionne un nouveau graphe serveur avec l'ancien modèle.
 *
 * Invariants :
 *  - une position déplacée par l'utilisateur (`pinned`) est conservée ;
 *  - les compteurs de passage et l'état d'exécution des nœuds survivants
 *    sont conservés (une édition de source ne doit pas effacer le direct) ;
 *  - les nœuds disparus sont oubliés, les nouveaux prennent la position du
 *    serveur.
 *
 * @returns {{model: object, added: string[], removed: string[], kept: string[]}}
 */
export function diffGraph(oldModel, rawGraph, opts = {}) {
  const positions = Object.assign({}, opts.positions || {});
  if (oldModel) {
    for (const n of oldModel.nodes) {
      if (n.pinned) positions[n.id] = { x: n.x, y: n.y };
    }
  }
  const model = normalizeGraph(rawGraph, { positions });

  const added = [];
  const kept = [];
  const removed = [];
  if (oldModel) {
    const before = oldModel.byId;
    for (const n of model.nodes) {
      const prev = before.get(n.id);
      if (prev) {
        kept.push(n.id);
        // report de l'état d'exécution
        n.hits = prev.hits;
        n.status = prev.status;
      } else {
        added.push(n.id);
      }
    }
    for (const id of before.keys()) {
      if (!model.byId.has(id)) removed.push(id);
    }
  } else {
    for (const n of model.nodes) added.push(n.id);
  }
  return { model, added, kept, removed };
}

/** Remet à zéro l'état d'exécution (appelé sur `run.started`). */
export function resetRuntime(model) {
  if (!model) return;
  for (const n of model.nodes) {
    n.hits = 0;
    n.status = '';
  }
}

// --------------------------------------------------------------------------
// Persistance des positions (localStorage, une entrée par fichier .agent)
// --------------------------------------------------------------------------

const STORE_PREFIX = 'agl.studio.pos:';

/** Clé de stockage pour un fichier donné. */
export function storeKey(file) {
  return STORE_PREFIX + (file || '(sans fichier)');
}

/** Lit les positions persistées ; jamais d'exception (mode privé, quota…). */
export function loadPositions(file) {
  try {
    const raw = window.localStorage.getItem(storeKey(file));
    if (!raw) return {};
    const obj = JSON.parse(raw);
    if (!obj || typeof obj !== 'object') return {};
    const out = {};
    for (const [k, v] of Object.entries(obj)) {
      if (v && isNum(v.x) && isNum(v.y)) out[k] = { x: v.x, y: v.y };
    }
    return out;
  } catch (err) {
    return {};   // stockage indisponible ou corrompu : on repart du layout serveur
  }
}

/** Écrit les positions persistées ; échec silencieux mais non masqué (retour). */
export function savePositions(file, positions) {
  try {
    const key = storeKey(file);
    if (!positions || !Object.keys(positions).length) {
      window.localStorage.removeItem(key);
      return true;
    }
    window.localStorage.setItem(key, JSON.stringify(positions));
    return true;
  } catch (err) {
    return false;
  }
}

// --------------------------------------------------------------------------
// Vue : ajustement à la fenêtre
// --------------------------------------------------------------------------

/**
 * Calcule la transformation (scale, tx, ty) qui fait tenir le graphe — ou un
 * sous-ensemble de nœuds — dans un viewport de `vw × vh` pixels.
 */
export function fitView(model, vw, vh, opts = {}) {
  const pad = isNum(opts.pad) ? opts.pad : 28;
  const maxScale = isNum(opts.maxScale) ? opts.maxScale : 1;
  const minScale = isNum(opts.minScale) ? opts.minScale : 0.08;
  let box = opts.box;
  if (!box) box = boundsOf(model.nodes, model);
  if (!box) return { scale: 1, tx: pad, ty: pad };
  const w = Math.max(1, box.w);
  const h = Math.max(1, box.h);
  const scale = clamp(
    Math.min((vw - pad * 2) / w, (vh - pad * 2) / h),
    minScale, maxScale,
  );
  return {
    scale,
    tx: (vw - w * scale) / 2 - box.x * scale,
    ty: (vh - h * scale) / 2 - box.y * scale,
  };
}

/** Boîte englobante d'une liste de nœuds (ou du graphe entier). */
export function boundsOf(nodes, model) {
  if (!nodes || !nodes.length) {
    if (!model) return null;
    return { x: 0, y: 0, w: model.width, h: model.height };
  }
  let x0 = Infinity; let y0 = Infinity; let x1 = -Infinity; let y1 = -Infinity;
  for (const n of nodes) {
    if (n.x < x0) x0 = n.x;
    if (n.y < y0) y0 = n.y;
    if (n.x + n.w > x1) x1 = n.x + n.w;
    if (n.y + n.h > y1) y1 = n.y + n.h;
  }
  if (!Number.isFinite(x0)) return null;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/** Nœuds dont la boîte intersecte un rectangle monde (cadre de sélection). */
export function nodesInRect(model, rect) {
  const out = [];
  const x1 = rect.x + rect.w;
  const y1 = rect.y + rect.h;
  for (const n of model.nodes) {
    if (n.x < x1 && n.x + n.w > rect.x && n.y < y1 && n.y + n.h > rect.y) out.push(n);
  }
  return out;
}

/** Découpe un texte à `max` caractères avec une ellipse propre. */
export function ellipsis(text, max) {
  const s = String(text == null ? '' : text);
  return s.length > max ? `${s.slice(0, Math.max(1, max - 1))}…` : s;
}
