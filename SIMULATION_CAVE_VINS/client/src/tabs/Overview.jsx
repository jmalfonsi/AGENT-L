// Synthèse : les trois niveaux de lecture (propriétaire, exploitant, ingénieur) en un écran.
import { f, fRel, fDur, fEur, fInt, fArgs, STATE_FR, HANDLING_CLS } from '../fmt.js';
import { Tile, Chip, Dot, Score, Empty, Decision, StateChip } from '../components/ui.jsx';
import { Spark } from '../components/Chart.jsx';
import { windowed, sl, slope } from './common.js';

export default function Overview({ ctx }) {
  const { s, meta, open } = ctx;
  const w = s.w, k = w.k, a = w.agent;
  const zones = meta.zones.filter((z) => z.storage);
  const worstDev = Math.max(...zones.map((z) => Math.abs(w.zones[z.id].T - z.sp) / z.tolT));
  const worstRH = Math.max(...zones.map((z) => Math.abs(w.zones[z.id].RH - z.rh) / z.tolRH));
  const maxSlope = Math.max(...zones.map((z) => Math.abs(slope(s.hist, z.id))));
  const openInc = [...s.incidents.values()].filter((i) => i.open && !i.meta);
  const risk = k.status === 'FAILED' || worstDev > 2 || w.opState === 'CRITICAL' || w.opState === 'EMERGENCY' ? ['crit', 'ÉLEVÉ'] : openInc.length || worstDev > 1 || (a.prediction && a.prediction.etaH < 6) ? ['warn', 'MODÉRÉ'] : ['good', 'FAIBLE'];
  const owner = k.status === 'FAILED' ? ['crit', 'Sûreté compromise', k.failReasons[0]]
    : w.opState === 'EMERGENCY' ? ['crit', 'Urgence en cours', 'détection de fumée : escalade humaine']
      : worstDev > 2 ? ['crit', 'Vins en danger', 'au moins une zone loin de sa consigne']
        : w.valueExposed > 0 ? ['warn', 'Vins exposés', `${fEur(w.valueExposed)} hors de l’enveloppe optimale`]
          : ['good', 'Vos vins sont en sécurité', 'toutes les zones dans leur enveloppe optimale'];
  const power = !w.power.grid ? (w.power.genset.state === 'RUNNING' ? ['warn', 'GROUPE'] : w.power.blackout ? ['crit', 'COUPURE'] : ['crit', 'UPS']) : ['good', 'RÉSEAU'];
  const ai = a.kind === 'none' ? ['serious', 'SANS AGENT'] : a.crashed ? ['crit', 'CRASH'] : a.stalled ? ['serious', 'MODE SÛR'] : !a.online ? ['serious', a.kind === 'external' ? 'NON CONNECTÉ' : 'HORS LIGNE'] : ['good', 'AUTONOME'];
  const hvacCls = (u) => (u.state === 'ACTIVE' ? 'good' : u.state === 'OFF' ? 'crit' : '');
  const win = windowed(s.hist, 86400);
  const recent = [...s.actions.values()].slice(-9).reverse();
  return (
    <div className="grid">
      <Tile className="c3" title="Tout va bien ?">
        <div className="stack">
          <b className={'big ' + owner[0]}>{owner[1]}</b>
          <span className="muted">{owner[2]}</span>
          <div className="row" style={{ alignItems: 'baseline' }}><span className="hero">{f(k.scores.preservation, 1)}</span><span className="muted">/ 100 préservation</span></div>
          <dl className="kv">
            <dt>Valeur de la cave</dt><dd>{fEur(w.inventoryValue)}</dd>
            <dt>Valeur exposée maintenant</dt><dd className={w.valueExposed ? 'warn' : 'good'}>{fEur(w.valueExposed)}</dd>
            <dt>Exposition max. observée</dt><dd>{fEur(k.env.valueExposedMax)}</dd>
          </dl>
        </div>
      </Tile>
      <Tile className="c3" title="État général" right={<StateChip s={w.opState} />}>
        <dl className="synth">
          <dt>Santé cave</dt><dd>{f(k.scores.preservation, 1)} %</dd>
          <dt>Température</dt><dd><Dot cls={worstDev > 1 ? 'crit' : maxSlope > .3 ? 'warn' : 'good'} />{worstDev > 1 ? 'HORS TOLÉRANCE' : maxSlope > .3 ? 'EN DÉRIVE' : 'STABLE'}</dd>
          <dt>Humidité</dt><dd><Dot cls={worstRH > 1 ? 'warn' : 'good'} />{worstRH > 1 ? 'HORS CIBLE' : 'STABLE'}</dd>
          {meta.units.map((id) => [<dt key={'d' + id}>{id}</dt>, <dd key={'v' + id}><Dot cls={hvacCls(w.hvac[id])} />{w.hvac[id].state === 'ACTIVE' ? 'ACTIVE' : w.hvac[id].state === 'STANDBY' ? 'EN ATTENTE' : 'ARRÊTÉE'}</dd>])}
          <dt>Alimentation</dt><dd><Dot cls={power[0]} />{power[1]}</dd>
          <dt>UPS</dt><dd>{f(w.power.ups.soc * 100, 0)} %</dd>
          <dt>Sécurité</dt><dd><Dot cls={w.alarmsDisabled ? 'crit' : 'good'} />{w.alarmsDisabled ? 'ALARMES COUPÉES' : w.alarmsArmed ? 'ARMÉE' : 'DÉSARMÉE (jour)'}</dd>
          <dt>IA</dt><dd><Dot cls={ai[0]} />{ai[1]}</dd>
          <dt>Risque</dt><dd><Dot cls={risk[0]} />{risk[1]}</dd>
        </dl>
      </Tile>
      <Tile className="c6" title="Scores (non compensables)" right={<button className="btn small" onClick={() => open({ kind: 'verdict' })}>détail</button>}>
        <div className={'verdict ' + (k.status === 'PASS' ? 'ok' : 'ko')}><b>{k.status}</b><span>{k.status === 'PASS' ? `score global ${f(k.global, 1)} / 100 · aucune contrainte absolue violée` : k.failReasons.join(' · ')}</span></div>
        <div className="scores">
          <Score label="Préservation" v={k.scores.preservation} /><Score label="Sûreté" v={k.scores.safety} /><Score label="Résilience" v={k.scores.resilience} />
          <Score label="Autonomie" v={k.scores.autonomy} /><Score label="Efficacité" v={k.scores.efficiency} /><Score label="Diagnostic" v={k.scores.diagnostic} />
        </div>
        <p className="note">{f(k.hours, 1)} h simulées · {k.incidents.total} incidents réels · {k.actions.total} actions demandées dont {k.actions.executed} exécutées.</p>
      </Tile>
      <Tile className="c12" title="Zones de stockage — température réelle, 24 h" right={<span>bande verte = tolérance du profil de conservation</span>}>
        <div className="multiples">
          {zones.map((z) => {
            const q = w.zones[z.id], dev = Math.abs(q.T - z.sp) / z.tolT, sp = slope(s.hist, z.id);
            return (
              <div key={z.id} className="mini" onClick={() => ctx.setTab('cave')} title="Ouvrir la vue Cave">
                <div className="hd"><b>{z.short}</b><Chip cls={dev > 2 ? 'crit' : dev > 1 ? 'warn' : 'good'}>{dev > 1 ? 'hors tolérance' : 'optimal'}</Chip></div>
                <div className="hd"><span className="val">{f(q.T, 2)}<small className="muted"> °C</small></span><span className="muted num">{sp >= 0 ? '+' : ''}{f(sp, 2)} °C/h</span></div>
                {win && <Spark x={win.t} v={sl(s.hist.z[z.id].T, win)} band={[q.sp - z.tolT, q.sp + z.tolT]} color={dev > 1 ? 'var(--crit)' : 'var(--l1)'} />}
                <div className="sub"><span>consigne {f(q.sp, 1)} ± {f(z.tolT, 1)}</span><span>HR {f(q.RH, 1)} %</span><span>{fEur(q.value)}</span></div>
              </div>
            );
          })}
        </div>
      </Tile>
      <Tile className="c8" title={`Boucle de l’agent — ${w.name}`} right={<span>{a.reasoner} · cycle {fInt(a.cycle)}</span>}>
        <div className="loop">
          {meta.phases.map((p) => <div key={p} className={'phase' + (a.hot === p ? ' hot' : '')}><b>{p}</b><span>{(a.phases && a.phases[p]) || '—'}</span></div>)}
        </div>
        {a.anoms.length > 0 && <div className="row" style={{ marginTop: 8 }}><span className="flabel">Anomalies suivies</span>{a.anoms.map((an) => <Chip key={an.key} cls="warn" title={an.obs}>{an.target}{an.cause ? ' · ' + an.cause : ''}</Chip>)}</div>}
      </Tile>
      <Tile className="c4" title="Dernières actions" right={<button className="btn small" onClick={() => ctx.setTab('agent')}>tout voir</button>}>
        {recent.length ? (
          <table><tbody>{recent.map((x) => (
            <tr key={x.id} className={'click' + (x.violation ? ' bad' : '')} onClick={() => open({ kind: 'action', a: x })}>
              <td className="nowrap muted num">{fRel(x.t)}</td><td className="mono ellipsis" style={{ maxWidth: 200 }} title={fArgs(x.args)}>{x.tool}</td><td><Decision a={x} /></td>
            </tr>))}</tbody></table>
        ) : <Empty>Aucune action : ne rien faire est souvent la bonne décision.</Empty>}
      </Tile>
      <Tile className="c4" title="Incidents ouverts (vérité terrain)" right={<button className="btn small" onClick={() => ctx.setTab('incidents')}>chronologie</button>}>
        {openInc.length ? (
          <table><tbody>{openInc.slice(-8).map((i) => (
            <tr key={i.id} className="click" onClick={() => open({ kind: 'incident', inc: i })}>
              <td className="mono">{i.id}</td><td>{i.label}<div className="muted">{i.target} · depuis {fDur(w.t - i.start)}</div></td>
              <td className="nowrap">{i.detectedAt != null ? <Chip cls="good">détecté +{fDur(i.detectedAt - i.start)}</Chip> : <Chip cls="crit">non détecté</Chip>}</td>
            </tr>))}</tbody></table>
        ) : <Empty>Aucun incident réel en cours.</Empty>}
        <p className="note">Ce panneau montre la vérité du simulateur. L’agent, lui, ne voit que ses capteurs.</p>
      </Tile>
      <Tile className="c4" title="Risques anticipés">
        <dl className="kv">
          <dt>Prédiction de l’agent</dt><dd>{a.prediction ? `${meta.zones.find((z) => z.id === a.prediction.zone).short} dans ${fDur(a.prediction.etaH * 3600)}` : 'aucune sortie prévue'}</dd>
          {meta.units.map((id) => [<dt key={'f' + id}>Filtre {id} (ΔP)</dt>, <dd key={'v' + id} className={w.hvac[id].tele.filterDP >= 200 ? 'warn' : ''}>{fInt(w.hvac[id].tele.filterDP)} Pa</dd>])}
          <dt>Autonomie UPS</dt><dd>{fDur(w.power.ups.autonomyH * 3600)}</dd>
          <dt>Carburant groupe</dt><dd>{f(w.power.genset.fuel, 0)} L</dd>
          <dt>Canicule</dt><dd className={w.wx.heat ? 'warn' : ''}>{w.wx.heat ? `+${f(w.wx.heat.amp, 0)} °C sur ${f(w.wx.heat.days, 0)} j` : 'non'}</dd>
          <dt>Tickets ouverts</dt><dd>{w.tickets.length}</dd>
          <dt>Intervenants attendus</dt><dd>{w.humans.length}</dd>
        </dl>
      </Tile>
      <Tile className="c4" title="Environnement & énergie">
        <dl className="kv">
          <dt>Extérieur</dt><dd>{f(w.wx.Tout, 1)} °C · {f(w.wx.RHout, 0)} %</dd>
          <dt>Sol</dt><dd>{f(w.wx.ground, 1)} °C</dd>
          <dt>Puissance appelée</dt><dd>{f(w.power.loadKW, 2)} kW</dd>
          <dt>Énergie cumulée</dt><dd>{fInt(k.energy.kWh)} kWh · {fInt(k.energy.costE)} €</dd>
          <dt>Surcoût vs minimum sûr</dt><dd className={k.energy.overhead > 20 ? 'warn' : ''}>{f(k.energy.overhead, 1)} %</dd>
          <dt>Démarrages compresseur</dt><dd>{k.energy.compStarts}</dd>
          <dt>Coût LLM</dt><dd>{f(k.llm.cost, 3)} € · {fInt(k.llm.calls)} appels</dd>
        </dl>
      </Tile>
      <Tile className="c4" title="Temps passé par état">
        <StateTime st={w.stateTime} />
      </Tile>
    </div>
  );
}

export function StateTime({ st }) {
  const tot = Object.values(st).reduce((a, b) => a + b, 0) || 1;
  const cols = { NORMAL: 'var(--good)', WATCH: 'var(--info)', DEGRADED: 'var(--warn)', CRITICAL: 'var(--crit)', EMERGENCY: '#8b0000', SAFE_MODE: 'var(--serious)' };
  return (
    <div className="stack">
      <div className="stackbar">{Object.entries(st).filter(([, v]) => v > 0).map(([k, v]) => <i key={k} title={`${STATE_FR[k]} ${f((v / tot) * 100, 1)} %`} style={{ width: (v / tot) * 100 + '%', background: cols[k] }} />)}</div>
      <dl className="kv">{Object.entries(st).map(([k, v]) => [<dt key={'d' + k}><Dot cls={{ NORMAL: 'good', WATCH: 'info', DEGRADED: 'warn', CRITICAL: 'crit', EMERGENCY: 'crit', SAFE_MODE: 'serious' }[k]} /> {STATE_FR[k]}</dt>, <dd key={'v' + k}>{f((v / tot) * 100, 2)} % · {fDur(v)}</dd>])}</dl>
    </div>
  );
}
