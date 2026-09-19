// Cave & zones : plan 2D avec carte de chaleur, détail de zone, courbes et inventaire.
import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { f, fEur, fInt, fDur, HEALTH_CLS } from '../fmt.js';
import { Tile, Chip, Seg, KV, Empty } from '../components/ui.jsx';
import { LineChart, Legend } from '../components/Chart.jsx';
import { WINDOWS, windowed, sl } from './common.js';

const HEAT = [['dev', 'Écart consigne'], ['T', 'Température'], ['RH', 'Humidité'], ['lux', 'Lumière'], ['vib', 'Vibration'], ['risk', 'Risque'], ['value', 'Valeur']];
const lerp = (a, b, t) => a.map((x, i) => Math.round(x + (b[i] - x) * t));
const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const rgb = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
function scale(kind, v, dark) {
  const blue = hex(dark ? '#3987e5' : '#2a78d6'), red = hex(dark ? '#e66767' : '#e34948'), mid = hex(dark ? '#383835' : '#f0efec');
  const seq0 = hex(dark ? '#1f2a3a' : '#e6effb'), seq1 = hex(dark ? '#5598e7' : '#1c5cab');
  if (v == null || isNaN(v)) return dark ? '#241f21' : '#f2eeee';
  if (kind === 'div') { const t = Math.max(-1, Math.min(1, v)); return rgb(t < 0 ? lerp(mid, blue, -t) : lerp(mid, red, t)); }
  const t = Math.max(0, Math.min(1, v));
  return rgb(lerp(seq0, seq1, t));
}

