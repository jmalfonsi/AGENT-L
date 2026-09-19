/* Persistance SQLite (node:sqlite, sans module natif).
   Le moteur garde en mémoire une fenêtre récente ; SQLite garde tout : télémétrie échantillonnée,
   événements, console agent, actions (journal d'audit chaîné), incidents, KPI horaires,
   événements exogènes et entrées rejouables (reprise après redémarrage, replay, fork). */
import { DatabaseSync } from 'node:sqlite';
import fs from 'node:fs';
import path from 'node:path';
import { computeKpis } from './engine/kpi.js';

const SCHEMA = `
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, updated_at TEXT, kind TEXT NOT NULL DEFAULT 'live',
  parent INTEGER, label TEXT, seed INTEGER, scenario TEXT, config TEXT NOT NULL, status TEXT NOT NULL,
  step INTEGER NOT NULL DEFAULT 0, sim_t INTEGER, final TEXT
);
CREATE TABLE IF NOT EXISTS lanes (run_id INTEGER, slot INTEGER, name TEXT, agent TEXT, kernel TEXT, model TEXT, token TEXT, PRIMARY KEY (run_id, slot));
CREATE TABLE IF NOT EXISTS samples (run_id INTEGER, slot INTEGER, t INTEGER, data TEXT, PRIMARY KEY (run_id, slot, t)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS events (run_id INTEGER, slot INTEGER, id INTEGER, t INTEGER, src TEXT, sev TEXT, text TEXT, ref TEXT, PRIMARY KEY (run_id, slot, id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS console (run_id INTEGER, slot INTEGER, id INTEGER, t INTEGER, kind TEXT, text TEXT, aid TEXT, prov TEXT, conf REAL, PRIMARY KEY (run_id, slot, id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS actions (run_id INTEGER, slot INTEGER, id TEXT, t INTEGER, tool TEXT, decision TEXT, rule TEXT, executed INTEGER, violation INTEGER, utility TEXT, risk INTEGER, origin TEXT, hash TEXT, data TEXT, PRIMARY KEY (run_id, slot, id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS incidents (run_id INTEGER, slot INTEGER, id TEXT, type TEXT, start INTEGER, open INTEGER, data TEXT, PRIMARY KEY (run_id, slot, id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kpis (run_id INTEGER, slot INTEGER, t INTEGER, data TEXT, PRIMARY KEY (run_id, slot, t)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS exo (run_id INTEGER, id INTEGER, step INTEGER, t INTEGER, type TEXT, src TEXT, label TEXT, p TEXT, PRIMARY KEY (run_id, id)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS inputs (run_id INTEGER, seq INTEGER, step INTEGER, slot INTEGER, type TEXT, p TEXT, PRIMARY KEY (run_id, seq)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS fingerprints (run_id INTEGER, slot INTEGER, day INTEGER, fp TEXT, PRIMARY KEY (run_id, slot, day)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS actions_t ON actions (run_id, slot, t);
CREATE INDEX IF NOT EXISTS events_t ON events (run_id, slot, t);
`;

const J = (v) => JSON.stringify(v);
const P = (s) => (s == null ? null : JSON.parse(s));
const now = () => new Date().toISOString();

