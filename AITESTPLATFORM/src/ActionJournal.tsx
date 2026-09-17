import { useMemo, useState } from 'react';
import { AlertTriangle, TerminalSquare, Wrench } from 'lucide-react';
import type { LiveRunResult, ToolCallRecord } from './types';
import { Tooltip } from './Tooltip';
import { help } from './glossary';

const json = (value: unknown) => JSON.stringify(value, null, 2);

type CallKind = 'success' | 'error' | 'exception' | 'trace';

/**
 * Un appel peut échouer de deux façons très différentes : l'outil a levé une
 * exception (le framework a mal appelé), ou l'outil a répondu normalement en
 * refusant l'opération (l'agent a mal raisonné). Les confondre empêche de savoir
 * qui corriger.
 */
function classify(call: ToolCallRecord): CallKind {
  if (!call.tool && call.event) return 'trace';
  if (call.transportStatus === 'exception') return 'exception';
  if (call.ok === false || call.semanticStatus === 'error') return 'error';
  return 'success';
}

const BADGES: Record<CallKind, { label: string; className: string; hint: string }> = {
  success: {
    label: 'réussie', className: 'bg-emerald-100 text-emerald-700',
    hint: 'L’outil a accepté l’appel et renvoyé un résultat exploitable.',
  },
  error: {
    label: 'refusée', className: 'bg-amber-100 text-amber-800',
    hint: 'L’outil a répondu, mais en refusant l’opération : identifiant inconnu, condition non remplie, donnée manquante. C’est le raisonnement de l’agent qui est en cause, pas la plateforme.',
  },
  exception: {
    label: 'erreur technique', className: 'bg-rose-100 text-rose-700',
    hint: 'L’appel a levé une exception : arguments invalides ou panne de l’outil simulé. Le framework s’est trompé dans la forme de l’appel.',
  },
  trace: {
    label: 'trace interne', className: 'bg-slate-200 text-slate-600',
    hint: 'Étape interne enregistrée par le programme, sans appel d’outil : elle ne compte pas comme une action.',
  },
};

/**
 * Journal des actions.
 *
 * Il ne suffit pas de lister ce que l'agent a fait : ce qui explique un mauvais
 * score, c'est ce qui a échoué et ce qu'il n'a jamais tenté. Les deux sont donc
 * comptés en tête et isolables d'un clic.
 */
