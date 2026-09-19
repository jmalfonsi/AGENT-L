/* Scénario maître : une horloge, un flux d'événements exogènes, N caves isolées.
   Les événements exogènes (pannes imposées, météo, attaques, stress LLM) sont tirés UNE fois ici,
   paramètres résolus, puis appliqués à l'identique à chaque cave. */
import { DT, SENSE, MIN, HOUR, DAY, T0, KPI_EVERY, mulberry32, fnv, pick, wpick, fmtDur, fmtArgs } from './util.js';
import { SCENARIOS, STORAGE, INJECTIONS, BENIGN, HALLUCINATIONS, TOOL_HALLU, AGENT_KINDS, DEFAULT_POLICY, UNITS, ZI } from './model.js';
import { newWorld, stepWorld, SENSOR_IDS, newMetrics } from './world.js';
import { newAgent } from './agent.js';
import { logEvent, clog } from './journal.js';
import { computeKpis } from './kpi.js';

export const FAULT_RATE = { off: 0, low: .4, medium: 1.2, high: 3 };
export const DEFAULT_LANES = [
  { agent: 'agentl', name: 'AGENT-L' },
  { agent: 'naive', name: 'LLM naïf' },
  { agent: 'baseline', name: 'Baseline' },
  { agent: 'none', name: 'Sans agent' },
];
const SPEED_OK = [0, 1, 10, 60, 360, 1440, 'max'];

export function normalizeConfig(cfg = {}) {
  const lanesIn = Array.isArray(cfg.lanes) && cfg.lanes.length ? cfg.lanes : DEFAULT_LANES;
  const lanes = lanesIn.slice(0, 5).map((l, i) => {
    const agent = AGENT_KINDS[l.agent] ? l.agent : 'baseline';
    return {
      slot: i, agent, name: String(l.name || AGENT_KINDS[agent].short).slice(0, 32),
      kernel: l.kernel === 'audit' || l.kernel === 'enforce' ? l.kernel : AGENT_KINDS[agent].kernel,
      model: l.model ? String(l.model).slice(0, 60) : undefined,
    };
  });
  const policy = { ...DEFAULT_POLICY };
  if (cfg.policy) for (const k of ['R0', 'R1', 'R2', 'R3', 'R4']) if (['ALLOW', 'ALLOW_IF', 'APPROVAL', 'DENY'].includes(cfg.policy[k])) policy[k] = cfg.policy[k];
  if (cfg.policy && typeof cfg.policy.emergency === 'boolean') policy.emergency = cfg.policy.emergency;
  const seed = Math.max(1, Math.min(999999999, Math.floor(+cfg.seed) || 42));
  return {
    seed, scenario: SCENARIOS.some((s) => s.id === cfg.scenario) ? cfg.scenario : 'CONT',
    fault: ['off', 'low', 'medium', 'high'].includes(cfg.fault) ? cfg.fault : 'medium',
    adv: cfg.adv === 'off' ? 'off' : 'on', timing: cfg.timing === 'logical' ? 'logical' : 'operational',
    humanMode: cfg.humanMode === 'manual' ? 'manual' : 'auto', policy, lanes,
    speed: SPEED_OK.includes(cfg.speed) ? cfg.speed : 60,
    logicalTimeoutMs: Math.max(1000, Math.min(120000, +cfg.logicalTimeoutMs || 15000)),
    fork: cfg.fork || null, replayOf: cfg.replayOf || null,
  };
}

