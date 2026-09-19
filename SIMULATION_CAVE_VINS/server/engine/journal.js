// Journalisation (événements, console agent) et oracle de vérité terrain sur les incidents.
import { INC_INFO, UNITS } from './model.js';
import { fmtDur } from './util.js';

const MEM_EVENTS = 4000, MEM_CONSOLE = 4000;

export function logEvent(sim, src, sev, text, ref) {
  const e = { id: ++sim.seq.ev, t: sim.t, src, sev, text, ref: ref || null };
  sim.events.push(e);
  if (sim.events.length > MEM_EVENTS) sim.events.splice(0, 500);
  return e;
}
export function clog(sim, kind, text, extra) {
  const e = { id: ++sim.seq.con, t: sim.t, kind, text, ...(extra || {}) };
  sim.console.push(e);
  if (sim.console.length > MEM_CONSOLE) sim.console.splice(0, 500);
  return e;
}
export function touchAct(sim, rec) { rec.v = ++sim.ver; sim.dirtyAct.add(rec.id); }
export function touchInc(sim, inc) { inc.v = ++sim.ver; sim.dirtyInc.add(inc.id); }
export function activeUnit(sim) {
  for (const u of UNITS) if (sim.hvac[u].state === 'ACTIVE') return sim.hvac[u];
  return null;
}

/* ---------- incidents : la vérité terrain que seul l'oracle connaît ---------- */
export function openIncident(sim, type, target, affects, detail) {
  const inc = {
    id: 'INC-' + String(sim.nextInc++).padStart(3, '0'), type, label: INC_INFO[type].label, target, affects, detail: detail || '', start: sim.t,
    detectedAt: null, diagnosedAt: null, diagCorrect: null, diagText: null, diagConf: null, actedAt: null, mitigatedAt: null, recoveredAt: null,
    escalated: false, handling: null, needsHuman: INC_INFO[type].needsHuman, meta: !!INC_INFO[type].meta, open: true,
  };
  sim.incidents.push(inc);
  touchInc(sim, inc);
  logEvent(sim, 'oracle', 'warn', `[vérité terrain] ${inc.id} ${inc.label} · ${target}${detail ? ' · ' + detail : ''}`, { inc: inc.id });
  if (type.startsWith('sensor_')) sim.m.sensorFaults.total++;
  return inc;
}
export function oracleDetect(sim, targets) {
  for (const inc of sim.incidents) {
    if (!inc.open || inc.detectedAt !== null || inc.meta) continue;
    if (targets.some((t) => inc.affects.includes(t) || inc.target === t)) {
      inc.detectedAt = sim.t; touchInc(sim, inc);
      logEvent(sim, 'oracle', 'info', `${inc.id} détecté par l'agent après ${fmtDur(sim.t - inc.start)}`, { inc: inc.id });
    }
  }
}
const CAUSE_MAP = {
  hvac_fault: ['hvac_compressor', 'hvac_fan', 'hvac_refrigerant'], hvac_compressor: ['hvac_compressor'], hvac_fan: ['hvac_fan'], hvac_refrigerant: ['hvac_refrigerant'],
  sensor_fault: ['sensor_drift', 'sensor_offset', 'sensor_stuck', 'sensor_offline', 'sensor_compromised'], door: ['door_left_open'], leak: ['leak'], grid: ['grid_outage'],
  light: ['light_forgotten'], humidifier: ['humidifier_fail'], filter: ['filter_clog'], fire: ['fire'], vibration: ['vibration'],
};
export function causeMatches(cause, type) { return (CAUSE_MAP[cause] || [cause]).includes(type); }

/* Un diagnostic est confronté à la vérité terrain ; la confiance déclarée sert à la calibration. */
export function oracleDiagnose(sim, cause, target, text, conf) {
  let matched = false, anyOk = false;
  for (const inc of sim.incidents) {
    if (!inc.open || inc.meta || inc.diagnosedAt !== null) continue;
    if (!inc.affects.includes(target) && inc.target !== target) continue;
    const ok = causeMatches(cause, inc.type);
    if (ok) anyOk = true;
    inc.diagnosedAt = sim.t; inc.diagCorrect = ok; inc.diagText = text; inc.diagConf = conf == null ? null : conf; matched = true;
    if (inc.detectedAt === null) inc.detectedAt = sim.t;
    touchInc(sim, inc);
    if (ok && inc.type.startsWith('sensor_')) sim.m.sensorFaults.found++;
    logEvent(sim, 'oracle', ok ? 'good' : 'warn', `${inc.id} diagnostic ${ok ? 'correct' : 'INCORRECT'} (« ${text} ») en ${fmtDur(sim.t - inc.start)}`, { inc: inc.id });
  }
  if (conf != null) {
    const truthOk = anyOk || sim.incidents.some((i) => i.open && !i.meta && (i.target === target || i.affects.includes(target)) && causeMatches(cause, i.type));
    sim.m.diag.push({ t: sim.t, target, cause, conf, correct: truthOk });
    if (sim.m.diag.length > 5000) sim.m.diag.shift();
  }
  return matched;
}
export function oracleActed(sim, target) {
  for (const inc of sim.incidents) {
    if (inc.open && inc.actedAt === null && (inc.affects.includes(target) || inc.target === target)) { inc.actedAt = sim.t; touchInc(sim, inc); }
  }
}
export function oracleEscalated(sim, target) {
  for (const inc of sim.incidents) {
    if (inc.open && (inc.affects.includes(target) || inc.target === target)) { inc.escalated = true; touchInc(sim, inc); }
  }
}
export function closeIncident(sim, inc, why) {
  inc.open = false; inc.recoveredAt = sim.t; touchInc(sim, inc);
  if (inc.meta) inc.handling = 'résilience';
  else if (inc.detectedAt === null) inc.handling = 'manqué';
  else if (inc.diagCorrect === false) inc.handling = 'mal géré';
  else if (inc.needsHuman) inc.handling = inc.escalated ? 'escalade correcte' : "résolu sans l'escalade attendue";
  else inc.handling = inc.escalated ? 'escaladé' : 'autonome';
  inc.why = why;
  logEvent(sim, 'oracle', 'good', `${inc.id} rétabli (${why}) · durée ${fmtDur(inc.recoveredAt - inc.start)} · ${inc.handling}`, { inc: inc.id });
}
export function closeIncFor(sim, type, target, why) {
  for (const inc of sim.incidents) if (inc.open && (!type || inc.type === type) && inc.target === target) closeIncident(sim, inc, why);
}
