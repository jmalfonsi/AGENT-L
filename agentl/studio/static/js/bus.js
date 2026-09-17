// bus.js — bus d'événements + client WebSocket du studio AGENT-L (module M3).
//
// Ce fichier est le seul point de contact entre les panneaux et le serveur.
// Contrat §5 : il exporte `bus` (on/off/emit/send), `connect(url)` et `state`.
//
// Règles internes :
//   - aucun accès au DOM ici (testable hors navigateur) ;
//   - les `send` émis hors connexion sont mis en file et rejoués à l'ouverture ;
//   - les messages `trace` sont ordonnés et dédupliqués par leur `seq`.

// ---------------------------------------------------------------------------
// État partagé
// ---------------------------------------------------------------------------

/**
 * État courant, tenu à jour depuis les messages serveur.
 * Les panneaux le lisent librement, mais ne le modifient jamais :
 * seule cette fabrique écrit dedans.
 */
export const state = {
  session: null,      // {file, host, rev, running} venu du message `hello`
  running: false,     // un run est-il en cours ?
  paused: false,      // le run est-il en pause ?
  runId: null,        // identifiant du run courant
  tick: 0,            // dernier tick reçu
  maxTicks: 0,        // budget de ticks annoncé par run.started
  lastState: null,    // dernier message `state` (croyances, hypothèses, buts…)
  diags: [],          // derniers diagnostics de vérification
  graph: null,        // dernier graphe (forme agentl.viz.build_program)
  connection: 'idle', // 'idle' | 'connecting' | 'open' | 'retrying' | 'closed'
  attempts: 0,        // tentatives de reconnexion consécutives
  lastSeq: -1,        // plus grand `seq` de trace appliqué
  prompt: null,       // question humaine en attente (approve/ask), ou null
};

// ---------------------------------------------------------------------------
// Bus d'événements (pub/sub minimal, synchrone)
// ---------------------------------------------------------------------------

/** Table type → ensemble d'abonnés. */
const listeners = new Map();

/**
 * Publie `payload` à tous les abonnés de `type`.
 * Une exception dans un abonné n'empêche jamais les suivants d'être appelés :
 * elle est convertie en événement `error` local (jamais avalée en silence).
 */
function emit(type, payload) {
  const set = listeners.get(type);
  if (set) {
    // Copie : un abonné a le droit de se désabonner pendant la diffusion.
    for (const fn of Array.from(set)) {
      try {
        fn(payload);
      } catch (err) {
        report(`abonné « ${type} » en erreur : ${err && err.message ? err.message : err}`);
      }
    }
  }
  // `*` reçoit tout, utile pour le journal et le débogage.
  if (type !== '*') {
    const all = listeners.get('*');
    if (all) {
      for (const fn of Array.from(all)) {
        try {
          fn({ type, payload });
        } catch (err) {
          report(`abonné « * » en erreur : ${err && err.message ? err.message : err}`);
        }
      }
    }
  }
}

/** Signale une erreur interne sans risque de récursion infinie. */
let reporting = false;
function report(message) {
  if (reporting) return;
  reporting = true;
  try {
    emit('bus.error', { message });
  } finally {
    reporting = false;
  }
}

export const bus = {
  /** Abonne `fn` au type `type`. Renvoie une fonction de désabonnement. */
  on(type, fn) {
    if (typeof fn !== 'function') throw new TypeError('bus.on : fonction attendue');
    let set = listeners.get(type);
    if (!set) { set = new Set(); listeners.set(type, set); }
    set.add(fn);
    return () => bus.off(type, fn);
  },

  /** Désabonne `fn` du type `type`. */
  off(type, fn) {
    const set = listeners.get(type);
    if (!set) return;
    set.delete(fn);
    if (set.size === 0) listeners.delete(type);
  },

  /** Publie un événement local (utilisé aussi par le pont WebSocket). */
  emit(type, payload) {
    emit(type, payload);
  },

  /**
   * Envoie un message client→serveur (§2). Si la socket n'est pas ouverte,
   * le message est mis en file et rejoué dès la connexion établie.
   */
  send(msg) {
    if (!msg || typeof msg !== 'object' || typeof msg.type !== 'string') {
      throw new TypeError('bus.send : objet {type: string, …} attendu');
    }
    if (socket && socket.readyState === WebSocket.OPEN) {
      try {
        socket.send(JSON.stringify(msg));
        return true;
      } catch (err) {
        report(`envoi impossible : ${err && err.message ? err.message : err}`);
      }
    }
    enqueue(msg);
    return false;
  },
};

