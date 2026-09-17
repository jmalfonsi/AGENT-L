import { useEffect, useState } from 'react';
import { BookOpen, Loader2, Scale, X } from 'lucide-react';
import type { RegimeId, RegimeInfo, TaskBriefing } from './types';
import { useModal } from './useModal';
import { Tooltip } from './Tooltip';

/**
 * Choix du régime de comparaison.
 *
 * Le régime décide de ce que le chiffre veut dire : « énoncé seul » mesure la
 * découverte et l'exécution ensemble, « parité de plan » n'évalue plus que
 * l'exécution. Le proposer sans dire ce qu'il change produirait des campagnes
 * lancées au hasard puis comparées à tort — d'où la description complète,
 * l'asymétrie résiduelle énoncée, et le plan consultable avant tout lancement.
 */
export function RegimePicker({ regimes, value, onChange, disabled, taskId }: {
  regimes: RegimeInfo[];
  value: RegimeId;
  onChange: (regime: RegimeId) => void;
  disabled?: boolean;
  /** Tâche dont le plan est affiché à la demande ; aucune requête sans clic. */
  taskId?: string;
}) {
  const [briefingOpen, setBriefingOpen] = useState(false);

  if (regimes.length === 0) return null;

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center justify-between gap-4">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-500">03 · Régime</p>
          <h2 className="mt-1 flex items-center gap-2 text-lg font-black text-slate-950">
            <Scale size={18} />Que voulez-vous comparer exactement
          </h2>
        </div>
        {value === 'plan_parity' && taskId && (
          <button
            onClick={() => setBriefingOpen(true)}
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50"
          >
            <BookOpen size={14} />Voir le plan transmis
          </button>
        )}
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        {regimes.map((regime) => {
          const selected = regime.id === value;
          return (
            <button
              key={regime.id}
              disabled={disabled}
              onClick={() => onChange(regime.id)}
              aria-pressed={selected}
              className={`rounded-xl border p-4 text-left transition ${selected ? 'border-orange-500 bg-orange-50 ring-2 ring-orange-100' : 'border-slate-200 bg-slate-50 hover:border-slate-300'} disabled:cursor-not-allowed disabled:opacity-50`}
            >
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm font-black text-slate-950">{regime.name}</p>
                {regime.id === 'prompt_only' && (
                  <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-white">officiel</span>
                )}
              </div>
              <p className="mt-2 text-xs leading-5 text-slate-600">{regime.measures}</p>
              <p className="mt-2 border-t border-slate-200 pt-2 text-[11px] leading-4 text-slate-500">
                <span className="font-bold text-slate-600">Ce qui reste inégal : </span>{regime.asymmetry}
              </p>
              <p className="mt-2 font-mono text-[9px] text-slate-400">{regime.protocolVersion}</p>
            </button>
          );
        })}
      </div>

      <p className="mt-3 flex items-start gap-1.5 text-[11px] leading-5 text-slate-500">
        Les résultats des deux régimes ne sont jamais moyennés ensemble : le bilan et l’historique les séparent.
        <Tooltip content="Un run « parité de plan » et un run « énoncé seul » ne répondent pas à la même question. Les additionner produirait une moyenne qui ne mesure rien." />
      </p>

      {briefingOpen && taskId && <BriefingModal taskId={taskId} onClose={() => setBriefingOpen(false)} />}
    </section>
  );
}

function BriefingModal({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const [briefing, setBriefing] = useState<TaskBriefing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useModal<HTMLDivElement>(true, onClose);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/tasks/${encodeURIComponent(taskId)}/briefing`)
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        if (!cancelled) setBriefing(data as TaskBriefing);
      })
      .catch((reason) => { if (!cancelled) setError(reason.message); });
    return () => { cancelled = true; };
  }, [taskId]);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/70 p-4 backdrop-blur-sm" onMouseDown={onClose}>
      <div
        ref={dialogRef} role="dialog" aria-modal="true" aria-label="Plan transmis aux frameworks"
        className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-2xl bg-white shadow-2xl"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 p-5">
          <div>
            <h2 className="text-lg font-black text-slate-950">Plan transmis aux trois baselines</h2>
            <p className="mt-1 text-xs leading-5 text-slate-500">
              Ce texte est généré depuis l’arbre syntaxique du programme <span className="font-mono">.agent</span> de
              la tâche, jamais rédigé à la main : aucune étape ne peut y être ajoutée, retirée ni adoucie.
              AGENT-L ne le reçoit pas — il exécute le programme dont il est tiré.
            </p>
          </div>
          <button onClick={onClose} aria-label="Fermer" className="rounded-lg p-1 text-slate-400 hover:bg-slate-100"><X size={18} /></button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto p-5">
          {error && <p className="rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
          {!error && !briefing && <div className="grid place-items-center p-10 text-slate-400"><Loader2 className="animate-spin" /></div>}
          {briefing && (
            <pre className="whitespace-pre-wrap break-words rounded-xl bg-slate-50 p-4 text-[11px] leading-5 text-slate-700">{briefing.briefing}</pre>
          )}
        </div>
      </div>
    </div>
  );
}
