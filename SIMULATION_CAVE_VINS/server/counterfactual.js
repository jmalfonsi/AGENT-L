/* Effet contre-factuel d'une action (KPI.md §5), calculé dans un worker pour ne pas figer la simulation.
   On rejoue la cave de façon déterministe deux fois : branche A telle qu'elle s'est produite,
   branche B où cette action précise est refusée. ActionUtility = perte(B) − perte(A) sur l'horizon. */
import { parentPort, workerData } from 'node:worker_threads';
import { Run } from './engine/run.js';
import { computeKpis } from './engine/kpi.js';

function branch(suppress) {
  const { cfg, slot, recorded, step, horizon } = workerData;
  const lane = cfg.lanes[slot];
  const c = { ...cfg, lanes: [{ ...lane, slot: 0 }], fork: cfg.fork ? { ...cfg.fork } : null };
  const rec = {
    inputs: recorded.inputs.filter((u) => u.slot === slot).map((u) => ({ ...u, slot: 0 })),
    chaos: recorded.chaos,
  };
  const run = new Run(0, c, { recorded: rec });
  run.silent = true;
  const w = run.worlds[0];
  if (suppress) w.suppress = suppress;
  while (run.step < step) run.stepOnce();
  const k0 = computeKpis(w), loss0 = w.m.loss, t0 = w.t;
  const snap = () => ({ T: Object.fromEntries(Object.entries(w.zones).map(([id, z]) => [id, +z.T.toFixed(3)])), op: w.opState });
  const series = [];
  while (run.step < step + horizon) {
    run.stepOnce();
    if (run.step % 30 === 0) series.push({ t: w.t, loss: +(w.m.loss - loss0).toFixed(5), pre: +w.zones.pre.T.toFixed(3), bdx: +w.zones.bdx.T.toFixed(3), chp: +w.zones.chp.T.toFixed(3) });
  }
  const k1 = computeKpis(w);
  return { loss: w.m.loss - loss0, kWh: k1.energy.kWh - k0.energy.kWh, oetDelta: k1.env.oet - k0.env.oet, end: snap(), t0, series, actions: w.actions.filter((r) => r.t >= t0).slice(0, 40).map((r) => ({ id: r.id, t: r.t, tool: r.tool, args: r.args, decision: r.decision, executed: r.executed })) };
}

try {
  const A = branch(null);
  const B = branch({ id: workerData.aid });
  parentPort.postMessage({ ok: true, A, B, utility: B.loss - A.loss, energyDelta: A.kWh - B.kWh });
} catch (e) {
  parentPort.postMessage({ ok: false, error: String(e && e.stack || e) });
}
