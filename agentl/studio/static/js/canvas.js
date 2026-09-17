/**
 * canvas.js — canevas de graphe interactif d'AGENT-L Studio (module M4).
 *
 * `mountCanvas(el)` installe dans `el` un canevas SVG **vivant** :
 *   · navigation  : zoom à la molette, pan (glisser le fond, maj+molette),
 *                   boutons), « ajuster à la vue », minimap sur les gros graphes ;
 *   · édition     : nœuds déplaçables (positions persistées par fichier),
 *                   sélection au clic et au cadre, survol qui isole les arêtes ;
 *   · lecture     : lanes teintées et libellées, nœuds typés (forme + icône),
 *                   badges RISK / SIDE_EFFECT / P≥θ lisibles sans clic ;
 *   · direct      : chaque `trace` illumine son `nodeId` (impulsion douce +
 *                   compteur de passages), `BLOCKED` laisse un état d'erreur
 *                   persistant, l'arête empruntée s'anime brièvement, chaque
 *                   `phase` met sa lane en avant, `run.started` remet à zéro.
 *
 * Pourquoi SVG plutôt que <canvas> : le graphe reste du texte sélectionnable et
 * accessible (chaque nœud porte un <title>), les états se pilotent par classes
 * CSS — donc par les variables du design system de M3 — et l'on met à jour
 * quelques attributs plutôt que de tout repeindre. À l'échelle visée (quelques
 * centaines de nœuds) c'est plus rapide qu'un repaint complet par événement,
 * à condition de ne jamais re-rendre l'ensemble : toutes les mises à jour de
 * direct sont accumulées puis appliquées en un seul `requestAnimationFrame`.
 *
 * Zéro dépendance, ESM natif, hors ligne.
 */

import { bus, state } from './bus.js';
import {
  KIND_ICON, PHASE_LANE,
  normalizeGraph, diffGraph, resetRuntime,
  routeEdges, recomputeBounds, incidentEdges, findEdge, resolveNode,
  loadPositions, savePositions, extractPositions,
  fitView, boundsOf, nodesInRect, nodeSubtitle, ellipsis, clamp, clampToLane,
} from './graph.js';

const SVGNS = 'http://www.w3.org/2000/svg';
const MIN_SCALE = 0.08;
const MAX_SCALE = 2.6;
/** Au-delà, on affiche la minimap et on allège le rendu. */
const BIG_GRAPH = 60;
/** Au-delà, on cesse de dessiner les libellés d'arêtes (illisibles + coûteux). */
const MAX_EDGE_LABELS = 140;

// --------------------------------------------------------------------------
// Feuille de style du module (préfixée `.agl-canvas`, injectée une seule fois)
// Seules les variables du design system de M3 sont utilisées pour les couleurs,
// avec une valeur de repli pour rester lisible si un jeton manque.
// --------------------------------------------------------------------------

