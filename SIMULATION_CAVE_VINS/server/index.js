/* Cave Autonomy Lab — serveur : simulation continue, persistance SQLite, flux SSE, API des agents. */
import express from 'express';
import crypto from 'node:crypto';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import { Worker } from 'node:worker_threads';
import { Store } from './store.js';
import { Run, normalizeConfig, DEFAULT_LANES } from './engine/run.js';
import { ZONES, TOOLS, SCENARIOS, CHAOS, AGENT_KINDS, CAUSES, RISK_LABEL, UNITS, DEFAULT_POLICY, INC_INFO, STORAGE } from './engine/model.js';
import { SENSOR_META } from './engine/world.js';
import { PHASES } from './engine/agent.js';
import { computeKpis } from './engine/kpi.js';
import { verifyChain } from './engine/kernel.js';
import { runInfo, laneSummaries, ghost, worldDetail, histSince, kpisFor } from './snapshot.js';
import { SENSE, DAY, HOUR, T0, DT } from './engine/util.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = +process.env.PORT || 4060;
const HOST = process.env.HOST || '127.0.0.1';
const store = new Store(process.env.CAVE_DB || path.join(ROOT, 'data', 'cave.db'));
const newTokens = (n) => Array.from({ length: n }, () => 'cal_' + crypto.randomBytes(12).toString('hex'));

let run = null, lanesDb = [];

/* ---------------- cycle de vie des runs ---------------- */
function closeCurrent() {
  if (!run) return;
  store.persist(run);
  store.finalize(run, run.status === 'finished' ? 'finished' : 'stopped');
  store.resetCursors(run);
}
function startRun(cfgIn, meta = {}) {
  closeCurrent();
  const cfg = normalizeConfig(cfgIn);
  const id = store.createRun(cfg, { kind: meta.kind || 'live', parent: meta.parent ?? null, label: meta.label || null, tokens: newTokens(cfg.lanes.length) });
  if (meta.seedInputs) store.seedInputs(id, meta.seedInputs);
  run = new Run(id, cfg, { recorded: meta.recorded || null, replayUntil: meta.replayUntil || null, fpRef: meta.fpRef || null });
  if (meta.ffTo) run.fastForward(meta.ffTo, false);
  lanesDb = store.run(id).lanes;
  for (const c of clients) { c.slot = Math.min(c.slot, run.worlds.length - 1); sendInit(c); }
  console.log(`[run ${id}] ${meta.kind || 'live'} · ${cfg.scenario} · seed ${cfg.seed} · ${cfg.lanes.map((l) => l.name).join(' | ')}`);
  return run;
}
function resumeRun(row) {
  const cfg = row.config;
  let recorded = store.recorded(row.id), replayUntil = null, fpRef = null;
  if (row.kind === 'replay' && cfg.replayOf) {
    recorded = store.recorded(cfg.replayOf);
    const src = store.run(cfg.replayOf); replayUntil = src ? src.step : row.step; fpRef = store.fingerprints(cfg.replayOf);
  }
  run = new Run(row.id, cfg, { recorded, replayUntil, fpRef });
  run.status = row.status;
  run.fastForward(row.step, true);
  lanesDb = row.lanes;
  console.log(`[run ${row.id}] reprise : rattrapage silencieux de ${row.step} pas (${((row.step * DT) / DAY).toFixed(1)} j simulés)`);
}
function boot() {
  const last = store.lastActiveRun();
  if (last) { try { resumeRun(store.run(last.id)); return; } catch (e) { console.error('Reprise impossible, nouveau run :', e); } }
  startRun({ lanes: DEFAULT_LANES, scenario: 'CONT', fault: 'medium', adv: 'on', seed: 42, speed: 60 });
}

/* ---------------- boucle temps réel ---------------- */
let lastTick = performance.now(), lastPersist = 0, lastBroadcast = 0;
function loop() {
  const now = performance.now(), dt = Math.min(1000, now - lastTick); lastTick = now;
  try {
    run.advance(dt);
    if (now - lastPersist > 500 || run.exoOut.length > 400) { store.persist(run); lastPersist = now; }
    if (now - lastBroadcast > 250) { broadcast(); lastBroadcast = now; }
  } catch (e) {
    console.error('Erreur moteur, simulation en pause :', e);
    run.status = 'paused'; run.error = String(e.message || e);
  }
}

