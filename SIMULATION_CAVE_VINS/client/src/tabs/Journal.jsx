// Journal & audit : historique des runs (SQLite), replay, export, vérification de la chaîne de hash, journal complet.
import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { f, fRel, fInt, DAY } from '../fmt.js';
import { Tile, Chip, Seg, Empty } from '../components/ui.jsx';

const SRC = [['', 'toutes'], ['oracle', 'oracle'], ['agent', 'agent'], ['policy', 'policy'], ['etat', 'état'], ['humain', 'humain'], ['chaos', 'chaos']];
const STATUS_FR = { running: 'en cours', paused: 'en pause', finished: 'terminé', stopped: 'arrêté' };

export default function Journal({ ctx }) {
  const { s, lane, act, open } = ctx;
  const [runs, setRuns] = useState(null);
  const [db, setDb] = useState(null);
  const [verif, setVerif] = useState({});
  const [src, setSrc] = useState('');
  const [q, setQ] = useState('');
  const [log, setLog] = useState([]);
  const [more, setMore] = useState(true);
  const loadRuns = () => { api.get('/api/runs').then(setRuns).catch(() => {}); api.get('/api/db').then(setDb).catch(() => {}); };
  useEffect(loadRuns, [s.runId, s.run.status]);
  const loadLog = (reset) => {
    const before = !reset && log.length ? `&before=${log[log.length - 1].id}` : '';
    api.get(`/api/events?lane=${lane}&limit=200${before}${src ? '&src=' + src : ''}${q ? '&q=' + encodeURIComponent(q) : ''}`).then((rows) => { setLog(reset ? rows : [...log, ...rows]); setMore(rows.length === 200); }).catch(() => {});
  };
  useEffect(() => loadLog(true), [lane, src, s.runId]);
  const verify = (l) => api.get(`/api/audit/verify?lane=${l}`).then((r) => setVerif((v) => ({ ...v, [l]: r }))).catch(() => {});
  return (
    <div className="grid">
      <Tile className="c12" title="Historique des runs (SQLite)" right={<>{db && <span>base {f(db.size / 1e6, 1)} Mo · {fInt(db.counts.samples)} échantillons · {fInt(db.counts.actions)} actions pour ce run</span>}<button className="btn small" onClick={loadRuns}>actualiser</button></>}>
        {runs ? (
          <div className="tw scroll">
            <table>
              <thead><tr><th>Run</th><th>Type</th><th>Créé le</th><th>Scénario</th><th className="r">Seed</th><th className="r">Durée simulée</th><th>Couloirs et verdicts</th><th>Statut</th><th></th></tr></thead>
              <tbody>{runs.map((r) => {
                const lanes = r.final ? r.final.lanes : null;
                return (
                  <tr key={r.id} className={r.live ? 'sel' : ''}>
                    <td className="mono">#{r.id}{r.label && <div className="muted" style={{ fontFamily: 'var(--f-body)' }}>{r.label}</div>}</td>
                    <td><Chip cls={r.kind === 'live' ? '' : 'info'}>{r.kind}</Chip>{r.parent ? <span className="muted"> ← #{r.parent}</span> : null}</td>
                    <td className="nowrap">{new Date(r.created_at).toLocaleString('fr-FR')}</td>
                    <td>{r.scenario}</td><td className="r">{r.seed}</td>
                    <td className="r num">{f((r.step * 10) / DAY, 2)} j</td>
                    <td>{(r.config.lanes || []).map((l, i) => {
                      const k = lanes && lanes[i] && lanes[i].k;
                      return <span key={i} style={{ marginRight: 8, whiteSpace: 'nowrap' }}>{l.name} {k ? <Chip cls={k.status === 'PASS' ? 'good' : 'crit'}>{k.status} · OET {f(k.env.oet, 1)}</Chip> : r.live ? <Chip>en cours</Chip> : null}</span>;
                    })}</td>
                    <td>{r.live ? <Chip cls="good">courant</Chip> : STATUS_FR[r.status] || r.status}</td>
                    <td className="nowrap">
                      <button className="btn small" title="Rejouer à l’identique (mêmes entrées, mêmes réponses enregistrées) et vérifier les empreintes jour par jour" onClick={() => act(api.post(`/api/runs/${r.id}/replay`), `Replay du run #${r.id} lancé`)}>rejouer</button>{' '}
                      <a className="btn small" href={`/api/export?run=${r.id}`} download>export JSON</a>
                    </td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
        ) : <Empty>Chargement…</Empty>}
        <p className="note">Replay = même état initial + mêmes événements + mêmes sorties enregistrées des agents ⇒ même simulation (§45, CA-02). Une empreinte (hash de l’état) est comparée chaque jour simulé.</p>
      </Tile>
      <Tile className="c5" title="Intégrité du journal d’audit (chaîne de hash)">
        <table>
          <thead><tr><th>Couloir</th><th className="r">Actions</th><th>Résultat</th><th></th></tr></thead>
          <tbody>{s.lanes.map((l) => {
            const v = verif[l.slot];
            return (
              <tr key={l.slot}><td>{l.name}</td><td className="r">{v ? fInt(v.total) : '—'}</td>
                <td>{v ? (v.ok ? <Chip cls="good">intègre · tête {v.head ? v.head.slice(0, 10) : '—'}</Chip> : <Chip cls="crit">rompue à {v.at}</Chip>) : <span className="muted">non vérifié</span>}</td>
                <td><button className="btn small" onClick={() => verify(l.slot)}>vérifier</button></td></tr>
            );
          })}</tbody>
        </table>
        <p className="note">Chaque action enregistre le hash de la précédente (§55). Toute modification a posteriori d’une ligne de la table <span className="mono">actions</span> rompt la chaîne.</p>
      </Tile>
      <Tile className="c7" title={`Journal complet — ${s.w.name}`} right={<><Seg small value={src} onChange={setSrc} options={SRC} /><input type="search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && loadLog(true)} placeholder="rechercher + Entrée" style={{ width: 160 }} /></>}>
        <div className="tl scroll tall">
          {log.map((e) => (
            <div key={e.id} className={'tli' + (e.ref && (e.ref.aid || e.ref.inc) ? ' click' : '')} onClick={() => {
              if (e.ref && e.ref.aid) api.get(`/api/actions/${lane}/${e.ref.aid}`).then((r) => open({ kind: 'action', a: r.action })).catch(() => {});
              else if (e.ref && e.ref.inc) { const i = s.incidents.get(e.ref.inc); if (i) open({ kind: 'incident', inc: i }); }
            }}>
              <span className="muted num">{fRel(e.t)}</span><span className={'src ' + e.src}>{e.src}</span><span className={e.sev === 'crit' ? 'crit' : e.sev === 'warn' ? 'warn' : e.sev === 'good' ? 'good' : ''}>{e.text}</span>
            </div>
          ))}
          {more && log.length > 0 && <button className="btn small" style={{ margin: 8 }} onClick={() => loadLog(false)}>plus ancien…</button>}
          {!log.length && <Empty>Journal vide.</Empty>}
        </div>
      </Tile>
    </div>
  );
}