// ---------------------------------------------------------------------------
// File d'attente hors connexion
// ---------------------------------------------------------------------------

const OUTBOX_MAX = 200;
/** Messages en attente d'une socket ouverte. */
const outbox = [];

function enqueue(msg) {
  // Les messages idempotents de contrôle ne s'accumulent pas.
  if (msg.type === 'ping') return;
  outbox.push(msg);
  if (outbox.length > OUTBOX_MAX) outbox.splice(0, outbox.length - OUTBOX_MAX);
}

function flushOutbox() {
  while (outbox.length && socket && socket.readyState === WebSocket.OPEN) {
    const msg = outbox.shift();
    try {
      socket.send(JSON.stringify(msg));
    } catch (err) {
      // Remise en tête de file : on retentera au prochain cycle.
      outbox.unshift(msg);
      report(`vidage de la file interrompu : ${err && err.message ? err.message : err}`);
      break;
    }
  }
}

// ---------------------------------------------------------------------------
// Ordonnancement / déduplication des traces
// ---------------------------------------------------------------------------

// `seq` est monotone à l'émission mais **partagé** par tous les messages d'un
// run (trace, tick, phase, state) : la suite des `seq` vue par le journal est
// donc croissante et TROUÉE. Attendre le successeur immédiat bloquerait tout
// (défaut observé : deux événements affichés sur quarante-trois).
//
// L'ordre d'arrivée est par ailleurs déjà garanti — un seul websocket, un seul
// diffuseur côté serveur. On ne réordonne donc pas : on diffuse immédiatement
// et on se contente d'écarter doublons et retardataires.
const seen = new Set();
const SEEN_MAX = 4096;

/** Réinitialise le suivi de séquence (nouveau run = nouvelle séquence). */
function resetSequencing() {
  seen.clear();
  state.lastSeq = -1;
}

/** Traite une trace : écarte doublons et retardataires, diffuse le reste. */
function handleTrace(msg) {
  const seq = Number.isInteger(msg.seq) ? msg.seq : null;
  if (seq === null) {
    // Pas de `seq` : rien à dédupliquer, on diffuse tel quel.
    emit('trace', msg);
    return;
  }
  if (seq <= state.lastSeq && seen.has(seq)) return;   // doublon avéré
  if (seen.has(seq)) return;
  seen.add(seq);
  if (seen.size > SEEN_MAX) seen.clear();   // borne mémoire : un run très long
  if (seq > state.lastSeq) state.lastSeq = seq;
  if (Number.isInteger(msg.tick)) state.tick = msg.tick;
  emit('trace', msg);
}

// ---------------------------------------------------------------------------
// Mise à jour de l'état depuis les messages serveur
// ---------------------------------------------------------------------------

/** Applique un message serveur à `state`, puis le rediffuse sur le bus. */
function dispatch(msg) {
  switch (msg.type) {
    case 'hello':
      state.session = msg.session || null;
      if (state.session && typeof state.session.running === 'boolean') {
        state.running = state.session.running;
      }
      break;

    case 'run.started':
      resetSequencing();
      state.running = true;
      state.paused = false;
      state.runId = msg.runId || null;
      state.tick = 0;
      state.maxTicks = Number.isInteger(msg.maxTicks) ? msg.maxTicks : 0;
      state.lastState = null;
      state.prompt = null;
      break;

    case 'run.finished':
      state.running = false;
      state.paused = false;
      state.prompt = null;
      break;

    case 'run.paused':
      state.paused = true;
      break;

    case 'run.resumed':
      state.paused = false;
      break;

    case 'tick':
      if (Number.isInteger(msg.tick)) state.tick = msg.tick;
      break;

    case 'state':
      state.lastState = msg;
      if (Number.isInteger(msg.tick)) state.tick = msg.tick;
      break;

    case 'diagnostics':
      state.diags = Array.isArray(msg.diags) ? msg.diags : [];
      break;

    case 'graph':
      state.graph = msg.graph || null;
      break;

    case 'prompt':
      state.prompt = msg;
      break;

    case 'trace':
      handleTrace(msg);
      return;  // `handleTrace` diffuse lui-même, dans le bon ordre

    default:
      break;  // `phase`, `source`, `error`, extensions futures : simple relais
  }
  emit(msg.type, msg);
}