export default function Cellar({ ctx }) {
  const { s, meta, lane } = ctx;
  const w = s.w;
  const [zone, setZone] = useState('pre');
  const [heat, setHeat] = useState('dev');
  const [win, setWin] = useState(86400);
  const [inv, setInv] = useState([]);
  useEffect(() => { api.get('/api/inventory?lane=' + lane).then(setInv).catch(() => {}); }, [lane, s.runId]);
  const dark = document.documentElement.dataset.theme !== 'light';
  const Z = meta.zones.find((z) => z.id === zone), q = w.zones[zone];
  const maxVal = Math.max(...meta.zones.map((z) => w.zones[z.id].value || 0), 1);
  const heatOf = (z) => {
    const v = w.zones[z.id];
    switch (heat) {
      case 'dev': return ['div', (v.T - v.sp) / (2 * z.tolT), `${v.T - v.sp >= 0 ? '+' : ''}${f(v.T - v.sp, 2)} °C`];
      case 'T': return ['seq', (v.T - 8) / 14, `${f(v.T, 2)} °C`];
      case 'RH': return ['div', (v.RH - z.rh) / (2 * z.tolRH), `${f(v.RH, 1)} %`];
      case 'lux': return ['seq', v.lux / 320, `${fInt(v.lux)} lx`];
      case 'vib': return ['seq', v.vib / .2, `${f(v.vib, 3)} g`];
      case 'risk': { const r = Math.max(Math.abs(v.T - v.sp) / z.tolT, Math.abs(v.RH - z.rh) / z.tolRH, v.dew != null && v.dew > v.T - 1.2 ? 1.5 : 0, v.water > .3 ? 2 : 0, v.smoke > .3 ? 3 : 0); return ['seq', r / 3, f(r, 2)]; }
      case 'value': return ['seq', (v.value || 0) / maxVal, fEur(v.value)];
      default: return ['seq', 0, ''];
    }
  };
  const sensorsByZone = {};
  meta.sensors.forEach((m, i) => { (sensorsByZone[m.zone] = sensorsByZone[m.zone] || []).push({ m, cur: w.sensors[i] }); });
  const ww = windowed(s.hist, win);
  const zi = inv.filter((b) => b.zone === zone);
  return (
    <div className="grid">
      <Tile className="c8 plan" title="Plan de la cave" right={<Seg small value={heat} onChange={setHeat} options={HEAT} label="Carte de chaleur" />}>
        <svg viewBox="0 0 1000 540" role="img" aria-label="Plan de la cave, zones colorées selon la carte de chaleur choisie">
          {meta.zones.map((z) => {
            const [kind, v, lab] = heatOf(z), zz = w.zones[z.id];
            const sens = sensorsByZone[z.id] || [];
            return (
              <g key={z.id} className={'z' + (zone === z.id ? ' sel' : '')} onClick={() => setZone(z.id)}>
                <rect x={z.x} y={z.y} width={z.w} height={z.h} rx="6" fill={scale(kind, v, dark)} />
                <text x={z.x + 12} y={z.y + 26} fontSize="19" fontWeight="600">{z.short}</text>
                <text x={z.x + 12} y={z.y + 56} fontSize="30" fontWeight="600">{lab}</text>
                <text x={z.x + 12} y={z.y + 78} fontSize="13" opacity=".75">{f(zz.T, 2)} °C · {f(zz.RH, 0)} %{z.w >= 240 ? ` · consigne ${f(zz.sp, 1)}` : ''}</text>
                {zz.lightOn && <text x={z.x + z.w - 30} y={z.y + 28} fontSize="20" aria-label="éclairage allumé">💡</text>}
                {zz.water > .3 && <text x={z.x + z.w - 58} y={z.y + 28} fontSize="20" aria-label="eau au sol">💧</text>}
                {zz.smoke > .3 && <text x={z.x + z.w - 86} y={z.y + 28} fontSize="20" aria-label="fumée">🔥</text>}
                {zz.presence && <text x={z.x + z.w - 114} y={z.y + 28} fontSize="18" aria-label="présence">👤</text>}
                {zz.isolated && <text x={z.x + 12} y={z.y + z.h - 40} fontSize="13" fill="var(--crit-ink)">ZONE ISOLÉE</text>}
                {sens.map(({ m, cur }, j) => {
                  const bad = cur.h !== 'NORMAL';
                  return (
                    <g key={m.id} transform={`translate(${z.x + 14 + (j % 9) * 20},${z.y + z.h - 16 - Math.floor(j / 9) * 18})`}>
                      <circle r="6.5" fill={bad ? 'var(--crit)' : cur.q ? 'transparent' : 'var(--ink2)'} stroke={cur.q ? 'var(--warn)' : 'var(--surface)'} strokeWidth="2"><title>{`${m.id} · ${m.label} : ${cur.v == null ? '—' : f(cur.v, 2) + ' ' + m.unit} (réel ${cur.tr == null ? '—' : f(cur.tr, 2)}) · ${cur.h}${cur.q ? ' · quarantaine' : ''}`}</title></circle>
                    </g>
                  );
                })}
              </g>
            );
          })}
          {meta.units.map((id, i) => {
            const u = w.hvac[id], col = u.fault ? 'var(--crit)' : u.state === 'ACTIVE' ? 'var(--good)' : u.state === 'OFF' ? 'var(--crit)' : 'var(--muted)';
            return <g key={id} transform={`translate(${500 + i * 56},102)`}><rect width="48" height="30" rx="4" fill="var(--surface)" stroke={col} strokeWidth="2.5" /><text x="24" y="20" fontSize="13" textAnchor="middle">{id.slice(-1)}</text><title>{`${id} · ${u.state}${u.fault ? ' · panne ' + u.fault + ' (vérité terrain)' : ''}`}</title></g>;
          })}
          <g transform="translate(6,70)"><rect width="10" height="60" rx="2" fill={w.doors.main.open ? 'var(--crit)' : 'var(--good)'} /><title>{`Porte principale ${w.doors.main.open ? 'ouverte' : 'fermée'}${w.doors.main.locked ? ', verrouillée' : ''}`}</title></g>
          <text x="20" y="518" fontSize="13" fill="var(--muted)">Extérieur {f(w.wx.Tout, 1)} °C · {f(w.wx.RHout, 0)} % · sol {f(w.wx.ground, 1)} °C{w.wx.heat ? ' · CANICULE' : ''} · porte du sas {w.doors.main.open ? 'OUVERTE' : 'fermée'} · alarmes {w.alarmsDisabled ? 'COUPÉES' : w.alarmsArmed ? 'armées' : 'désarmées (jour)'}</text>
        </svg>
        <div className="row" style={{ justifyContent: 'space-between', marginTop: 6 }}>
          <div className="heatlegend">{HEAT.find((h) => h[0] === heat)[1]} : <span className="bar" style={{ background: heatOf(meta.zones[0])[0] === 'div' ? `linear-gradient(90deg, ${scale('div', -1, dark)}, ${scale('div', 0, dark)}, ${scale('div', 1, dark)})` : `linear-gradient(90deg, ${scale('seq', 0, dark)}, ${scale('seq', 1, dark)})` }} />{heatOf(meta.zones[0])[0] === 'div' ? 'trop froid · neutre · trop chaud' : 'faible → élevé'}</div>
          <span className="muted" style={{ fontSize: 12 }}>● capteur sain · <span className="crit">●</span> défaillant (vérité) · ○ en quarantaine · rectangles du local technique = unités CVC</span>
        </div>
      </Tile>
      <Tile className="c4" title={Z.name} right={Z.profile ? <Chip cls="acc">{Z.profile}</Chip> : null}>
        <KV rows={[
          ['Consigne', `${f(q.sp, 2)} °C ${q.spOff ? `(nominale ${f(Z.sp, 1)}, décalage ${f(q.spOff, 2)})` : ''}`],
          ['Tolérance', `± ${f(Z.tolT, 1)} °C · HR ${Z.rh} ± ${Z.tolRH} %`],
          ['Air (vérité terrain)', <b className={Math.abs(q.T - q.sp) > Z.tolT ? 'crit' : ''}>{f(q.T, 3)} °C</b>],
          ['Température régulée (médiane capteurs)', `${f(q.ctlT, 3)} °C`],
          ['Bouteille témoin', `${f(q.Tb, 3)} °C`],
          ['Humidité relative', `${f(q.RH, 2)} %`],
          ['Point de rosée', q.dew != null ? `${f(q.dew, 2)} °C · marge ${f(q.T - q.dew, 1)} °C` : '—'],
          ['Taux de marche froid', `${f(q.duty * 100, 0)} %`],
          ['Lumière', `${fInt(q.lux)} lx · dose ${fInt(q.lightDose)} lx·h`],
          ['Vibration', `${f(q.vib, 3)} g · exposition ${f(q.vibExp, 1)} h`],
          ['Eau au sol', q.water > .01 ? `${f(q.water, 1)} L${q.valve ? '' : ' · vanne fermée'}` : 'sèche'],
          ['CO₂', `${fInt(q.co2)} ppm`],
          ['Valeur stockée', fEur(q.value)],
        ]} />
        <div className="flabel" style={{ marginTop: 10 }}>Capteurs de la zone</div>
        <table><tbody>{(sensorsByZone[zone] || []).map(({ m, cur }) => (
          <tr key={m.id} className="click" onClick={() => ctx.open({ kind: 'sensor', id: m.id })}>
            <td className="mono">{m.id}</td><td className="r num">{cur.v == null ? '—' : f(cur.v, 2)} {m.unit}</td><td><Chip cls={HEALTH_CLS[cur.h]}>{cur.h}</Chip></td>
          </tr>))}</tbody></table>
      </Tile>
      <Tile className="c8" title={`${Z.short} — températures`} right={<Seg small value={win} onChange={setWin} options={WINDOWS} />}>
        <Legend items={[{ label: 'Air (vérité terrain)', color: 'var(--l1)' }, { label: 'Régulation (médiane des sondes)', color: 'var(--l2)', dash: true }, { label: 'Bouteille témoin', color: 'var(--l3)' }]} />
        {ww ? <LineChart x={ww.t} h={220} unit="°C" band={[q.sp - Z.tolT, q.sp + Z.tolT]} bandWarn={[[q.sp - 2 * Z.tolT - .4, q.sp - Z.tolT], [q.sp + Z.tolT, q.sp + 2 * Z.tolT + .4]]} refLines={[{ v: q.sp }]}
          series={[{ label: 'Air', v: sl(s.hist.z[zone].T, ww), color: 'var(--l1)' }, { label: 'Régulation', v: sl(s.hist.z[zone].Tm, ww), color: 'var(--l2)', dash: [5, 4] }, { label: 'Bouteille', v: sl(s.hist.z[zone].Tb, ww), color: 'var(--l3)' }]} /> : <Empty>Pas encore d’historique.</Empty>}
        <p className="note">Bande verte : enveloppe optimale · bandes jaunes : enveloppe sûre (jusqu’à 2 × la tolérance). La régulation suit la médiane des sondes non écartées : une sonde faussée la déplace, pas l’air réel.</p>
      </Tile>
      <Tile className="c4" title={`${Z.short} — humidité`}>
        {ww ? <LineChart x={ww.t} h={220} unit="%" dec={1} band={[Z.rh - Z.tolRH, Z.rh + Z.tolRH]} series={[{ label: 'HR', v: sl(s.hist.z[zone].RH, ww), color: 'var(--l1)' }]} /> : <Empty>—</Empty>}
      </Tile>
      <Tile className="c12" title={`Inventaire — ${Z.name}`} right={<span>{zi.length} références · {fEur(zi.reduce((a, b) => a + b.value, 0))}</span>}>
        {zi.length ? (
          <div className="tw"><table>
            <thead><tr><th>Réf.</th><th>Producteur</th><th>Appellation</th><th className="r">Millésime</th><th className="r">Qté</th><th>Rack</th><th className="r">Prix unitaire</th><th className="r">Valeur</th></tr></thead>
            <tbody>{zi.map((b) => <tr key={b.id}><td className="mono">{b.id}</td><td>{b.prod}</td><td>{b.app}</td><td className="r">{b.vint}</td><td className="r">{b.qty}</td><td className="mono">{b.rack}</td><td className="r">{fEur(b.unit)}</td><td className="r">{fEur(b.value)}</td></tr>)}</tbody>
          </table></div>
        ) : <Empty>Zone sans stockage de bouteilles.</Empty>}
      </Tile>
    </div>
  );
}