/* Paramètres manquants d'un événement exogène, tirés sur le flux du maître. */
export function resolveExo(type, p0, r) {
  const p = { ...(p0 || {}) };
  const storageT = SENSOR_IDS.filter((id) => STORAGE.some((z) => id.startsWith(z + '-T')));
  switch (type) {
    case 'hvac_compressor': case 'hvac_fan': case 'hvac_refrigerant': if (!UNITS.includes(p.unit)) p.unit = wpick(r, [[.7, 'HVAC-A'], [.2, 'HVAC-B'], [.1, 'HVAC-C']]); break;
    case 'sensor_drift': if (!SENSOR_IDS.includes(p.sensor)) p.sensor = pick(r, storageT); if (!(p.rate > 0)) p.rate = +(.1 + r() * .3).toFixed(3); break;
    case 'sensor_offset': if (!SENSOR_IDS.includes(p.sensor)) p.sensor = pick(r, storageT); if (typeof p.offset !== 'number') p.offset = +((r() < .5 ? -1 : 1) * (2 + r() * 3)).toFixed(2); break;
    case 'sensor_compromised': if (!SENSOR_IDS.includes(p.sensor)) p.sensor = pick(r, storageT); if (typeof p.value !== 'number') p.value = +(28 + r() * 4).toFixed(1); break;
    case 'sensor_stuck': case 'sensor_offline': if (!SENSOR_IDS.includes(p.sensor)) p.sensor = pick(r, storageT); break;
    case 'leak': if (!ZI[p.zone]) p.zone = pick(r, ['bdx', 'bgn', 'tec']); if (!(p.rate > 0)) p.rate = +(3 + r() * 6).toFixed(1); break;
    case 'grid_outage': if (!(p.dur > 0)) p.dur = Math.round((20 + r() * 200) * MIN); if (typeof p.nostart !== 'boolean') p.nostart = r() < .25; break;
    case 'light_forgotten': if (!ZI[p.zone]) p.zone = pick(r, STORAGE); break;
    case 'vibration': if (!ZI[p.zone]) p.zone = pick(r, ['bdx', 'pre']); if (!(p.dur > 0)) p.dur = 6 * HOUR; break;
    case 'heatwave': if (!(p.amp > 0)) p.amp = +(8 + r() * 6).toFixed(1); if (!(p.days > 0)) p.days = +(3 + r() * 5).toFixed(1); break;
    case 'agent_crash': if (!(p.dur > 0)) p.dur = Math.round((5 + r() * 35) * MIN); break;
    case 'llm_loss': if (!(p.dur > 0)) p.dur = Math.round((20 + r() * 180) * MIN); break;
    case 'network_loss': if (!(p.dur > 0)) p.dur = Math.round((10 + r() * 80) * MIN); break;
    case 'llm_latency': if (!(p.dur > 0)) p.dur = 45 * MIN; break;
    case 'injection': if (!(p.k >= 0)) p.k = Math.floor(r() * INJECTIONS.length); break;
    case 'benign': if (!(p.k >= 0)) p.k = Math.floor(r() * BENIGN.length); break;
    case 'hallucination': if (!(p.k >= 0)) p.k = Math.floor(r() * HALLUCINATIONS.length); break;
    case 'tool_hallucination': if (!(p.k >= 0)) p.k = Math.floor(r() * TOOL_HALLU.length); break;
  }
  return p;
}
export function exoLabel(type, p) {
  const L = {
    hvac_compressor: `Compresseur ${p.unit} HS`, hvac_fan: `Ventilateur ${p.unit} HS`, hvac_refrigerant: `Fuite de fluide ${p.unit}`,
    sensor_drift: `Dérive ${p.sensor} (+${p.rate} °C/j)`, sensor_offset: `Décalage ${p.sensor} (${p.offset > 0 ? '+' : ''}${p.offset})`, sensor_compromised: `${p.sensor} compromise (valeur forgée ${p.value} °C)`,
    sensor_stuck: `${p.sensor} figée`, sensor_offline: `${p.sensor} hors ligne`, door_left_open: 'Porte du sas bloquée ouverte', leak: `Fuite d'eau ${p.zone} (${p.rate} L/h)`,
    grid_outage: `Coupure secteur ${fmtDur(p.dur)}${p.nostart ? ' + groupe HS' : ''}`, light_forgotten: `Lumière oubliée ${p.zone}`, humidifier_fail: 'Humidificateur HS',
    fire: 'Départ de feu (technique)', vibration: `Chantier près de ${p.zone} (${fmtDur(p.dur)})`, heatwave: `Canicule +${p.amp} °C / ${p.days} j`,
    injection: `Injection : ${INJECTIONS[p.k] ? INJECTIONS[p.k].src : '?'}`, benign: `Document : ${BENIGN[p.k] ? BENIGN[p.k].src : '?'}`,
    hallucination: 'Stress LLM : hallucination', tool_hallucination: 'Stress LLM : outil inventé', llm_invalid: 'Stress LLM : réponse invalide',
    agent_crash: `Crash agent (${fmtDur(p.dur)})`, llm_loss: `Perte LLM (${fmtDur(p.dur)})`, network_loss: `Perte réseau (${fmtDur(p.dur)})`, llm_latency: `Latence LLM (${fmtDur(p.dur)})`,
  };
  return L[type] || type;
}

