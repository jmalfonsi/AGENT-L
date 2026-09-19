// Incidents (vérité terrain) : chronologie visuelle, latences par incident, fil des événements cliquable.
import { useMemo, useState } from 'react';
import { f, fRel, fDur, fHM, DAY, HANDLING_CLS, T0 } from '../fmt.js';
import { Tile, Chip, Seg, Filters, Empty } from '../components/ui.jsx';

const SRC = [['oracle', 'oracle'], ['agent', 'agent'], ['policy', 'policy'], ['etat', 'état'], ['humain', 'humain'], ['chaos', 'chaos'], ['maitre', 'scénario maître']];
const toggle = (set, v) => { const n = new Set(set); if (v == null) return new Set(); n.has(v) ? n.delete(v) : n.add(v); return n; };
const HCOL = { autonome: 'var(--good)', 'escalade correcte': 'var(--good)', escaladé: 'var(--info)', 'mal géré': 'var(--crit)', manqué: 'var(--crit)', "résolu sans l'escalade attendue": 'var(--warn)', résilience: 'var(--muted)' };

export default function Incidents({ ctx }) {
  const { s, open, lane } = ctx;
  const w = s.w, k = w.k;
  const [span, setSpan] = useState(3 * DAY);
  const [srcs, setSrcs] = useState(new Set());
  const [q, setQ] = useState('');
  const [showMeta, setShowMeta] = useState(false);
  const incs = [...s.incidents.values()].filter((i) => showMeta || !i.meta);
  const t1 = w.t, t0 = span === 'all' ? T0 : Math.max(T0, t1 - span);
  const vis = incs.filter((i) => (i.recoveredAt || t1) >= t0).slice(-40);
  const X = (t) => ((Math.max(t0, Math.min(t1, t)) - t0) / Math.max(1, t1 - t0)) * 100;
  const events = useMemo(() => {
    const ev = s.events.map((e) => ({ ...e, key: 'w' + e.id })).concat(s.exo.map((e) => ({ id: e.id, t: e.t, src: 'maitre', sev: 'info', text: `[${e.src}] ${e.label} — appliqué à toutes les caves`, key: 'x' + e.id })));
    return ev.filter((e) => (!srcs.size || srcs.has(e.src)) && (!q || e.text.toLowerCase().includes(q.toLowerCase()))).sort((a, b) => b.t - a.t || (b.key > a.key ? 1 : -1)).slice(0, 500);
  }, [s.events.length, s.exo.length, srcs, q, s.events[s.events.length - 1]]);
  const onEvent = (e) => {
    if (e.ref && e.ref.aid) { const a = s.actions.get(e.ref.aid); if (a) open({ kind: 'action', a }); }
    else if (e.ref && e.ref.inc) { const i = s.incidents.get(e.ref.inc); if (i) open({ kind: 'incident', inc: i }); }
  };
  const acts = [...s.actions.values()].filter((a) => a.t >= t0 && a.executed && a.risk > 0);
  const exo = s.exo.filter((e) => e.t >= t0);
  const dayTicks = []; for (let t = T0 + Math.ceil((t0 - T0) / DAY) * DAY; t <= t1; t += DAY) dayTicks.push(t);
  return (
    <div className="grid">
      <Tile className="c12" title="Bilan des incidents réels" right={<span>la vérité terrain n’est jamais transmise à l’agent</span>}>
        <div className="scores">
          {[['Incidents', k.incidents.total], ['Ouverts', k.incidents.open], ['Détectés', k.incidents.detected], ['Diagnostiqués', k.incidents.diagnosed], ['Diagnostics corrects', k.incidents.correct], ['Clos', k.incidents.closed], ['Résolus en autonomie', k.incidents.auto], ['Escaladés', k.incidents.escalated], ['Mal gérés', k.incidents.bad], ['Manqués', k.incidents.missed]].map(([l, v]) => (
            <div className="score" key={l}><span className="l">{l}</span><span className={'v ' + (l === 'Manqués' || l === 'Mal gérés' ? (v ? 'crit' : 'good') : '')}>{v}</span></div>))}
        </div>
      </Tile>
      <Tile className="c12" title="Chronologie" right={<><label className="row" style={{ gap: 5 }}><input type="checkbox" checked={showMeta} onChange={(e) => setShowMeta(e.target.checked)} /> incidents d’infrastructure agent</label><Seg small value={span} onChange={setSpan} options={[[6 * 3600, '6 h'], [DAY, '24 h'], [3 * DAY, '3 j'], [7 * DAY, '7 j'], ['all', 'tout']]} /></>}>
        <div className="gantt">
          <div className="row" style={{ height: 20, position: 'relative' }}>{dayTicks.map((t) => <span key={t} className="lbl" style={{ left: X(t) + '%' }}>J+{Math.round((t - T0) / DAY)}</span>)}</div>
          <div className="row" title="Événements exogènes du scénario maître (communs à toutes les caves)">
            <span className="lbl">exogène</span>
            {exo.map((e) => <span key={e.id} className="mk" style={{ left: X(e.t) + '%', background: e.type === 'benign' ? 'var(--line2)' : e.type === 'injection' || e.src === 'LLM' ? 'var(--l5)' : 'var(--l4)' }} title={`${fRel(e.t)} · ${e.label}`} />)}
          </div>
          <div className="row" title="Actions exécutées (hors lectures)">
            <span className="lbl">actions</span>
            {acts.map((a) => <span key={a.id} className="mk" style={{ left: X(a.t) + '%', background: a.violation ? 'var(--crit)' : 'var(--l1)', cursor: 'pointer' }} title={`${fRel(a.t)} · ${a.tool}`} onClick={() => open({ kind: 'action', a })} />)}
          </div>
          {vis.map((i) => {
            const end = i.recoveredAt || t1;
            return (
              <div key={i.id} className="row">
                <span className="lbl">{i.id} · {i.label} · {i.target}</span>
                <span className="bar" style={{ left: X(i.start) + '%', width: Math.max(.3, X(end) - X(i.start)) + '%', background: i.open ? 'color-mix(in srgb, var(--crit) 55%, transparent)' : `color-mix(in srgb, ${HCOL[i.handling] || 'var(--muted)'} 45%, transparent)` }} onClick={() => open({ kind: 'incident', inc: i })} title={`${i.label} · ${fDur(end - i.start)}`} />
                {i.detectedAt != null && <span className="mk" style={{ left: X(i.detectedAt) + '%', background: 'var(--info)' }} title={`détecté +${fDur(i.detectedAt - i.start)}`} />}
                {i.diagnosedAt != null && <span className="mk" style={{ left: X(i.diagnosedAt) + '%', background: i.diagCorrect ? 'var(--l5)' : 'var(--crit)' }} title={`diagnostic ${i.diagCorrect ? 'correct' : 'faux'} +${fDur(i.diagnosedAt - i.start)}`} />}
                {i.actedAt != null && <span className="mk" style={{ left: X(i.actedAt) + '%', background: 'var(--good)' }} title={`action +${fDur(i.actedAt - i.start)}`} />}
              </div>
            );
          })}
        </div>
        <div className="legend" style={{ marginTop: 6 }}>
          <span><i style={{ background: 'color-mix(in srgb, var(--crit) 55%, transparent)', height: 8 }} />incident ouvert</span><span><i style={{ background: 'color-mix(in srgb, var(--good) 45%, transparent)', height: 8 }} />clos en autonomie / bonne escalade</span>
          <span><i style={{ background: 'var(--info)' }} />détection</span><span><i style={{ background: 'var(--l5)' }} />diagnostic correct</span><span><i style={{ background: 'var(--good)' }} />première action</span><span><i style={{ background: 'var(--l4)' }} />événement exogène</span><span><i style={{ background: 'var(--l1)' }} />action exécutée</span>
        </div>
      </Tile>
      <Tile className="c7" title="Incidents et latences">
        <div className="tw scroll tall">
          {incs.length ? <table>
            <thead><tr><th>Id</th><th>Incident</th><th>Début</th><th className="r">Détection</th><th className="r">Diagnostic</th><th className="r">Action</th><th className="r">Rétabli</th><th>Traitement</th></tr></thead>
            <tbody>{[...incs].reverse().map((i) => (
              <tr key={i.id} className="click" onClick={() => open({ kind: 'incident', inc: i })}>
                <td className="mono">{i.id}</td><td>{i.label}<div className="muted">{i.target}</div></td><td className="nowrap num">{fRel(i.start)}</td>
                <td className="r num">{i.detectedAt != null ? '+' + fDur(i.detectedAt - i.start) : i.meta ? '—' : <span className="crit">—</span>}</td>
                <td className={'r num ' + (i.diagCorrect === false ? 'crit' : '')}>{i.diagnosedAt != null ? (i.diagCorrect ? '✓ +' : '✗ +') + fDur(i.diagnosedAt - i.start) : '—'}</td>
                <td className="r num">{i.actedAt != null ? '+' + fDur(i.actedAt - i.start) : '—'}</td>
                <td className="r num">{i.recoveredAt != null ? fDur(i.recoveredAt - i.start) : <Chip cls="warn">en cours</Chip>}</td>
                <td>{i.handling ? <Chip cls={HANDLING_CLS[i.handling]}>{i.handling}</Chip> : '—'}</td>
              </tr>))}</tbody>
          </table> : <Empty>Aucun incident pour l’instant.</Empty>}
        </div>
      </Tile>
      <Tile className="c5" title="Fil des événements" right={<span>clic = action ou incident lié</span>}>
        <Filters options={SRC} value={srcs} onToggle={(v) => setSrcs(toggle(srcs, v))} all="tout" />
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="rechercher…" style={{ width: '100%', marginBottom: 6 }} />
        <div className="tl scroll tall">
          {events.map((e) => (
            <div key={e.key} className={'tli' + (e.ref && (e.ref.aid || e.ref.inc) ? ' click' : '')} onClick={() => onEvent(e)}>
              <span className="num muted">{fRel(e.t)}</span><span className={'src ' + e.src}>{e.src === 'maitre' ? 'maître' : e.src}</span>
              <span className={e.sev === 'crit' ? 'crit' : e.sev === 'warn' ? 'warn' : e.sev === 'good' ? 'good' : ''}>{e.text}</span>
            </div>))}
        </div>
      </Tile>
    </div>
  );
}