/* ---------------- SSE ---------------- */
const clients = new Set();
function send(c, obj) { if (c.res.writableLength < 8e6) c.res.write('data: ' + JSON.stringify(obj) + '\n\n'); }
function sendInit(c) {
  const w = run.worlds[c.slot], k = kpisFor(run);
  const hist = histSince(w, null, 3);
  send(c, {
    type: 'init', run: runInfo(run, lanesDb), lanes: laneSummaries(run, k), ghost: ghost(run), w: worldDetail(run, w, k[c.slot]),
    events: w.events.slice(-300), exo: run.events.slice(-300), console: w.console.slice(-400), actions: w.actions.slice(-300),
    incidents: w.incidents.slice(-400), hist,
  });
  c.runId = run.id;
  c.cur = { ev: w.seq.ev, con: w.seq.con, exo: run.seqEv, ver: w.ver, st: hist ? hist.t[hist.t.length - 1] : null, step: run.step, sentAt: Date.now() };
}
function broadcast() {
  if (!clients.size) return;
  const k = kpisFor(run), info = runInfo(run, lanesDb), lanes = laneSummaries(run, k), gh = ghost(run);
  const detail = {};
  for (const c of clients) {
    if (c.runId !== run.id) { sendInit(c); continue; }
    const w = run.worlds[c.slot];
    const changed = run.step !== c.cur.step || w.ver !== c.cur.ver || w.seq.con !== c.cur.con;
    if (!changed && Date.now() - c.cur.sentAt < 2000) continue;
    detail[c.slot] = detail[c.slot] || worldDetail(run, w, k[c.slot]);
    const actions = [];
    for (let i = w.actions.length - 1, n = 0; i >= 0 && n < 500; i--, n++) if (w.actions[i].v > c.cur.ver) actions.push(w.actions[i]);
    const msg = {
      type: 'tick', run: info, lanes, ghost: gh, w: detail[c.slot],
      events: w.events.filter((e) => e.id > c.cur.ev).slice(-300), exo: run.events.filter((e) => e.id > c.cur.exo).slice(-300),
      console: w.console.filter((e) => e.id > c.cur.con).slice(-400), actions: actions.reverse(),
      incidents: w.incidents.filter((i) => i.v > c.cur.ver), hist: histSince(w, c.cur.st),
    };
    send(c, msg);
    c.cur = { ev: w.seq.ev, con: w.seq.con, exo: run.seqEv, ver: w.ver, st: msg.hist ? msg.hist.t[msg.hist.t.length - 1] : c.cur.st, step: run.step, sentAt: Date.now() };
  }
}

/* ---------------- HTTP ---------------- */
const app = express();
app.use(express.json({ limit: '256kb' }));
app.disable('x-powered-by');
app.use((req, res, next) => { res.setHeader('X-Content-Type-Options', 'nosniff'); next(); });
const lane = (v) => Math.max(0, Math.min(run.worlds.length - 1, Math.floor(+v) || 0));
const runOf = (q) => (q && +q ? +q : run.id);
const bad = (res, msg, code = 400) => res.status(code).json({ error: msg });

app.get('/api/meta', (req, res) => res.json({
  zones: ZONES, sensors: SENSOR_META, tools: TOOLS, scenarios: SCENARIOS, chaos: CHAOS, agentKinds: AGENT_KINDS, causes: CAUSES, riskLabels: RISK_LABEL,
  phases: PHASES, units: UNITS, storage: STORAGE, defaultPolicy: DEFAULT_POLICY, incInfo: INC_INFO, defaultLanes: DEFAULT_LANES, T0, DT, SENSE,
}));
app.get('/api/run', (req, res) => res.json(runInfo(run, lanesDb)));

app.get('/api/stream', (req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache, no-transform', Connection: 'keep-alive', 'X-Accel-Buffering': 'no' });
  res.write('retry: 2000\n\n');
  const c = { res, slot: lane(req.query.lane), runId: null, cur: null };
  clients.add(c);
  sendInit(c);
  req.on('close', () => clients.delete(c));
});