export class Store {
  constructor(file) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    this.db = new DatabaseSync(file);
    this.db.exec(SCHEMA);
    this.cursors = new Map();
    this.inputSeq = new Map();
    const q = (sql) => this.db.prepare(sql);
    this.s = {
      insRun: q('INSERT INTO runs (created_at, updated_at, kind, parent, label, seed, scenario, config, status) VALUES (?,?,?,?,?,?,?,?,?)'),
      insLane: q('INSERT INTO lanes (run_id, slot, name, agent, kernel, model, token) VALUES (?,?,?,?,?,?,?)'),
      updRun: q('UPDATE runs SET step = ?, sim_t = ?, status = ?, updated_at = ? WHERE id = ?'),
      finRun: q('UPDATE runs SET status = ?, final = ?, updated_at = ? WHERE id = ?'),
      sample: q('INSERT OR REPLACE INTO samples (run_id, slot, t, data) VALUES (?,?,?,?)'),
      event: q('INSERT OR IGNORE INTO events (run_id, slot, id, t, src, sev, text, ref) VALUES (?,?,?,?,?,?,?,?)'),
      con: q('INSERT OR IGNORE INTO console (run_id, slot, id, t, kind, text, aid, prov, conf) VALUES (?,?,?,?,?,?,?,?,?)'),
      action: q('INSERT OR REPLACE INTO actions (run_id, slot, id, t, tool, decision, rule, executed, violation, utility, risk, origin, hash, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)'),
      inc: q('INSERT OR REPLACE INTO incidents (run_id, slot, id, type, start, open, data) VALUES (?,?,?,?,?,?,?)'),
      kpi: q('INSERT OR REPLACE INTO kpis (run_id, slot, t, data) VALUES (?,?,?,?)'),
      exo: q('INSERT OR IGNORE INTO exo (run_id, id, step, t, type, src, label, p) VALUES (?,?,?,?,?,?,?,?)'),
      input: q('INSERT INTO inputs (run_id, seq, step, slot, type, p) VALUES (?,?,?,?,?,?)'),
      fp: q('INSERT OR REPLACE INTO fingerprints (run_id, slot, day, fp) VALUES (?,?,?,?)'),
    };
  }

  createRun(cfg, { kind = 'live', parent = null, label = null, tokens = [] } = {}) {
    const info = this.s.insRun.run(now(), now(), kind, parent, label, cfg.seed, cfg.scenario, J(cfg), 'running');
    const id = Number(info.lastInsertRowid);
    cfg.lanes.forEach((l, i) => this.s.insLane.run(id, i, l.name, l.agent, l.kernel, l.model || null, tokens[i] || null));
    return id;
  }
  /* Entrées recopiées (fork) : écrites avant le démarrage pour qu'une reprise retrouve le préfixe. */
  seedInputs(runId, inputs) {
    this.db.exec('BEGIN');
    try {
      let seq = 0;
      for (const u of inputs) this.s.input.run(runId, ++seq, u.step, u.slot, u.type, J(u.p ?? null));
      this.inputSeq.set(runId, seq);
      this.db.exec('COMMIT');
    } catch (e) { this.db.exec('ROLLBACK'); throw e; }
  }

  /* Draine les files du moteur vers SQLite dans une seule transaction. */
  persist(run) {
    const rid = run.id;
    if (!this.inputSeq.has(rid)) {
      const r = this.db.prepare('SELECT COALESCE(MAX(seq), 0) AS m FROM inputs WHERE run_id = ?').get(rid);
      this.inputSeq.set(rid, Number(r.m));
    }
    this.db.exec('BEGIN');
    try {
      for (const w of run.worlds) {
        let c = this.cursors.get(w.id);
        if (!c) { c = { ev: 0, con: 0, fp: 0 }; this.cursors.set(w.id, c); }
        const silent = run.silent;
        if (!silent) {
          for (const e of w.events) if (e.id > c.ev) this.s.event.run(rid, w.slot, e.id, e.t, e.src, e.sev, e.text, e.ref ? J(e.ref) : null);
          for (const e of w.console) if (e.id > c.con) this.s.con.run(rid, w.slot, e.id, e.t, e.kind, e.text, e.aid || null, e.prov || null, e.conf ?? null);
          if (w.dirtyAct.size) {
            const want = w.dirtyAct;
            for (let i = w.actions.length - 1, left = want.size; i >= 0 && left > 0; i--) {
              const r = w.actions[i];
              if (!want.has(r.id)) continue;
              left--;
              this.s.action.run(rid, w.slot, r.id, r.t, r.tool, r.decision, r.rule, r.executed ? 1 : 0, r.violation ? 1 : 0, r.utility, r.risk, r.origin, r.hash, J(r));
            }
          }
          for (const inc of w.incidents) if (w.dirtyInc.has(inc.id)) this.s.inc.run(rid, w.slot, inc.id, inc.type, inc.start, inc.open ? 1 : 0, J(inc));
          for (const s of w.samplesOut) this.s.sample.run(rid, w.slot, s.t, J(s));
          for (const d of Object.keys(w.fingerprints)) if (+d > c.fp) { this.s.fp.run(rid, w.slot, +d, w.fingerprints[d]); c.fp = +d; }
        } else {
          for (const d of Object.keys(w.fingerprints)) if (+d > c.fp) c.fp = +d;
        }
        if (w.events.length) c.ev = w.events[w.events.length - 1].id;
        if (w.console.length) c.con = w.console[w.console.length - 1].id;
        w.dirtyAct.clear(); w.dirtyInc.clear(); w.samplesOut.length = 0;
      }
      for (const e of run.exoOut.splice(0)) this.s.exo.run(rid, e.id, e.step, e.t, e.type, e.src, e.label, J(e.p));
      for (const u of run.inputOut.splice(0)) { const seq = this.inputSeq.get(rid) + 1; this.inputSeq.set(rid, seq); this.s.input.run(rid, seq, u.step, u.slot, u.type, J(u.p ?? null)); }
      for (const k of run.kpiOut.splice(0)) this.s.kpi.run(rid, +k.world.split(':')[1], k.t, J(k.k));
      if (!run.silent) this.s.updRun.run(run.step, run.t, run.status, now(), rid);
      this.db.exec('COMMIT');
    } catch (e) { this.db.exec('ROLLBACK'); throw e; }
  }
  finalize(run, status) {
    const final = run.worlds.map((w) => ({ slot: w.slot, name: w.name, kind: w.kind, k: computeKpis(w) }));
    this.s.finRun.run(status, J({ step: run.step, t: run.t, lanes: final }), now(), run.id);
  }
  resetCursors(run) { for (const w of run.worlds) this.cursors.delete(w.id); }

  /* ---------------- lectures ---------------- */
  runs(limit = 50) {
    return this.db.prepare('SELECT id, created_at, updated_at, kind, parent, label, seed, scenario, status, step, sim_t, config, final FROM runs ORDER BY id DESC LIMIT ?').all(limit)
      .map((r) => ({ ...r, config: P(r.config), final: P(r.final) }));
  }
  run(id) {
    const r = this.db.prepare('SELECT * FROM runs WHERE id = ?').get(id);
    if (!r) return null;
    return { ...r, config: P(r.config), final: P(r.final), lanes: this.db.prepare('SELECT * FROM lanes WHERE run_id = ? ORDER BY slot').all(id) };
  }
  lastActiveRun() { return this.db.prepare('SELECT id FROM runs WHERE final IS NULL ORDER BY id DESC LIMIT 1').get(); }
  updateSpeed(run) { this.db.prepare("UPDATE runs SET config = json_set(config, '$.speed', json(?)) WHERE id = ?").run(J(run.speed), run.id); }
  recorded(id) {
    return {
      inputs: this.db.prepare('SELECT step, slot, type, p FROM inputs WHERE run_id = ? ORDER BY step, seq').all(id).map((u) => ({ ...u, p: P(u.p) })),
      chaos: this.db.prepare("SELECT id, step, t, type, src, label, p FROM exo WHERE run_id = ? AND src = 'testeur' ORDER BY step, id").all(id).map((e) => ({ ...e, p: P(e.p) })),
    };
  }
  fingerprints(id) {
    const out = {};
    for (const r of this.db.prepare('SELECT slot, day, fp FROM fingerprints WHERE run_id = ?').all(id)) (out[r.slot] = out[r.slot] || {})[r.day] = r.fp;
    return out;
  }
  samples(id, slot, from, to, maxPts = 1500) {
    const n = this.db.prepare('SELECT COUNT(*) AS n FROM samples WHERE run_id = ? AND slot = ? AND t BETWEEN ? AND ?').get(id, slot, from, to).n;
    const stride = Math.max(1, Math.ceil(Number(n) / maxPts));
    const rows = this.db.prepare('SELECT t, data FROM samples WHERE run_id = ? AND slot = ? AND t BETWEEN ? AND ? ORDER BY t').all(id, slot, from, to);
    const out = [];
    for (let i = 0; i < rows.length; i += stride) out.push(P(rows[i].data));
    if (rows.length && (rows.length - 1) % stride) out.push(P(rows[rows.length - 1].data));
    return out;
  }
  events(id, slot, { before = null, limit = 200, src = null, q = null } = {}) {
    let sql = 'SELECT * FROM events WHERE run_id = ? AND slot = ?'; const a = [id, slot];
    if (before != null) { sql += ' AND id < ?'; a.push(before); }
    if (src) { sql += ' AND src = ?'; a.push(src); }
    if (q) { sql += ' AND text LIKE ?'; a.push('%' + q + '%'); }
    sql += ' ORDER BY id DESC LIMIT ?'; a.push(limit);
    return this.db.prepare(sql).all(...a).map((e) => ({ ...e, ref: P(e.ref) }));
  }
  exo(id, limit = 2000) { return this.db.prepare('SELECT * FROM exo WHERE run_id = ? ORDER BY id DESC LIMIT ?').all(id, limit).map((e) => ({ ...e, p: P(e.p) })); }
  console(id, slot, { before = null, limit = 300, kinds = null } = {}) {
    let sql = 'SELECT * FROM console WHERE run_id = ? AND slot = ?'; const a = [id, slot];
    if (before != null) { sql += ' AND id < ?'; a.push(before); }
    if (kinds && kinds.length) { sql += ` AND kind IN (${kinds.map(() => '?').join(',')})`; a.push(...kinds); }
    sql += ' ORDER BY id DESC LIMIT ?'; a.push(limit);
    return this.db.prepare(sql).all(...a);
  }
  actions(id, slot, { before = null, limit = 200, tool = null, decision = null, utility = null, onlyViolations = false } = {}) {
    let sql = 'SELECT data FROM actions WHERE run_id = ? AND slot = ?'; const a = [id, slot];
    if (before) { sql += ' AND id < ?'; a.push(before); }
    if (tool) { sql += ' AND tool = ?'; a.push(tool); }
    if (decision) { sql += ' AND decision = ?'; a.push(decision); }
    if (utility) { sql += ' AND utility = ?'; a.push(utility); }
    if (onlyViolations) sql += ' AND violation = 1';
    sql += ' ORDER BY id DESC LIMIT ?'; a.push(limit);
    return this.db.prepare(sql).all(...a).map((r) => P(r.data));
  }
  action(id, slot, aid) { const r = this.db.prepare('SELECT data FROM actions WHERE run_id = ? AND slot = ? AND id = ?').get(id, slot, aid); return r ? P(r.data) : null; }
  actionsAll(id, slot) { return this.db.prepare('SELECT data FROM actions WHERE run_id = ? AND slot = ? ORDER BY id').all(id, slot).map((r) => P(r.data)); }
  incidents(id, slot) { return this.db.prepare('SELECT data FROM incidents WHERE run_id = ? AND slot = ? ORDER BY start').all(id, slot).map((r) => P(r.data)); }
  kpis(id, slot, maxPts = 800) {
    const rows = this.db.prepare('SELECT t, data FROM kpis WHERE run_id = ? AND slot = ? ORDER BY t').all(id, slot);
    const stride = Math.max(1, Math.ceil(rows.length / maxPts));
    const out = [];
    for (let i = 0; i < rows.length; i += stride) {
      const k = P(rows[i].data);
      out.push({ t: rows[i].t, oet: k.env.oet, set: k.env.set, loss: k.env.loss, kWh: k.energy.kWh, actions: k.actions.total, violations: k.violationsExec, status: k.status, preservation: k.scores.preservation, resilience: k.scores.resilience, autonomy: k.scores.autonomy, efficiency: k.scores.efficiency, safety: k.scores.safety, incidents: k.incidents.total, open: k.incidents.open });
    }
    return out;
  }
  counts(id) {
    const c = (t) => Number(this.db.prepare(`SELECT COUNT(*) AS n FROM ${t} WHERE run_id = ?`).get(id).n);
    return { samples: c('samples'), events: c('events'), console: c('console'), actions: c('actions'), incidents: c('incidents'), kpis: c('kpis'), exo: c('exo'), inputs: c('inputs') };
  }
  dbSize() { try { return this.db.prepare('SELECT page_count * page_size AS s FROM pragma_page_count(), pragma_page_size()').get().s; } catch { return null; } }
}
