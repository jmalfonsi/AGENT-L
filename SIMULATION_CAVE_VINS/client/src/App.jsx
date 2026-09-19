import { useEffect, useMemo, useState, useCallback } from 'react';
import { useLive } from './live.js';
import { api } from './api.js';
import { f, fRel, fHM, fDate, fDur, laneColor, STATE_CLS, STATE_FR, T0, DAY } from './fmt.js';
import { KPI, RIBBON } from './kpidefs.js';
import { Chip, Dot, Seg } from './components/ui.jsx';
import { Drawer } from './components/drawers.jsx';
import Overview from './tabs/Overview.jsx';
import Cellar from './tabs/Cellar.jsx';
import Equipment from './tabs/Equipment.jsx';
import Sensors from './tabs/Sensors.jsx';
import Agent from './tabs/Agent.jsx';
import Incidents from './tabs/Incidents.jsx';
import Performance from './tabs/Performance.jsx';
import Compare from './tabs/Compare.jsx';
import Chaos from './tabs/Chaos.jsx';
import Journal from './tabs/Journal.jsx';

const store = (k, v) => { try { if (v === undefined) return localStorage.getItem('cal.' + k); localStorage.setItem('cal.' + k, v); } catch { return null; } };
const TABS = [
  ['synthese', 'Synthèse', Overview], ['cave', 'Cave & zones', Cellar], ['equip', 'Équipements', Equipment], ['capteurs', 'Capteurs', Sensors],
  ['agent', 'Agent', Agent], ['incidents', 'Incidents', Incidents], ['perf', 'Performances', Performance], ['compare', 'Comparaison', Compare],
  ['chaos', 'Chaos & runs', Chaos], ['journal', 'Journal & audit', Journal],
];
const SPEEDS = [['pause', '❚❚', 'Pause (barre d’espace)'], ['play', '▶', 'Reprendre (barre d’espace)'], ['step', 'Pas', 'Avancer d’un cycle agent (1 min simulée)'], [1, '×1'], [10, '×10'], [60, '×60'], [360, '×360'], [1440, '×1440'], ['max', 'MAX', 'Aussi vite que possible']];
const KIND_FR = { live: 'live', replay: 'replay', fork: 'fork' };