app.post('/api/control', (req, res) => {
  const { action, value } = req.body || {};
  if (run.ffTo != null) return bad(res, 'Rattrapage en cours, patientez.', 409);
  switch (action) {
    case 'speed':
      if (![1, 10, 60, 360, 1440, 'max'].includes(value)) return bad(res, 'vitesse invalide');
      run.speed = value; if (run.status === 'paused') run.status = 'running'; run.acc = 0; store.updateSpeed(run); break;
    case 'pause': if (run.status === 'running') run.status = 'paused'; break;
    case 'play': if (run.status === 'paused' || run.status === 'finished') { run.status = 'running'; run.acc = 0; } break;
    case 'step': run.stepMinute(); break;
    default: return bad(res, 'action inconnue');
  }
  broadcast(); res.json(runInfo(run, lanesDb));
});

app.post('/api/chaos', (req, res) => {
  const { type, p } = req.body || {};
  if (!CHAOS.some((c) => c.type === type)) return bad(res, 'événement inconnu');
  if (run.replaying) return bad(res, 'Replay en cours : aucune entrée du testeur.', 409);
  const e = run.addChaos(type, p && typeof p === 'object' ? p : {});
  res.json({ ok: true, scheduled: e });
});

const INPUTS = { policy: 1, policy_emergency: 1, kernel: 1, humanMode: 1, approve: 1 };
app.post('/api/input', (req, res) => {
  const { lane: l, type, p = {} } = req.body || {};
  if (!INPUTS[type]) return bad(res, 'entrée inconnue');
  if (run.replaying) return bad(res, 'Replay en cours : aucune entrée du testeur.', 409);
  if (type === 'policy' && (!['R0', 'R1', 'R2', 'R3', 'R4'].includes(p.level) || !['ALLOW', 'ALLOW_IF', 'APPROVAL', 'DENY'].includes(p.mode))) return bad(res, 'politique invalide');
  if (type === 'kernel' && !['enforce', 'audit'].includes(p.mode)) return bad(res, 'mode noyau invalide');
  if (type === 'humanMode' && !['auto', 'manual'].includes(p.mode)) return bad(res, 'mode invalide');
  if (type === 'policy_emergency' && typeof p.on !== 'boolean') return bad(res, 'booléen attendu');
  if (type === 'approve' && (typeof p.id !== 'string' || typeof p.ok !== 'boolean')) return bad(res, 'approbation invalide');
  const target = type === 'policy' || type === 'policy_emergency' || type === 'humanMode' ? 'all' : lane(l);
  run.addInput(target, type, p);
  res.json({ ok: true, step: run.step });
});

/* ---------------- runs : nouveau, liste, replay, fork ---------------- */
app.post('/api/runs', (req, res) => {
  const cfg = req.body && req.body.config;
  if (!cfg || typeof cfg !== 'object') return bad(res, 'config attendue');
  if (Array.isArray(cfg.lanes) && (cfg.lanes.length < 1 || cfg.lanes.length > 5)) return bad(res, '1 à 5 couloirs');
  startRun(cfg, { label: req.body.label ? String(req.body.label).slice(0, 80) : null });
  res.json(runInfo(run, lanesDb));
});
app.get('/api/runs', (req, res) => res.json(store.runs(80).map((r) => ({ ...r, live: run && r.id === run.id }))));
app.get('/api/runs/:id', (req, res) => {
  const r = store.run(+req.params.id); if (!r) return bad(res, 'run inconnu', 404);
  res.json({ ...r, counts: store.counts(r.id), live: r.id === run.id, lanes: r.lanes.map((l) => ({ ...l, token: undefined })) });
});
app.post('/api/runs/:id/replay', (req, res) => {
  const src = store.run(+req.params.id); if (!src) return bad(res, 'run inconnu', 404);
  if (src.id === run.id) store.persist(run);
  const until = src.id === run.id ? run.step : src.step;
  if (until < SENSE) return bad(res, 'rien à rejouer');
  const cfg = { ...src.config, replayOf: src.id, fork: src.config.fork || null, speed: 'max' };
  startRun(cfg, { kind: 'replay', parent: src.id, label: `Replay du run #${src.id}`, recorded: store.recorded(src.id), replayUntil: until, fpRef: store.fingerprints(src.id) });
  res.json(runInfo(run, lanesDb));
});
app.post('/api/runs/:id/fork', (req, res) => {
  const src = store.run(+req.params.id); if (!src) return bad(res, 'run inconnu', 404);
  if (src.id === run.id) store.persist(run);
  const b = req.body || {};
  const slot = Math.max(0, Math.min(src.config.lanes.length - 1, Math.floor(+b.slot) || 0));
  const maxStep = src.id === run.id ? run.step : src.step;
  let atStep = b.t != null ? Math.round((+b.t - T0) / DT) : Math.floor(+b.step);
  if (!(atStep > 0)) atStep = maxStep;
  atStep = Math.min(maxStep, atStep); atStep -= atStep % SENSE;
  if (atStep < SENSE) return bad(res, 'instant de fork trop précoce');
  const srcLane = src.config.lanes[slot];
  const lanes = Array.isArray(b.lanes) && b.lanes.length ? b.lanes : src.config.lanes.map((l) => ({ agent: l.agent, name: l.name, kernel: l.kernel, model: l.model }));
  const rec = store.recorded(src.id);
  const prefix = rec.inputs.filter((u) => u.slot === slot && u.step < atStep);
  const seedInputs = [];
  lanes.forEach((_, i) => { for (const u of prefix) seedInputs.push({ ...u, slot: i }); });
  const cfg = { ...src.config, lanes, speed: 60, replayOf: null, fork: { run: src.id, slot, atStep, agent: srcLane.agent, kernel: srcLane.kernel, srcName: srcLane.name } };
  startRun(cfg, { kind: 'fork', parent: src.id, label: `Fork du run #${src.id} (${srcLane.name}) à J+${((atStep * DT) / DAY).toFixed(2)}`, seedInputs, recorded: { inputs: seedInputs, chaos: rec.chaos.filter((e) => e.step < atStep) }, ffTo: atStep });
  res.json(runInfo(run, lanesDb));
});

