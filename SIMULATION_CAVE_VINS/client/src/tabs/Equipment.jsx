// Équipements : CVC A/B/C, électricité (réseau, UPS, groupe), humidité, portes et alarmes.
import { useState } from 'react';
import { f, fInt, fDur, fRel } from '../fmt.js';
import { Tile, Chip, Seg, Empty } from '../components/ui.jsx';
import { LineChart } from '../components/Chart.jsx';
import { WINDOWS, windowed, sl } from './common.js';

const Eq = ({ l, v, u, alert }) => <div className={'eqs' + (alert ? ' alert' : '')}><div className="l">{l}</div><div className="v">{v}{u && <small>{u}</small>}</div></div>;
const HBar = ({ l, v, cls, txt }) => <div className="hbar"><span>{l}</span><span className="t"><i className={cls} style={{ width: Math.max(0, Math.min(100, v * 100)) + '%' }} /></span><span>{txt}</span></div>;

export default function Equipment({ ctx }) {
  const { s, meta } = ctx;
  const w = s.w, P = w.power;
  const [win, setWin] = useState(86400);
  const ww = windowed(s.hist, win);
  return (
    <div className="grid">
      {meta.units.map((id) => {
        const u = w.hvac[id], t = u.tele, on = u.state === 'ACTIVE';
        return (
          <Tile key={id} className="c4" title={null}>
            <div className="eqhd">
              <b>{id}</b><Chip>{u.role}</Chip>
              <Chip cls={on ? 'good' : u.state === 'OFF' ? 'crit' : ''}>{on ? 'active' : u.state === 'OFF' ? 'arrêtée' : 'en attente'}</Chip>
              <span className="muted">capacité {f(u.cap * 100, 0)} %</span>
            </div>
            <div className="eqgrid">
              <Eq l="Intensité" v={f(t.current, 1)} u="A" alert={on && t.current < 2.2} />
              <Eq l="Ventilateur" v={fInt(t.rpm)} u="tr/min" alert={on && t.rpm < 500} />
              <Eq l="Puissance" v={f(t.power, 2)} u="kW" />
              <Eq l="Charge" v={fInt(t.charge)} u="%" />
              <Eq l="Aspiration" v={f(t.suction, 2)} u="bar" alert={on && t.suction < 2.3} />
              <Eq l="Refoulement" v={f(t.discharge, 1)} u="bar" />
              <Eq l="T° compresseur" v={f(t.compT, 0)} u="°C" alert={t.compT > 75} />
              <Eq l="COP" v={f(t.cop, 2)} alert={on && t.cop > 0 && t.cop < 2.4} />
              <Eq l="ΔP filtre" v={fInt(t.filterDP)} u="Pa" alert={t.filterDP >= 200} />
              <Eq l="Entrée / sortie" v={`${f(t.inlet, 1)} / ${f(t.outlet, 1)}`} u="°C" />
              <Eq l="Vibrations" v={f(t.vibr, 1)} u="mm/s" alert={t.vibr > 5} />
              <Eq l="Bruit" v={f(t.noise, 0)} u="dB" />
            </div>
            <div style={{ marginTop: 8 }}>
              <HBar l="Santé estimée" v={t.health} cls={t.health > .6 ? 'good' : t.health > .35 ? 'warn' : 'crit'} txt={f(t.health * 100, 0) + ' %'} />
              <HBar l="Colmatage filtre" v={u.filter} cls={u.filter < .45 ? 'good' : u.filter < .62 ? 'warn' : 'crit'} txt={f(u.filter * 100, 0) + ' %'} />
              <HBar l="Vieillissement" v={u.aging} cls={u.aging < .4 ? 'good' : 'warn'} txt={f(u.aging * 100, 0) + ' %'} />
              <HBar l="Proba. de panne" v={t.failP} cls={t.failP < .2 ? 'good' : t.failP < .5 ? 'warn' : 'crit'} txt={f(t.failP * 100, 0) + ' %'} />
            </div>
            <p className="note">{fInt(u.starts)} démarrages · {fInt(u.runH)} h de marche · {fInt(u.kWh)} kWh sur ce run · ventilateur {u.fan} %</p>
            <div className="truth" style={{ marginTop: 6 }}><b>vérité terrain</b>{u.fault ? <span className="crit">panne « {u.fault} »{u.refLoss ? ` · perte de fluide ${f(u.refLoss * 100, 0)} %` : ''}</span> : <span className="good">aucune panne</span>} · rendement {f(u.eff * 100, 0)} %</div>
          </Tile>
        );
      })}
      <Tile className="c4" title="Électricité">
        <div className="eqhd"><Chip cls={P.grid ? 'good' : 'crit'}>{P.grid ? 'réseau présent' : 'coupure secteur'}</Chip>{!P.grid && P.gridBackAt && <span className="muted">retour prévu (vérité) {fRel(P.gridBackAt)}</span>}{P.blackout && <Chip cls="crit">BLACK-OUT</Chip>}</div>
        <div className="eqgrid">
          <Eq l="Tension" v={f(P.voltage, 0)} u="V" alert={P.voltage < 200} />
          <Eq l="Appel" v={f(P.loadKW, 2)} u="kW" />
          <Eq l="Pic" v={f(P.peakKW, 2)} u="kW" />
          <Eq l="UPS" v={f(P.ups.soc * 100, 0)} u="%" alert={P.ups.soc < .4} />
          <Eq l="Autonomie UPS" v={fDur(P.ups.autonomyH * 3600)} />
          <Eq l="Santé UPS" v={f(P.ups.health * 100, 0)} u="%" />
          <Eq l="Cycles UPS" v={P.ups.cycles} />
          <Eq l="Groupe" v={{ OFF: 'arrêt', CRANKING: 'démarrage', RUNNING: 'en charge', FAILED: 'ÉCHEC' }[P.genset.state]} alert={P.genset.state === 'FAILED'} />
          <Eq l="Carburant" v={f(P.genset.fuel, 0)} u="L" />
          <Eq l="T° groupe" v={f(P.genset.temp, 0)} u="°C" />
        </div>
        {P.genset.fault && <div className="truth" style={{ marginTop: 8 }}><b>vérité terrain</b><span className="crit">le groupe ne démarrera pas (défaut « {P.genset.fault} »)</span></div>}
      </Tile>
      <Tile className="c4" title="Humidification & portes">
        <div className="eqgrid">
          <Eq l="Humidificateur" v={w.humid.on ? 'marche' : 'arrêt'} alert={w.humid.on && w.humid.flow === 0} />
          <Eq l="Débit" v={f(w.humid.flow, 2)} u="L/h" />
          <Eq l="Réservoir" v={f(w.humid.tank, 0)} u="%" />
          <Eq l="Commande" v={w.humid.forced == null ? 'auto' : w.humid.forced ? 'forcé' : 'forcé arrêt'} />
          <Eq l="Déshumidificateur" v={w.dehum.on ? 'marche' : 'arrêt'} />
          <Eq l="Eau consommée" v={f(w.humid.waterL, 1)} u="L" />
          <Eq l="Porte principale" v={w.doors.main.open ? 'OUVERTE' : 'fermée'} alert={w.doors.main.open && w.doors.main.stuck} />
          <Eq l="Verrou" v={w.doors.main.locked ? 'verrouillée' : 'DÉVERROUILLÉE'} alert={!w.doors.main.locked} />
          <Eq l="Baie technique" v={w.doors.tech.locked ? 'verrouillée' : 'DÉVERROUILLÉE'} alert={!w.doors.tech.locked} />
          <Eq l="Alarmes" v={w.alarmsDisabled ? 'COUPÉES' : w.alarmsArmed ? 'armées' : 'jour'} alert={w.alarmsDisabled} />
        </div>
        {w.humid.fault && <div className="truth" style={{ marginTop: 8 }}><b>vérité terrain</b><span className="crit">humidificateur en panne ({w.humid.fault})</span></div>}
      </Tile>
      <Tile className="c4" title="Interventions en cours">
        <div className="flabel">Tickets de maintenance</div>
        {w.tickets.length ? <table><tbody>{w.tickets.map((t) => <tr key={t.id}><td className="mono">{t.id}</td><td>{t.target}<div className="muted">{t.reason}</div></td><td className="nowrap muted">prévu {fRel(t.due)}</td></tr>)}</tbody></table> : <Empty>aucun</Empty>}
        <div className="flabel" style={{ marginTop: 8 }}>Intervenants attendus</div>
        {w.humans.length ? <table><tbody>{w.humans.map((h, i) => <tr key={i}><td>{h.reason}</td><td className="nowrap muted">arrivée {fRel(h.at)}</td></tr>)}</tbody></table> : <Empty>aucun</Empty>}
      </Tile>
      <Tile className="c6" title="Puissance appelée" right={<Seg small value={win} onChange={setWin} options={WINDOWS} />}>
        {ww ? <LineChart x={ww.t} h={180} unit="kW" series={[{ label: 'Puissance', v: sl(s.hist.kw, ww), color: 'var(--l1)', area: true }]} /> : <Empty>—</Empty>}
      </Tile>
      <Tile className="c6" title="Charge UPS & température extérieure">
        {ww ? <div className="split">
          <LineChart x={ww.t} h={180} unit="%" dec={0} ymin={0} ymax={100} series={[{ label: 'UPS', v: sl(s.hist.soc, ww), color: 'var(--l3)' }]} />
          <LineChart x={ww.t} h={180} unit="°C" dec={1} series={[{ label: 'Extérieur', v: sl(s.hist.out, ww), color: 'var(--l2)' }]} />
        </div> : <Empty>—</Empty>}
      </Tile>
    </div>
  );
}
