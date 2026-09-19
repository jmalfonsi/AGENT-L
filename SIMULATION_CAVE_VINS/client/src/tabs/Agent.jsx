// Agent : boucle OBSERVE→LEARN, console, actions détaillées, approbations, contenus non fiables, connexion API.
import { useMemo, useState } from 'react';
import { api } from '../api.js';
import { f, fRel, fHM, fDur, fInt, fArgs } from '../fmt.js';
import { Tile, Chip, Prov, Seg, Filters, Empty, ActionRow, ActionHead, StackBar } from '../components/ui.jsx';

const KINDS = [['observation', 'observations'], ['hypothese', 'hypothèses'], ['plan', 'plans'], ['demande', 'demandes'], ['policy', 'policy'], ['refus', 'refus'], ['execution', 'exécutions'], ['verification', 'vérifications'], ['escalade', 'escalades'], ['injection', 'injections'], ['hallucination', 'hallucinations'], ['systeme', 'système']];
const toggle = (set, v) => { const n = new Set(set); if (v == null) return new Set(); n.has(v) ? n.delete(v) : n.add(v); return n; };

export default function Agent({ ctx }) {
  const { s, meta, lane, open, act } = ctx;
  const w = s.w, a = w.agent, k = w.k;
  const [kinds, setKinds] = useState(new Set());
  const [dec, setDec] = useState('all');
  const [q, setQ] = useState('');
  const lines = useMemo(() => s.console.filter((c) => !kinds.size || kinds.has(c.kind)).slice(-400).reverse(), [s.console.length, kinds, s.console[s.console.length - 1]]);
  const actions = [...s.actions.values()].filter((x) => (dec === 'all' || (dec === 'exec' ? x.executed : dec === 'viol' ? x.violation : dec === 'deny' ? !x.executed && x.decision !== 'REQUIRE_APPROVAL' : dec === 'harm' ? ['nuisible', 'inutile'].includes(x.utility) : x.decision === 'REQUIRE_APPROVAL')) && (!q || x.tool.includes(q) || fArgs(x.args).includes(q))).slice(-300).reverse();
  const lanecfg = s.run.lanes[lane];
  const openAid = (aid) => { const x = s.actions.get(aid); if (x) open({ kind: 'action', a: x }); else api.get(`/api/actions/${lane}/${aid}`).then((r) => open({ kind: 'action', a: r.action })).catch(() => {}); };
  const status = a.kind === 'none' ? ['serious', 'aucun agent'] : a.crashed ? ['crit', `crash · redémarrage ${fRel(a.crashUntil)}`] : a.stalled ? ['serious', 'LLM indisponible, mode sûr'] : !a.online ? ['serious', a.kind === 'external' ? (a.connected ? 'agent silencieux' : 'en attente de connexion') : 'hors ligne'] : ['good', 'en ligne'];
  return (
    <div className="grid">
      <Tile className="c12" title={null}>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div className="row">
            <b style={{ font: '700 22px/1 var(--f-cond)', letterSpacing: '.04em', textTransform: 'uppercase' }}>{w.name}</b>
            <Chip>{meta.agentKinds[a.kind].label}</Chip><Chip>{a.model}</Chip><Chip cls={status[0]}>{status[1]}</Chip>
            {a.llmDown && <Chip cls="warn">LLM injoignable</Chip>}{a.netDown && <Chip cls="warn">réseau coupé</Chip>}{a.slow && <Chip cls="warn">latence LLM</Chip>}
            <span className="muted">{a.reasoner} · cycle {fInt(a.cycle)}</span>
          </div>
          <div className="row">
            <span className="flabel">Noyau du banc</span>
            <Seg small value={w.kernel} onChange={(v) => act(api.input(lane, 'kernel', { mode: v }), v === 'audit' ? 'Noyau en audit : il mesure sans bloquer' : 'Noyau appliqué')} options={[['enforce', 'appliqué', 'Les actions refusées par la politique ne sont pas exécutées'], ['audit', 'audit', 'Tout est exécuté (sauf verrous physiques) ; les violations sont comptées']]} />
            <span className="flabel">Approbations R3</span>
            <Seg small value={w.humanMode} onChange={(v) => act(api.input(lane, 'humanMode', { mode: v }))} options={[['auto', 'opérateur simulé'], ['manual', 'testeur']]} />
          </div>
        </div>
        <p className="note">{meta.agentKinds[a.kind].desc}</p>
      </Tile>
      <Tile className="c12" title="Boucle de décision" right={<span>une itération par minute simulée</span>}>
        <div className="loop">{meta.phases.map((p) => <div key={p} className={'phase' + (a.hot === p ? ' hot' : '')}><b>{p}</b><span>{(a.phases && a.phases[p]) || '—'}</span></div>)}</div>
      </Tile>
      <Tile className="c4" title={`Anomalies suivies (${a.anoms.length})`}>
        {a.anoms.length ? <div className="stack">{a.anoms.map((an) => (
          <div key={an.key} className="card"><div className="row"><b className="mono">{an.target}</b>{an.cause && <Chip cls="warn">{meta.causes[an.cause] || an.cause}</Chip>}{an.conf != null && <span className="muted">confiance {f(an.conf * 100, 0)} %</span>}</div><div className="muted" style={{ fontSize: 12 }}>{fRel(an.t)} · {an.obs}</div></div>))}</div> : <Empty>Aucune anomalie : l’agent observe.</Empty>}
      </Tile>
      <Tile className="c4" title="Plans">
        {a.plans.length ? <div className="stack scroll short">{[...a.plans].reverse().map((p) => (
          <div key={p.id} className="card">
            <div className="row"><b className="mono">{p.id}</b><Chip cls={p.done ? '' : 'acc'}>{p.done ? 'terminé' : `étape ${p.i + 1}/${p.steps.length}`}</Chip><span className="muted">{p.origin}</span></div>
            <div style={{ fontSize: 12.5 }}>{p.hyp}</div>
            <div className="plan-steps">{p.steps.map((st, i) => <span key={i} className={i < p.i ? 'done' : i === p.i && !p.done ? 'cur' : ''} title={st.why}>{st.tool}</span>)}</div>
            {p.alternatives && p.alternatives.length > 0 && <details style={{ fontSize: 12, marginTop: 4 }}><summary className="muted">alternatives écartées ({p.alternatives.length})</summary><ul style={{ margin: '4px 0 0', paddingLeft: 16 }}>{p.alternatives.map((x, i) => <li key={i}>{x}</li>)}</ul></details>}
          </div>))}</div> : <Empty>{a.kind === 'external' ? 'Les plans d’un agent externe apparaissent dans la console (items « note ») et dans ses phases.' : 'Aucun plan.'}</Empty>}
      </Tile>
      <Tile className="c4" title={`Approbations en attente (${w.approvals.length})`} right={<span>{w.humanMode === 'auto' ? 'opérateur simulé' : 'vous décidez'}</span>}>
        {w.approvals.length ? <div className="stack">{w.approvals.map((ap) => (
          <div key={ap.id} className="card pending">
            <div className="row"><b className="mono">{ap.id}</b><span className="mono">{ap.tool}({fArgs(ap.args)})</span></div>
            <div style={{ fontSize: 12.5 }}>{ap.why}</div>
            <div className="row">{(ap.evidence || []).map((e, i) => <span key={i} className="mono" style={{ fontSize: 11.5 }}>{e.src} <Prov p={e.prov} /></span>)}</div>
            <div className="row">
              <button className="btn small good" onClick={() => act(api.input(lane, 'approve', { id: ap.id, ok: true }), `${ap.id} approuvée`)}>Approuver</button>
              <button className="btn small danger" onClick={() => act(api.input(lane, 'approve', { id: ap.id, ok: false }), `${ap.id} refusée`)}>Refuser</button>
              <button className="btn small" onClick={() => openAid(ap.aid)}>détail</button>
              {ap.autoAt && <span className="muted">décision auto {fRel(ap.autoAt)}</span>}
            </div>
          </div>))}</div> : <Empty>Aucune action R3 en attente.</Empty>}
        <div className="flabel" style={{ marginTop: 10 }}>Boîte de réception (contenus UNTRUSTED)</div>
        <div className="stack scroll short" style={{ marginTop: 6 }}>
          {w.inbox.length ? w.inbox.map((d) => (
            <div key={d.id} className={'inbox' + (d.hostile ? '' : ' benign')}>
              <div className="row" style={{ fontFamily: 'var(--f-body)' }}><b>{d.src}</b><span className="muted">{d.id} · {fRel(d.t)}</span>{d.hostile && <Chip cls="crit">hostile (vérité)</Chip>}{d.flags && d.flags.executed && <Chip cls="crit">a produit un effet</Chip>}{d.flags && d.flags.influenced && !d.flags.executed && <Chip cls="warn">a influencé le LLM</Chip>}{d.flags && d.flags.blocked && <Chip cls="good">bloquée</Chip>}</div>
              <div>{d.text}</div>
            </div>)) : <Empty>vide</Empty>}
        </div>
      </Tile>
      <Tile className="c6" title="Console de l’agent" right={<span>{lines.length} lignes · plus récentes en haut</span>}>
        <Filters options={KINDS} value={kinds} onToggle={(v) => setKinds(toggle(kinds, v))} all="tout" />
        <div className="console scroll tall">
          {lines.length ? lines.map((c) => (
            <div key={c.id} className={'cline' + (c.aid ? ' click' : '')} onClick={() => c.aid && openAid(c.aid)} title={c.aid ? 'Voir le détail de l’action ' + c.aid : ''}>
              <span className="muted">{fRel(c.t).split(' ')[0]} {fHM(c.t)}</span><span className={'k ' + c.kind}>{c.kind}</span><span>{c.text}</span>
              <span className="row" style={{ gap: 4 }}>{c.prov && <Prov p={c.prov} />}{c.conf != null && <span className="muted">{f(c.conf * 100, 0)} %</span>}{c.aid && <span className="muted">{c.aid}</span>}</span>
            </div>)) : <Empty>Console vide.</Empty>}
        </div>
      </Tile>
      <Tile className="c6" title="Actions demandées" right={<span>{k.actions.total} au total · clic = explication complète</span>}>
        <div className="row" style={{ marginBottom: 8 }}>
          <Seg small value={dec} onChange={setDec} options={[['all', 'toutes'], ['exec', 'exécutées'], ['deny', 'refusées'], ['appr', 'approbation'], ['viol', 'violations'], ['harm', 'inutiles/nuisibles']]} />
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="outil ou argument…" style={{ width: 170 }} />
        </div>
        <div className="tw scroll tall">
          {actions.length ? <table><ActionHead /><tbody>{actions.map((x) => <ActionRow key={x.id} a={x} onOpen={(y) => open({ kind: 'action', a: y })} />)}</tbody></table> : <Empty>Aucune action pour ce filtre.</Empty>}
        </div>
      </Tile>
      <Tile className="c4" title="Décisions du noyau">
        <StackBar parts={[{ label: 'exécutées', n: k.actions.executed, color: 'var(--good)' }, { label: 'refusées (politique)', n: k.actions.denied, color: 'var(--crit)' }, { label: 'verrous physiques', n: k.actions.interlock, color: 'var(--serious)' }, { label: 'approbation', n: k.actions.approval, color: 'var(--warn)' }, { label: 'doublons bloqués', n: k.actions.dup, color: 'var(--info)' }]} />
        <div className="flabel" style={{ marginTop: 10 }}>Utilité des actions exécutées (oracle)</div>
        <StackBar parts={[{ label: 'utiles', n: k.actions.useful, color: 'var(--good)' }, { label: 'neutres', n: k.actions.neutral, color: 'var(--muted)' }, { label: 'inutiles', n: k.actions.unnecessary, color: 'var(--warn)' }, { label: 'nuisibles', n: k.actions.harmful, color: 'var(--crit)' }]} />
      </Tile>
      <Tile className="c4" title="Consommation du modèle">
        <dl className="kv">
          <dt>Appels LLM</dt><dd>{fInt(k.llm.calls)}</dd><dt>Jetons entrée / sortie</dt><dd>{fInt(k.llm.tin)} / {fInt(k.llm.tout)}</dd>
          <dt>Coût</dt><dd>{f(k.llm.cost, 4)} €</dd><dt>Latence P50 / P95</dt><dd>{f(k.llm.lat.p50, 2)} s / {f(k.llm.lat.p95, 2)} s</dd>
          <dt>Réponses invalides rejetées</dt><dd>{k.invalid}</dd><dt>Outils inventés bloqués</dt><dd>{k.toolHall.blocked} / {k.toolHall.total}</dd>
          <dt>Coût moyen d’une décision</dt><dd>{f(k.decisionCost, 3)} €</dd>
        </dl>
        {a.kind === 'agentl' || a.kind === 'naive' ? <p className="note">LLM simulé : gemini-3.5-lite n’est qu’une étiquette, les coûts et latences sont modélisés. Pour un vrai modèle, branchez un agent externe.</p> : null}
      </Tile>
      <Tile className="c4" title="Brancher un agent externe">
        {lanecfg.agent === 'external' ? <ExtPanel ctx={ctx} lanecfg={lanecfg} a={a} /> : (
          <div className="stack">
            <p style={{ margin: 0 }}>Ce couloir exécute un agent intégré. Pour tester votre agent (AGENT-L réel, LangGraph, PydanticAI, CrewAI…), lancez un run avec un couloir <b>Agent externe (API)</b> : il reçoit un jeton et interroge le banc en mode pull.</p>
            <button className="btn" onClick={() => ctx.open({ kind: 'newrun' })}>Configurer un run</button>
          </div>)}
      </Tile>
    </div>
  );
}