export default function App() {
  const [meta, setMeta] = useState(null);
  const [lane, setLaneS] = useState(() => +(store('lane') || 0));
  const [tab, setTabS] = useState(() => store('tab') || 'synthese');
  const [theme, setThemeS] = useState(() => store('theme') || 'dark');
  const [drawer, setDrawer] = useState(null);
  const [toast, setToast] = useState(null);
  const { s, conn } = useLive(lane);
  useEffect(() => { api.get('/api/meta').then(setMeta).catch(() => setTimeout(() => api.get('/api/meta').then(setMeta), 2000)); }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; store('theme', theme); }, [theme]);
  useEffect(() => { if (s && s.lanes && lane >= s.lanes.length) setLaneS(0); }, [s, lane]);
  const setLane = (l) => { setLaneS(l); store('lane', l); };
  const setTab = (t) => { setTabS(t); store('tab', t); };
  const say = useCallback((m) => { setToast(m); clearTimeout(window.__toastT); window.__toastT = setTimeout(() => setToast(null), 3200); }, []);
  const act = useCallback(async (p, okMsg) => { try { const r = await p; if (okMsg) say(okMsg); return r; } catch (e) { say('Erreur : ' + e.message); return null; } }, [say]);
  useEffect(() => {
    const onKey = (e) => {
      if (e.target.closest('input, select, textarea')) return;
      if (e.key === 'Escape') setDrawer(null);
      if (e.key === ' ' && s) { e.preventDefault(); act(api.control(s.run.status === 'running' ? 'pause' : 'play')); }
      if (/^[1-5]$/.test(e.key) && s && +e.key <= s.lanes.length) setLane(+e.key - 1);
    };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  });
  const ctx = useMemo(() => ({ meta, s, lane, setLane, open: setDrawer, say, act, setTab }), [meta, s, lane, say, act]);
  if (!meta || !s) return <div className="loading">Connexion au banc d’essai…<br /><span className="muted" style={{ fontSize: 13 }}>{conn === 'error' ? 'serveur injoignable, nouvel essai…' : 'flux temps réel'}</span></div>;
  const r = s.run, w = s.w, k = w.k;
  const Tab = (TABS.find((t) => t[0] === tab) || TABS[0])[2];
  const pend = w.approvals.length, openInc = k.incidents.open, failed = s.lanes.filter((l) => l.status === 'FAILED').length;
  const speedVal = r.status === 'paused' || r.status === 'finished' ? 'pause' : r.speed;
  const onSpeed = (v) => act(v === 'pause' ? api.control('pause') : v === 'play' ? api.control('play') : v === 'step' ? api.control('step') : api.control('speed', v));
  const status = r.ff ? ['info', `rattrapage ${r.ff.pct} %`] : r.blocked ? ['warn', 'attente agent'] : r.status === 'running' ? ['good', 'en cours'] : r.status === 'paused' ? ['warn', 'en pause'] : r.status === 'finished' ? ['info', 'terminé'] : ['', r.status];
  const day = Math.floor((w.t - T0) / DAY);
  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <svg width="28" height="28" viewBox="0 0 32 32" aria-hidden="true"><path d="M9 3h14c0 7-1.6 11.4-5 12.8V26h4v3H10v-3h4V15.8C10.6 14.4 9 10 9 3z" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" /><path d="M10.2 8h11.6c-.5 3.6-2 6-5.8 6s-5.3-2.4-5.8-6z" fill="var(--accent)" /></svg>
          <div><b>Cave Autonomy Lab</b><span>Banc d’essai continu d’agents autonomes</span></div>
        </div>
        <div className="runbadge">
          <b>Run #{r.id} · {KIND_FR[r.replay ? 'replay' : r.fork ? 'fork' : 'live']} · {r.scenarioName}</b>
          <span>seed {r.seed} · pannes {r.fault} · attaques {r.adv === 'on' ? 'oui' : 'non'} · temps {r.timing === 'logical' ? 'logique' : 'opérationnel'}</span>
        </div>
        <div className="clock" title={fDate(w.t)}><b>J+{day} · {fHM(w.t)}</b><span>{fDate(w.t)}{r.scenarioDays ? ` · scénario ${f(((w.t - T0) / DAY / r.scenarioDays) * 100, 0)} %` : ''}</span></div>
        <Seg value={speedVal} options={SPEEDS} onChange={onSpeed} label="Vitesse de simulation" />
        <span className={'pill ' + status[0]}><i />{status[1]}</span>
        <div className="spacer" />
        <button className="btn primary" onClick={() => setDrawer({ kind: 'newrun' })}>Nouveau run</button>
        <button className="icon-btn" title="Plein écran (F11)" onClick={() => (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen().catch(() => {}))}>⛶</button>
        <button className="icon-btn" title="Thème clair / sombre" onClick={() => setThemeS(theme === 'dark' ? 'light' : 'dark')}>{theme === 'dark' ? '☀' : '☾'}</button>
      </header>
      <Banner r={r} conn={conn} />
      <nav className="lanes" aria-label="Couloirs : une cave clonée par agent">
        {s.lanes.map((l) => (
          <button key={l.slot} className="lane" aria-pressed={l.slot === lane} style={{ '--lc': laneColor(l.slot) }} onClick={() => setLane(l.slot)} title={`Afficher la cave de ${l.name} (touche ${l.slot + 1})`}>
            <span className="nm"><span className="ellipsis">{l.name}</span><Chip cls={l.status === 'PASS' ? 'good' : 'crit'}>{l.status === 'PASS' ? 'sûr' : 'FAILED'}</Chip></span>
            <span className="kd ellipsis">{meta.agentKinds[l.kind].label}{l.kernel === 'audit' ? ' · noyau en audit' : ''}{l.kind === 'external' ? (l.connected ? (l.online ? ' · connecté' : ' · silencieux') : ' · en attente de connexion') : ''}</span>
            <span className="big" title="Score de préservation (0-100)">{f(l.preservation, 1)}<small>préservation</small></span>
            <span className="row">
              <Chip cls={STATE_CLS[l.opState]}>{STATE_FR[l.opState]}</Chip>
              <span>OET <b>{f(l.oet, 2)} %</b></span>
              <span>Prestige <b>{f(l.Tpre, 2)} °C</b></span>
              <span>incidents <b>{l.openInc}</b></span>
              {l.violations > 0 && <span className="crit">violations <b className="crit">{l.violations}</b></span>}
              {l.lastAction && <span className="muted ellipsis mono" style={{ maxWidth: 170 }}>{l.lastAction.tool}</span>}
            </span>
          </button>
        ))}
      </nav>
      <section className="kpis" aria-label={`Indicateurs surveillés — ${w.name}`}>
        <button className="kpi" onClick={() => setDrawer({ kind: 'verdict' })} title="Verdict de sûreté non compensable">
          <span className="l"><Dot cls={k.status === 'PASS' ? 'good' : 'crit'} />Statut sûreté</span>
          <span className={'v ' + (k.status === 'PASS' ? 'good' : 'crit')}>{k.status}</span>
          <span className="s">{k.status === 'PASS' ? 'aucune contrainte absolue violée' : k.failReasons[0]}</span>
        </button>
        {RIBBON.map((id) => {
          const d = KPI[id], cls = d.cls(k);
          return (
            <button key={id} className="kpi" onClick={() => setDrawer({ kind: 'kpi', id })} title={d.name + ' — ' + d.calc}>
              <span className="l"><Dot cls={cls} />{d.short || d.fr}</span>
              <span className={'v ' + (d.zero ? cls : '')}>{d.show(k)}{d.unit && !['', '€'].includes(d.unit) && <small>{d.unit}</small>}</span>
              <span className="s">{d.sub ? d.sub(k) : 'cible ' + d.target}</span>
            </button>
          );
        })}
      </section>
      <nav className="tabs" role="tablist">
        {TABS.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
            {label}
            {id === 'agent' && pend > 0 && <span className="badge warn" title="approbations en attente">{pend}</span>}
            {id === 'incidents' && openInc > 0 && <span className="badge" title="incidents ouverts (vérité terrain)">{openInc}</span>}
            {id === 'compare' && failed > 0 && <span className="badge" title="couloirs en échec de sûreté">{failed}</span>}
          </button>
        ))}
      </nav>
      <main key={tab + ':' + lane}><Tab ctx={ctx} /></main>
      {drawer && <Drawer d={drawer} ctx={ctx} close={() => setDrawer(null)} />}
      {toast && <div className="toast" role="status">{toast}</div>}
    </div>
  );
}