/* ---------------- lectures (SQLite) ---------------- */
app.get('/api/series', (req, res) => {
  const rid = runOf(req.query.run), l = lane(req.query.lane);
  const to = +req.query.to || (rid === run.id ? run.t : 9e12), from = +req.query.from || to - DAY;
  res.json(store.samples(rid, l, from, to, Math.min(3000, +req.query.n || 1500)));
});
app.get('/api/compare', (req, res) => {
  const rid = runOf(req.query.run), zone = STORAGE.includes(req.query.zone) ? req.query.zone : 'pre';
  const metric = ['T', 'RH', 'Tb', 'loss', 'kw'].includes(req.query.metric) ? req.query.metric : 'T';
  const r = rid === run.id ? { config: run.cfg } : store.run(rid); if (!r) return bad(res, 'run inconnu', 404);
  const to = +req.query.to || (rid === run.id ? run.t : 9e12), from = +req.query.from || to - DAY;
  const idx = { T: 0, RH: 2, Tb: 3 }[metric];
  const series = r.config.lanes.map((l, i) => {
    const rows = store.samples(rid, i, from, to, 1200);
    return { slot: i, name: l.name, t: rows.map((s) => s.t), v: rows.map((s) => (metric === 'loss' ? s.loss : metric === 'kw' ? s.kw : s.z[zone][idx])) };
  });
  res.json({ zone, metric, series });
});
app.get('/api/actions', (req, res) => {
  const rid = runOf(req.query.run), l = lane(req.query.lane);
  res.json(store.actions(rid, l, { before: req.query.before || null, limit: Math.min(1000, +req.query.limit || 200), tool: req.query.tool || null, decision: req.query.decision || null, utility: req.query.utility || null, onlyViolations: req.query.violations === '1' }));
});
app.get('/api/actions/:lane/:aid', (req, res) => {
  const rid = runOf(req.query.run), l = lane(req.params.lane);
  const mem = rid === run.id ? run.worlds[l].actions.find((a) => a.id === req.params.aid) : null;
  const a = mem || store.action(rid, l, req.params.aid);
  if (!a) return bad(res, 'action inconnue', 404);
  const con = store.db.prepare('SELECT * FROM console WHERE run_id = ? AND slot = ? AND aid = ? ORDER BY id').all(rid, l, a.id);
  res.json({ action: a, console: con });
});
app.get('/api/console', (req, res) => {
  const kinds = req.query.kinds ? String(req.query.kinds).split(',').slice(0, 20) : null;
  res.json(store.console(runOf(req.query.run), lane(req.query.lane), { before: req.query.before ? +req.query.before : null, limit: Math.min(1000, +req.query.limit || 300), kinds }));
});
app.get('/api/events', (req, res) => res.json(store.events(runOf(req.query.run), lane(req.query.lane), { before: req.query.before ? +req.query.before : null, limit: Math.min(2000, +req.query.limit || 300), src: req.query.src || null, q: req.query.q ? String(req.query.q).slice(0, 80) : null })));
app.get('/api/exo', (req, res) => res.json(store.exo(runOf(req.query.run))));
app.get('/api/incidents', (req, res) => res.json(store.incidents(runOf(req.query.run), lane(req.query.lane))));
app.get('/api/kpis', (req, res) => res.json(store.kpis(runOf(req.query.run), lane(req.query.lane))));
app.get('/api/audit/verify', (req, res) => {
  const rid = runOf(req.query.run), l = lane(req.query.lane);
  if (rid === run.id) store.persist(run);
  const all = store.actionsAll(rid, l);
  const v = verifyChain(all);
  res.json({ ...v, total: all.length, head: all.length ? all[all.length - 1].hash : null });
});
app.get('/api/inventory', (req, res) => res.json(run.worlds[lane(req.query.lane)].inventory));
app.get('/api/db', (req, res) => res.json({ size: store.dbSize(), counts: store.counts(runOf(req.query.run)) }));