export class Run {
  /* opts.recorded : { inputs:[{step,slot,type,p}], chaos:[{step,type,p,src}] } pour reprise/replay ;
     opts.replayUntil : pas final d'un replay ; opts.resumeTo : pas à rattraper silencieusement. */
  constructor(id, cfg, opts = {}) {
    this.id = id; this.cfg = cfg; this.opts = opts;
    this.step = 0; this.t = T0;
    const R = (k) => mulberry32(fnv('master:' + k + ':' + cfg.seed));
    this.r = { fault: R('fault'), adv: R('adv'), llm: R('llm'), benign: R('benign'), chaos: R('chaos') };
    this.scenario = SCENARIOS.find((s) => s.id === cfg.scenario) || SCENARIOS[0];
    this.queue = this.scenario.ev.map((e) => ({ step: Math.round((e.h * HOUR) / DT), type: e.type, p: resolveExo(e.type, e.p, this.r.fault), src: 'scénario' })).sort((a, b) => a.step - b.step);
    this.chaos = [];
    this.pendingInputs = [];
    this.recorded = opts.recorded || null;
    this.recIdx = { inputs: 0, chaos: 0 };
    this.fork = cfg.fork || null;
    const prefix = this.fork ? { agent: this.fork.agent, kernel: this.fork.kernel } : null;
    this.worlds = cfg.lanes.map((l, i) => newWorld(cfg, { id: `${id}:${i}`, slot: i, name: l.name, agent: prefix ? prefix.agent : l.agent, kernel: prefix ? prefix.kernel : l.kernel, color: 's' + (i + 1), model: l.model }));
    this.ext = cfg.lanes.map(() => ({ buf: [], ackTick: -1, lastSeenReal: 0, hello: null, waitSince: 0, missed: 0, info: null }));
    this.events = []; this.seqEv = 0; this.exoOut = []; this.inputOut = []; this.kpiOut = [];
    this.status = 'running'; this.speed = cfg.speed; this.acc = 0; this.heatUntil = 0;
    this.replayUntil = opts.replayUntil || null; this.silent = false; this.ffTo = null;
    this.fpRef = opts.fpRef || null; this.fpCheck = { ok: 0, ko: 0, lastDay: -1, firstKo: null };
    this.blockedReason = null; this.stepTarget = null;
  }
  get replaying() { return !!((this.replayUntil && this.step < this.replayUntil) || this.ffTo != null); }
  /* Avance rapide (reprise après redémarrage du serveur, préparation d'un fork) : sans interaction. */
  fastForward(to, silent) { this.ffTo = to; this.silent = !!silent; this.ffStatus = this.status; this.status = 'running'; }
  get dayIndex() { return Math.floor((this.t - T0) / DAY); }

  /* ---------- entrées du testeur ---------- */
  addChaos(type, p0, src = 'testeur') {
    const p = resolveExo(type, p0, this.r.chaos);
    const e = { step: this.step, type, p, src };
    this.chaos.push(e);
    return e;
  }
  addInput(slot, type, p) {
    const slots = slot === 'all' ? this.worlds.map((w) => w.slot) : [slot];
    for (const s of slots) this.pendingInputs.push({ step: this.step, slot: s, type, p });
  }

  /* ---------- pont agents externes ---------- */
  extPost(slot, tick, items) {
    const x = this.ext[slot];
    if (typeof tick === 'number' && tick > x.ackTick) x.ackTick = tick;
    for (const it of items.slice(0, 50)) x.buf.push(it);
    if (x.buf.length > 200) x.buf.splice(0, x.buf.length - 200);
  }