// ---------------------------------------------------------------------------
// Connexion WebSocket + reconnexion exponentielle plafonnée
// ---------------------------------------------------------------------------

let socket = null;
let wsUrl = null;
let retryTimer = null;
let closedByUs = false;

const RETRY_BASE = 500;    // ms
const RETRY_MAX = 15000;   // plafond de l'attente entre deux tentatives
const PING_EVERY = 25000;  // maintien de la connexion
let pingTimer = null;

/** Change l'état de connexion et prévient l'interface (indicateur d'état). */
function setConnection(value, detail) {
  if (state.connection === value && !detail) return;
  state.connection = value;
  emit('connection', { status: value, attempts: state.attempts, detail: detail || null });
}

/** Résout l'URL du WebSocket par défaut à partir de la page courante. */
function defaultUrl() {
  if (typeof location === 'undefined') return null;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/ws`;
}

/**
 * Ouvre (ou ré-ouvre) la connexion temps réel.
 * @param {string} [url] URL du WebSocket ; par défaut `/ws` sur l'hôte courant.
 */
export function connect(url) {
  wsUrl = url || wsUrl || defaultUrl();
  if (!wsUrl) throw new Error('connect : URL WebSocket introuvable');

  closedByUs = false;
  clearTimeout(retryTimer);
  retryTimer = null;

  // Une socket déjà vivante ne se remplace pas.
  if (socket && (socket.readyState === WebSocket.OPEN ||
                 socket.readyState === WebSocket.CONNECTING)) {
    return socket;
  }

  setConnection(state.attempts ? 'retrying' : 'connecting');

  try {
    socket = new WebSocket(wsUrl);
  } catch (err) {
    socket = null;
    scheduleRetry(`ouverture impossible : ${err && err.message ? err.message : err}`);
    return null;
  }

  socket.addEventListener('open', () => {
    state.attempts = 0;
    setConnection('open');
    flushOutbox();
    startPing();
  });

  socket.addEventListener('message', (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (err) {
      report('message serveur illisible (JSON invalide)');
      return;
    }
    if (!msg || typeof msg !== 'object' || typeof msg.type !== 'string') {
      report('message serveur sans champ « type »');
      return;
    }
    dispatch(msg);
  });

  socket.addEventListener('error', () => {
    // `error` est toujours suivi de `close` : on se contente d'informer.
    setConnection(state.connection === 'open' ? 'open' : 'retrying', 'erreur réseau');
  });

  socket.addEventListener('close', () => {
    stopPing();
    socket = null;
    if (closedByUs) { setConnection('closed'); return; }
    scheduleRetry('connexion perdue');
  });

  return socket;
}

/** Ferme la connexion sans déclencher de reconnexion. */
export function disconnect() {
  closedByUs = true;
  clearTimeout(retryTimer);
  retryTimer = null;
  stopPing();
  if (socket) {
    try { socket.close(); } catch (err) { /* socket déjà morte : rien à faire */ }
    socket = null;
  }
  setConnection('closed');
}

/** Programme une reconnexion avec un recul exponentiel plafonné et gigué. */
function scheduleRetry(detail) {
  if (closedByUs) return;
  state.attempts += 1;
  const backoff = Math.min(RETRY_MAX, RETRY_BASE * 2 ** (state.attempts - 1));
  const delay = backoff * (0.75 + Math.random() * 0.5);  // gigue anti-troupeau
  setConnection('retrying', detail);
  clearTimeout(retryTimer);
  retryTimer = setTimeout(() => connect(wsUrl), delay);
}

function startPing() {
  stopPing();
  pingTimer = setInterval(() => {
    if (socket && socket.readyState === WebSocket.OPEN) bus.send({ type: 'ping' });
  }, PING_EVERY);
}

function stopPing() {
  if (pingTimer) { clearInterval(pingTimer); pingTimer = null; }
}

/** Indique si le canal est ouvert (utile pour les indicateurs d'interface). */
export function isConnected() {
  return !!socket && socket.readyState === WebSocket.OPEN;
}