/* Première divergence significative entre couloirs (multiple_agents_context.md « Find divergence »). */
app.get('/api/divergence', (req, res) => {
  const rid = runOf(req.query.run), thr = Math.max(.05, +req.query.threshold || .3);
  const r = rid === run.id ? { config: run.cfg } : store.run(rid); if (!r) return bad(res, 'run inconnu', 404);
  if (rid === run.id) store.persist(run);
  const n = r.config.lanes.length; if (n < 2) return res.json({ found: false, reason: 'un seul couloir' });
  const from = +req.query.from || 0, to = +req.query.to || 9e12;
  const all = r.config.lanes.map((_, i) => store.samples(rid, i, from, to, 1e9));
  const len = Math.min(...all.map((a) => a.length));
  let at = -1, zone = null, spread = 0;
  for (let j = 0; j < len && at < 0; j++) {
    for (const z of STORAGE) {
      const vals = all.map((a) => a[j].z[z][0]); const s = Math.max(...vals) - Math.min(...vals);
      if (s > thr) { at = j; zone = z; spread = s; break; }
    }
  }
  if (at < 0) return res.json({ found: false, reason: `aucun écart > ${thr} °C sur la période`, samples: len });
  const t = all[0][at].t, before = all[0][Math.max(0, at - 1)];
  const lossAt = (a, tt) => { let best = a[0]; for (const s of a) { if (s.t <= tt) best = s; else break; } return best.loss; };
  const lanes = r.config.lanes.map((l, i) => {
    const acts = store.db.prepare('SELECT data FROM actions WHERE run_id = ? AND slot = ? AND t BETWEEN ? AND ? ORDER BY t LIMIT 30').all(rid, i, t - 2 * HOUR, t + 30 * 60).map((x) => JSON.parse(x.data));
    const con = store.db.prepare("SELECT t, kind, text FROM console WHERE run_id = ? AND slot = ? AND t BETWEEN ? AND ? AND kind IN ('observation','hypothese','plan','refus','escalade') ORDER BY id LIMIT 12").all(rid, i, t - 2 * HOUR, t + 30 * 60);
    const a = all[i];
    return { slot: i, name: l.name, agent: l.agent, T: a[at].z[zone][0], state: a[at].st, loss0: lossAt(a, t), loss30: lossAt(a, t + 30 * 60) - lossAt(a, t), loss2h: lossAt(a, t + 2 * HOUR) - lossAt(a, t), actions: acts.map((x) => ({ id: x.id, t: x.t, tool: x.tool, args: x.args, decision: x.decision, executed: x.executed, why: x.why })), thoughts: con };
  });
  const exo = store.db.prepare('SELECT t, label, src FROM exo WHERE run_id = ? AND t BETWEEN ? AND ? ORDER BY t').all(rid, t - 6 * HOUR, t + 30 * 60);
  res.json({ found: true, t, zone, spread, threshold: thr, common: { t: before.t, z: Object.fromEntries(STORAGE.map((z) => [z, all.map((a) => a[Math.max(0, at - 1)].z[z][0])])) }, lanes, exo });
});