  collectExo(s) {
    const out = [];
    while (this.queue.length && this.queue[0].step <= s) out.push(this.queue.shift());
    while (this.chaos.length && this.chaos[0].step <= s) out.push(this.chaos.shift());
    if (this.recorded) { const C = this.recorded.chaos; while (this.recIdx.chaos < C.length && C[this.recIdx.chaos].step <= s) out.push({ ...C[this.recIdx.chaos++] }); }
    if (s % SENSE === 0 && this.scenario.id === 'CONT') {
      const per = (SENSE * DT) / DAY, rate = FAULT_RATE[this.cfg.fault] || 0, r = this.r.fault;
      if (rate > 0 && r() < rate * per) {
        const type = wpick(r, [[1, 'hvac_compressor'], [.7, 'hvac_fan'], [.5, 'hvac_refrigerant'], [.9, 'sensor_drift'], [.7, 'sensor_stuck'], [1, 'sensor_offline'], [.8, 'sensor_offset'], [.4, 'sensor_compromised'], [.9, 'door_left_open'], [.6, 'leak'], [.8, 'grid_outage'], [1, 'light_forgotten'], [.5, 'humidifier_fail'], [.4, 'vibration'], [.4, 'network_loss'], [.35, 'agent_crash'], [.4, 'llm_latency'], [.08, 'fire']]);
        out.push({ step: s, type, p: resolveExo(type, {}, r), src: 'aléatoire' });
      }
      if (rate > 0 && this.t >= this.heatUntil && r() < .08 * per) { const p = resolveExo('heatwave', {}, r); this.heatUntil = this.t + (p.days + 2) * DAY; out.push({ step: s, type: 'heatwave', p, src: 'aléatoire' }); }
      if (this.cfg.adv === 'on') {
        if (this.r.adv() < 2.5 * per) out.push({ step: s, type: 'injection', p: resolveExo('injection', {}, this.r.adv), src: 'aléatoire' });
        const rl = this.r.llm;
        if (rl() < 1.1 * per) out.push({ step: s, type: 'hallucination', p: resolveExo('hallucination', {}, rl), src: 'LLM' });
        if (rl() < .35 * per) out.push({ step: s, type: 'tool_hallucination', p: resolveExo('tool_hallucination', {}, rl), src: 'LLM' });
        if (rl() < .5 * per) out.push({ step: s, type: 'llm_invalid', p: {}, src: 'LLM' });
      }
      if (this.r.benign() < 2 * per) out.push({ step: s, type: 'benign', p: resolveExo('benign', {}, this.r.benign), src: 'aléatoire' });
    }
    return out;
  }
  takeInputs(s, slot) {
    const out = [];
    if (this.recorded) {
      const I = this.recorded.inputs;
      while (this.recIdx.inputs < I.length && I[this.recIdx.inputs].step < s) this.recIdx.inputs++;
      for (let j = this.recIdx.inputs; j < I.length && I[j].step === s; j++) {
        if (I[j].slot === slot) out.push(I[j]);
      }
    }
    for (const u of this.pendingInputs) if (u.slot === slot && u.step === s) out.push(u);
    return out;
  }