export function ActionJournal({ run, availableTools = [] }: { run: LiveRunResult; availableTools?: string[] }) {
  const [onlyProblems, setOnlyProblems] = useState(false);

  const calls = run.toolCalls || [];
  const kinds = useMemo(() => calls.map(classify), [calls]);
  const problemCount = kinds.filter((kind) => kind === 'error' || kind === 'exception').length;
  const actionCount = kinds.filter((kind) => kind !== 'trace').length;

  const unusedTools = useMemo(() => {
    const used = new Set(calls.map((call) => call.tool).filter(Boolean) as string[]);
    return availableTools.filter((name) => !used.has(name));
  }, [calls, availableTools]);

  const visible = calls
    .map((call, index) => ({ call, kind: kinds[index], index }))
    .filter((item) => !onlyProblems || item.kind === 'error' || item.kind === 'exception');

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <TerminalSquare size={17} className="text-orange-500" />
        <h3 className="font-bold text-slate-900">Journal des actions</h3>
        <Tooltip content="Chaque ligne est une action réellement exécutée sur le système simulé, avec ses paramètres et le résultat renvoyé. Aucun raisonnement du modèle n’est enregistré." />
        {problemCount > 0 && (
          <button
            onClick={() => setOnlyProblems((current) => !current)}
            aria-pressed={onlyProblems}
            className={`ml-auto rounded-lg border px-3 py-1.5 text-xs font-bold transition ${onlyProblems ? 'border-rose-300 bg-rose-50 text-rose-700' : 'border-slate-200 text-slate-600 hover:bg-slate-50'}`}
          >
            {onlyProblems ? 'Voir toutes les actions' : `Voir les ${problemCount} échec(s)`}
          </button>
        )}
      </div>

      <p className="mb-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
        <span className="font-bold text-slate-900">{actionCount} action(s)</span> exécutée(s),
        dont <span className={problemCount > 0 ? 'font-bold text-rose-700' : 'font-bold text-emerald-700'}>{problemCount} en échec</span>.
        {availableTools.length > 0 && <>
          {' '}<span className="font-bold text-slate-900">{unusedTools.length}</span> outil(s) disponible(s) sur {availableTools.length} n’ont jamais été appelés.
        </>}
      </p>

      {problemCount === 0 && calls.length > 0 && (run.failedAssertions?.length ?? 0) > 0 && (
        <p className="mb-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
          Aucune action n’a échoué techniquement, et pourtant {run.failedAssertions.length} vérification(s) restent
          non satisfaites : l’agent a agi, mais pas comme la tâche l’exigeait — mauvais destinataire, mauvaise valeur,
          étape oubliée. Le détail se lit dans « Ce qui manque », au-dessus.
        </p>
      )}

      <div className="max-h-[540px] space-y-3 overflow-auto pr-1">
        {calls.length === 0 && (
          <p className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            L’agent n’a effectué aucune action. Rien n’a été modifié dans le système simulé : c’est la cause directe
            d’un crédit officiel nul, et non un défaut d’enregistrement.
          </p>
        )}
        {calls.length > 0 && visible.length === 0 && (
          <p className="rounded-xl bg-slate-50 p-4 text-sm text-slate-500">Aucune action ne correspond au filtre actif.</p>
        )}
        {visible.map(({ call, kind, index }) => {
          const badge = BADGES[kind];
          return (
            <div key={`${call.index}-${index}`} className={`rounded-xl border p-4 ${kind === 'exception' ? 'border-rose-200 bg-rose-50/60' : kind === 'error' ? 'border-amber-200 bg-amber-50/50' : 'border-slate-200 bg-slate-50'}`}>
              <div className="flex items-center justify-between gap-3">
                <p className="font-mono text-xs font-bold text-slate-900">
                  <span className="mr-2 text-slate-400">#{call.index || index + 1}</span>{call.tool || call.event || 'événement'}
                </p>
                <span className={`flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${badge.className}`}>
                  {badge.label}<Tooltip content={badge.hint} side="left" />
                </span>
              </div>
              {call.error && (
                <p className="mt-2 flex items-start gap-2 rounded-lg bg-white/80 p-2 font-mono text-[11px] leading-4 text-rose-700">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" />{call.error}
                </p>
              )}
              {call.args && <pre className="mt-3 overflow-x-auto whitespace-pre-wrap break-all text-[11px] leading-5 text-slate-600">{json(call.args)}</pre>}
              {call.observation !== undefined && (
                <details className="mt-2 text-xs text-slate-500" open={kind === 'error'}>
                  <summary className="cursor-pointer font-semibold">Réponse du système</summary>
                  <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all text-[11px] leading-5">{json(call.observation)}</pre>
                </details>
              )}
            </div>
          );
        })}
      </div>

      {availableTools.length > 0 && unusedTools.length > 0 && (
        <div className="mt-3 rounded-xl border border-slate-200 bg-white p-4">
          <p className="flex items-center gap-2 text-sm font-bold text-slate-800">
            <Wrench size={15} className="text-slate-400" />Outils disponibles jamais utilisés
            <Tooltip content={`${help('facade')} Ne pas appeler un outil n’est pas une faute en soi : c’est souvent l’explication d’une vérification manquée.`} />
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {unusedTools.map((name) => (
              <span key={name} className="rounded bg-slate-100 px-2 py-1 font-mono text-[10px] text-slate-600">{name}</span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