const STYLE_ID = 'agl-canvas-style';
const CSS = `
.agl-canvas{
  /* Jetons du design system de M3 (studio.css), avec repli sur les couleurs
     historiques de viz.py si un jeton venait à manquer. */
  --agl-observe: var(--lane-observe, #3fb950);
  --agl-hypothesis: var(--lane-hypothesis, #a371f7);
  --agl-goal: var(--lane-goal, #e3b341);
  --agl-trigger: var(--lane-trigger, #58a6ff);
  --agl-plan: var(--lane-plan, #f0883e);
  --agl-tool: var(--lane-tool, #ec6a5e);
  --agl-policy: var(--lane-policy, #db61a2);
  --agl-society: var(--lane-society, #2dd4bf);
  --agl-ink: var(--ink, #e6edf3);
  --agl-muted: var(--muted, #8b949e);
  --agl-line: var(--hair-strong, #39414d);
  --agl-panel: var(--surface, #161b22);
  --agl-bg: var(--paper, #0d1016);
  --agl-accent: var(--accent, #58a6ff);
  --agl-accent-ink: var(--accent-ink, #07101f);
  --agl-danger: var(--err, #ec6a5e);
  --agl-ok: var(--ok, #3fb950);
  --agl-warn: var(--warn, #e3b341);
  --agl-radius: var(--radius, 9px);
  position:relative; width:100%; height:100%; overflow:hidden;
  background:var(--agl-bg); color:var(--agl-ink);
  font:12px/1.35 var(--font-ui, -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif);
  user-select:none; outline:none;
}
.agl-canvas svg.agl-stage{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:grab;touch-action:none}
.agl-canvas.is-panning svg.agl-stage{cursor:grabbing}
.agl-canvas.is-marquee svg.agl-stage{cursor:crosshair}

/* — lanes — */
.agl-lane-bg{opacity:.075}
.agl-lane-sep{stroke:var(--agl-line);stroke-opacity:.35;stroke-width:1}
.agl-lane-hd{opacity:.16}
.agl-lane-tx{font-size:10.5px;font-weight:700;letter-spacing:.09em;
  text-transform:uppercase;dominant-baseline:middle;pointer-events:none}
.agl-lane.is-hot .agl-lane-bg{opacity:.2;transition:opacity 160ms ease}
.agl-lane.is-hot .agl-lane-hd{opacity:.34}
.agl-lane .agl-lane-bg{transition:opacity 160ms ease}

/* — bandes d'agents — */
.agl-band-line{stroke:var(--agl-line);stroke-dasharray:4 5;stroke-opacity:.6}
.agl-band-pill{fill:var(--agl-panel);stroke:var(--agl-line)}
.agl-band-tx{fill:var(--agl-ink);font-size:11.5px;font-weight:700;dominant-baseline:middle}

/* — arêtes — */
.agl-edge{fill:none;stroke:var(--agl-line);stroke-width:1.7;
  transition:stroke-opacity 140ms ease,stroke-width 140ms ease}
.agl-edge.k-evidence{stroke:var(--agl-hypothesis)}
.agl-edge.k-state{stroke:var(--agl-observe)}
.agl-edge.k-achieves{stroke:var(--agl-observe)}
.agl-edge.k-supports,.agl-edge.k-gates{stroke:var(--agl-policy)}
.agl-edge.k-select,.agl-edge.k-invoke{stroke:var(--agl-plan)}
.agl-edge.k-requires{stroke:var(--agl-muted);stroke-dasharray:5 4}
.agl-edge.k-allow{stroke:var(--agl-observe)}
.agl-edge.k-never,.agl-edge.k-deny{stroke:var(--agl-danger)}
.agl-edge.k-require_approval{stroke:var(--agl-warn)}
.agl-edge.k-send,.agl-edge.k-deliver{stroke:var(--agl-society)}
.agl-edge.k-deliver{stroke-dasharray:2 5;stroke-width:2.1}
.agl-edge.is-hot{stroke-width:2.6;stroke-opacity:1}
.agl-edge.is-dim{stroke-opacity:.09}
.agl-edge-label{fill:var(--agl-muted);font-size:9px;text-anchor:middle;pointer-events:none}
.agl-flow{fill:none;stroke:var(--agl-accent);stroke-width:3;stroke-linecap:round;
  stroke-dasharray:14 220;filter:drop-shadow(0 0 4px var(--agl-accent));pointer-events:none}
@keyframes aglFlow{from{stroke-dashoffset:234}to{stroke-dashoffset:0}}
.agl-flow{animation:aglFlow 620ms linear}

/* — nœuds — */
.agl-n{cursor:grab}
.agl-n.is-dragging{cursor:grabbing}
.agl-nbox{fill:var(--agl-panel);stroke:var(--agl-line);stroke-width:1.2}
.agl-nacc{stroke:none}
.agl-nicon{font-size:13px;text-anchor:middle;dominant-baseline:central}
.agl-ntitle{fill:var(--agl-ink);font-size:12px;font-weight:600;dominant-baseline:middle}
.agl-nkind{fill:var(--agl-muted);font-size:8.5px;letter-spacing:.07em;
  text-transform:uppercase;text-anchor:end;dominant-baseline:middle}
.agl-nsub{fill:var(--agl-muted);font-size:10px;dominant-baseline:middle}
.agl-badge-bg{stroke:none;opacity:.18}
.agl-badge-tx{font-size:8.5px;font-weight:700;letter-spacing:.03em;dominant-baseline:middle}
.agl-badge.t-neutral .agl-badge-bg{fill:var(--agl-muted)} .agl-badge.t-neutral .agl-badge-tx{fill:var(--agl-muted)}
.agl-badge.t-info .agl-badge-bg{fill:var(--agl-accent)} .agl-badge.t-info .agl-badge-tx{fill:var(--agl-accent)}
.agl-badge.t-warn .agl-badge-bg{fill:var(--agl-warn)} .agl-badge.t-warn .agl-badge-tx{fill:var(--agl-warn)}
.agl-badge.t-danger .agl-badge-bg{fill:var(--agl-danger)} .agl-badge.t-danger .agl-badge-tx{fill:var(--agl-danger)}
.agl-hits{opacity:0;transition:opacity 140ms ease}
.agl-n.has-hits .agl-hits{opacity:1}
.agl-hits-bg{fill:var(--agl-accent);opacity:.9}
.agl-hits-tx{fill:var(--agl-accent-ink);font-size:9px;font-weight:700;text-anchor:middle;dominant-baseline:central}
.agl-n.is-hover .agl-nbox{stroke:var(--agl-muted)}
.agl-n.is-sel .agl-nbox{stroke:var(--agl-accent);stroke-width:2}
.agl-n.is-dim{opacity:.28;transition:opacity 140ms ease}
.agl-n.st-active .agl-nbox{stroke:var(--agl-accent)}
.agl-n.st-error .agl-nbox{stroke:var(--agl-danger);stroke-width:2}
.agl-n.st-error .agl-nhalo{stroke:var(--agl-danger);opacity:.45}
.agl-n.st-ok .agl-nbox{stroke:var(--agl-ok)}

/* — nœuds qui interrogent le LLM — */
/* Marque permanente : ces nœuds confient une décision à un oracle non
   déterministe, ça se voit sans avoir à ouvrir le nœud. */
.agl-n.is-llm .agl-nbox{stroke:var(--agl-warn);stroke-dasharray:5 3}
.agl-n.is-llm .agl-llm{opacity:.9}
.agl-llm{opacity:0;pointer-events:none}
.agl-llm-ring{fill:none;stroke:var(--agl-warn);stroke-width:2;opacity:.55}
.agl-llm-tx{font-size:11px;text-anchor:middle;dominant-baseline:central}
/* Appel en cours : clignotement franc, impossible à manquer. */
@keyframes aglLLM{
  0%,100%{opacity:.15;stroke-width:2}
  50%{opacity:1;stroke-width:4.5}
}
@keyframes aglLLMBox{
  0%,100%{stroke:var(--agl-warn)}
  50%{stroke:var(--agl-accent)}
}
.agl-n.is-calling .agl-llm{opacity:1}
.agl-n.is-calling .agl-llm-ring{animation:aglLLM 620ms ease-in-out infinite}
.agl-n.is-calling .agl-nbox{stroke-width:2.4;animation:aglLLMBox 620ms ease-in-out infinite}
.agl-n.is-calling .agl-nhalo{stroke:var(--agl-warn);opacity:.5}
.agl-nhalo{fill:none;stroke:var(--agl-accent);stroke-width:2;opacity:0;pointer-events:none}
@keyframes aglPulse{
  0%{opacity:.85;stroke-width:2;transform:scale(1)}
  100%{opacity:0;stroke-width:6;transform:scale(1.035)}
}
/* Copie exacte de aglPulse : alterner les deux relance l'animation sans
   forcer de reflow (indispensable quand des dizaines de nœuds pulsent). */
@keyframes aglPulse2{
  0%{opacity:.85;stroke-width:2;transform:scale(1)}
  100%{opacity:0;stroke-width:6;transform:scale(1.035)}
}
.agl-n.is-pulse-a .agl-nhalo{animation:aglPulse 520ms ease-out;transform-origin:center;transform-box:fill-box}
.agl-n.is-pulse-b .agl-nhalo{animation:aglPulse2 520ms ease-out;transform-origin:center;transform-box:fill-box}

/* — cadre de sélection — */
.agl-marquee{fill:var(--agl-accent);fill-opacity:.10;stroke:var(--agl-accent);
  stroke-width:1;stroke-dasharray:4 3}

/* — habillage (barre d'outils, minimap, message vide) — */
.agl-tools{position:absolute;right:10px;bottom:10px;display:flex;gap:6px;z-index:3}
.agl-tools button{width:28px;height:28px;border-radius:7px;cursor:pointer;
  background:var(--agl-panel);border:1px solid var(--agl-line);color:var(--agl-ink);
  font-size:13px;line-height:1;display:flex;align-items:center;justify-content:center;
  transition:background 140ms ease,border-color 140ms ease}
.agl-tools button:hover{border-color:var(--agl-accent)}
.agl-tools button:focus-visible{outline:2px solid var(--agl-accent);outline-offset:1px}
/* Bascule armée : le suivi de caméra est actif. */
.agl-tools button.is-on{color:var(--agl-accent-ink);background:var(--agl-accent);
  border-color:var(--agl-accent)}
.agl-zoomlabel{position:absolute;right:10px;bottom:44px;font-size:10px;color:var(--agl-muted);z-index:3}
.agl-mini{position:absolute;left:10px;bottom:10px;z-index:3;border:1px solid var(--agl-line);
  border-radius:7px;background:var(--agl-panel);
  cursor:crosshair;display:none}
.agl-canvas.has-mini .agl-mini{display:block}
.agl-mini-n{opacity:.75}
.agl-mini-vp{fill:var(--agl-accent);fill-opacity:.14;stroke:var(--agl-accent);stroke-width:1}
.agl-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  color:var(--agl-muted);font-size:12px;pointer-events:none;text-align:center;padding:24px}
.agl-hint{position:absolute;left:10px;top:8px;color:var(--agl-muted);font-size:10.5px;
  z-index:3;pointer-events:none;opacity:.75}

@media (prefers-reduced-motion: reduce){
  .agl-canvas *{animation:none !important;transition:none !important}
  /* Sans animation, l'appel en cours doit rester lisible : contour franc. */
  .agl-n.is-calling .agl-nbox{stroke:var(--agl-accent);stroke-width:3.5}
  .agl-n.is-calling .agl-llm{opacity:1}
  .agl-n.is-calling .agl-llm-ring{opacity:1;stroke-width:3}
}
`;