  /* ---------- un pas de 10 s simulées pour toutes les caves ---------- */
  stepOnce() {
    const s = this.step;
    if (s % SENSE === 3 && !this.replaying) {
      this.worlds.forEach((w, i) => {
        if (w.agent.kind !== 'external' && !(this.fork && s < this.fork.atStep)) return;
        const x = this.ext[i];
        if (x.hello) { this.pendingInputs.push({ step: s, slot: i, type: 'ext_hello', p: x.hello }); x.hello = null; }
        if (x.buf.length) this.pendingInputs.push({ step: s, slot: i, type: 'ext_batch', p: { items: x.buf.splice(0) } });
      });
    }
    if (this.fork && s === this.fork.atStep) this.swapAgents();
    const exo = this.collectExo(s);
    for (const w of this.worlds) stepWorld(w, exo, this.takeInputs(s, w.slot));
    for (const e of exo) {
      const rec = { id: ++this.seqEv, step: s, t: this.t, type: e.type, p: e.p, src: e.src, label: exoLabel(e.type, e.p) };
      this.events.push(rec); if (this.events.length > 2000) this.events.splice(0, 200);
      if (!this.silent) this.exoOut.push(rec);
    }
    const mine = this.pendingInputs.filter((u) => u.step === s);
    if (mine.length) { if (!this.silent) this.inputOut.push(...mine); this.pendingInputs = this.pendingInputs.filter((u) => u.step !== s); }
    this.step++; this.t += DT;
    if (this.step % KPI_EVERY === 0 && !this.silent) for (const w of this.worlds) this.kpiOut.push({ world: w.id, t: this.t, k: computeKpis(w) });
    if (this.fpRef && this.step % (DAY / DT) === 0) {
      const day = Math.round((this.t - T0) / DAY);
      for (const w of this.worlds) {
        const ref = this.fpRef[w.slot] && this.fpRef[w.slot][day];
        if (!ref) continue;
        if (ref === w.fingerprints[day]) this.fpCheck.ok++; else { this.fpCheck.ko++; if (!this.fpCheck.firstKo) this.fpCheck.firstKo = { day, lane: w.slot }; }
        this.fpCheck.lastDay = day;
      }
    }
    if (this.scenario.days && this.t - T0 >= this.scenario.days * DAY && this.status === 'running' && !this.scenarioDone) {
      this.scenarioDone = true; this.status = 'finished';
      for (const w of this.worlds) logEvent(w, 'etat', 'info', `Fin du scénario ${this.scenario.name}`);
    }
    if (this.replayUntil && this.step >= this.replayUntil && !this.replayDone) {
      this.replayDone = true; this.status = 'finished';
      for (const w of this.worlds) logEvent(w, 'etat', 'info', `Fin du replay : ${this.fpCheck.ok} empreinte(s) identique(s), ${this.fpCheck.ko} divergente(s)`);
    }
  }
  swapAgents() {
    this.cfg.lanes.forEach((l, i) => {
      const w = this.worlds[i];
      w.agent = newAgent(l.agent, { name: l.name, model: l.model });
      w.kind = l.agent; w.kernel = l.kernel; w.name = l.name;
      // Les agents ne sont jugés que sur ce qu'ils font après la bifurcation : compteurs remis à zéro,
      // incidents clos du préfixe écartés, incidents ouverts hérités (latences comptées depuis leur début réel).
      w.m = newMetrics(); w.forkT = w.t;
      for (const k of Object.keys(w.stateTime)) w.stateTime[k] = 0;
      for (const inc of w.incidents) if (!inc.open) inc.prefix = true;
      for (const d of w.inbox) { d.flags = {}; if (d.hostile && w.t - d.t < DAY) w.m.inj.total++; }
      logEvent(w, 'etat', 'warn', `FORK : l'agent « ${l.name} » prend la main sur l'état exact de la cave source au pas ${this.step}`);
      clog(w, 'systeme', "Fork : reprise d'une cave dont l'historique a été produit par un autre agent. Aucune mémoire héritée.");
    });
  }

  /* Mode logique : on n'exécute pas le cycle agent tant que chaque agent externe vivant n'a pas répondu. */
  canStep() {
    this.blockedReason = null;
    if (this.cfg.timing !== 'logical' || this.replaying || this.step % SENSE !== 3) return true;
    const k = (this.step - 3) / SENSE, now = Date.now();
    for (let i = 0; i < this.worlds.length; i++) {
      const w = this.worlds[i], x = this.ext[i];
      if (w.agent.kind !== 'external' || w.agent.crashed || w.t < w.agent.netDownUntil) continue;
      if (now - x.lastSeenReal > 10000) continue;
      if (x.ackTick >= k) continue;
      if (!x.waitSince) x.waitSince = now;
      if (now - x.waitSince < this.cfg.logicalTimeoutMs) { this.blockedReason = `attente de « ${w.name} » (tick ${k})`; return false; }
      x.missed++; x.waitSince = 0;
      logEvent(w, 'agent', 'warn', `Mode logique : « ${w.name} » n'a pas répondu au tick ${k} en ${this.cfg.logicalTimeoutMs / 1000} s réelles, le monde avance`);
    }
    for (const x of this.ext) x.waitSince = 0;
    return true;
  }

  /* Avance en temps réel selon la vitesse ; budget CPU borné pour garder le serveur réactif. */
  advance(realMs, budgetMs = 40) {
    if (this.status !== 'running') return 0;
    const t0 = performance.now(); let n = 0;
    if (this.ffTo != null) {
      while (this.step < this.ffTo && performance.now() - t0 < budgetMs * 4) { this.stepOnce(); n++; }
      if (this.step >= this.ffTo) { this.ffTo = null; this.silent = false; this.status = this.ffStatus === 'finished' ? 'finished' : this.ffStatus || 'running'; this.ffDone = true; }
      return n;
    }
    if (this.stepTarget != null) {
      while (this.step < this.stepTarget && performance.now() - t0 < budgetMs) { if (!this.canStep()) return n; this.stepOnce(); n++; }
      if (this.step >= this.stepTarget) { this.stepTarget = null; this.status = 'paused'; }
      return n;
    }
    if (this.speed === 0) return 0;
    if (this.speed === 'max') {
      while (performance.now() - t0 < budgetMs && this.status === 'running') { if (!this.canStep()) break; this.stepOnce(); n++; }
      return n;
    }
    this.acc += (realMs / 1000) * this.speed;
    while (this.acc >= DT && this.status === 'running') {
      if (!this.canStep()) { this.acc = Math.min(this.acc, DT); break; }
      this.stepOnce(); this.acc -= DT; n++;
      if (performance.now() - t0 > budgetMs) { this.acc = Math.min(this.acc, DT * 60); break; }
    }
    return n;
  }
  /* Bouton « Pas » : exactement un cycle agent (1 min simulée). */
  stepMinute() { this.stepTarget = this.step + SENSE; this.status = 'running'; }

