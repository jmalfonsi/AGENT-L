import { useState } from 'react';
import { CheckCircle2, MinusCircle, ShieldCheck, XCircle } from 'lucide-react';
import type { AssertionRecord, LiveRunResult } from './types';
import { assertionGap, describeAssertion, tallyAssertions } from './assertions';
import { assertionEvidence } from './runDiagnosis';
import { Tooltip } from './Tooltip';
import { help } from './glossary';

type Group = 'failed' | 'satisfied' | 'notApplicable';

const TONES: Record<Group, { border: string; head: string; chip: string }> = {
  failed: { border: 'border-rose-200', head: 'text-rose-900', chip: 'bg-rose-100 text-rose-800' },
  satisfied: { border: 'border-emerald-200', head: 'text-emerald-900', chip: 'bg-emerald-100 text-emerald-800' },
  notApplicable: { border: 'border-slate-200', head: 'text-slate-700', chip: 'bg-slate-100 text-slate-600' },
};

function AssertionItem({ record, group, run }: { record: AssertionRecord; group: Group; run: LiveRunResult }) {
  const readable = describeAssertion(record);
  const evidence = assertionEvidence(record, run);
  const tone = TONES[group];
  return (
    <li className={`rounded-xl border bg-white p-3 ${tone.border}`}>
      <div className="flex items-start gap-2">
        {group === 'satisfied' ? <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-emerald-500" />
          : group === 'failed' ? <XCircle size={16} className="mt-0.5 shrink-0 text-rose-500" />
            : <MinusCircle size={16} className="mt-0.5 shrink-0 text-slate-400" />}
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-5 text-slate-800">
            {readable.app && <span className={`mr-2 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${tone.chip}`}>{readable.app}</span>}
            <span className="font-semibold">{readable.negative ? 'À ne pas faire' : 'Attendu'} :</span> {readable.expectation}
          </p>
          {group === 'failed' && <p className="mt-1 text-xs font-semibold text-rose-700">{assertionGap(record)}</p>}
          {group !== 'notApplicable' && (
            <div className={`mt-2 rounded-lg px-3 py-2 text-xs leading-5 ${group === 'failed' ? 'bg-rose-50 text-rose-900' : 'bg-emerald-50 text-emerald-900'}`}>
              <p><span className="font-black">Observé :</span> {evidence.summary}</p>
              {evidence.actionHint && <p className="mt-1 text-amber-800"><span className="font-black">Indice :</span> {evidence.actionHint}</p>}
              {group === 'failed' && evidence.samples.length > 0 && (
                <details className="mt-2">
                  <summary className="cursor-pointer font-bold text-slate-600">Voir les données finales proches</summary>
                  <ul className="mt-1 space-y-1 border-l-2 border-rose-200 pl-3 font-mono text-[10px] leading-4 text-slate-600">
                    {evidence.samples.map((sample, index) => <li key={index}>{sample}</li>)}
                  </ul>
                </details>
              )}
            </div>
          )}
          {readable.facts.length > 0 && (
            <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
              {readable.facts.map((fact) => (
                <div key={fact.label} className="flex gap-1">
                  <dt className="font-semibold uppercase tracking-wide text-slate-400">{fact.label}</dt>
                  <dd className="break-all text-slate-600">{fact.value}</dd>
                </div>
              ))}
            </dl>
          )}
          <details className="mt-2">
            <summary className="cursor-pointer text-[10px] font-mono text-slate-400 hover:text-slate-600">{readable.type}</summary>
            <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded bg-slate-50 p-2 text-[10px] leading-4 text-slate-500">{JSON.stringify(record, null, 2)}</pre>
          </details>
        </div>
      </div>
    </li>
  );
}