function ExtPanel({ ctx, lanecfg, a }) {
  const origin = window.location.origin;
  const tok = lanecfg.token || '—';
  const cmd = `python3 examples/external_agent.py --url ${origin} --token ${tok}`;
  const copy = (t) => navigator.clipboard && navigator.clipboard.writeText(t).then(() => ctx.say('Copié'));
  const e = a.ext || {};
  return (
    <div className="stack">
      <dl className="kv">
        <dt>État</dt><dd>{a.connected ? (a.online ? <span className="good">connecté</span> : <span className="warn">silencieux</span>) : <span className="muted">jamais connecté</span>}</dd>
        <dt>Dernier appel</dt><dd>{e.lastSeenAgoMs == null ? '—' : fDur(e.lastSeenAgoMs / 1000) + ' (réel)'}</dd>
        <dt>Dernier tick acquitté</dt><dd>{e.ackTick ?? '—'}</dd>
        <dt>Ticks manqués (mode logique)</dt><dd>{e.missed ?? 0}</dd>
        <dt>Items en attente</dt><dd>{e.buffered ?? 0}</dd>
        {e.info && <><dt>Déclaré</dt><dd>{[e.info.name, e.info.framework, e.info.model].filter(Boolean).join(' · ')}</dd></>}
      </dl>
      <div className="field"><label>Jeton du couloir</label><div className="row" style={{ gap: 6 }}><code className="mono ellipsis" style={{ flex: 1 }}>{tok}</code><button className="btn small" onClick={() => copy(tok)}>copier</button></div></div>
      <div className="field"><label>Agent de référence (Python, sans dépendance)</label><pre className="code">{cmd}</pre><button className="btn small" onClick={() => copy(cmd)}>copier la commande</button></div>
      <p className="note">Protocole : <span className="mono">GET /api/agent/v1/observe</span> puis <span className="mono">POST /api/agent/v1/act</span> avec <span className="mono">{'{tick, items}'}</span>. Catalogue et schémas : <span className="mono">GET /api/agent/v1/tools</span>. L’agent ne voit jamais la vérité terrain.</p>
    </div>
  );
}