function Banner({ r, conn }) {
  if (conn === 'error') return <div className="banner crit">Flux temps réel interrompu : reconnexion automatique…</div>;
  if (r.error) return <div className="banner crit">Erreur moteur : {r.error} — simulation en pause.</div>;
  if (r.ff) return <div className="banner info">{r.ff.silent ? 'Reprise après redémarrage : rattrapage déterministe de la simulation' : 'Préparation du fork : reconstitution exacte de la cave source'} <div className="progress"><i style={{ width: r.ff.pct + '%' }} /></div> {r.ff.pct} %</div>;
  if (r.replay && !r.replay.done) return <div className="banner info">Replay du run #{r.replay.of} : entrées enregistrées rejouées au même pas · empreintes identiques {r.replay.fp.ok} · divergentes {r.replay.fp.ko}</div>;
  if (r.replay && r.replay.done) return <div className={'banner ' + (r.replay.fp.ko ? 'crit' : 'info')}>Replay terminé : {r.replay.fp.ok} empreinte(s) journalière(s) identique(s), {r.replay.fp.ko} divergente(s){r.replay.fp.firstKo ? ` (première : J+${r.replay.fp.firstKo.day}, couloir ${r.replay.fp.firstKo.lane + 1})` : ' — déterminisme vérifié (CA-02)'}.</div>;
  if (r.fork && r.step < r.fork.atStep + 360) return <div className="banner info">Fork du run #{r.fork.run} : chaque agent reprend l’état exact de la cave « {r.fork.srcName} » à J+{f((r.fork.atStep * 10) / DAY, 2)}.</div>;
  if (r.blocked) return <div className="banner">Mode logique : le monde attend — {r.blocked}.</div>;
  return null;
}
