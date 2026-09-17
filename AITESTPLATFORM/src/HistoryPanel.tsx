import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, ChevronRight, CircleAlert, History, Loader2, RefreshCw } from 'lucide-react';
import type { HistoryPage, HistoryRunRow, LiveRunResult } from './types';
import { Tooltip } from './Tooltip';
import { help } from './glossary';

// Une métrique absente reste « N/D » : la plateforme n'estime jamais un chiffre
// qu'elle n'a pas mesuré, et 0 % n'est pas la même information que « inconnu ».
const pct = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)} %` : 'N/D';
const ms = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value)
    ? (value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`)
    : 'N/D';

async function loadPage(beforeSeq: number | null): Promise<HistoryPage> {
  // Pagination par curseur : l'offset se décalait dès qu'un run était écrit
  // entre deux pages, ce qui dupliquait une ligne et en sautait une autre.
  const params = new URLSearchParams({ limit: '50' });
  if (beforeSeq != null) params.set('beforeSeq', String(beforeSeq));
  const response = await fetch(`/api/history?${params.toString()}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

export function HistoryPanel({
  refreshKey,
  onInspect,
}: {
  refreshKey: string;
  onInspect: (run: LiveRunResult) => void;
}) {
  const [runs, setRuns] = useState<HistoryRunRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [openingId, setOpeningId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await loadPage(null);
      setRuns(page.runs);
      setTotal(page.total);
    } catch (reason: any) {
      setError(reason.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh, refreshKey]);

  const loadMore = async () => {
    setLoading(true);
    try {
      const oldest = runs.length ? runs[runs.length - 1].seq : null;
      const page = await loadPage(oldest);
      setRuns((current) => {
        const seen = new Set(current.map((run) => run.id));
        return [...current, ...page.runs.filter((run) => !seen.has(run.id))];
      });
      setTotal(page.total);
    } catch (reason: any) {
      setError(reason.message);
    } finally {
      setLoading(false);
    }
  };

  /**
   * La liste ne transporte plus les objets complets (~29 Ko chacun) : le détail
   * — état final, assertions, appels d'outils — n'est chargé qu'à l'ouverture.
   */
  const inspect = async (row: HistoryRunRow) => {
    setOpeningId(row.id);
    setError(null);
    try {
      const response = await fetch(`/api/history/${encodeURIComponent(row.id)}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      onInspect(data as LiveRunResult);
    } catch (reason: any) {
      setError(`Détail indisponible pour cette exécution : ${reason.message}`);
    } finally {
      setOpeningId(null);
    }
  };

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <header className="flex items-center justify-between gap-4 border-b border-slate-200 p-5">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-500">04 · Historique</p>
          <h2 className="mt-1 flex items-center gap-2 text-lg font-black text-slate-950"><History size={19} />Toutes les exécutions</h2>
          <p className="mt-1 text-xs text-slate-400">Conservées après redémarrage · {total} exécution(s) · cliquez une ligne pour voir ses preuves</p>
        </div>
        <button onClick={refresh} disabled={loading} className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50 disabled:opacity-50" aria-label="Actualiser l’historique" title="Actualiser l’historique"><RefreshCw size={16} className={loading ? 'animate-spin' : ''} /></button>
      </header>

      {error && <p className="m-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
      {!loading && runs.length === 0 && <p className="p-10 text-center text-sm text-slate-400">Aucune exécution enregistrée pour l’instant. Lancez une comparaison : chaque exécution est écrite ici immédiatement.</p>}

      <div className="max-h-[620px] overflow-auto">
        {runs.map((run) => (
          <button
            key={run.id}
            onClick={() => inspect(run)}
            disabled={openingId !== null}
            className="grid w-full grid-cols-[1fr_auto] items-center gap-4 border-b border-slate-100 p-4 text-left last:border-0 hover:bg-slate-50 disabled:opacity-60"
          >
            <span>
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-bold text-slate-950">{run.frameworkName}</span>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[9px] text-slate-500">#{run.taskNumber ?? '—'}</span>
                <span className="font-mono text-[10px] text-slate-400">{run.taskId}</span>
              </span>
              <span className="mt-1 flex flex-wrap gap-3 text-xs text-slate-500">
                <span className="font-bold text-slate-700">{pct(run.partialCredit)} de crédit</span>
                <span>{run.toolCallCount ?? 'N/D'} outils</span>
                <span>{run.llmCallCount ?? 'N/D'} LLM</span>
                <span>{ms(run.executionTimeMs)}</span>
                {run.assertionsEvaluated != null && (
                  <span className={(run.assertionsFailed ?? 0) > 0 ? 'font-semibold text-rose-600' : 'font-semibold text-emerald-600'}>
                    {(run.assertionsEvaluated ?? 0) - (run.assertionsFailed ?? 0)}/{run.assertionsEvaluated} vérifications
                  </span>
                )}
                {run.regime === 'plan_parity' && (
                  <span className="rounded bg-violet-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide text-violet-700">parité de plan</span>
                )}
                {run.status === 'invalid' && <span className="font-bold text-violet-600">invalide · exclu des moyennes</span>}
                <span className="font-mono text-[10px] text-slate-400">{run.protocolVersion || 'legacy-v1'}</span>
                <span>{new Date(run.createdAt).toLocaleString('fr-FR')}</span>
              </span>
            </span>
            <span className="flex items-center gap-3">
              <span className="hidden text-xs font-bold text-orange-600 sm:inline">{run.success ? 'Voir les preuves' : 'Comprendre pourquoi'}</span>
              {openingId === run.id
                ? <Loader2 size={18} className="animate-spin text-slate-400" />
                : run.success
                  ? <CheckCircle2 className="text-emerald-500" size={19} />
                  : <CircleAlert className={run.status === 'invalid' ? 'text-violet-500' : run.error ? 'text-rose-500' : 'text-amber-500'} size={19} />}
              <ChevronRight size={17} className="text-slate-300" />
            </span>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-200 p-4 text-xs text-slate-400">
        <span className="flex items-center gap-1.5">
          Métriques absentes affichées « N/D »<Tooltip content={help('nd')} />
        </span>
        {runs.length < total && (
          <button onClick={loadMore} disabled={loading} className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-xs font-bold text-white disabled:opacity-50">
            {loading && <Loader2 size={14} className="animate-spin" />}
            Charger les {Math.min(50, total - runs.length)} suivantes
          </button>
        )}
      </div>
    </section>
  );
}
