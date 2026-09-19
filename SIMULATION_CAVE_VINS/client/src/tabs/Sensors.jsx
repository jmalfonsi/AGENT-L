// Capteurs : ce que l'agent reçoit (valeur, provenance, santé déclarée, confiance) face à la vérité terrain.
import { useState } from 'react';
import { f, fDur, HEALTH_CLS, KIND_FR } from '../fmt.js';
import { Tile, Chip, Seg } from '../components/ui.jsx';

export default function Sensors({ ctx }) {
  const { s, meta, open } = ctx;
  const w = s.w;
  const [zone, setZone] = useState('all');
  const [kind, setKind] = useState('all');
  const [only, setOnly] = useState('all');
  const [q, setQ] = useState('');
  const rows = meta.sensors.map((m, i) => ({ m, c: w.sensors[i] })).filter(({ m, c }) =>
    (zone === 'all' || m.zone === zone) && (kind === 'all' || m.kind === kind) && (!q || m.id.toLowerCase().includes(q.toLowerCase()) || m.label.toLowerCase().includes(q.toLowerCase()))
    && (only === 'all' || (only === 'bad' && c.h !== 'NORMAL') || (only === 'flag' && (c.q || c.rj || (c.su != null && c.su >= 3)))));
  const bad = w.sensors.filter((c) => c.h !== 'NORMAL').length, quar = w.sensors.filter((c) => c.q).length, rej = w.sensors.filter((c) => c.rj).length;
  const kinds = [...new Set(meta.sensors.map((m) => m.kind))];
  return (
    <div className="grid">
      <Tile className="c12" title={`${meta.sensors.length} capteurs`} right={<span>{bad} défaillant(s) (vérité) · {quar} en quarantaine (agent) · {rej} écarté(s) par le contrôleur</span>}>
        <div className="row" style={{ marginBottom: 8 }}>
          <div className="field"><label>Zone</label><select value={zone} onChange={(e) => setZone(e.target.value)}><option value="all">toutes</option>{meta.zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}<option value="ext">Extérieur</option></select></div>
          <div className="field"><label>Type</label><select value={kind} onChange={(e) => setKind(e.target.value)}><option value="all">tous</option>{kinds.map((k) => <option key={k} value={k}>{KIND_FR[k] || k}</option>)}</select></div>
          <div className="field"><label>Filtre</label><Seg small value={only} onChange={setOnly} options={[['all', 'tous'], ['bad', 'défaillants'], ['flag', 'signalés']]} /></div>
          <div className="field"><label>Recherche</label><input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="bdx-T2…" /></div>
        </div>
        <div className="tw scroll tall">
          <table>
            <thead><tr><th>Capteur</th><th>Zone</th><th>Type</th><th className="r">Mesure transmise</th><th className="r">Vérité</th><th className="r">Écart</th><th>Santé réelle</th><th>Santé déclarée</th><th className="r">Confiance</th><th className="r">Calibration</th><th className="r">Batterie</th><th className="r">Radio</th><th className="r">Latence</th><th className="r">Âge</th><th>Régulation</th><th className="r">Suspicion agent</th></tr></thead>
            <tbody>
              {rows.map(({ m, c }) => {
                const err = c.v != null && c.tr != null ? c.v - c.tr : null;
                const big = err != null && Math.abs(err) > Math.max(.5, 5 * m.prec);
                return (
                  <tr key={m.id} className={'click' + (c.h !== 'NORMAL' ? ' bad' : '')} onClick={() => open({ kind: 'sensor', id: m.id })}>
                    <td className="mono"><b>{m.id}</b><div className="muted" style={{ fontFamily: 'var(--f-body)' }}>{m.label}</div></td>
                    <td>{m.zone}</td><td>{KIND_FR[m.kind] || m.kind}</td>
                    <td className="r num"><b>{c.v == null ? '—' : f(c.v, m.unit === 'g' ? 3 : 2)}</b> {m.unit}</td>
                    <td className="r num muted">{c.tr == null ? '—' : f(c.tr, m.unit === 'g' ? 3 : 2)}</td>
                    <td className={'r num ' + (big ? 'crit' : 'muted')}>{err == null ? '—' : (err >= 0 ? '+' : '') + f(err, 2)}</td>
                    <td><Chip cls={HEALTH_CLS[c.h]}>{c.h}</Chip></td>
                    <td><Chip cls={c.dh === 'NORMAL' ? '' : 'warn'}>{c.dh}</Chip></td>
                    <td className="r num">{f(c.c * 100, 0)} %</td><td className="r num">{c.cal} j</td>
                    <td className={'r num ' + (c.bat < 30 ? 'warn' : '')}>{c.bat} %</td><td className="r num">{c.rad} dBm</td><td className="r num">{c.lat} ms</td>
                    <td className="r num muted">{c.ts ? fDur(w.t - c.ts) : '—'}</td>
                    <td>{c.q ? <Chip cls="warn">quarantaine</Chip> : c.rj ? <Chip cls="serious">écartée</Chip> : <span className="muted">utilisée</span>}</td>
                    <td className="r num">{c.su == null ? '—' : c.su >= 8 ? <b className="crit">{c.su}</b> : c.su}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="note">« Mesure transmise », provenance OBSERVED, santé déclarée et confiance sont ce que l’agent reçoit. « Vérité » et « santé réelle » ne servent qu’à l’oracle (§16-17, §42). Le contrôleur déterministe écarte seul les sauts physiquement impossibles ; tout le reste est le travail de l’agent.</p>
      </Tile>
    </div>
  );
}
