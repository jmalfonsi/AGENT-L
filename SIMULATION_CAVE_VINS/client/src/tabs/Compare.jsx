// Comparaison multi-agents : matrice de KPI, vue « fantôme », première divergence, fork d'un instant.
import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { f, fRel, fDur, fInt, fArgs, laneColor, STATE_CLS, STATE_FR, DAY } from '../fmt.js';
import { Tile, Chip, Seg, Empty, Decision } from '../components/ui.jsx';
import { LineChart, Legend } from '../components/Chart.jsx';

const ROWS = [
  ['Statut sûreté', (k) => k.status, null, (v) => v],
  ['Préservation', (k) => k.scores.preservation, 1, (v) => f(v, 1)],
  ['Enveloppe optimale %', (k) => k.env.oet, 1, (v) => f(v, 2)],
  ['Enveloppe sûre %', (k) => k.env.set, 1, (v) => f(v, 3)],
  ['Perte de conservation', (k) => k.env.loss, -1, (v) => f(v, 3)],
  ['Exposition critique (min)', (k) => k.env.critMin, -1, (v) => f(v, 0)],
  ['Violations exécutées', (k) => k.violationsExec, -1, (v) => fInt(v)],
  ['Injections → effet', (k) => k.inj.executed, -1, (v, k) => `${v}/${k.inj.total}`],
  ['Hallucinations → action', (k) => k.hall.executed, -1, (v, k) => `${v}/${k.hall.total}`],
  ['Effets dupliqués', (k) => k.dupExec, -1, (v) => fInt(v)],
  ['Détection P50', (k) => k.lat.detect.p50, -1, (v) => fDur(v)],
  ['Rétablissement P95', (k) => k.lat.recover.p95, -1, (v) => fDur(v)],
  ['Diagnostics corrects %', (k) => k.rates.correctDiagnosis, 1, (v) => f(v, 1)],
  ['Incidents manqués %', (k) => k.rates.missedIncident, -1, (v) => f(v, 1)],
  ['Résolution autonome %', (k) => k.rates.autonomousResolution, 1, (v) => f(v, 1)],
  ['Interventions inutiles/nuisibles %', (k) => k.rates.falseIntervention, -1, (v) => f(v, 1)],
  ['Surcoût énergie %', (k) => k.energy.overhead, -1, (v) => f(v, 1)],
  ['Stress équipements', (k) => k.energy.stress, -1, (v) => fInt(v)],
  ['Actions exécutées', (k) => k.actions.executed, 0, (v) => fInt(v)],
  ['Coût LLM €', (k) => k.llm.cost, 0, (v) => f(v, 3)],
];

function useSeries(ctx, metric, zone, win) {
  const { s } = ctx;
  const [data, setData] = useState(null);
  const refresh = () => { const to = s.w.t; api.get(`/api/compare?metric=${metric}&zone=${zone}&from=${win === 'all' ? 0 : to - win}&to=${to}`).then(setData).catch(() => {}); };
  useEffect(refresh, [metric, zone, win, s.runId]);
  if (!data) return null;
  // Prolonge les séries SQLite avec les points temps réel reçus depuis (flux « fantôme »).
  return data.series.map((sr) => {
    const g = s.ghostH[sr.slot]; const t = [...sr.t], v = [...sr.v];
    if (g) { const last = t.length ? t[t.length - 1] : 0; for (let i = 0; i < g.t.length; i++) if (g.t[i] > last + 290 && (!t.length || g.t[i] - t[t.length - 1] >= 290)) { t.push(g.t[i]); v.push(metric === 'loss' ? g.loss[i] : g.z[zone] ? g.z[zone][i] : null); } }
    return { ...sr, t, v };
  });
}
/* Aligne des séries aux instants de la première (échantillons communs toutes les 5 min). */
function align(series) {
  if (!series || !series.length) return null;
  const x = series[0].t;
  const idx = series.map((sr) => { const m = new Map(); sr.t.forEach((t, i) => m.set(t, sr.v[i])); return m; });
  return { x, vs: idx.map((m) => x.map((t) => (m.has(t) ? m.get(t) : null))) };
}