/** Injecte la feuille de style du module (idempotent). */
function ensureStyle(doc) {
  if (doc.getElementById(STYLE_ID)) return;
  const st = doc.createElement('style');
  st.id = STYLE_ID;
  st.textContent = CSS;
  doc.head.appendChild(st);
}

/** Raccourci de création d'élément SVG. */
function svg(tag, attrs, parent) {
  const e = document.createElementNS(SVGNS, tag);
  if (attrs) for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}

/** Couleur de lane sous forme de variable CSS. */
function laneVar(lane) {
  return `var(--agl-${lane}, var(--agl-muted))`;
}

/** Rayon d'arrondi par kind : la forme distingue les familles au premier coup d'œil. */
function kindRadius(kind) {
  if (kind === 'hypothesis') return 18;      // inférence : capsule
  if (kind === 'decide' || kind === 'event' || kind === 'inbox') return 3;  // déclencheur : anguleux
  if (kind === 'tool') return 6;
  if (kind === 'policy') return 10;
  if (kind === 'message' || kind === 'memory') return 14;
  return 9;
}

// --------------------------------------------------------------------------
// Montage
// --------------------------------------------------------------------------

/**
 * Monte le canevas dans `el`.
 * @param {HTMLElement} el conteneur (doit avoir une taille)
 * @returns {{destroy():void, setGraph(g:object):void, focusNode(id:string):void}}
 */
