// Configuration d'un nouveau run : scénario, seed, aléas, couloirs (un agent par cave clonée), politique.
import { useState } from 'react';
import { api } from '../api.js';
import { laneColor } from '../fmt.js';
import { Seg } from './ui.jsx';

const PRESETS = {
  reference: { label: 'Référence intégrée', desc: 'AGENT-L simulé, LLM naïf, baseline déterministe, cave sans agent.', lanes: [{ agent: 'agentl', name: 'AGENT-L' }, { agent: 'naive', name: 'LLM naïf' }, { agent: 'baseline', name: 'Baseline' }, { agent: 'none', name: 'Sans agent' }] },
  frameworks: { label: 'Banc 5 agents', desc: 'Quatre couloirs externes à brancher (AGENT-L, LangGraph, PydanticAI, CrewAI) + le contrôleur de référence.', lanes: [{ agent: 'external', name: 'AGENT-L', kernel: 'audit' }, { agent: 'external', name: 'LangGraph', kernel: 'audit' }, { agent: 'external', name: 'PydanticAI', kernel: 'audit' }, { agent: 'external', name: 'CrewAI', kernel: 'audit' }, { agent: 'baseline', name: 'Baseline' }] },
  kernel: { label: 'Valeur du noyau', desc: 'Le même LLM naïf, avec et sans noyau de policy du banc, face à AGENT-L.', lanes: [{ agent: 'agentl', name: 'AGENT-L' }, { agent: 'naive', name: 'Naïf sans noyau', kernel: 'audit' }, { agent: 'naive', name: 'Naïf + noyau', kernel: 'enforce' }, { agent: 'baseline', name: 'Baseline' }] },
};

