/* Construction des messages temps réel (SSE) à partir de l'état en mémoire. */
import { ZONES, ZI, STORAGE, UNITS } from './engine/model.js';
import { truthOf } from './engine/world.js';
import { computeKpis, summarize } from './engine/kpi.js';
import { DAY } from './engine/util.js';

const rd = (v, d = 3) => (v == null || !isFinite(v) ? null : Math.round(v * 10 ** d) / 10 ** d);

export function runInfo(run, lanesDb) {
  return {
    id: run.id, status: run.status, speed: run.speed, step: run.step, t: run.t, seed: run.cfg.seed, scenario: run.scenario.id, scenarioName: run.scenario.name,
    scenarioDays: run.scenario.days, fault: run.cfg.fault, adv: run.cfg.adv, timing: run.cfg.timing, humanMode: run.worlds[0] ? run.worlds[0].humanMode : run.cfg.humanMode, error: run.error || null,
    blocked: run.blockedReason, ff: run.ffTo != null ? { to: run.ffTo, pct: Math.round((run.step / run.ffTo) * 100), silent: run.silent } : null,
    replay: run.replayUntil ? { until: run.replayUntil, of: run.cfg.replayOf, fp: run.fpCheck, done: !!run.replayDone } : null,
    fork: run.fork ? { ...run.fork } : null, policy: run.worlds[0] ? run.worlds[0].policy : null,
    lanes: run.cfg.lanes.map((l, i) => ({ ...l, token: lanesDb && lanesDb[i] ? lanesDb[i].token : null })),
  };
}

export function laneSummaries(run, kCache) {
  return run.worlds.map((w) => ({ ...summarize(w, kCache[w.slot]), k: kCache[w.slot] }));
}

export function ghost(run) {
  const out = {};
  for (const w of run.worlds) {
    const z = {};
    for (const id of STORAGE) z[id] = rd(w.zones[id].T, 3);
    out[w.slot] = { t: w.t, z, loss: rd(w.m.loss, 4), kWh: rd(w.m.kWh, 2), op: w.opState };
  }
  return out;
}

