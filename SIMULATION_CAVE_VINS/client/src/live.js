// Flux temps réel (SSE) : un seul abonnement pour le couloir affiché, état muté en place puis re-rendu.
import { useEffect, useRef, useState } from 'react';

const CAP = { events: 1500, exo: 1000, console: 2500, actions: 3000, incidents: 1500, hist: 2016, ghost: 3000 };
const push = (arr, add, cap) => { if (add && add.length) { arr.push(...add); if (arr.length > cap) arr.splice(0, arr.length - cap); } };

function mergeHist(H, h) {
  if (!h) return H;
  if (!H) return h;
  const last = H.t.length ? H.t[H.t.length - 1] : -Infinity;
  let i = 0; while (i < h.t.length && h.t[i] <= last) i++;
  if (i >= h.t.length) return H;
  const sl = (a) => a.slice(i);
  H.t.push(...sl(h.t)); H.out.push(...sl(h.out)); H.kw.push(...sl(h.kw)); H.soc.push(...sl(h.soc));
  for (const z of Object.keys(h.z)) for (const k of ['T', 'Tm', 'RH', 'Tb']) H.z[z][k].push(...sl(h.z[z][k]));
  const over = H.t.length - CAP.hist;
  if (over > 0) {
    for (const k of ['t', 'out', 'kw', 'soc']) H[k].splice(0, over);
    for (const z of Object.keys(H.z)) for (const k of ['T', 'Tm', 'RH', 'Tb']) H.z[z][k].splice(0, over);
  }
  return H;
}
function addGhost(G, g) {
  for (const [slot, v] of Object.entries(g || {})) {
    const q = G[slot] || (G[slot] = { t: [], loss: [], kWh: [], z: {} });
    if (q.t.length && v.t <= q.t[q.t.length - 1]) continue;
    q.t.push(v.t); q.loss.push(v.loss); q.kWh.push(v.kWh);
    for (const [z, T] of Object.entries(v.z)) (q.z[z] || (q.z[z] = new Array(q.t.length - 1).fill(null))).push(T);
    if (q.t.length > CAP.ghost) { q.t.shift(); q.loss.shift(); q.kWh.shift(); for (const z of Object.keys(q.z)) q.z[z].shift(); }
  }
}
function trimMap(m, cap) { if (m.size <= cap) return; let n = m.size - cap; for (const k of m.keys()) { m.delete(k); if (--n <= 0) break; } }

export function useLive(lane) {
  const S = useRef(null);
  const [ver, setVer] = useState(0);
  const [conn, setConn] = useState('connecting');
  useEffect(() => {
    S.current = null; setVer((v) => v + 1);
    const es = new EventSource('/api/stream?lane=' + lane);
    es.onopen = () => setConn('open');
    es.onerror = () => setConn('error');
    es.onmessage = (m) => {
      let d; try { d = JSON.parse(m.data); } catch { return; }
      const s0 = S.current;
      if (d.type === 'init' || !s0 || s0.runId !== d.run.id || s0.slot !== d.w.slot) {
        const G = {}; addGhost(G, d.ghost);
        S.current = {
          runId: d.run.id, slot: d.w.slot, run: d.run, lanes: d.lanes, w: d.w, ghost: d.ghost, ghostH: G,
          events: d.events || [], exo: d.exo || [], console: d.console || [],
          actions: new Map((d.actions || []).map((a) => [a.id, a])), incidents: new Map((d.incidents || []).map((i) => [i.id, i])), hist: d.hist,
        };
      } else {
        const s = S.current;
        s.run = d.run; s.lanes = d.lanes; s.w = d.w; s.ghost = d.ghost;
        push(s.events, d.events, CAP.events); push(s.exo, d.exo, CAP.exo); push(s.console, d.console, CAP.console);
        for (const a of d.actions || []) s.actions.set(a.id, a);
        trimMap(s.actions, CAP.actions);
        for (const i of d.incidents || []) s.incidents.set(i.id, i);
        trimMap(s.incidents, CAP.incidents);
        s.hist = mergeHist(s.hist, d.hist);
        addGhost(s.ghostH, d.ghost);
      }
      setConn('open');
      setVer((v) => v + 1);
    };
    return () => es.close();
  }, [lane]);
  return { s: S.current, ver, conn };
}