export default function RunConfig({ ctx, onDone, initial }) {
  const { meta, s } = ctx;
  const r = s.run;
  const [c, setC] = useState(() => initial || {
    scenario: r.scenario, seed: r.seed, fault: r.fault, adv: r.adv, timing: r.timing, humanMode: r.humanMode, speed: 60,
    lanes: r.lanes.map((l) => ({ agent: l.agent, name: l.name, kernel: l.kernel, model: l.model || '' })), policy: { ...r.policy },
  });
  const [label, setLabel] = useState('');
  const set = (k, v) => setC((o) => ({ ...o, [k]: v }));
  const setLane = (i, k, v) => setC((o) => ({ ...o, lanes: o.lanes.map((l, j) => (j === i ? { ...l, [k]: v, ...(k === 'agent' ? { kernel: meta.agentKinds[v].kernel, name: l.name && l.name !== meta.agentKinds[l.agent].short ? l.name : meta.agentKinds[v].short } : {}) } : l)) }));
  const sc = meta.scenarios.find((x) => x.id === c.scenario);
  const launch = async () => {
    const cfg = { ...c, lanes: c.lanes.map((l) => ({ ...l, model: l.model || undefined })) };
    const res = await ctx.act(api.post('/api/runs', { config: cfg, label: label || undefined }), 'Nouveau run lancé');
    if (res) { ctx.setLane(0); onDone && onDone(); }
  };
  return (
    <div className="stack">
      <p className="muted">Chaque couloir reçoit sa propre copie de la cave. Même seed, mêmes événements exogènes (météo, pannes imposées, attaques) au même pas ; seules les décisions des agents font diverger les caves.</p>
      <div className="flabel">Préréglages</div>
      <div className="scen">
        {Object.entries(PRESETS).map(([id, p]) => <button key={id} onClick={() => set('lanes', p.lanes.map((l) => ({ kernel: meta.agentKinds[l.agent].kernel, model: '', ...l })))}><b>{p.label}</b><span>{p.desc}</span></button>)}
      </div>
      <div className="flabel">Couloirs ({c.lanes.length}/5)</div>
      <div className="lanecfg">
        <span /><span className="flabel">Nom</span><span className="flabel">Agent</span><span className="flabel">Noyau du banc</span><span className="flabel">Modèle (externe)</span><span />
        {c.lanes.map((l, i) => [
          <span key={'s' + i} className="swatch" style={{ background: laneColor(i) }} />,
          <input key={'n' + i} type="text" value={l.name} maxLength={32} onChange={(e) => setLane(i, 'name', e.target.value)} aria-label={`Nom du couloir ${i + 1}`} />,
          <select key={'a' + i} value={l.agent} onChange={(e) => setLane(i, 'agent', e.target.value)} aria-label={`Agent du couloir ${i + 1}`}>
            {Object.entries(meta.agentKinds).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>,
          <select key={'k' + i} value={l.kernel} onChange={(e) => setLane(i, 'kernel', e.target.value)} aria-label="Noyau">
            <option value="enforce">appliqué (bloquant)</option><option value="audit">audit (mesure seulement)</option>
          </select>,
          <input key={'m' + i} type="text" value={l.model || ''} disabled={l.agent !== 'external'} placeholder={l.agent === 'external' ? 'ex. gemini-3.5-lite' : '—'} onChange={(e) => setLane(i, 'model', e.target.value)} aria-label="Modèle" />,
          <button key={'x' + i} className="btn small" disabled={c.lanes.length <= 1} onClick={() => set('lanes', c.lanes.filter((_, j) => j !== i))} title="Retirer ce couloir">✕</button>,
        ])}
      </div>
      <div><button className="btn small" disabled={c.lanes.length >= 5} onClick={() => set('lanes', [...c.lanes, { agent: 'external', name: 'Mon agent', kernel: 'audit', model: '' }])}>+ couloir externe</button></div>
      <div className="flabel">Scénario</div>
      <div className="scen">
        {meta.scenarios.map((x) => <button key={x.id} aria-pressed={c.scenario === x.id} onClick={() => set('scenario', x.id)}><b>{x.name}</b><span>{x.desc}</span><em>{x.days ? `${x.days} jour(s) simulé(s)` : 'sans fin'}</em></button>)}
      </div>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div className="field"><label htmlFor="seed">Seed</label><div className="row" style={{ gap: 4 }}><input id="seed" type="number" value={c.seed} min={1} max={999999999} onChange={(e) => set('seed', +e.target.value)} style={{ width: 110 }} /><button className="btn small" onClick={() => set('seed', 1 + Math.floor(Math.random() * 999999))} title="Seed aléatoire">⟳</button></div></div>
        <div className="field"><label>Pannes aléatoires</label><Seg small value={c.fault} onChange={(v) => set('fault', v)} options={[['off', 'aucune'], ['low', 'faibles'], ['medium', 'moyennes'], ['high', 'élevées']]} /></div>
        <div className="field"><label>Attaques & stress LLM</label><Seg small value={c.adv} onChange={(v) => set('adv', v)} options={[['on', 'oui'], ['off', 'non']]} /></div>
        <div className="field"><label>Temps</label><Seg small value={c.timing} onChange={(v) => set('timing', v)} options={[['operational', 'opérationnel', 'Le monde avance pendant que l’agent réfléchit : la latence du modèle compte'], ['logical', 'logique', 'Le banc attend la réponse de chaque agent externe à chaque tick']]} /></div>
        <div className="field"><label>Approbations R3</label><Seg small value={c.humanMode} onChange={(v) => set('humanMode', v)} options={[['auto', 'opérateur simulé'], ['manual', 'testeur']]} /></div>
        <div className="field"><label>Vitesse initiale</label><Seg small value={c.speed} onChange={(v) => set('speed', v)} options={[[1, '×1'], [60, '×60'], [360, '×360'], [1440, '×1440'], ['max', 'MAX']]} /></div>
      </div>
      {sc && sc.id !== 'CONT' && c.fault !== 'off' && <p className="note">Les pannes aléatoires ne s’appliquent qu’en exploitation continue ; ce scénario rejoue ses propres événements.</p>}
      <div className="flabel">Politique d’autorisation initiale</div>
      <div className="row">
        {['R0', 'R1', 'R2', 'R3', 'R4'].map((lv) => (
          <div className="field" key={lv}><label>{lv}</label>
            <select value={c.policy[lv]} onChange={(e) => set('policy', { ...c.policy, [lv]: e.target.value })}><option value="ALLOW">ALLOW</option><option value="ALLOW_IF">ALLOW_IF</option><option value="APPROVAL">APPROVAL</option><option value="DENY">DENY</option></select>
          </div>
        ))}
        <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={c.policy.emergency} onChange={(e) => set('policy', { ...c.policy, emergency: e.target.checked })} /> politique d’urgence (perte secteur)</label>
      </div>
      <div className="row" style={{ alignItems: 'flex-end' }}>
        <div className="field" style={{ flex: 1 }}><label htmlFor="lbl">Libellé (facultatif)</label><input id="lbl" type="text" value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} placeholder="ex. campagne S15 × 5 agents" /></div>
        <button className="btn primary" onClick={launch}>Lancer le run</button>
      </div>
      <p className="note">Le run courant est clos et archivé dans SQLite (KPI finaux, journal d’audit, entrées rejouables). Les couloirs externes reçoivent un jeton affiché dans l’onglet Agent.</p>
    </div>
  );
}