export function worldDetail(run, w, k) {
  const a = w.agent, x = run.ext[w.slot];
  const zones = {};
  for (const z of ZONES) {
    const q = w.zones[z.id];
    let value = 0; for (const b of w.inventory) if (b.zone === z.id) value += b.value;
    zones[z.id] = {
      T: rd(q.T), Tb: rd(q.Tb), ctlT: rd(q.ctlT), RH: rd(q.RH, 2), lux: rd(q.lux, 0), vib: rd(q.vib, 4), water: rd(q.water, 2), valve: q.valve, leak: q.leakRate > 0,
      lightOn: q.lightOn, presence: q.presence, smoke: rd(q.smoke, 2), co2: rd(q.co2, 0), sp: rd(z.sp + q.spOff, 2), spOff: rd(q.spOff, 2), dew: rd(q.dew, 2),
      devT: rd(q.devT), devRH: rd(q.devRH, 2), isolated: q.isolated, lightDose: rd(q.lightDose, 0), vibExp: rd(q.vibExp, 2), duty: rd(q.duty, 3), value,
    };
  }
  const load = Math.max(.1, w.power.loadKW);
  return {
    slot: w.slot, name: w.name, kind: w.kind, kernel: w.kernel, color: w.color, opState: w.opState, stateSince: w.stateSince, stateTime: w.stateTime,
    policy: w.policy, humanMode: w.humanMode, t: w.t, step: w.step,
    wx: { Tout: rd(w.wx.Tout, 2), RHout: rd(w.wx.RHout, 1), P: rd(w.wx.P, 1), ground: rd(w.wx.ground, 2), heat: w.wx.heat ? { amp: w.wx.heat.amp, days: w.wx.heat.days, t0: w.wx.heat.t0 } : null },
    zones,
    sensors: w.sensors.map((s) => {
      const q = a.sq && a.sq[s.id];
      return { id: s.id, v: rd(s.value), tr: rd(truthOf(w, s)), h: s.health, dh: s.devHealth, c: s.conf, q: s.quarantined, rj: s.ctlReject, cal: s.calibDays, bat: s.battery, rad: s.radio, lat: s.latency, ts: s.ts, su: q ? q.susp : null };
    }),
    hvac: Object.fromEntries(UNITS.map((id) => { const u = w.hvac[id]; return [id, { state: u.state, role: u.role, fault: u.fault, eff: rd(u.eff), aging: rd(u.aging), filter: rd(u.filter), fan: u.fan, kw: rd(u.kw, 2), kWh: rd(u.kWh, 1), refLoss: rd(u.refLoss), starts: u.starts, runH: Math.round(u.runH), cap: u.cap, tele: Object.fromEntries(Object.entries(u.tele).map(([kk, v]) => [kk, typeof v === 'number' ? rd(v, 3) : v])) }]; })),
    humid: { ...w.humid, tank: rd(w.humid.tank, 1), waterL: rd(w.humid.waterL, 1) }, dehum: w.dehum,
    power: {
      grid: w.power.grid, voltage: rd(w.power.voltage, 1), loadKW: rd(w.power.loadKW, 2), peakKW: rd(w.m.peakKW, 2), blackout: w.power.blackout, gridBackAt: w.power.grid ? null : w.power.gridBackAt,
      ups: { soc: rd(w.power.ups.soc, 4), health: w.power.ups.health, cycles: w.power.ups.cycles, capKWh: w.power.ups.capKWh, autonomyH: rd((w.power.ups.soc * w.power.ups.capKWh * w.power.ups.health) / load, 2) },
      genset: { state: w.power.genset.state, fuel: rd(w.power.genset.fuel, 1), fault: w.power.genset.fault, temp: rd(w.power.genset.temp, 1) },
    },
    doors: w.doors, alarmsArmed: w.alarmsArmed, alarmsDisabled: w.alarmsDisabled,
    agent: {
      kind: a.kind, label: a.label, model: a.model, online: a.online, crashed: a.crashed, stalled: a.stalled, connected: a.connected, reasoner: a.reasoner,
      phases: a.phases, hot: a.hot, cycle: a.cycle, prediction: a.prediction || null,
      llmDown: w.t < a.llmDownUntil, netDown: w.t < a.netDownUntil, slow: w.t < a.latencyUntil, crashUntil: a.crashed ? a.crashUntil : null,
      anoms: Object.values(a.anoms).map((an) => ({ key: an.key, t: an.t, target: an.target, cause: an.cause, conf: an.conf, obs: an.obs })),
      plans: a.plans.slice(-12).map((p) => ({ id: p.id, origin: p.origin, hyp: p.hyp, target: p.target, i: p.i, done: p.done, t: p.t, alternatives: p.alternatives, steps: p.steps.map((s) => ({ tool: s.tool, args: s.args, why: s.why, expected: s.expected })) })),
      ext: a.kind === 'external' || run.fork ? { lastSeenAgoMs: x.lastSeenReal ? Date.now() - x.lastSeenReal : null, ackTick: x.ackTick, missed: x.missed, buffered: x.buf.length, info: x.info, lastContactT: a.lastContactT } : null,
    },
    approvals: w.approvals.filter((ap) => ap.state === 'pending').map((ap) => ({ id: ap.id, t: ap.t, aid: ap.aid, tool: ap.req.tool, args: ap.req.args, why: ap.req.why, evidence: ap.req.evidence, autoAt: w.humanMode === 'auto' ? ap.autoAt : null })),
    inbox: w.inbox.slice(-15).reverse().map((d) => ({ id: d.id, t: d.t, src: d.src, text: d.text, hostile: d.hostile, flags: d.flags || null })),
    tickets: w.tickets.filter((tk) => !tk.done).map((tk) => ({ id: tk.id, target: tk.target, reason: tk.reason, t: tk.t, due: tk.due })),
    humans: w.humans.filter((h) => !h.done).map((h) => ({ t: h.t, at: h.at, reason: h.reason })),
    valueExposed: w.valueExposed, inventoryValue: w.inventory.reduce((s, b) => s + b.value, 0),
    k,
  };
}

/* Fenêtre d'historique mémoire (échantillons à 5 min) postérieure à `since`. */
export function histSince(w, since, maxDays = 3) {
  const H = w.hist, n = H.t.length;
  let i = 0;
  const floor = since != null ? since : (n ? H.t[n - 1] - maxDays * DAY : 0);
  while (i < n && H.t[i] <= floor) i++;
  if (i >= n) return null;
  const sl = (arr) => arr.slice(i);
  const z = {};
  for (const zz of ZONES) z[zz.id] = { T: sl(H.z[zz.id].T), Tm: sl(H.z[zz.id].Tm), RH: sl(H.z[zz.id].RH), Tb: sl(H.z[zz.id].Tb) };
  return { t: sl(H.t), out: sl(H.out), kw: sl(H.kw), soc: sl(H.soc), z };
}

export function kpisFor(run) {
  const out = {};
  for (const w of run.worlds) out[w.slot] = computeKpis(w);
  return out;
}
export { ZI };
