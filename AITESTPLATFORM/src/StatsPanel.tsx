import { useCallback, useEffect, useMemo, useState } from 'react';
import { BarChart3, Download, Info, Loader2, RefreshCw, Trophy } from 'lucide-react';
import type { FrameworkStats, HistoryStats, RegimeId } from './types';
import { Tooltip } from './Tooltip';
import { help } from './glossary';

/**
 * Couleurs d'identité des frameworks : attribuées par framework, JAMAIS par rang.
 * Un filtre qui retire un framework ne doit pas repeindre les survivants, sinon
 * le lecteur croit que les séries ont changé de nature.
 * Ordre catégoriel validé (séparation daltonisme et contraste vérifiés).
 */
const SERIES: Record<string, string> = {
  agent_l: '#2a78d6',
  langgraph: '#eb6834',
  crewai: '#1baf7a',
  openai_agents: '#eda100',
};
const SERIES_FALLBACK = '#4a3aa7';
const seriesColor = (id: string) => SERIES[id] || SERIES_FALLBACK;

/** Rampe séquentielle bleue, du clair au foncé : magnitude du crédit officiel. */
const RAMP = ['#e8f1fd', '#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95'];
function rampStep(value: number | null): string {
  if (value == null) return 'transparent';
  const index = Math.min(RAMP.length - 1, Math.max(0, Math.round(value * (RAMP.length - 1))));
  return RAMP[index];
}
/** Au-delà de la moitié de la rampe le fond est trop sombre pour de l'encre foncée. */
const rampInk = (value: number | null) => (value != null && value >= 0.62 ? '#ffffff' : '#0b0b0b');

const pct = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)} %` : 'N/D';
const num = (value: number | null | undefined, digits = 1) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : 'N/D';
const secs = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value)
    ? (value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`)
    : 'N/D';

/**
 * Phrase de lecture : sans elle, le lecteur non spécialiste voit quatre
 * pourcentages côte à côte et doit faire la comparaison de tête.
 */
function readingSentence(stats: HistoryStats): string {
  const ranked = stats.byFramework
    .filter((row) => row.averagePartialCredit != null && row.validRuns > 0)
    .sort((a, b) => (b.averagePartialCredit ?? 0) - (a.averagePartialCredit ?? 0));

  if (ranked.length === 0) return "Aucune exécution valide à comparer pour l'instant.";
  const [best, second] = ranked;
  if (ranked.length === 1) {
    return `Seul ${best.frameworkName} a des exécutions valides : ${pct(best.averagePartialCredit)} de crédit officiel sur ${best.validRuns} exécution(s). Il n’y a pas de comparaison possible tant qu’un deuxième framework n’a pas été mesuré.`;
  }
  const gap = Math.round(((best.averagePartialCredit ?? 0) - (second.averagePartialCredit ?? 0)) * 100);
  const lead = gap === 0
    ? `à égalité avec ${second.frameworkName}`
    : `soit ${gap} point(s) de plus que ${second.frameworkName} (${pct(second.averagePartialCredit)})`;
  const caution = best.validRuns < 3
    ? ` Attention : la conclusion repose sur ${best.validRuns} exécution(s) seulement, c’est trop peu pour être solide.`
    : '';
  return `${best.frameworkName} arrive en tête avec ${pct(best.averagePartialCredit)} de crédit officiel moyen, ${lead}.${caution}`;
}

function Bar({ row, max }: { row: FrameworkStats; max: number }) {
  const value = row.averagePartialCredit;
  const width = value == null || max <= 0 ? 0 : Math.max(1.5, (value / max) * 100);
  return (
    <div className="grid grid-cols-[130px_1fr_auto] items-center gap-3">
      <span className="flex items-center gap-2 truncate text-xs font-bold text-slate-700">
        <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: seriesColor(row.frameworkId) }} aria-hidden />
        {row.frameworkName}
      </span>
      <span className="relative block h-5">
        {/* Extrémité arrondie côté donnée, ancrée sur la ligne de base. */}
        <span
          className="absolute inset-y-0 left-0 rounded-r"
          style={{ width: `${width}%`, background: seriesColor(row.frameworkId) }}
          role="img"
          aria-label={`${row.frameworkName} : ${pct(value)} de crédit officiel moyen`}
        />
      </span>
      {/* Le contraste des teintes claires impose une étiquette visible. */}
      <span className="w-28 text-right text-xs font-black tabular-nums text-slate-900">
        {pct(value)}
        <span className="ml-1 font-normal text-slate-400">· {row.validRuns} val.</span>
      </span>
    </div>
  );
}