/* Effet contre-factuel d'une action : worker dédié, la simulation continue pendant le calcul. */
const cfJobs = new Map();
app.post('/api/counterfactual', (req, res) => {
  const { lane: l, aid, horizonH = 2 } = req.body || {};
  const slot = lane(l);
  if (!/^A-\d{5}$/.test(String(aid))) return bad(res, 'identifiant action invalide');
  store.persist(run);
  const rec = run.worlds[slot].actions.find((a) => a.id === aid) || store.action(run.id, slot, aid);
  if (!rec) return bad(res, 'action inconnue', 404);
  const key = `${run.id}:${slot}:${aid}:${horizonH}`;
  if (cfJobs.has(key)) return res.json(cfJobs.get(key));
  const horizon = Math.round((Math.max(.5, Math.min(12, +horizonH)) * HOUR) / DT);
  const job = { state: 'running', key, started: Date.now() };
  cfJobs.set(key, job);
  const wk = new Worker(new URL('./counterfactual.js', import.meta.url), { workerData: { cfg: run.cfg, slot, recorded: store.recorded(run.id), aid, step: rec.step, horizon } });
  wk.once('message', (m) => { Object.assign(job, m.ok ? { state: 'done', ...m, ms: Date.now() - job.started } : { state: 'error', error: m.error }); });
  wk.once('error', (e) => Object.assign(job, { state: 'error', error: String(e) }));
  res.json(job);
});
app.get('/api/counterfactual', (req, res) => { const j = cfJobs.get(String(req.query.key)); if (!j) return bad(res, 'calcul inconnu', 404); res.json(j); });

app.get('/api/export', (req, res) => {
  const rid = runOf(req.query.run);
  if (rid === run.id) store.persist(run);
  const r = store.run(rid); if (!r) return bad(res, 'run inconnu', 404);
  const live = rid === run.id ? run.worlds.map((w) => ({ slot: w.slot, name: w.name, kind: w.kind, k: computeKpis(w) })) : null;
  res.setHeader('Content-Disposition', `attachment; filename="cave-run-${rid}.json"`);
  res.json({ run: { ...r, lanes: r.lanes.map((l) => ({ ...l, token: undefined })) }, kpis: live || (r.final && r.final.lanes), exo: store.exo(rid, 100000), inputs: store.recorded(rid).inputs, fingerprints: store.fingerprints(rid), exportedAt: new Date().toISOString() });
});

