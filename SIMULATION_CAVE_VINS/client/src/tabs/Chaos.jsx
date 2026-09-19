// Chaos & runs : injection manuelle de pannes et d'attaques (communes à toutes les caves), politique en direct, nouveau run.
import { useState } from 'react';
import { api } from '../api.js';
import { fRel } from '../fmt.js';
import { Tile, Chip, Empty } from '../components/ui.jsx';
import RunConfig from '../components/RunConfig.jsx';

export default function Chaos({ ctx }) {
  const { s, meta, act } = ctx;
  const w = s.w;
  const [unit, setUnit] = useState('HVAC-A');
  const [sensor, setSensor] = useState('bdx-T2');
  const [zone, setZone] = useState('bdx');
  const tempSensors = meta.sensors.filter((m) => ['T', 'Tb', 'RH'].includes(m.kind));
  const fire = (c) => {
    const p = { ...(c.p || {}) };
    if (c.needs === 'unit') p.unit = unit;
    if (c.needs === 'sensor') p.sensor = sensor;
    if (c.needs === 'zone') p.zone = c.type === 'leak' && !['bdx', 'bgn', 'tec'].includes(zone) ? 'bdx' : zone;
    act(api.chaos(c.type, p), `« ${c.label} » injecté dans toutes les caves au prochain pas`);
  };
  const pol = w.policy;
  return (
    <div className="grid">
      <Tile className="c8" title="Mode chaos" right={<span>appliqué à toutes les caves au même pas · l’agent n’est pas prévenu</span>}>
        <div className="row" style={{ marginBottom: 10 }}>
          <div className="field"><label>Unité CVC</label><select value={unit} onChange={(e) => setUnit(e.target.value)}>{meta.units.map((u) => <option key={u}>{u}</option>)}</select></div>
          <div className="field"><label>Capteur</label><select value={sensor} onChange={(e) => setSensor(e.target.value)}>{tempSensors.map((m) => <option key={m.id} value={m.id}>{m.id} · {m.label}</option>)}</select></div>
          <div className="field"><label>Zone</label><select value={zone} onChange={(e) => setZone(e.target.value)}>{meta.zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}</select></div>
        </div>
        <div className="chaos">
          {meta.chaos.map((c, i) => (
            <button key={i} onClick={() => fire(c)} title={c.needs ? `cible : ${c.needs === 'unit' ? unit : c.needs === 'sensor' ? sensor : zone}` : ''}>
              <b>{c.label}</b><span>{c.hint}{c.needs ? ` · ${c.needs === 'unit' ? unit : c.needs === 'sensor' ? sensor : zone}` : ''}</span>
            </button>
          ))}
        </div>
        <p className="note">Hallucinations, outils inventés et réponses invalides ne touchent que les agents à LLM simulé. Les agents externes lisent les injections dans leur boîte de réception, et le banc détecte seul une contamination (action égale à la consigne hostile ou citant le document).</p>
      </Tile>
      <Tile className="c4" title="Derniers événements exogènes">
        <div className="tl scroll tall">
          {s.exo.length ? [...s.exo].reverse().slice(0, 80).map((e) => (
            <div key={e.id} className="tli"><span className="muted num">{fRel(e.t)}</span><span className={'src ' + (e.src === 'testeur' ? 'chaos' : 'maitre')}>{e.src}</span><span>{e.label}</span></div>
          )) : <Empty>Aucun pour l’instant.</Empty>}
        </div>
      </Tile>
      <Tile className="c12" title="Politique d’autorisation (en direct, tous les couloirs)" right={<span>R0 lecture · R1 réversible · R2 régulation · R3 affecte la cave · R4 critique</span>}>
        <div className="row">
          {['R0', 'R1', 'R2', 'R3', 'R4'].map((lv) => (
            <div className="field" key={lv}><label>{meta.riskLabels[+lv[1]]}</label>
              <select value={pol[lv]} onChange={(e) => act(api.input('all', 'policy', { level: lv, mode: e.target.value }), `${lv} → ${e.target.value}`)}>
                <option value="ALLOW">ALLOW</option><option value="ALLOW_IF">ALLOW_IF (conditions)</option><option value="APPROVAL">APPROVAL (humain)</option><option value="DENY">DENY</option>
              </select>
            </div>
          ))}
          <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={pol.emergency} onChange={(e) => act(api.input('all', 'policy_emergency', { on: e.target.checked }))} /> politique d’urgence : R3 énergie autorisé si perte secteur observée</label>
        </div>
        <p className="note">Les modifications de politique sont journalisées et rejouées au même pas lors d’un replay. Le mode du noyau (appliqué / audit) se règle par couloir dans l’onglet Agent.</p>
        <div className="row" style={{ marginTop: 8 }}>
          {s.lanes.map((l) => <Chip key={l.slot} cls={l.kernel === 'audit' ? 'warn' : 'good'}>{l.name} : noyau {l.kernel === 'audit' ? 'en audit' : 'appliqué'}</Chip>)}
        </div>
      </Tile>
      <Tile className="c12" title="Nouveau run">
        <RunConfig ctx={ctx} />
      </Tile>
    </div>
  );
}