export default function Compare({ ctx }) {
  const { s, meta, act, setLane, setTab } = ctx;
  const [zone, setZone] = useState('pre');
  const [win, setWin] = useState(DAY);
  const [div, setDiv] = useState(null);
  const [thr, setThr] = useState(.3);
  const [forkLane, setForkLane] = useState(0);
  const lanes = s.lanes;
  const ser = useSeries(ctx, 'T', zone, win), loss = useSeries(ctx, 'loss', zone, win);
  const A = align(ser), B = align(loss);
  const Z = meta.zones.find((z) => z.id === zone);
  const best = (row) => {
    const [, get, dir] = row; if (!dir) return {};
    const vals = lanes.map((l) => (l.k ? get(l.k) : null)).filter((v) => v != null);
    if (vals.length < 2) return {};
    return { best: dir > 0 ? Math.max(...vals) : Math.min(...vals), worst: dir > 0 ? Math.min(...vals) : Math.max(...vals) };
  };
  const findDiv = () => act(api.get(`/api/divergence?threshold=${thr}`)).then((r) => r && setDiv(r));
  const fork = (t) => act(api.post(`/api/runs/${s.run.id}/fork`, { slot: forkLane, t }), 'Fork créé : reconstitution de la cave source…');
  return (
    <div className="grid">
      <Tile className="c12" title="Matrice de comparaison" right={<span>même seed, mêmes événements exogènes · vert = meilleur couloir, rouge = pire</span>}>
        <div className="tw">
          <table className="matrix">
            <thead><tr><th>Indicateur</th>{lanes.map((l) => <th key={l.slot} className="r"><span className="swatch" style={{ background: laneColor(l.slot) }} /> {l.name}</th>)}</tr></thead>
            <tbody>
              <tr><td>État opérationnel</td>{lanes.map((l) => <td key={l.slot} className="r"><Chip cls={STATE_CLS[l.opState]}>{STATE_FR[l.opState]}</Chip></td>)}</tr>
              <tr><td>Agent</td>{lanes.map((l) => <td key={l.slot} className="r muted">{meta.agentKinds[l.kind].short}{l.kernel === 'audit' ? ' · audit' : ''}</td>)}</tr>
              {ROWS.map((row) => {
                const [label, get, , fmt] = row, b = best(row);
                return (
                  <tr key={label}><td>{label}</td>{lanes.map((l) => {
                    const v = l.k ? get(l.k) : null;
                    const cls = label === 'Statut sûreté' ? (v === 'PASS' ? 'good' : 'crit') : v != null && v === b.best && b.best !== b.worst ? 'best' : v != null && v === b.worst && b.best !== b.worst ? 'worst' : '';
                    return <td key={l.slot} className={'r num ' + cls}>{v == null ? '—' : fmt(v, l.k)}</td>;
                  })}</tr>
                );
              })}
              <tr><td></td>{lanes.map((l) => <td key={l.slot} className="r"><button className="btn small" onClick={() => { setLane(l.slot); setTab('synthese'); }}>ouvrir</button></td>)}</tr>
            </tbody>
          </table>
        </div>
      </Tile>
      <Tile className="c8" title={`Vue fantôme — ${Z.name}`} right={<><Seg small value={zone} onChange={setZone} options={meta.storage.map((id) => [id, meta.zones.find((z) => z.id === id).short])} /><Seg small value={win} onChange={setWin} options={[[6 * 3600, '6 h'], [DAY, '24 h'], [3 * DAY, '3 j'], [7 * DAY, '7 j'], ['all', 'tout']]} /></>}>
        <Legend items={lanes.map((l) => ({ label: `${l.name} · ${f(s.ghost[l.slot] && s.ghost[l.slot].z[zone], 2)} °C`, color: laneColor(l.slot) }))} />
        {A ? <LineChart x={A.x} h={300} unit="°C" band={[Z.sp - Z.tolT, Z.sp + Z.tolT]} refLines={[{ v: Z.sp }]} series={lanes.map((l, i) => ({ label: l.name, v: A.vs[i] || [], color: laneColor(l.slot) }))} onPick={(t) => setDiv((d) => ({ ...(d || {}), pick: t }))} /> : <Empty>Chargement…</Empty>}
        <p className="note">Les caves divergent uniquement par les décisions des agents. Cliquez sur la courbe pour retenir un instant (fork ci-contre).</p>
      </Tile>
      <Tile className="c4" title="Perte de conservation cumulée">
        {B ? <LineChart x={B.x} h={170} dec={3} series={lanes.map((l, i) => ({ label: l.name, v: B.vs[i] || [], color: laneColor(l.slot) }))} /> : <Empty>—</Empty>}
        <div className="flabel" style={{ marginTop: 10 }}>Fork d’un instant (« git branch » du monde physique)</div>
        <div className="stack" style={{ marginTop: 6 }}>
          <div className="row"><span>Cave source</span><select value={forkLane} onChange={(e) => setForkLane(+e.target.value)}>{lanes.map((l) => <option key={l.slot} value={l.slot}>{l.name}</option>)}</select></div>
          <div className="row">
            <button className="btn" onClick={() => fork(s.w.t)}>Forker maintenant</button>
            {div && div.pick && <button className="btn" onClick={() => fork(div.pick)}>Forker à {fRel(div.pick)}</button>}
            {div && div.found && <button className="btn" onClick={() => fork(div.t - 300)}>Forker avant la divergence</button>}
          </div>
          <p className="note">Tous les agents repartent de l’état exact de la cave source, avec le même historique, et on observe leurs futurs.</p>
        </div>
      </Tile>
      <Tile className="c12" title="Première divergence significative" right={<><span>seuil</span><Seg small value={thr} onChange={setThr} options={[[.1, '0,1 °C'], [.3, '0,3 °C'], [.5, '0,5 °C'], [1, '1 °C']]} /><button className="btn small primary" onClick={findDiv}>Chercher</button></>}>
        {!div || div.found === undefined ? <Empty>Le moteur cherche le premier instant où les températures des caves s’écartent de plus du seuil, puis montre ce que chaque agent pensait et faisait.</Empty>
          : !div.found ? <Empty>{div.reason}</Empty> : (
            <div className="stack">
              <p style={{ margin: 0 }}>Divergence à <b>{fRel(div.t)}</b> en zone <b>{meta.zones.find((z) => z.id === div.zone).name}</b> : écart de <b>{f(div.spread, 2)} °C</b> entre couloirs.</p>
              {div.exo.length > 0 && <p className="muted" style={{ margin: 0 }}>Événements exogènes des 6 h précédentes : {div.exo.map((e) => `${fRel(e.t)} ${e.label}`).join(' · ')}</p>}
              <div className="grid" style={{ gridTemplateColumns: `repeat(${div.lanes.length}, minmax(0, 1fr))` }}>
                {div.lanes.map((l) => (
                  <div key={l.slot} className="card" style={{ borderTop: `3px solid ${laneColor(l.slot)}` }}>
                    <div className="row"><b>{l.name}</b><Chip cls={STATE_CLS[l.state]}>{STATE_FR[l.state]}</Chip></div>
                    <div className="muted" style={{ fontSize: 12 }}>{f(l.T, 2)} °C · perte +30 min {f(l.loss30, 4)} · +2 h {f(l.loss2h, 4)}</div>
                    <div className="flabel" style={{ marginTop: 6 }}>Pensées</div>
                    {l.thoughts.length ? l.thoughts.slice(0, 6).map((c, i) => <div key={i} style={{ fontSize: 11.5 }}><span className={'k ' + c.kind} style={{ font: '600 10px var(--f-cond)', textTransform: 'uppercase' }}>{c.kind}</span> {c.text}</div>) : <div className="muted" style={{ fontSize: 12 }}>—</div>}
                    <div className="flabel" style={{ marginTop: 6 }}>Actions</div>
                    {l.actions.length ? l.actions.slice(0, 8).map((a) => <div key={a.id} style={{ fontSize: 11.5 }} className="mono">{fRel(a.t).split(' ')[1]} {a.tool}({fArgs(a.args)}) <Decision a={a} /></div>) : <div className="muted" style={{ fontSize: 12 }}>ne fait rien</div>}
                  </div>
                ))}
              </div>
            </div>)}
      </Tile>
    </div>
  );
}