export function mountCanvas(el) {
  if (!el || !el.appendChild) {
    throw new TypeError('mountCanvas(el) : conteneur HTML invalide');
  }
  ensureStyle(el.ownerDocument || document);

  const root = el;
  root.classList.add('agl-canvas');
  root.setAttribute('tabindex', '0');
  root.setAttribute('role', 'application');
  root.setAttribute('aria-label', 'Canevas du graphe AGENT-L');
  root.innerHTML = '';

  // -- squelette SVG -------------------------------------------------------
  const stage = svg('svg', { class: 'agl-stage', 'aria-hidden': 'false' }, root);
  const view = svg('g', { class: 'agl-view' }, stage);
  const gLanes = svg('g', { class: 'agl-lanes' }, view);
  const gBands = svg('g', { class: 'agl-bands' }, view);
  const gEdges = svg('g', { class: 'agl-edges' }, view);
  const gFlow = svg('g', { class: 'agl-flows' }, view);
  const gNodes = svg('g', { class: 'agl-nodes' }, view);
  const gOverlay = svg('g', { class: 'agl-overlay' }, view);

  const hint = document.createElement('div');
  hint.className = 'agl-hint';
  hint.textContent = 'molette = zoom · glisser le fond = déplacer · maj+molette = horizontal · les nœuds restent dans leur colonne · maj+glisser = sélection';
  root.appendChild(hint);

  const empty = document.createElement('div');
  empty.className = 'agl-empty';
  empty.textContent = 'Aucun graphe : ouvrez un fichier .agent.';
  root.appendChild(empty);

  const zoomLabel = document.createElement('div');
  zoomLabel.className = 'agl-zoomlabel';
  root.appendChild(zoomLabel);

  const tools = document.createElement('div');
  tools.className = 'agl-tools';
  const mkBtn = (label, title, fn) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = label;
    b.title = title;
    b.setAttribute('aria-label', title);
    b.addEventListener('click', (ev) => { ev.preventDefault(); fn(); root.focus(); });
    tools.appendChild(b);
    return b;
  };
  mkBtn('+', 'Zoom avant', () => zoomBy(1.18));
  mkBtn('−', 'Zoom arrière', () => zoomBy(1 / 1.18));
  mkBtn('⤢', 'Ajuster à la vue (F)', () => fit());
  mkBtn('⌖', 'Recentrer la sélection', () => centerSelection());
  const followBtn = mkBtn('◉', 'Suivre le nœud en action pendant un run '
    + '(déplace la vue, ne change jamais le zoom)', () => {
    follow = !follow;
    syncFollowBtn();
    if (follow && lastTracedId) followNode(lastTracedId);
  });
  root.appendChild(tools);

  const mini = svg('svg', { class: 'agl-mini', width: 170, height: 110 });
  root.appendChild(mini);
  const gMiniNodes = svg('g', { class: 'agl-mini-nodes' }, mini);
  const miniVp = svg('rect', { class: 'agl-mini-vp', rx: 2 }, mini);

  // -- état ----------------------------------------------------------------
  let model = null;                 // modèle normalisé courant
  let file = '';                    // fichier .agent courant (clé localStorage)
  let announcedFile = '';           // fichier annoncé par le dernier `hello`
  const nodeEls = new Map();        // id → {g, box, halo, hits, hitsTx, sub}
  const edgeEls = new Map();        // id → {path, label}
  const laneEls = new Map();        // key → <g>
  const selection = new Set();
  let hoverId = null;
  let hotLane = null;
  let lastTracedId = null;
  let scale = 1; let tx = 24; let ty = 24;
  let reduced = false;
  let destroyed = false;

  const mq = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  const syncReduced = () => { reduced = !!(mq && mq.matches); };
  syncReduced();
  if (mq) {
    if (mq.addEventListener) mq.addEventListener('change', syncReduced);
    else if (mq.addListener) mq.addListener(syncReduced);
  }

  // File d'attente des mises à jour de direct, vidée par frame.
  const dirty = new Set();
  const pendingFlow = [];
  let raf = 0;

  /** Programme un vidage groupé des mises à jour au prochain frame. */
  function schedule() {
    if (raf || destroyed) return;
    raf = window.requestAnimationFrame(() => { raf = 0; flush(); });
  }

  // ---------------------------------------------------------------- rendu

  /** Applique la transformation de vue (pan/zoom) et rafraîchit la minimap. */
  function applyTransform() {
    view.setAttribute('transform', `translate(${tx.toFixed(2)},${ty.toFixed(2)}) scale(${scale.toFixed(4)})`);
    zoomLabel.textContent = `${Math.round(scale * 100)} %`;
    updateMiniViewport();
  }

  /** Coordonnées monde d'un événement souris. */
  function toWorld(ev) {
    const r = stage.getBoundingClientRect();
    return {
      x: (ev.clientX - r.left - tx) / scale,
      y: (ev.clientY - r.top - ty) / scale,
    };
  }

  /** Reconstruit intégralement le SVG (uniquement sur changement de graphe). */
  function render() {
    gLanes.innerHTML = '';
    gBands.innerHTML = '';
    gEdges.innerHTML = '';
    gFlow.innerHTML = '';
    gNodes.innerHTML = '';
    gOverlay.innerHTML = '';
    gMiniNodes.innerHTML = '';
    nodeEls.clear();
    edgeEls.clear();
    laneEls.clear();

    if (!model || !model.nodes.length) {
      empty.style.display = 'flex';
      root.classList.remove('has-mini');
      return;
    }
    empty.style.display = 'none';

    const H = model.height;
    // -- lanes : bande teintée + entête libellée --------------------------
    for (const L of model.lanes) {
      const g = svg('g', { class: `agl-lane lane-${L.key}` }, gLanes);
      svg('rect', {
        class: 'agl-lane-bg', x: L.x, y: 0, width: L.w, height: H,
        fill: laneVar(L.key),
      }, g);
      svg('line', {
        class: 'agl-lane-sep', x1: L.x, y1: 0, x2: L.x, y2: H,
      }, g);
      svg('rect', {
        class: 'agl-lane-hd', x: L.x, y: 0, width: L.w, height: 30,
        fill: laneVar(L.key),
      }, g);
      const t = svg('text', {
        class: 'agl-lane-tx', x: L.x + 12, y: 15, fill: laneVar(L.key),
      }, g);
      t.textContent = ellipsis(L.label, Math.floor(L.w / 7));
      laneEls.set(L.key, g);
    }
    svg('line', {
      class: 'agl-lane-sep',
      x1: model.lanes.length ? model.lanes[model.lanes.length - 1].x + model.lanes[model.lanes.length - 1].w : 0,
      y1: 0,
      x2: model.lanes.length ? model.lanes[model.lanes.length - 1].x + model.lanes[model.lanes.length - 1].w : 0,
      y2: H,
    }, gLanes);

    // -- bandes d'agents (société) ----------------------------------------
    if (model.multi) {
      for (const b of model.bands) {
        const g = svg('g', { class: 'agl-band' }, gBands);
        svg('line', {
          class: 'agl-band-line', x1: 0, y1: b.top, x2: model.width, y2: b.top,
        }, g);
        const label = `🤖 ${b.name}${b.version ? ` v${b.version}` : ''}`;
        const w = 14 + label.length * 7;
        svg('rect', {
          class: 'agl-band-pill', x: 10, y: b.top + 6, width: w, height: 22, rx: 11,
        }, g);
        const t = svg('text', { class: 'agl-band-tx', x: 20, y: b.top + 17 }, g);
        t.textContent = label;
      }
    }

    // -- arêtes ------------------------------------------------------------
    const withLabels = model.edges.length <= MAX_EDGE_LABELS;
    for (const e of model.edges) {
      const p = svg('path', {
        class: `agl-edge${e.kind ? ` k-${e.kind}` : ''}`, d: e.d,
      }, gEdges);
      let lab = null;
      if (withLabels && e.label) {
        lab = svg('text', {
          class: 'agl-edge-label', x: e.mid.x, y: e.mid.y - 5,
        }, gEdges);
        lab.textContent = e.label;
      }
      edgeEls.set(e.id, { path: p, label: lab });
    }

    // -- nœuds -------------------------------------------------------------
    for (const n of model.nodes) nodeEls.set(n.id, buildNode(n));

    // -- minimap -----------------------------------------------------------
    const big = model.nodes.length >= BIG_GRAPH || model.width > 2400;
    root.classList.toggle('has-mini', big);
    if (big) buildMinimap();

    // La sélection survit à une régénération de graphe : on élague les ids
    // disparus puis on la repeint.
    for (const id of [...selection]) if (!model.byId.has(id)) selection.delete(id);
    paintSelection();
    applyRuntimeAll();
    applyTransform();
  }

  /** Construit le groupe SVG d'un nœud (une seule fois par graphe). */
  function buildNode(n) {
    const col = laneVar(n.lane);
    const r = kindRadius(n.kind);
    const g = svg('g', {
      // `is-llm` est posée dès la construction : c'est une propriété statique
      // du programme, elle ne doit pas attendre qu'un événement touche le nœud.
      class: `agl-n k-${n.kind} lane-${n.lane}${n.llm ? ' is-llm' : ''}`,
      transform: `translate(${n.x},${n.y})`,
      'data-id': n.id,
      tabindex: '-1',
      role: 'button',
      'aria-label': `${n.kind} ${n.label}`,
    }, gNodes);

    const title = svg('title', null, g);
    title.textContent = `${n.kind.toUpperCase()} · ${n.label}${n.agent ? `\n${n.agent}` : ''}`;

    svg('rect', {
      class: 'agl-nhalo', x: -3, y: -3, width: n.w + 6, height: n.h + 6, rx: r + 3,
    }, g);
    const box = svg('rect', {
      class: 'agl-nbox', x: 0, y: 0, width: n.w, height: n.h, rx: r,
    }, g);
    if (n.kind === 'policy') box.setAttribute('stroke-dasharray', '5 3');
    // Barre d'accent gauche : rappel de la lane (couleur = kind). Elle est
    // bornée à la partie droite du bord pour ne pas dépasser des coins arrondis.
    svg('rect', {
      class: 'agl-nacc', fill: col,
      x: 0, y: Math.min(r, 12), width: 4, height: n.h - 2 * Math.min(r, 12), rx: 1,
    }, g);

    const icon = svg('text', { class: 'agl-nicon', x: 20, y: 20, fill: col }, g);
    icon.textContent = KIND_ICON[n.kind] || '•';

    const t = svg('text', { class: 'agl-ntitle', x: 33, y: 20 }, g);
    t.textContent = ellipsis(n.label, 22);

    const k = svg('text', { class: 'agl-nkind', x: n.w - 10, y: 20 }, g);
    k.textContent = n.kind;

    const subText = nodeSubtitle(n);
    let sub = null;
    if (subText) {
      sub = svg('text', { class: 'agl-nsub', x: 12, y: 39 }, g);
      sub.textContent = ellipsis(subText, 34);
    }

    // Badges (max 3), disposés en ligne, largeur estimée sur la longueur du texte.
    let bx = 12;
    for (const b of n.badges) {
      const label = ellipsis(b.text, 20);
      const w = 10 + label.length * 5.4;
      if (bx + w > n.w - 8) break;
      const bg = svg('g', { class: `agl-badge t-${b.tone}` }, g);
      svg('rect', {
        class: 'agl-badge-bg', x: bx, y: n.h - 25, width: w, height: 15, rx: 4,
      }, bg);
      const bt = svg('text', { class: 'agl-badge-tx', x: bx + 5, y: n.h - 17 }, bg);
      bt.textContent = label;
      bx += w + 5;
    }

    // Marqueur d'oracle : anneau + glyphe, visibles en permanence sur un
    // nœud qui peut interroger le LLM, animés pendant l'appel.
    if (n.llm) {
      const llmG = svg('g', { class: 'agl-llm' }, g);
      svg('rect', { class: 'agl-llm-ring', x: -6, y: -6, width: n.w + 12,
        height: n.h + 12, rx: 13 }, llmG);
      const tx = svg('text', { class: 'agl-llm-tx', x: n.w - 15, y: 15 }, llmG);
      tx.textContent = '🧠';
      const t = svg('title', {}, llmG);
      t.textContent = 'Ce plan interroge le LLM (étape REASON)';
    }

    // Compteur de passages (masqué tant que hits === 0).
    const hitsG = svg('g', { class: 'agl-hits' }, g);
    svg('circle', { class: 'agl-hits-bg', cx: n.w - 13, cy: n.h - 14, r: 9 }, hitsG);
    const hitsTx = svg('text', { class: 'agl-hits-tx', x: n.w - 13, y: n.h - 14 }, hitsG);

    g.addEventListener('pointerdown', (ev) => onNodePointerDown(ev, n));
    g.addEventListener('pointerenter', () => setHover(n.id));
    g.addEventListener('pointerleave', () => setHover(null));
    return { g, box, hitsTx, node: n };
  }

  /** Minimap : rectangles statiques, redessinés seulement au changement de graphe. */
  function buildMinimap() {
    const W = 170; const Hm = 110; const pad = 4;
    const s = Math.min((W - pad * 2) / Math.max(1, model.width),
      (Hm - pad * 2) / Math.max(1, model.height));
    mini._s = s; mini._pad = pad;
    for (const n of model.nodes) {
      svg('rect', {
        class: 'agl-mini-n',
        x: pad + n.x * s, y: pad + n.y * s,
        width: Math.max(1.5, n.w * s), height: Math.max(1.5, n.h * s),
        rx: 1, fill: laneVar(n.lane),
      }, gMiniNodes);
    }
    updateMiniViewport();
  }

  /** Positionne le rectangle « vue courante » dans la minimap. */
  function updateMiniViewport() {
    if (!model || !mini._s) return;
    const r = stage.getBoundingClientRect();
    const s = mini._s; const pad = mini._pad;
    const x = (-tx / scale); const y = (-ty / scale);
    const w = r.width / scale; const h = r.height / scale;
    miniVp.setAttribute('x', pad + x * s);
    miniVp.setAttribute('y', pad + y * s);
    miniVp.setAttribute('width', Math.max(3, w * s));
    miniVp.setAttribute('height', Math.max(3, h * s));
  }

  // ------------------------------------------------------- mise à jour ciblée

  /** Repositionne un nœud et re-route uniquement ses arêtes incidentes. */
  function moveNode(n, x, y) {
    n.x = x; n.y = y;
    const rec = nodeEls.get(n.id);
    if (rec) rec.g.setAttribute('transform', `translate(${x.toFixed(1)},${y.toFixed(1)})`);
    const inc = incidentEdges(model, n.id);
    routeEdges(model, inc);
    for (const e of inc) {
      const er = edgeEls.get(e.id);
      if (!er) continue;
      er.path.setAttribute('d', e.d);
      if (er.label) {
        er.label.setAttribute('x', e.mid.x);
        er.label.setAttribute('y', e.mid.y - 5);
      }
    }
  }

  /** Applique l'état d'exécution (hits/status) sur tout le graphe. */
  function applyRuntimeAll() {
    for (const n of model.nodes) applyRuntime(n);
  }

  /** Applique l'état d'exécution d'un seul nœud. */
  function applyRuntime(n) {
    const rec = nodeEls.get(n.id);
    if (!rec) return;
    const g = rec.g;
    g.classList.toggle('has-hits', n.hits > 0);
    if (n.hits > 0) rec.hitsTx.textContent = n.hits > 999 ? '999+' : String(n.hits);
    g.classList.remove('st-active', 'st-error', 'st-ok');
    if (n.status) g.classList.add(`st-${n.status}`);
    g.classList.toggle('is-llm', !!n.llm);
    g.classList.toggle('is-calling', !!n.calling);
  }

  /** Vidage groupé : un seul passage DOM par frame, quoi qu'il arrive. */
  function flush() {
    if (!model) { dirty.clear(); pendingFlow.length = 0; return; }
    // Au-delà de ce seuil, la pluie d'impulsions devient du bruit visuel :
    // on garde les compteurs et les états, on abandonne l'animation.
    const animate = !reduced && dirty.size <= 120;
    for (const id of dirty) {
      const n = model.byId.get(id);
      if (!n) continue;
      applyRuntime(n);
      if (animate) pulse(id);
    }
    dirty.clear();
    if (!reduced) {
      for (const e of pendingFlow.slice(-8)) animateFlow(e);
    }
    pendingFlow.length = 0;
  }

  /**
   * Impulsion douce sur un nœud (halo qui s'estompe). Deux classes d'animation
   * identiques sont alternées : cela relance l'animation CSS sans jamais
   * forcer de recalcul de mise en page, même quand cent nœuds pulsent.
   */
  function pulse(id) {
    const rec = nodeEls.get(id);
    if (!rec) return;
    rec.flip = !rec.flip;
    rec.g.classList.remove(rec.flip ? 'is-pulse-b' : 'is-pulse-a');
    rec.g.classList.add(rec.flip ? 'is-pulse-a' : 'is-pulse-b');
  }

  /** Animation brève de flux sur l'arête empruntée. */
  function animateFlow(edge) {
    if (!edge || !edge.d) return;
    const p = svg('path', { class: 'agl-flow', d: edge.d }, gFlow);
    const kill = () => { if (p.parentNode) p.parentNode.removeChild(p); };
    p.addEventListener('animationend', kill, { once: true });
    window.setTimeout(kill, 1200);   // filet de sécurité si l'animation est coupée
  }

  // ------------------------------------------------------------- sélection

  /** Met à jour la surbrillance liée au survol (arêtes incidentes). */
  function setHover(id) {
    if (hoverId === id) return;
    hoverId = id;
    const active = id ? new Set(incidentEdges(model, id).map((e) => e.id)) : null;
    for (const [eid, er] of edgeEls) {
      er.path.classList.toggle('is-hot', !!active && active.has(eid));
      er.path.classList.toggle('is-dim', !!active && !active.has(eid));
    }
    for (const [nid, rec] of nodeEls) rec.g.classList.toggle('is-hover', nid === id);
  }

  /** Applique visuellement l'ensemble sélectionné. */
  function paintSelection() {
    for (const [id, rec] of nodeEls) rec.g.classList.toggle('is-sel', selection.has(id));
  }

  let selfSelect = false;   // évite la boucle emit → on('ui.select') → emit

  /** Sélectionne un nœud et prévient l'inspecteur. */
  function select(ids, announce) {
    selection.clear();
    for (const id of ids) selection.add(id);
    paintSelection();
    if (announce !== false) {
      const id = ids.length === 1 ? ids[0] : null;
      const node = id ? model.byId.get(id) : null;
      selfSelect = true;
      try {
        bus.emit('ui.select', { nodeId: id, node: node ? publicNode(node) : null });
      } finally {
        selfSelect = false;
      }
    }
  }

  /** Vue publique d'un nœud (sans les champs internes de rendu). */
  function publicNode(n) {
    return {
      id: n.id, kind: n.kind, lane: n.lane, agent: n.agent, label: n.label,
      detail: n.detail, badges: n.badges, hits: n.hits, status: n.status,
      x: n.x, y: n.y,
    };
  }

  /** Centre la vue sur un nœud (sans changer le zoom) et le met en évidence. */
  function focusNode(id) {
    const n = model && model.byId.get(id);
    if (!n) return;
    const r = stage.getBoundingClientRect();
    tx = r.width / 2 - (n.x + n.w / 2) * scale;
    ty = r.height / 2 - (n.y + n.h / 2) * scale;
    applyTransform();
    setHover(id);
  }

  /** Recentre sur la sélection (ou ajuste tout si rien n'est sélectionné). */
  function centerSelection() {
    if (!model) return;
    if (!selection.size) { fit(); return; }
    const nodes = [...selection].map((i) => model.byId.get(i)).filter(Boolean);
    const box = boundsOf(nodes, model);
    const r = stage.getBoundingClientRect();
    const v = fitView(model, r.width, r.height, { box, maxScale: 1.4 });
    scale = v.scale; tx = v.tx; ty = v.ty;
    applyTransform();
  }

  // ------------------------------------------------- suivi du nœud en action

  /**
   * Amène le nœud actif au centre de la vue **sans toucher au zoom**.
   *
   * Trois précautions, sans lesquelles le suivi devient pénible :
   *  · zone morte — on ne bouge que si le nœud sort du cœur de la vue, sinon
   *    la caméra tremblerait à chaque événement ;
   *  · animation courte et interruptible — un nouveau nœud reprend la main ;
   *  · abandon dès que l'utilisateur navigue lui-même (voir `suspendFollow`).
   */
  const FOLLOW_MS = 260;
  const DEAD_ZONE = 0.45;           // fraction de la vue considérée « centrale »
  let follow = true;                // suivi armé (bascule dans la barre d'outils)
  let followRaf = 0;

  function stopFollowAnim() {
    if (followRaf) { window.cancelAnimationFrame(followRaf); followRaf = 0; }
  }

  /** L'utilisateur reprend la main : le suivi se met en veille, pas en panne. */
  function suspendFollow() {
    stopFollowAnim();
    if (!follow) return;
    follow = false;
    syncFollowBtn();
  }

  function syncFollowBtn() {
    if (!followBtn) return;
    followBtn.classList.toggle('is-on', follow);
    followBtn.setAttribute('aria-pressed', String(follow));
  }

  function panTo(nx, ny) {
    const r = stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const targetTx = r.width / 2 - nx * scale;
    const targetTy = r.height / 2 - ny * scale;
    stopFollowAnim();
    if (reduced) { tx = targetTx; ty = targetTy; applyTransform(); return; }
    const fromTx = tx; const fromTy = ty;
    const t0 = (window.performance || Date).now();
    const tick = () => {
      const p = Math.min(1, ((window.performance || Date).now() - t0) / FOLLOW_MS);
      const e = 1 - (1 - p) * (1 - p);          // sortie douce
      tx = fromTx + (targetTx - fromTx) * e;
      ty = fromTy + (targetTy - fromTy) * e;
      applyTransform();
      followRaf = p < 1 ? window.requestAnimationFrame(tick) : 0;
    };
    followRaf = window.requestAnimationFrame(tick);
  }

  /** Suit le nœud que la trace vient d'illuminer, si le suivi est armé. */
  function followNode(id) {
    if (!follow || !model || destroyed) return;
    const n = model.byId.get(id);
    if (!n) return;
    const r = stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const cx = (n.x + n.w / 2) * scale + tx;
    const cy = (n.y + n.h / 2) * scale + ty;
    const dx = Math.abs(cx - r.width / 2);
    const dy = Math.abs(cy - r.height / 2);
    if (dx < r.width * DEAD_ZONE / 2 && dy < r.height * DEAD_ZONE / 2) return;
    panTo(n.x + n.w / 2, n.y + n.h / 2);
  }

  syncFollowBtn();

  // --------------------------------------------------------- navigation

  function zoomAt(cx, cy, factor) {
    const ns = clamp(scale * factor, MIN_SCALE, MAX_SCALE);
    if (ns === scale) return;
    tx = cx - (cx - tx) * (ns / scale);
    ty = cy - (cy - ty) * (ns / scale);
    scale = ns;
    applyTransform();
  }

  function zoomBy(factor) {
    const r = stage.getBoundingClientRect();
    zoomAt(r.width / 2, r.height / 2, factor);
  }

  /** Ajuste le graphe entier dans la vue. */
  function fit() {
    if (!model || !model.nodes.length) return;
    const r = stage.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const v = fitView(model, r.width, r.height, { minScale: MIN_SCALE, maxScale: 1 });
    scale = v.scale; tx = v.tx; ty = v.ty;
    applyTransform();
  }

  // -------------------------------------------------------- interactions

  /** Glisser un nœud (ou toute la sélection s'il en fait partie). */
  function onNodePointerDown(ev, n) {
    if (ev.button !== 0) return;
    ev.stopPropagation();
    const additive = ev.shiftKey || ev.ctrlKey || ev.metaKey;
    if (additive) {
      if (selection.has(n.id)) selection.delete(n.id); else selection.add(n.id);
      select([...selection]);
    } else if (!selection.has(n.id)) {
      select([n.id]);
    }
    const group = selection.has(n.id)
      ? [...selection].map((i) => model.byId.get(i)).filter(Boolean)
      : [n];
    const start = toWorld(ev);
    const origin = group.map((g) => ({ n: g, x: g.x, y: g.y }));
    let moved = false;
    const rec = nodeEls.get(n.id);
    if (rec) rec.g.classList.add('is-dragging');

    const onMove = (mv) => {
      const p = toWorld(mv);
      const dx = p.x - start.x;
      const dy = p.y - start.y;
      if (!moved && Math.abs(dx) + Math.abs(dy) < 3 / scale) return;
      moved = true;
      // Le confinement est appliqué nœud par nœud : dans une sélection
      // multiple, chacun reste dans SA colonne plutôt que de suivre
      // aveuglément le déplacement du groupe.
      for (const o of origin) {
        const c = clampToLane(model, o.n, o.x + dx, o.y + dy);
        moveNode(o.n, c.x, c.y);
      }
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      if (rec) rec.g.classList.remove('is-dragging');
      if (moved) {
        for (const o of origin) o.n.pinned = true;
        recomputeBounds(model);
        persistPositions();
      } else {
        // clic net : on annonce la sélection à l'inspecteur
        select([n.id]);
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
  }

  /** Fond : pan (glisser simple) ou cadre de sélection (maj / alt). */
  stage.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 || !model) return;
    suspendFollow();
    const marquee = ev.shiftKey || ev.altKey;
    const startClient = { x: ev.clientX, y: ev.clientY };
    if (marquee) {
      root.classList.add('is-marquee');
      const a = toWorld(ev);
      const rect = svg('rect', { class: 'agl-marquee', x: a.x, y: a.y, width: 0, height: 0 }, gOverlay);
      const onMove = (mv) => {
        const b = toWorld(mv);
        rect.setAttribute('x', Math.min(a.x, b.x));
        rect.setAttribute('y', Math.min(a.y, b.y));
        rect.setAttribute('width', Math.abs(b.x - a.x));
        rect.setAttribute('height', Math.abs(b.y - a.y));
      };
      const onUp = (mu) => {
        window.removeEventListener('pointermove', onMove);
        root.classList.remove('is-marquee');
        const b = toWorld(mu);
        const box = {
          x: Math.min(a.x, b.x), y: Math.min(a.y, b.y),
          w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y),
        };
        if (rect.parentNode) rect.parentNode.removeChild(rect);
        const hitNodes = (box.w > 3 && box.h > 3) ? nodesInRect(model, box) : [];
        select(hitNodes.map((n) => n.id));
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp, { once: true });
      return;
    }
    root.classList.add('is-panning');
    const ox = tx - ev.clientX;
    const oy = ty - ev.clientY;
    let moved = false;
    const onMove = (mv) => {
      tx = mv.clientX + ox; ty = mv.clientY + oy;
      if (Math.abs(mv.clientX - startClient.x) + Math.abs(mv.clientY - startClient.y) > 3) moved = true;
      applyTransform();
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      root.classList.remove('is-panning');
      if (!moved && selection.size) select([]);   // clic à vide = désélection
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp, { once: true });
  });

  /**
   * Molette : **zoom au pointeur** — c'est le geste attendu sur un canevas.
   * Maj (ou un défilement horizontal de pavé tactile) : déplacement.
   *
   * `deltaMode` vaut 1 pour un défilement par lignes et 2 par pages : sans
   * normalisation, une même molette zoomerait cent fois trop vite d'un
   * navigateur à l'autre.
   */
  stage.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    suspendFollow();
    const r = stage.getBoundingClientRect();
    const unit = ev.deltaMode === 1 ? 16 : ev.deltaMode === 2 ? 400 : 1;
    const dy = ev.deltaY * unit;
    const dx = ev.deltaX * unit;

    if (ev.shiftKey || (Math.abs(dx) > Math.abs(dy) && !ev.ctrlKey && !ev.metaKey)) {
      tx -= dx || dy;               // Maj+molette : déplacement horizontal
      applyTransform();
      return;
    }
    // Le pincement d'un pavé tactile arrive en `wheel` + ctrlKey : même geste,
    // amplitude plus fine.
    const rate = (ev.ctrlKey || ev.metaKey) ? 0.01 : 0.0022;
    zoomAt(ev.clientX - r.left, ev.clientY - r.top,
           Math.exp(-Math.max(-120, Math.min(120, dy)) * rate));
  }, { passive: false });

  /** Double-clic sur le fond : ajuster à la vue. */
  stage.addEventListener('dblclick', (ev) => {
    if (ev.target.closest && ev.target.closest('.agl-n')) return;
    fit();
  });

  /** Minimap : cliquer/glisser déplace la vue. */
  function miniGoto(ev) {
    if (!model || !mini._s) return;
    suspendFollow();
    const r = mini.getBoundingClientRect();
    const s = mini._s; const pad = mini._pad;
    const wx = (ev.clientX - r.left - pad) / s;
    const wy = (ev.clientY - r.top - pad) / s;
    const sr = stage.getBoundingClientRect();
    tx = sr.width / 2 - wx * scale;
    ty = sr.height / 2 - wy * scale;
    applyTransform();
  }
  mini.addEventListener('pointerdown', (ev) => {
    ev.preventDefault();
    miniGoto(ev);
    const onMove = (mv) => miniGoto(mv);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', () => {
      window.removeEventListener('pointermove', onMove);
    }, { once: true });
  });

  /**
   * Clavier — volontairement limité au canevas focalisé et à des touches que
   * M3 ne réserve pas (Espace, →, ⌘+Entrée, ⌘+S restent globaux) :
   *   F ajuster · 0 zoom 100 % · +/− zoomer · Échap désélectionner ·
   *   Alt+flèches déplacer les nœuds sélectionnés de 8 px.
   */
  root.addEventListener('keydown', (ev) => {
    if (ev.target !== root) return;
    const k = ev.key;
    if (k === 'f' || k === 'F') { fit(); ev.preventDefault(); return; }
    if (k === '0') { const r = stage.getBoundingClientRect(); zoomAt(r.width / 2, r.height / 2, 1 / scale); ev.preventDefault(); return; }
    if (k === '+' || k === '=') { zoomBy(1.18); ev.preventDefault(); return; }
    if (k === '-' || k === '_') { zoomBy(1 / 1.18); ev.preventDefault(); return; }
    if (k === 'Escape') { select([]); setHover(null); ev.preventDefault(); return; }
    if (ev.altKey && k.startsWith('Arrow') && selection.size) {
      const d = { ArrowLeft: [-8, 0], ArrowRight: [8, 0], ArrowUp: [0, -8], ArrowDown: [0, 8] }[k];
      if (!d) return;
      for (const id of selection) {
        const n = model.byId.get(id);
        if (!n) continue;
        const c = clampToLane(model, n, n.x + d[0], n.y + d[1]);
        moveNode(n, c.x, c.y);
        n.pinned = true;
      }
      persistPositions();
      ev.preventDefault();
    }
  });

  // -------------------------------------------------------- persistance

  let saveTimer = 0;
  /** Écrit les positions déplacées dans localStorage (débruitée à 400 ms). */
  function persistPositions() {
    if (saveTimer) window.clearTimeout(saveTimer);
    // Le fichier est capturé maintenant : un changement de fichier pendant le
    // délai ne doit jamais écrire les positions sous la mauvaise clé.
    const target = file;
    const snapshot = model;
    saveTimer = window.setTimeout(() => {
      saveTimer = 0;
      savePositions(target, extractPositions(snapshot));
    }, 400);
  }

  // ------------------------------------------------------------- graphe

  /** Charge/recharge un graphe serveur en préservant le travail utilisateur. */
  function setGraph(raw) {
    const nextFile = announcedFile
      || (state && state.session && state.session.file)
      || file || '';
    const fileChanged = nextFile !== file;
    file = nextFile;
    if (!raw) {
      model = normalizeGraph(null);
      render();
      return;
    }
    if (!model || fileChanged) {
      model = normalizeGraph(raw, { positions: loadPositions(file) });
      render();
      fit();
      return;
    }
    const res = diffGraph(model, raw, { positions: loadPositions(file) });
    model = res.model;
    render();   // reconstruction du SVG, mais positions/compteurs préservés
  }

  // ------------------------------------------------------------ direct

  /** Marque un nœud comme touché par une trace. */
  function touch(node, status) {
    node.hits += 1;
    // Un blocage de politique laisse une trace durable : il ne doit pas être
    // effacé par le simple passage suivant.
    if (status === 'error') node.status = 'error';
    else if (node.status !== 'error') node.status = status || 'active';
    dirty.add(node.id);
  }

  /**
   * Un appel au LLM est-il en train de commencer, ou de finir ?
   *
   * La session encadre chaque appel de deux traces `LLM` dont le `detail` dit
   * « appel en cours » puis « appel terminé » : c'est cet intervalle-là qu'il
   * faut montrer, pas l'instant où la réponse arrive.
   */
  function llmPhase(msg) {
    const d = String((msg && msg.detail) || '');
    if (/en cours/.test(d)) return 'start';
    if (/terminé|termine/.test(d)) return 'end';
    return null;
  }

  /** Traite un message `trace` (§2 du contrat). */
  function onTrace(msg) {
    if (!model || !msg) return;
    const kind = String(msg.kind || '').toUpperCase();
    const node = resolveNode(model, msg.nodeId, { kind, key: msg.text });
    if (!node) return;
    if (kind === 'LLM') {
      const phase = llmPhase(msg);
      if (phase === 'start') { node.calling = true; node.llm = true; }
      else if (phase === 'end') node.calling = false;
      if (phase) { dirty.add(node.id); schedule(); if (phase === 'start') followNode(node.id); }
      if (phase === 'end') return;   // la fin ne compte pas comme un passage
    }
    let status = 'active';
    if (kind === 'BLOCKED' || kind === 'ERROR' || kind === 'VERIFY_FAIL') status = 'error';
    else if (kind === 'VERIFY_OK') status = 'ok';
    touch(node, status);

    // Arête empruntée : on anime le lien entre le nœud précédent et celui-ci.
    if (lastTracedId && lastTracedId !== node.id) {
      const e = findEdge(model, lastTracedId, node.id);
      if (e && pendingFlow.indexOf(e) === -1) pendingFlow.push(e);
    }
    lastTracedId = node.id;
    followNode(node.id);
    schedule();
  }

  /** Met une lane en avant le temps d'une phase. */
  function onPhase(msg) {
    if (!model || !msg) return;
    const lane = PHASE_LANE[String(msg.phase || '').toUpperCase()];
    const exiting = String(msg.status || '') === 'exit';
    const next = exiting ? null : lane;
    if (next === hotLane) return;
    if (hotLane && laneEls.has(hotLane)) laneEls.get(hotLane).classList.remove('is-hot');
    hotLane = next;
    if (hotLane && laneEls.has(hotLane)) laneEls.get(hotLane).classList.add('is-hot');
  }

  /** Remise à zéro complète de l'état de direct. */
  function onRunStarted() {
    if (!model) return;
    resetRuntime(model);
    for (const n of model.nodes) n.calling = false;
    lastTracedId = null;
    if (!follow) { follow = true; syncFollowBtn(); }
    dirty.clear();
    pendingFlow.length = 0;
    gFlow.innerHTML = '';
    if (hotLane && laneEls.has(hotLane)) laneEls.get(hotLane).classList.remove('is-hot');
    hotLane = null;
    applyRuntimeAll();
  }

  /** Fin de run : on éteint la lane courante, on garde les compteurs. */
  function onRunFinished() {
    for (const n of (model ? model.nodes : [])) {
      if (n.calling) { n.calling = false; dirty.add(n.id); }
    }
    schedule();
    if (hotLane && laneEls.has(hotLane)) laneEls.get(hotLane).classList.remove('is-hot');
    hotLane = null;
    lastTracedId = null;
    stopFollowAnim();
  }

  /** Sélection venue d'ailleurs (inspecteur, chronologie…). */
  function onExternalSelect(payload) {
    if (selfSelect || !model || !payload) return;
    const id = payload.nodeId || (payload.node && payload.node.id);
    if (!id) { select([], false); return; }
    const n = resolveNode(model, id);
    if (!n) return;
    select([n.id], false);
    focusNode(n.id);
  }

  /** Réception du graphe par le bus. */
  function onGraph(msg) {
    setGraph(msg && msg.graph ? msg.graph : msg);
  }

  /**
   * `hello` : un autre fichier vient d'être ouvert. On ne touche pas à `file`
   * ici — `setGraph()` le relit depuis `state.session` et détecte lui-même le
   * changement, ce qui déclenche le chargement des positions du NOUVEAU
   * fichier plutôt que la fusion avec l'ancien.
   */
  function onHello(msg) {
    const f = msg && msg.session && msg.session.file;
    if (!f || f === file) return;
    // Le fichier annoncé fait autorité (il précède parfois la mise à jour du
    // graphe) : le prochain `setGraph` chargera les positions de CE fichier.
    announcedFile = f;
    if (state && state.graph) setGraph(state.graph);
  }

  // ------------------------------------------------------- abonnements

  const subs = [
    ['graph', onGraph],
    ['trace', onTrace],
    ['phase', onPhase],
    ['run.started', onRunStarted],
    ['run.finished', onRunFinished],
    ['hello', onHello],
    ['ui.select', onExternalSelect],
  ];
  for (const [type, fn] of subs) {
    if (bus && typeof bus.on === 'function') bus.on(type, fn);
  }

  // Redimensionnement du panneau : la minimap et le zoom suivent.
  let ro = null;
  if (window.ResizeObserver) {
    ro = new ResizeObserver(() => updateMiniViewport());
    ro.observe(root);
  }

  // Graphe éventuellement déjà présent dans l'état partagé (montage tardif).
  if (state && state.graph) setGraph(state.graph);
  else setGraph(null);

  /** Démonte proprement : désabonnements, timers, DOM. */
  function destroy() {
    destroyed = true;
    for (const [type, fn] of subs) {
      if (bus && typeof bus.off === 'function') bus.off(type, fn);
    }
    if (raf) window.cancelAnimationFrame(raf);
    if (saveTimer) window.clearTimeout(saveTimer);
    if (ro) ro.disconnect();
    if (mq) {
      if (mq.removeEventListener) mq.removeEventListener('change', syncReduced);
      else if (mq.removeListener) mq.removeListener(syncReduced);
    }
    root.innerHTML = '';
    root.classList.remove('agl-canvas');
  }

  return {
    destroy,
    setGraph,
    focusNode,
    fit,
    /** Exposé pour les tests / le débogage : modèle courant. */
    get model() { return model; },
  };
}

export default mountCanvas;
