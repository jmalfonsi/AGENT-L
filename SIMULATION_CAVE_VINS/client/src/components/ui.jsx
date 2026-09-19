// Briques d'interface partagées.
import { f, fRel, fArgs, DEC_CLS, DEC_FR, UTIL_CLS, STATE_CLS, STATE_FR } from '../fmt.js';

export const Tile = ({ title, right, className = '', children, style }) => (
  <section className={'tile ' + className} style={style}>
    {title != null && <h3>{title}{right != null && <span className="sp">{right}</span>}</h3>}
    {children}
  </section>
);
export const Chip = ({ cls = '', children, title }) => <span className={'chip ' + cls} title={title}>{children}</span>;
export const Prov = ({ p }) => (p ? <span className={'prov ' + p}>{p}</span> : null);
export const Dot = ({ cls }) => <span className={'dot ' + (cls || '')} />;
export const Meter = ({ v, max = 100, cls }) => <div className="meter"><i className={cls} style={{ width: Math.max(0, Math.min(100, (v / max) * 100)) + '%' }} /></div>;
export const Empty = ({ children }) => <div className="empty">{children}</div>;

export function Seg({ value, options, onChange, small, label }) {
  return (
    <div className={'seg' + (small ? ' small' : '')} role="group" aria-label={label}>
      {options.map(([v, l, title]) => <button key={String(v)} aria-pressed={value === v} title={title} onClick={() => onChange(v)}>{l}</button>)}
    </div>
  );
}
export function Filters({ options, value, onToggle, all }) {
  return (
    <div className="filters">
      {all && <button aria-pressed={value.size === 0} onClick={() => onToggle(null)}>{all}</button>}
      {options.map(([v, l]) => <button key={v} aria-pressed={value.has(v)} onClick={() => onToggle(v)}>{l}</button>)}
    </div>
  );
}
export const StateChip = ({ s }) => <Chip cls={STATE_CLS[s]}>{STATE_FR[s] || s}</Chip>;
export const Decision = ({ a }) => {
  if (!a) return null;
  if (a.rule === 'AUDIT') return <Chip cls={a.violation ? 'crit' : 'warn'} title={`Référence : ${a.refDecision} (${a.refRule})`}>{a.violation ? 'exécutée · violation' : 'exécutée (audit)'}</Chip>;
  return <Chip cls={a.decision === 'DENY' && a.rule && ['SCHEMA', 'OUTIL_INCONNU', 'ANTI_COURT_CYCLE', 'CONTREFACTUEL'].includes(a.rule) ? 'serious' : DEC_CLS[a.decision]}>{a.decision === 'DENY' && ['SCHEMA', 'OUTIL_INCONNU', 'ANTI_COURT_CYCLE'].includes(a.rule) ? 'verrou' : DEC_FR[a.decision] || a.decision}</Chip>;
};
export const Utility = ({ u }) => (u ? <Chip cls={UTIL_CLS[u]}>{u}</Chip> : null);
export const Risk = ({ r }) => (r == null ? <Chip cls="crit">R?</Chip> : <Chip cls={r >= 4 ? 'crit' : r === 3 ? 'serious' : r === 2 ? 'warn' : ''}>R{r}</Chip>);

export function ActionRow({ a, onOpen, compact }) {
  return (
    <tr className={'click' + (a.violation ? ' bad' : '')} onClick={() => onOpen(a)}>
      <td className="nowrap num">{fRel(a.t)}</td>
      <td className="mono"><b>{a.tool}</b>{!compact && <span className="muted">({fArgs(a.args)})</span>}</td>
      {!compact && <td><Risk r={a.risk} /></td>}
      <td><Decision a={a} /></td>
      {!compact && <td className="mono muted nowrap">{a.rule}</td>}
      {!compact && <td><Utility u={a.utility} /></td>}
      {!compact && <td className="nowrap">{a.verification ? (a.verification.ok ? <span className="good">✓ vérifiée</span> : <span className="crit">✗ inefficace</span>) : <span className="muted">—</span>}</td>}
      {!compact && <td className="muted ellipsis" style={{ maxWidth: 360 }}>{a.result}</td>}
    </tr>
  );
}
export const ActionHead = ({ compact }) => (
  <thead><tr><th>Heure</th><th>Action</th>{!compact && <th>Risque</th>}<th>Décision</th>{!compact && <th>Règle</th>}{!compact && <th>Utilité</th>}{!compact && <th>Vérif.</th>}{!compact && <th>Résultat</th>}</tr></thead>
);

export function KV({ rows, className = '' }) {
  return <dl className={'kv ' + className}>{rows.filter(Boolean).map(([k, v], i) => [<dt key={'k' + i}>{k}</dt>, <dd key={'v' + i}>{v}</dd>])}</dl>;
}
export function Score({ label, v, fmt = (x) => f(x, 1) }) {
  const cls = v == null ? '' : v >= 90 ? 'good' : v >= 70 ? 'warn' : 'crit';
  return <div className="score"><span className="l">{label}</span><span className="v">{v == null ? '—' : fmt(v)}</span><Meter v={v || 0} cls={cls} /></div>;
}
export function Funnel({ steps }) {
  const max = Math.max(1, ...steps.map((s) => s.n || 0));
  return (
    <div className="funnel">
      {steps.map((s, i) => (
        <div key={i} className={'fstep ' + (s.cls || '')}>
          <div className="b"><i style={{ width: ((s.n || 0) / max) * 100 + '%' }} /><span>{s.label}</span></div>
          <div className="n">{s.n ?? '—'}</div>
        </div>
      ))}
    </div>
  );
}
export function StackBar({ parts }) {
  const tot = parts.reduce((a, p) => a + (p.n || 0), 0);
  return (
    <div>
      <div className="stackbar">{tot ? parts.filter((p) => p.n).map((p, i) => <i key={i} title={`${p.label} : ${p.n}`} style={{ width: (p.n / tot) * 100 + '%', background: p.color }} />) : null}</div>
      <div className="legend" style={{ marginTop: 6 }}>{parts.map((p, i) => <span key={i}><i style={{ background: p.color, height: 8, width: 8 }} />{p.label} <b className="num">{p.n}</b></span>)}</div>
    </div>
  );
}