function GroupBlock({ title, hint, records, group, defaultOpen, run }: {
  title: string; hint: string; records: AssertionRecord[]; group: Group; defaultOpen: boolean; run: LiveRunResult;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (records.length === 0) return null;
  return (
    <div>
      <button
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className={`flex w-full items-center gap-2 rounded-lg px-1 py-2 text-left text-sm font-black ${TONES[group].head}`}
      >
        <span className={`grid h-5 min-w-5 place-items-center rounded px-1 text-[11px] ${TONES[group].chip}`}>{records.length}</span>
        {title}
        <Tooltip content={hint} />
        <span className="ml-auto text-[11px] font-semibold text-slate-400">{open ? 'masquer' : 'afficher'}</span>
      </button>
      {open && <ul className="mt-1 space-y-2">{records.map((record, index) => <AssertionItem key={index} record={record} group={group} run={run} />)}</ul>}
    </div>
  );
}

/**
 * Détail de la note officielle AutomationBench.
 *
 * Le score n'a de valeur que si l'on voit sur quoi il porte : sans la liste des
 * vérifications, « 43 % » n'apprend rien et ne se corrige pas. Les vérifications
 * non satisfaites sont ouvertes par défaut — c'est l'information utile.
 */
export function OfficialScorePanel({ run }: { run: LiveRunResult }) {
  const tally = tallyAssertions(run.assertions || []);
  const percent = run.partialCredit == null ? null : Math.round(run.partialCredit * 100);

  /** Répartition par application Zapier : où le travail a tenu, où il a lâché. */
  const byApp = (() => {
    const map = new Map<string, { satisfied: number; evaluated: number }>();
    for (const record of [...tally.satisfied, ...tally.failed]) {
      const app = describeAssertion(record).app || 'Autre';
      const cell = map.get(app) || { satisfied: 0, evaluated: 0 };
      cell.evaluated += 1;
      if (tally.satisfied.includes(record)) cell.satisfied += 1;
      map.set(app, cell);
    }
    return [...map.entries()].sort((a, b) => b[1].evaluated - a[1].evaluated);
  })();

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <ShieldCheck size={18} className="text-emerald-600" />
        <h3 className="font-black text-slate-950">Notation officielle AutomationBench</h3>
        <Tooltip content={help('rubric')} />
        <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-500">
          {run.scorer || 'AutomationBench official rubric'}
        </span>
      </div>

      <p className="mt-1 text-xs leading-5 text-slate-500">
        Les vérifications portent sur l’état final des applications Zapier simulées — Gmail, Google Sheets, Slack,
        Salesforce selon la tâche — et non sur les outils que l’agent a choisi d’appeler.
      </p>

      <p className="mt-3 text-sm leading-6 text-slate-700">
        {tally.evaluated === 0
          ? 'Aucune vérification n’était évaluable sur cette exécution : le barème n’a rien pu noter.'
          : <>
            Le barème a évalué <span className="font-bold text-slate-950">{tally.evaluated} vérification(s)</span> sur les {run.assertions.length} que
            contient la tâche. L’agent en a satisfait <span className="font-bold text-slate-950">{tally.satisfied.length}</span>
            {' '}— soit <span className="font-bold text-slate-950">{percent == null ? 'N/D' : `${percent} %`}</span> de crédit officiel.
            {tally.failed.length > 0 && <> Il en reste <span className="font-bold text-rose-700">{tally.failed.length}</span> à corriger.</>}
          </>}
      </p>

      <div className="mt-3 flex h-3 overflow-hidden rounded-full bg-slate-100">
        <div className="bg-emerald-500" style={{ width: `${tally.evaluated ? (tally.satisfied.length / tally.evaluated) * 100 : 0}%` }} />
        <div className="bg-rose-400" style={{ width: `${tally.evaluated ? (tally.failed.length / tally.evaluated) * 100 : 0}%` }} />
      </div>
      <p className="mt-1 flex flex-wrap gap-x-4 text-[11px] text-slate-400">
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-500" />satisfaites</span>
        <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-rose-400" />non satisfaites</span>
        {tally.notApplicable.length > 0 && <span>{tally.notApplicable.length} hors barème, sans effet sur la note</span>}
      </p>

      {byApp.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {byApp.map(([app, cell]) => {
            const complete = cell.satisfied === cell.evaluated;
            return (
              <span key={app} className={`flex items-baseline gap-1.5 rounded-lg border px-3 py-1.5 text-xs ${complete ? 'border-emerald-200 bg-emerald-50' : 'border-rose-200 bg-rose-50'}`}>
                <span className="font-bold text-slate-800">{app}</span>
                <span className={complete ? 'font-black text-emerald-700' : 'font-black text-rose-700'}>{cell.satisfied}/{cell.evaluated}</span>
              </span>
            );
          })}
          <Tooltip content="Répartition des vérifications par application Zapier simulée. Elle montre d’un coup d’œil sur quelle brique de l’automatisation l’agent a échoué." />
        </div>
      )}

      <div className="mt-4 space-y-3">
        <GroupBlock
          group="failed" defaultOpen title="Ce qui a échoué" records={tally.failed} run={run}
          hint="Ce que la tâche exigeait et que l’agent n’a pas produit — ou, pour une interdiction, ce qu’il a fait alors qu’il ne devait pas. Chaque ligne coûte une part du crédit officiel."
        />
        <GroupBlock
          group="satisfied" defaultOpen={tally.failed.length === 0} title="Ce qui a fonctionné" records={tally.satisfied} run={run}
          hint="Les vérifications que l’agent a réellement satisfaites dans le système simulé."
        />
        <GroupBlock
          group="notApplicable" defaultOpen={false} title="Hors barème" records={tally.notApplicable} run={run}
          hint={`${help('excludedAssertion')} Concrètement : ces conditions étaient déjà vraies au départ. Les satisfaire ne rapporte rien, mais les casser aurait compté comme un échec.`}
        />
      </div>
    </section>
  );
}