export function StatsPanel({ refreshKey }: { refreshKey: string }) {
  const [stats, setStats] = useState<HistoryStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [framework, setFramework] = useState('');
  const [includeAllProtocols, setIncludeAllProtocols] = useState(false);
  // Le régime n'est jamais « tous » : additionner deux régimes produirait une
  // moyenne qui ne répond à aucune question. On en regarde un à la fois.
  const [regime, setRegime] = useState<RegimeId>('prompt_only');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const params = new URLSearchParams();
      if (framework) params.set('framework', framework);
      if (includeAllProtocols) params.set('protocol', 'all');
      params.set('regime', regime);
      const response = await fetch(`/api/stats?${params.toString()}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      setStats(data as HistoryStats);
    } catch (reason: any) {
      setError(reason?.message || 'Agrégats indisponibles.');
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, [framework, includeAllProtocols, regime]);

  useEffect(() => { load(); }, [load, refreshKey]);

  const ranked = useMemo(
    () => (stats?.byFramework ?? []).slice().sort((a, b) => (b.averagePartialCredit ?? -1) - (a.averagePartialCredit ?? -1)),
    [stats],
  );
  const max = useMemo(
    () => Math.max(0, ...ranked.map((row) => row.averagePartialCredit ?? 0)),
    [ranked],
  );
  const frameworkIds = useMemo(
    () => (stats?.byFramework ?? []).map((row) => row.frameworkId),
    [stats],
  );
  // `protocolVersions` est trié du plus ancien au plus récent : la version
  // réellement comparée est la dernière qui n'a pas été écartée.
  const comparedVersion = useMemo(() => {
    if (!stats) return null;
    const excludedSet = new Set(stats.excludedProtocolVersions ?? []);
    const kept = stats.protocolVersions.filter((version) => !excludedSet.has(version));
    return kept[kept.length - 1] ?? null;
  }, [stats]);

  /** Runs présents dans l'autre régime : évite de conclure à un historique vide. */
  const otherRegimeCount = useMemo(() => {
    const counts = stats?.regimeCounts ?? {};
    return Object.entries(counts)
      .filter(([key]) => key !== regime)
      .reduce((total, [, value]) => total + value, 0);
  }, [stats, regime]);

  const exportUrl = (format: 'csv' | 'json', scope: 'runs' | 'stats') => {
    const params = new URLSearchParams({ format, scope });
    if (framework) params.set('framework', framework);
    if (includeAllProtocols) params.set('protocol', 'all');
    params.set('regime', regime);
    return `/api/export?${params.toString()}`;
  };

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 p-5">
        <div>
          <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-500">03 · Bilan</p>
          <h2 className="mt-1 flex items-center gap-2 text-lg font-black text-slate-950">
            <BarChart3 size={19} />Qui gagne, sur tout l’historique
          </h2>
          <p className="mt-1 text-xs text-slate-400">
            Calculé sur toutes les campagnes enregistrées, pas seulement la dernière.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600">
            Régime
            <select
              value={regime}
              onChange={(event) => setRegime(event.target.value as RegimeId)}
              className="bg-transparent font-bold text-slate-900 outline-none"
            >
              <option value="prompt_only">énoncé seul</option>
              <option value="plan_parity">parité de plan</option>
            </select>
            <Tooltip content={help('regime')} side="bottom" />
          </label>
          <label className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs text-slate-600">
            Framework
            <select
              value={framework}
              onChange={(event) => setFramework(event.target.value)}
              className="bg-transparent font-bold text-slate-900 outline-none"
            >
              <option value="">tous</option>
              {frameworkIds.map((id) => <option key={id} value={id}>{SERIES[id] ? id : id}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600">
            <input type="checkbox" checked={includeAllProtocols} onChange={(event) => setIncludeAllProtocols(event.target.checked)} />
            Toutes versions de protocole
            <Tooltip content={help('protocolVersion')} side="bottom" />
          </label>
          <a href={exportUrl('csv', 'runs')} download className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50" title="Télécharger toutes les exécutions filtrées au format CSV">
            <Download size={14} />CSV
          </a>
          <a href={exportUrl('json', 'stats')} download className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50" title="Télécharger ce bilan au format JSON">
            <Download size={14} />JSON
          </a>
          <button onClick={load} disabled={loading} className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50 disabled:opacity-50" aria-label="Recalculer le bilan">
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </header>

      {error && <p className="m-4 rounded-lg bg-rose-50 p-3 text-sm text-rose-700">{error}</p>}
      {loading && !stats && <div className="grid place-items-center p-14 text-slate-400"><Loader2 className="animate-spin" /></div>}

      {stats && stats.totalRuns === 0 && (
        <div className="p-10 text-center text-sm text-slate-400">
          <p>Aucune exécution ne correspond à ces filtres.</p>
          {/* Sans ce rappel, un régime vide se lit comme un historique vide. */}
          {otherRegimeCount > 0 && (
            <p className="mt-2 text-slate-500">
              L’autre régime compte {otherRegimeCount} exécution(s).
              <button onClick={() => setRegime(regime === 'prompt_only' ? 'plan_parity' : 'prompt_only')}
                className="ml-2 font-bold text-orange-600 underline">
                Basculer sur « {regime === 'prompt_only' ? 'parité de plan' : 'énoncé seul'} »
              </button>
            </p>
          )}
        </div>
      )}

      {stats && stats.totalRuns > 0 && (
        <div className="space-y-6 p-5">
          <div className="flex items-start gap-3 rounded-xl border border-sky-200 bg-sky-50 p-4">
            <Trophy className="mt-0.5 shrink-0 text-sky-600" size={18} />
            <div>
              <p className="text-sm font-bold leading-6 text-sky-950">{readingSentence(stats)}</p>
              <p className="mt-1 text-xs text-sky-800">
                {stats.validRuns} exécution(s) valide(s) retenue(s) sur {stats.totalRuns}
                {stats.invalidRuns > 0 && <> · {stats.invalidRuns} invalide(s) écartée(s) des moyennes</>}
                {stats.failedRuns > 0 && <> · {stats.failedRuns} en erreur</>}
                {' '}· {stats.taskCount} tâche(s)
              </p>
            </div>
          </div>

          {stats.excludedProtocolVersions && stats.excludedProtocolVersions.length > 0 && !includeAllProtocols && (
            <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
              <Info size={15} className="mt-0.5 shrink-0" />
              <p>
                Seule la version de protocole la plus récente est comparée
                {comparedVersion && <> (<span className="font-mono">{comparedVersion}</span>)</>}.
                {' '}{stats.excludedProtocolVersions.length} version(s) plus ancienne(s) sont écartées : les règles d’exécution
                n’étaient pas les mêmes, les scores ne se comparent pas. Cochez « toutes versions » pour les inclure quand même.
              </p>
            </div>
          )}

          <div>
            <div className="mb-3 flex items-center gap-2">
              <h3 className="text-sm font-black text-slate-900">Crédit officiel moyen par framework</h3>
              <Tooltip content={help('partialCredit')} />
            </div>
            <div className="space-y-2.5">
              {ranked.map((row) => <Bar key={row.frameworkId} row={row} max={max} />)}
            </div>
          </div>

          <div className="overflow-x-auto">
            <div className="mb-3 flex items-center gap-2">
              <h3 className="text-sm font-black text-slate-900">Détail chiffré</h3>
              <Tooltip content="Les mêmes données que le graphique, sous forme de tableau lisible et copiable." />
            </div>
            <table className="w-full min-w-[720px] border-collapse text-left text-xs">
              <thead>
                <tr className="border-b border-slate-200 text-[10px] uppercase tracking-wider text-slate-400">
                  <th className="py-2 pr-3 font-black">Framework</th>
                  <th className="py-2 pr-3 font-black">Crédit moyen</th>
                  <th className="py-2 pr-3 font-black">Taux de succès</th>
                  <th className="py-2 pr-3 font-black">Valides</th>
                  <th className="py-2 pr-3 font-black">Invalides</th>
                  <th className="py-2 pr-3 font-black">Outils</th>
                  <th className="py-2 pr-3 font-black">Appels LLM</th>
                  <th className="py-2 pr-3 font-black">Durée</th>
                  <th className="py-2 font-black">Tokens</th>
                </tr>
              </thead>
              <tbody>
                {ranked.map((row) => (
                  <tr key={row.frameworkId} className="border-b border-slate-100 last:border-0">
                    <td className="py-2 pr-3 font-bold text-slate-900">
                      <span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ background: seriesColor(row.frameworkId) }} aria-hidden />
                      {row.frameworkName}
                    </td>
                    <td className="py-2 pr-3 font-black tabular-nums text-slate-900">{pct(row.averagePartialCredit)}</td>
                    <td className="py-2 pr-3 tabular-nums text-slate-700">{pct(row.officialSuccessRate)}</td>
                    <td className="py-2 pr-3 tabular-nums text-slate-700">{row.validRuns}/{row.totalRuns}</td>
                    <td className="py-2 pr-3 tabular-nums text-slate-700">{row.invalidRuns}</td>
                    <td className="py-2 pr-3 tabular-nums text-slate-700">{num(row.averageToolCalls)}</td>
                    <td className="py-2 pr-3 tabular-nums text-slate-700">{num(row.averageLlmCalls)}</td>
                    <td className="py-2 pr-3 tabular-nums text-slate-700">{secs(row.averageTimeMs)}</td>
                    <td className="py-2 tabular-nums text-slate-700">{num(row.averageTotalTokens, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {stats.matrix.length > 0 && (
            <div className="overflow-x-auto">
              <div className="mb-3 flex items-center gap-2">
                <h3 className="text-sm font-black text-slate-900">Tâche par tâche</h3>
                <Tooltip content="Plus la case est foncée, plus le crédit officiel est élevé. Une case vide signifie que cette combinaison n’a jamais été exécutée." />
              </div>
              <table className="w-full min-w-[680px] border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-slate-200 text-[10px] uppercase tracking-wider text-slate-400">
                    <th className="py-2 pr-3 font-black">Tâche</th>
                    {frameworkIds.map((id) => (
                      <th key={id} className="py-2 pr-3 font-black">
                        <span className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ background: seriesColor(id) }} aria-hidden />
                        {id}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {stats.matrix.map((row) => (
                    <tr key={row.taskId} className="border-b border-slate-100 last:border-0">
                      <td className="py-2 pr-3">
                        <span className="font-bold text-slate-900">{row.taskTitle}</span>
                        <span className="ml-2 font-mono text-[10px] text-slate-400">#{row.taskNumber ?? '—'}</span>
                      </td>
                      {frameworkIds.map((id) => {
                        const cell = row.cells[id];
                        const value = cell?.averagePartialCredit ?? null;
                        return (
                          <td key={id} className="py-1.5 pr-3">
                            {cell ? (
                              <span
                                className="inline-flex min-w-14 items-center justify-center rounded-md px-2 py-1 text-[11px] font-black tabular-nums"
                                style={{ background: rampStep(value), color: rampInk(value) }}
                                title={`${row.taskTitle} · ${id} : ${pct(value)} de crédit officiel sur ${cell.runs} exécution(s), ${secs(cell.averageTimeMs)} en moyenne`}
                              >
                                {pct(value)}
                              </span>
                            ) : (
                              <span className="text-slate-300" title="Jamais exécuté">—</span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