/* ---------------- API des agents externes (pull) ---------------- */
function agentAuth(req, res) {
  const h = String(req.headers.authorization || ''); const tok = h.startsWith('Bearer ') ? h.slice(7).trim() : '';
  const i = lanesDb.findIndex((l) => l.token && l.token.length === tok.length && crypto.timingSafeEqual(Buffer.from(l.token), Buffer.from(tok)));
  if (i < 0) { bad(res, 'jeton inconnu pour le run courant', 401); return null; }
  const w = run.worlds[i];
  if (run.cfg.lanes[i].agent !== 'external') { bad(res, `le couloir « ${w.name} » n'est pas un couloir externe`, 403); return null; }
  if (run.replaying) { bad(res, 'replay ou rattrapage en cours : la cave ne prend pas de décision externe', 409); return null; }
  if (run.fork && run.step < run.fork.atStep) { bad(res, 'préfixe de fork en cours', 409); return null; }
  const a = w.agent;
  run.ext[i].lastSeenReal = Date.now();
  if (a.crashed || w.t < a.netDownUntil) {
    const until = a.crashed ? a.crashUntil : a.netDownUntil;
    res.setHeader('Retry-After', '1');
    bad(res, a.crashed ? 'crash simulé de l’agent : API indisponible' : 'perte réseau simulée', 503);
    void until; return null;
  }
  return i;
}
const TOOL_DOC = Object.fromEntries(Object.entries(TOOLS).map(([k, v]) => [k, { risk: v.risk, level: RISK_LABEL[v.risk], args: v.args, description: v.d, condition: v.cond || null }]));
app.get('/api/agent/v1/tools', (req, res) => {
  res.json({
    tools: TOOL_DOC, causes: CAUSES, phases: PHASES, provenance: ['OBSERVED', 'DERIVED', 'LLM_DERIVED', 'EXTERNAL', 'HUMAN', 'TOOL', 'UNTRUSTED', 'ATTESTED'],
    protocol: {
      observe: 'GET /api/agent/v1/observe → état aveugle (aucune vérité terrain). Le champ tick identifie le relevé.',
      act: 'POST /api/agent/v1/act {tick, items:[…]} ; items.type ∈ act | alert | diagnose | clear | note | phases | usage.',
      act_item: '{type:"act", tool, args, why, hypothesis, evidence:[{src,value,unit,prov,conf}], expected, alternatives, key (idempotence), confidence}',
      diagnose_item: '{type:"diagnose", target, cause (voir causes), confidence 0-1, text}',
      alert_item: '{type:"alert", target, text} — compte comme détection',
      timing: 'operational : le monde avance pendant que vous réfléchissez. logical : le banc attend votre réponse à chaque tick (délai max configurable).',
      cadence: 'Un cycle de décision par minute simulée (step ≡ 3 mod 6). Les items reçus sont appliqués au cycle suivant.',
    },
  });
});
app.post('/api/agent/v1/hello', (req, res) => {
  const i = agentAuth(req, res); if (i == null) return;
  const b = req.body || {};
  run.ext[i].info = { name: String(b.name || '').slice(0, 60), framework: String(b.framework || '').slice(0, 60), model: String(b.model || '').slice(0, 60) };
  if (!run.worlds[i].agent.connected) run.ext[i].hello = { model: run.ext[i].info.model || null };
  res.json({ ok: true, lane: i, name: run.worlds[i].name, timing: run.cfg.timing });
});
app.get('/api/agent/v1/observe', (req, res) => {
  const i = agentAuth(req, res); if (i == null) return;
  const w = run.worlds[i];
  if (!w.agent.connected && !run.ext[i].hello) run.ext[i].hello = { model: (run.ext[i].info && run.ext[i].info.model) || null };
  const o = run.observe(i);
  w.agent.lastTick = o.tick;
  if (run.cfg.timing === 'logical') o.awaiting_tick = (run.step % SENSE === 3) ? (run.step - 3) / SENSE : null;
  res.json(o);
});
app.post('/api/agent/v1/act', (req, res) => {
  const i = agentAuth(req, res); if (i == null) return;
  const b = req.body || {};
  const items = Array.isArray(b.items) ? b.items : [];
  if (items.length > 50) return bad(res, '50 items maximum par envoi');
  const clean = [];
  for (const it of items) {
    if (!it || typeof it !== 'object' || !['act', 'alert', 'diagnose', 'clear', 'note', 'phases', 'usage'].includes(it.type)) return bad(res, 'item invalide : type ∈ act | alert | diagnose | clear | note | phases | usage');
    if (it.type === 'act' && (typeof it.tool !== 'string' || it.tool.length > 80)) return bad(res, 'item act : tool (chaîne) requis');
    clean.push(JSON.parse(JSON.stringify(it)));
  }
  run.extPost(i, typeof b.tick === 'number' ? b.tick : null, clean);
  const next = run.step + ((3 - (run.step % SENSE) + SENSE) % SENSE);
  res.json({ accepted: clean.length, applies_at_step: next, step: run.step });
});
app.get('/api/agent/v1/results', (req, res) => {
  const i = agentAuth(req, res); if (i == null) return;
  const since = String(req.query.since || '');
  res.json(run.worlds[i].actions.filter((a) => !since || a.id > since).slice(-100).map((r) => ({ id: r.id, key: r.key, t: r.t, tool: r.tool, args: r.args, decision: r.decision, rule: r.rule, reason: r.reason, executed: r.executed, result: r.result, verification: r.verification })));
});

/* ---------------- client React ---------------- */
const dist = path.join(ROOT, 'client', 'dist');
if (fs.existsSync(dist)) {
  app.use(express.static(dist, { index: false, maxAge: '1h' }));
  app.get(/^\/(?!api\/).*/, (req, res) => res.sendFile(path.join(dist, 'index.html')));
} else app.get('/', (req, res) => res.type('text').send('Client non construit : lancez « npm run build ».'));
app.use((err, req, res, next) => { console.error(err); res.status(500).json({ error: 'erreur interne' }); });

boot();
setInterval(loop, 50);
app.listen(PORT, HOST, () => console.log(`Cave Autonomy Lab → http://${HOST}:${PORT}`));
for (const sig of ['SIGINT', 'SIGTERM']) process.on(sig, () => { try { store.persist(run); } catch {} process.exit(0); });