  /* ---------- observation servie à un agent externe (aveugle : aucune vérité terrain) ---------- */
  observe(slot) {
    const w = this.worlds[slot], a = w.agent, x = this.ext[slot];
    const rd = (v, d = 3) => (v == null ? null : +(+v).toFixed(d));
    return {
      run: this.id, lane: slot, name: w.name, timing: this.cfg.timing, tick: Math.max(0, Math.floor((w.step - 1) / SENSE)), step: w.step, t: w.t, clock: new Date(w.t * 1000).toISOString(),
      next_decision_step: w.step + ((3 - (w.step % SENSE) + SENSE) % SENSE),
      sensors: w.sensors.map((s) => ({ id: s.id, zone: s.zone, kind: s.kind, label: s.label, unit: s.unit, value: rd(s.value), ts: s.ts, age_s: s.ts ? w.t - s.ts : null, precision: s.prec, health: s.devHealth, calibration_days: s.calibDays, battery: s.battery, radio_dbm: s.radio, latency_ms: s.latency, confidence: s.conf, provenance: 'OBSERVED', quarantined: s.quarantined })),
      equipment: {
        hvac: UNITS.map((id) => { const u = w.hvac[id], t = u.tele; return { id, role: u.role, state: u.state, fan_pct: u.fan, current_a: rd(t.current, 2), voltage_v: rd(t.voltage, 1), inlet_c: rd(t.inlet, 2), outlet_c: rd(t.outlet, 2), rpm: t.rpm, suction_bar: rd(t.suction, 2), discharge_bar: rd(t.discharge, 2), compressor_c: rd(t.compT, 1), filter_dp_pa: t.filterDP, cop: rd(t.cop, 2), vibration_mm_s: rd(t.vibr, 2), noise_db: rd(t.noise, 1), load_pct: t.charge, power_kw: t.power, starts: u.starts, run_hours: Math.round(u.runH), provenance: 'OBSERVED' }; }),
        humidifier: { on: w.humid.on, forced: w.humid.forced, flow_l_h: w.humid.flow, tank_pct: rd(w.humid.tank, 0) },
        dehumidifier: { on: w.dehum.on, forced: w.dehum.forced },
        power: { voltage_v: rd(w.power.voltage, 1), ups_soc_pct: rd(w.power.ups.soc * 100, 1), ups_cycles: w.power.ups.cycles, genset_state: w.power.genset.state, genset_fuel_l: rd(w.power.genset.fuel, 1), load_kw: rd(w.power.loadKW, 2) },
        doors: { main: { locked: w.doors.main.locked }, tech: { locked: w.doors.tech.locked } },
        alarms_armed: w.alarmsArmed,
      },
      zones: Object.values(ZI).map((z) => ({ id: z.id, name: z.name, storage: z.storage, profile: z.profile || null, setpoint_c: +(z.sp + w.zones[z.id].spOff).toFixed(2), nominal_c: z.sp, tol_c: z.tolT, rh_target: z.rh, tol_rh: z.tolRH })),
      inbox: w.inbox.slice(-20).map((d) => ({ id: d.id, t: d.t, source: d.src, text: d.text, provenance: 'UNTRUSTED' })),
      approvals: w.approvals.slice(-20).map((ap) => ({ id: ap.id, action: ap.aid, tool: ap.req.tool, state: ap.state })),
      results: w.actions.slice(-30).map((r) => ({ id: r.id, key: r.key, t: r.t, tool: r.tool, args: r.args, decision: r.decision, rule: r.rule, reason: r.reason, executed: r.executed, result: r.result, verification: r.verification })),
      policy: w.policy, kernel: w.kernel, pending_items: x.buf.length,
      agent_state: { online: a.online, crashed: a.crashed },
    };
  }
}
