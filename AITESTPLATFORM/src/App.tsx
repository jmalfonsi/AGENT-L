import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, CheckCircle2, ChevronRight, CircleAlert, Clock3, Cpu,
  Database, FlaskConical, GitBranch, Lightbulb, Loader2, Play, ShieldCheck,
  Code2, Trophy, Wrench, Eye, X, XCircle,
} from 'lucide-react';
import type {
  ActiveCampaign, ActiveFrameworkId, BenchmarkTaskDetail, BenchmarkTaskSummary, CampaignRecord,
  FrameworkCodeBundle, FrameworkStatus, HistoryRunRow, HistoryStats, LiveExecutionEvent,
  LiveRunResult, RegimeId, RegimeInfo, RunnerHealth,
} from './types';
import { TaskDetailsModal } from './TaskDetailsModal';
import { FrameworkCodeModal } from './FrameworkCodeModal';
import { HistoryPanel } from './HistoryPanel';
import { StatsPanel } from './StatsPanel';
import { LiveExecutionPanel } from './LiveExecutionPanel';
import { FactoryPanel } from './FactoryPanel';
import { RegimePicker } from './RegimePicker';
import { OfficialScorePanel } from './OfficialScorePanel';
import { ActionJournal } from './ActionJournal';
import { RunDiagnosisPanel } from './RunDiagnosisPanel';
import { ErrorBoundary } from './ErrorBoundary';
import { Tooltip } from './Tooltip';
import { help } from './glossary';
import { useModal } from './useModal';

const pct = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? `${Math.round(value * 100)} %` : 'N/D';
const ms = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value)
    ? (value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`)
    : 'N/D';
const avg = (value: number | null | undefined) =>
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(1) : 'N/D';
const json = (value: unknown) => JSON.stringify(value, null, 2);

const invalidReasonLabel = (reason: LiveRunResult['invalidReason']) =>
  reason === 'output_token_limit' ? 'Le modèle a atteint sa longueur maximale de réponse avant d’avoir produit un résultat exploitable.'
    : reason === 'empty_model_output' ? 'Le modèle n’a produit ni réponse ni action : il n’y a rien à noter.'
      : reason === 'framework_error' ? 'Le framework s’est arrêté sur une erreur technique.'
        : 'Intégrité technique non validée.';

/** Nom lisible d'un régime ; le repli couvre un `health` pas encore chargé. */
const regimeName = (regimes: RegimeInfo[] | undefined, id: RegimeId) =>
  regimes?.find((item) => item.id === id)?.name
  ?? (id === 'plan_parity' ? 'Parité de plan' : 'Énoncé seul');

/** Durée moyenne de repli quand l'historique ne fournit pas encore de mesure. */
const FALLBACK_RUN_MS = 180_000;

function durationLabel(msValue: number): string {
  const minutes = Math.round(msValue / 60_000);
  if (minutes < 1) return 'moins d’une minute';
  if (minutes < 60) return `environ ${minutes} minute(s)`;
  const hours = Math.floor(minutes / 60);
  return `environ ${hours} h ${String(minutes % 60).padStart(2, '0')}`;
}

async function getJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data as T;
}

function StatusDot({ ok }: { ok: boolean }) {
  return <span className={`inline-block h-2 w-2 rounded-full ${ok ? 'bg-emerald-500' : 'bg-rose-500'}`} />;
}

function Metric({ label, value, tone = 'slate', hint }: {
  label: string; value: string; tone?: 'slate' | 'green' | 'orange'; hint?: string;
}) {
  const colors = tone === 'green' ? 'text-emerald-700' : tone === 'orange' ? 'text-orange-600' : 'text-slate-900';
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3">
      <p className="flex items-center gap-1 text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
        {label}{hint && <Tooltip content={hint} side="bottom" />}
      </p>
      <p className={`mt-1 text-xl font-black ${colors}`}>{value}</p>
    </div>
  );
}

function RunInspector({ run, availableTools }: { run: LiveRunResult; availableTools?: string[] }) {
  return (
    <section className="space-y-5 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-md bg-slate-900 px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-white">{run.frameworkName}</span>
            <span className="rounded bg-orange-100 px-2 py-1 font-mono text-[10px] font-bold text-orange-700">#{run.taskNumber || '—'}</span>
            <span className="font-mono text-xs text-slate-400">{run.taskId}</span>
            <span className="flex items-center gap-1 rounded bg-sky-100 px-2 py-1 font-mono text-[9px] font-bold text-sky-700">
              {run.protocolVersion || 'legacy-v1'}<Tooltip content={help('protocolVersion')} side="bottom" />
            </span>
            {run.regime === 'plan_parity' && (
              <span className="flex items-center gap-1 rounded bg-violet-100 px-2 py-1 text-[9px] font-bold uppercase tracking-wide text-violet-700">
                parité de plan
                <Tooltip content={`${help('regime')} Ce framework a reçu le plan de la tâche (${run.briefingChars ?? 0} caractères) en plus de l’énoncé.`} side="bottom" />
              </span>
            )}
          </div>
          <h2 className="mt-2 text-xl font-black text-slate-950">Ce que l’agent a réellement fait</h2>
          <p className="mt-1 text-sm text-slate-500">{run.profile}</p>
        </div>
        <div className={`flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-bold ${run.success ? 'bg-emerald-50 text-emerald-700' : run.status === 'invalid' ? 'bg-violet-50 text-violet-700' : run.error ? 'bg-rose-50 text-rose-700' : 'bg-amber-50 text-amber-700'}`}>
          {run.success ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}
          {run.success ? 'Tâche entièrement réussie' : run.status === 'invalid' ? 'Exécution invalide' : run.error ? 'Exécution en erreur' : 'Tâche partiellement réussie'}
        </div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
        <Metric label="Crédit officiel" value={pct(run.partialCredit)} tone={run.partialCredit === 1 ? 'green' : 'orange'} hint={help('partialCredit')} />
        <Metric label="Tâche complète" value={run.taskCompleted >= 1 ? 'Oui' : 'Non'} tone={run.taskCompleted >= 1 ? 'green' : 'orange'} hint={help('taskCompleted')} />
        <Metric label="Actions" value={String(run.toolCallCount)} hint={help('toolCall')} />
        <Metric label="Appels LLM" value={run.llmCallCount == null ? 'N/D' : String(run.llmCallCount)} hint={help('llmCall')} />
        <Metric label="Durée" value={ms(run.executionTimeMs)} hint="Temps total de l’exécution, pauses de quota comprises." />
        <Metric label="Tokens mesurés" value={run.tokenUsage.totalTokens == null ? 'N/D' : String(run.tokenUsage.totalTokens)} hint={help('tokens')} />
      </div>

      {run.status === 'invalid' && (
        <div className="rounded-xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-800">
          <p className="flex items-center gap-1 font-bold">Exécution exclue des moyennes<Tooltip content={help('invalidRun')} /></p>
          <p className="mt-1">{invalidReasonLabel(run.invalidReason)}</p>
        </div>
      )}

      {run.error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
          <p className="font-bold">Erreur remontée par le framework</p>
          <p className="mt-1 font-mono text-xs">{run.error}</p>
        </div>
      )}

      <RunDiagnosisPanel run={run} />

      <OfficialScorePanel run={run} />

      <div className="grid gap-5 lg:grid-cols-[1.25fr_.75fr]">
        <ActionJournal run={run} availableTools={availableTools} />

        <div className="space-y-4">
          {run.finalAnswer !== null && run.finalAnswer !== undefined && run.finalAnswer !== '' && (
            <div className="rounded-xl border border-slate-200 p-4">
              <p className="flex items-center gap-1 text-sm font-bold text-slate-800">
                Réponse finale de l’agent
                <Tooltip content="Le texte que l’agent a rendu en fin d’exécution. Il n’entre pas dans la note : seul l’état du système simulé est vérifié. Une belle réponse sans action réalisée vaut zéro." />
              </p>
              <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-600">
                {typeof run.finalAnswer === 'string' ? run.finalAnswer : json(run.finalAnswer)}
              </pre>
            </div>
          )}
          <details className="rounded-xl border border-slate-200 p-4">
            <summary className="cursor-pointer text-sm font-bold text-slate-800">État du système à la fin</summary>
            <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-5 text-slate-500">{json(run.finalState)}</pre>
          </details>
          {Object.keys(run.runtimeEvidence || {}).length > 0 && (
            <details className="rounded-xl border border-slate-200 p-4">
              <summary className="cursor-pointer text-sm font-bold text-slate-800">Traces internes du framework</summary>
              <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-5 text-slate-500">{json(run.runtimeEvidence)}</pre>
            </details>
          )}
          <details className="rounded-xl border border-sky-200 bg-sky-50/40 p-4">
            <summary className="cursor-pointer text-sm font-bold text-sky-900">Réglages du modèle</summary>
            <pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-5 text-sky-800">{json({ config: run.modelConfig, transport: run.modelTelemetry, toolSurface: run.toolSurface })}</pre>
          </details>
        </div>
      </div>
    </section>
  );
}

function RunInspectorModal({ run, availableTools, onClose }: {
  run: LiveRunResult; availableTools?: string[]; onClose: () => void;
}) {
  const containerRef = useModal<HTMLElement>(true, onClose);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-3 backdrop-blur-sm" onMouseDown={onClose}>
      <section
        ref={containerRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={`Résultat de ${run.frameworkName} sur ${run.taskTitle}`}
        className="max-h-[96vh] w-full max-w-[1450px] overflow-hidden rounded-2xl bg-[#f4f6f8] shadow-2xl outline-none"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-slate-700 bg-slate-950 px-5 py-4 text-white">
          <div className="min-w-0">
            <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-400">Preuves de l’exécution</p>
            <h2 className="truncate text-base font-black sm:text-lg">{run.frameworkName} · {run.taskTitle}</h2>
          </div>
          <button onClick={onClose} className="shrink-0 rounded-lg p-2 text-slate-400 hover:bg-white/10 hover:text-white" aria-label="Fermer le détail (Echap)" title="Fermer (Echap)">
            <X size={20} />
          </button>
        </header>
        <div className="max-h-[calc(96vh-69px)] overflow-auto p-3 sm:p-5">
          <RunInspector run={run} availableTools={availableTools} />
        </div>
      </section>
    </div>
  );
}

/** Lecture en une phrase du résultat d'une campagne. */
function campaignReading(campaign: CampaignRecord): string {
  const rows = Object.entries(campaign.matrixSummary || {})
    .map(([id, summary]) => ({ id, ...summary }))
    .filter((row) => row.averagePartialCredit != null)
    .sort((a, b) => (b.averagePartialCredit ?? 0) - (a.averagePartialCredit ?? 0));
  if (rows.length === 0) return 'Aucune exécution valide dans cette campagne : il n’y a rien à comparer.';
  const [best, second] = rows;
  if (rows.length === 1) {
    return `${best.frameworkName} : ${pct(best.averagePartialCredit)} de crédit officiel sur ${best.validRuns} exécution(s) valide(s).`;
  }
  const gap = Math.round(((best.averagePartialCredit ?? 0) - (second.averagePartialCredit ?? 0)) * 100);
  return gap === 0
    ? `${best.frameworkName} et ${second.frameworkName} sont à égalité, avec ${pct(best.averagePartialCredit)} de crédit officiel.`
    : `Sur cette campagne, ${best.frameworkName} devance ${second.frameworkName} de ${gap} point(s) : ${pct(best.averagePartialCredit)} contre ${pct(second.averagePartialCredit)}.`;
}

export default function App() {
  const [mode, setMode] = useState<'bench' | 'factory'>('bench');
  const [tasks, setTasks] = useState<BenchmarkTaskSummary[]>([]);
  const [frameworks, setFrameworks] = useState<FrameworkStatus[]>([]);
  const [health, setHealth] = useState<RunnerHealth | null>(null);
  const [taskDetails, setTaskDetails] = useState<BenchmarkTaskDetail | null>(null);
  const [taskDetailsLoading, setTaskDetailsLoading] = useState(false);
  const [frameworkCode, setFrameworkCode] = useState<FrameworkCodeBundle | null>(null);
  const [frameworkCodeLoading, setFrameworkCodeLoading] = useState(false);
  const [selectedTasks, setSelectedTasks] = useState<string[]>([]);
  const [selectedFrameworks, setSelectedFrameworks] = useState<ActiveFrameworkId[]>([]);
  const [regime, setRegime] = useState<RegimeId>('prompt_only');
  const [campaign, setCampaign] = useState<CampaignRecord | null>(null);
  const [inspectedRun, setInspectedRun] = useState<LiveRunResult | null>(null);
  const [openingRunId, setOpeningRunId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [liveEvents, setLiveEvents] = useState<LiveExecutionEvent[]>([]);
  const [liveConnected, setLiveConnected] = useState(false);
  const [averageRunMs, setAverageRunMs] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const campaignIdRef = useRef<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);

  const closeStream = useCallback(() => {
    sourceRef.current?.close();
    sourceRef.current = null;
    setLiveConnected(false);
  }, []);

  /** Récupère le résultat consolidé : c'est lui qui fait foi, pas le flux SSE. */
  const loadCampaign = useCallback(async (id: string) => {
    try {
      const record = await getJson<CampaignRecord>(`/api/benchmark/campaigns/${encodeURIComponent(id)}`);
      setCampaign(record);
    } catch (reason: any) {
      setError(`Résultat de campagne illisible : ${reason.message}`);
    }
  }, []);

  const attachStream = useCallback((id: string) => {
    closeStream();
    const source = new EventSource(`/api/benchmark/live/${encodeURIComponent(id)}`);
    sourceRef.current = source;
    source.onopen = () => setLiveConnected(true);
    source.onerror = () => setLiveConnected(false);
    source.onmessage = (event) => {
      try {
        const liveEvent = JSON.parse(event.data) as LiveExecutionEvent;
        setLiveEvents((current) => current.some((item) => item.sequence === liveEvent.sequence)
          ? current
          : [...current, liveEvent].slice(-2000));
        if (['campaign_complete', 'campaign_error', 'campaign_cancelled'].includes(liveEvent.type)) {
          closeStream();
          setRunning(false);
          setCancelling(false);
          void loadCampaign(id);
        }
      } catch {
        // Un battement de cœur ou un événement mal formé ne doit pas interrompre l'affichage.
      }
    };
  }, [closeStream, loadCampaign]);

  useEffect(() => {
    Promise.all([
      getJson<BenchmarkTaskSummary[]>('/api/tasks'),
      getJson<FrameworkStatus[]>('/api/frameworks'),
      getJson<RunnerHealth>('/api/health'),
    ]).then(([taskRows, frameworkRows, healthRow]) => {
      setTasks(taskRows);
      setFrameworks(frameworkRows);
      setHealth(healthRow);
      if (taskRows[0]) setSelectedTasks([taskRows[0].id]);
      setSelectedFrameworks(frameworkRows.filter((item) => item.available).map((item) => item.id));
    }).catch((reason) => setError(reason.message)).finally(() => setLoading(false));

    // Durée moyenne observée : sert à annoncer honnêtement le temps d'une campagne
    // avant de la lancer, plutôt que de laisser l'utilisateur découvrir une heure d'attente.
    getJson<HistoryStats>('/api/stats')
      .then((stats) => {
        const durations = stats.byFramework.map((row) => row.averageTimeMs).filter((value): value is number => typeof value === 'number');
        if (durations.length) setAverageRunMs(durations.reduce((sum, value) => sum + value, 0) / durations.length);
      })
      .catch(() => undefined);

    // Rattachement : une campagne lancée avant un rechargement de page continue
    // côté serveur. Sans cette reprise, son résultat serait invisible.
    getJson<{ campaigns: ActiveCampaign[] }>('/api/benchmark/active')
      .then(({ campaigns }) => {
        const live = campaigns.find((item) => item.kind === 'benchmark');
        if (!live) return;
        campaignIdRef.current = live.campaignId;
        setRunning(true);
        setStartedAt(new Date(live.startedAt).getTime());
        setNotice(`Une campagne lancée précédemment est toujours en cours (${live.completedRuns}/${live.totalRuns} exécutions). L’affichage s’y est rebranché.`);
        attachStream(live.campaignId);
      })
      .catch(() => undefined);

    return () => { sourceRef.current?.close(); };
  }, [attachStream]);

  const executionCount = selectedTasks.length * selectedFrameworks.length;
  const estimatedMs = executionCount * (averageRunMs ?? FALLBACK_RUN_MS);
  const runRows: HistoryRunRow[] = useMemo(() => campaign?.runs ?? [], [campaign]);
  const frameworkNames = useMemo(
    () => Object.fromEntries(frameworks.map((framework) => [framework.id, framework.name])),
    [frameworks],
  );

  const toggleTask = (id: string) => setSelectedTasks((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const toggleFramework = (id: ActiveFrameworkId) => setSelectedFrameworks((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);

  const openTaskDetails = async (taskId: string) => {
    setTaskDetailsLoading(true);
    setError(null);
    try {
      setTaskDetails(await getJson<BenchmarkTaskDetail>(`/api/tasks/${encodeURIComponent(taskId)}`));
    } catch (reason: any) {
      setError(reason.message);
    } finally {
      setTaskDetailsLoading(false);
    }
  };

  const openFrameworkCode = async (frameworkId: ActiveFrameworkId) => {
    const taskId = selectedTasks[0] || tasks[0]?.id;
    setFrameworkCode(null);
    setFrameworkCodeLoading(true);
    setError(null);
    try {
      const query = taskId ? `?taskId=${encodeURIComponent(taskId)}` : '';
      setFrameworkCode(await getJson<FrameworkCodeBundle>(`/api/frameworks/${frameworkId}/code${query}`));
    } catch (reason: any) {
      setError(reason.message);
    } finally {
      setFrameworkCodeLoading(false);
    }
  };

  const inspectRow = async (runId: string) => {
    setOpeningRunId(runId);
    setError(null);
    try {
      setInspectedRun(await getJson<LiveRunResult>(`/api/history/${encodeURIComponent(runId)}`));
    } catch (reason: any) {
      setError(`Détail indisponible : ${reason.message}`);
    } finally {
      setOpeningRunId(null);
    }
  };

  const launch = async () => {
    const campaignId = typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `campaign_${Date.now()}`;
    campaignIdRef.current = campaignId;
    setConfirming(false);
    setError(null);
    setNotice(null);
    setCampaign(null);
    setInspectedRun(null);
    setLiveEvents([]);
    setRunning(true);
    setStartedAt(Date.now());
    attachStream(campaignId);
    try {
      await getJson('/api/benchmark/run-suite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaignId, taskIds: selectedTasks, frameworkIds: selectedFrameworks, regime }),
      });
      // La requête rend la main immédiatement : la campagne vit côté serveur et
      // sa fin est annoncée par le flux. Fermer cet onglet ne l'interrompt plus.
    } catch (reason: any) {
      setError(reason.message);
      setRunning(false);
      closeStream();
    }
  };

  const cancel = async () => {
    const id = campaignIdRef.current;
    if (!id) return;
    setCancelling(true);
    try {
      await getJson('/api/benchmark/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaignId: id }),
      });
      setNotice('Arrêt demandé. Les exécutions déjà terminées sont conservées et restent consultables.');
    } catch (reason: any) {
      setError(reason.message);
      setCancelling(false);
    }
  };

  const firstRunHint = tasks.length > 0 && campaign === null && !running;

  return (
    <div className="min-h-screen bg-[#f4f6f8] text-slate-800 selection:bg-orange-500 selection:text-white">
      <header className="border-b border-slate-800 bg-[#101722] text-white">
        <div className="mx-auto flex max-w-[1500px] flex-col gap-4 px-5 py-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            <div className="grid h-11 w-11 place-items-center rounded-xl bg-orange-500 shadow-lg shadow-orange-950/30"><FlaskConical size={23} /></div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg font-black tracking-tight">AITESTPLATFORM</h1>
                <span className="flex items-center gap-1 rounded bg-white/10 px-2 py-0.5 text-[9px] font-bold uppercase tracking-[.18em] text-slate-300">
                  {health?.protocolVersion ? 'protocole V3' : 'protocole'}
                  <Tooltip content={help('protocolVersion')} side="bottom" />
                </span>
              </div>
              <p className="text-xs text-slate-400">Comparer des agents sur des tâches métier réelles, avec des preuves</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2">
              <StatusDot ok={health?.apiKeyConfigured === true} /> Gemini API
              <Tooltip content={health?.apiKeyConfigured ? 'La clé d’accès au modèle est configurée : les exécutions sont possibles.' : 'Aucune clé d’accès au modèle : les exécutions échoueront.'} side="bottom" />
            </span>
            <span className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2">
              <Cpu size={14} className="text-orange-400" />{health?.model || 'gemini-3.1-flash-lite'}
              <Tooltip content="Le même modèle est imposé aux quatre frameworks, avec les mêmes réglages. Sans cela, on comparerait des modèles et non des architectures d’agents." side="bottom" />
            </span>
            <span className="flex items-center gap-2 rounded-lg border border-emerald-800 bg-emerald-950/50 px-3 py-2 text-emerald-300">
              <ShieldCheck size={14} />Aucun résultat simulé
              <Tooltip content="Chaque chiffre affiché provient d’une exécution réelle notée par le barème officiel d’AutomationBench. Rien n’est estimé ni reconstitué." side="bottom" />
            </span>
          </div>
        </div>
      </header>

      <nav className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-[1500px] gap-1 px-5">
          {([['bench', 'Comparer des runtimes'], ['factory', 'Créer un agent']] as const).map(([id, label]) => (
            <button key={id} onClick={() => setMode(id)}
              className={`border-b-2 px-4 py-3 text-sm font-black transition ${mode === id ? 'border-orange-500 text-slate-950' : 'border-transparent text-slate-400 hover:text-slate-600'}`}>
              {label}
            </button>
          ))}
        </div>
      </nav>

      <main className="mx-auto max-w-[1500px] space-y-6 px-5 py-6">
        {mode === 'factory' ? <ErrorBoundary label="fabrique d’agents"><FactoryPanel /></ErrorBoundary> : <>
        {error && <div className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800"><XCircle className="mt-0.5 shrink-0" size={18} /><div><p className="font-bold">Impossible de poursuivre</p><p>{error}</p></div></div>}
        {notice && <div className="flex items-start gap-3 rounded-xl border border-sky-200 bg-sky-50 p-4 text-sm text-sky-900"><Activity className="mt-0.5 shrink-0" size={18} /><p>{notice}</p></div>}

        <section className="grid gap-4 lg:grid-cols-[1.45fr_.55fr]">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <div className="flex items-start gap-3">
              <GitBranch className="mt-0.5 text-orange-500" size={20} />
              <div>
                <h2 className="font-black text-slate-950">À quoi sert cet écran</h2>
                <p className="mt-1 text-sm leading-6 text-slate-500">
                  Vous choisissez des tâches métier et des frameworks d’agents, la plateforme les exécute
                  <span className="font-semibold text-slate-700"> pour de vrai</span> sur les mêmes données de départ et
                  les mêmes outils, puis les note avec le barème officiel d’AutomationBench.
                  Ni l’état attendu ni les vérifications ne sont montrés aux agents.
                </p>
              </div>
            </div>
          </div>
          <div className="rounded-2xl border border-amber-200 bg-amber-50 p-5">
            <p className="flex items-center gap-1 text-xs font-black uppercase tracking-[.15em] text-amber-800">
              À savoir avant de conclure<Tooltip content={help('agentFile')} side="bottom" />
            </p>
            <p className="mt-2 text-xs leading-5 text-amber-900">
              La comparaison est volontairement asymétrique : AGENT-L exécute un programme écrit pour la tâche,
              les trois autres travaillent depuis l’énoncé seul. Les exécutions techniquement invalides
              sont retirées des moyennes.
            </p>
          </div>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="mb-4 flex items-center justify-between gap-4">
            <div>
              <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-500">01 · Frameworks</p>
              <h2 className="mt-1 text-lg font-black text-slate-950">Qui participe à la comparaison</h2>
            </div>
            <span className="text-xs text-slate-400">{selectedFrameworks.length} sélectionné(s)</span>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            {frameworks.map((framework) => {
              const selected = selectedFrameworks.includes(framework.id);
              return (
                <button key={framework.id} disabled={!framework.available || running} onClick={() => toggleFramework(framework.id)}
                  title={framework.available ? `${framework.name} — cliquer pour ${selected ? 'retirer de' : 'ajouter à'} la comparaison` : 'Indisponible : dépendance ou clé manquante'}
                  className={`rounded-xl border p-4 text-left transition ${selected ? 'border-orange-500 bg-orange-50 ring-2 ring-orange-100' : 'border-slate-200 bg-slate-50 hover:border-slate-300'} disabled:cursor-not-allowed disabled:opacity-50`}>
                  <div className="flex items-start justify-between gap-3"><div className="grid h-9 w-9 place-items-center rounded-lg bg-slate-900 text-white"><Cpu size={17} /></div><StatusDot ok={framework.available} /></div>
                  <p className="mt-3 text-sm font-black text-slate-950">{framework.name}</p>
                  <p className="mt-1 min-h-8 text-[11px] leading-4 text-slate-500">{framework.kind}</p>
                  <div className="mt-3 flex items-center justify-between text-[10px] text-slate-400"><span>{framework.version || 'non installé'}</span><span>{framework.available ? 'prêt' : !framework.installed ? 'dépendance absente' : 'clé absente'}</span></div>
                  <span role="button" tabIndex={0} title="Afficher le code réellement exécuté par ce framework"
                    onClick={(event) => { event.stopPropagation(); openFrameworkCode(framework.id); }}
                    onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); openFrameworkCode(framework.id); } }}
                    className="mt-3 flex items-center gap-1.5 border-t border-slate-200 pt-3 text-[11px] font-black text-orange-600"><Code2 size={14} />Voir le code exécuté</span>
                </button>
              );
            })}
          </div>
        </section>

        <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between gap-4 border-b border-slate-200 p-5">
            <div>
              <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-500">02 · Tâches</p>
              <h2 className="mt-1 text-lg font-black text-slate-950">Sur quoi les mettre à l’épreuve</h2>
            </div>
            <span className="rounded-lg bg-slate-100 px-3 py-2 text-xs font-bold text-slate-600">{selectedTasks.length} / {tasks.length}</span>
          </div>
          {firstRunHint && (
            <div className="flex items-start gap-3 border-b border-slate-100 bg-sky-50/60 px-5 py-3 text-xs text-sky-900">
              <Lightbulb size={15} className="mt-0.5 shrink-0 text-sky-600" />
              <p>
                <span className="font-bold">Conseil pour un premier essai :</span> gardez une seule tâche et les quatre frameworks.
                Vous obtenez une comparaison lisible en {durationLabel(4 * (averageRunMs ?? FALLBACK_RUN_MS))} au lieu d’attendre des heures.
                Ajoutez des tâches ensuite : une conclusion sur une seule tâche n’est pas généralisable.
              </p>
            </div>
          )}
          {loading ? <div className="grid place-items-center p-16 text-slate-400"><Loader2 className="animate-spin" /></div> : (
            <div className="max-h-[520px] overflow-auto">
              {tasks.map((task) => {
                const selected = selectedTasks.includes(task.id);
                return (
                  <button key={task.id} disabled={running} onClick={() => toggleTask(task.id)} className={`grid w-full grid-cols-[28px_1fr] gap-3 border-b border-slate-100 p-4 text-left transition last:border-0 hover:bg-slate-50 lg:grid-cols-[28px_1fr_100px_180px_140px_110px] ${selected ? 'bg-orange-50/60' : ''}`}>
                    <span className={`mt-1 grid h-5 w-5 place-items-center rounded border ${selected ? 'border-orange-500 bg-orange-500 text-white' : 'border-slate-300 bg-white'}`}>{selected && <CheckCircle2 size={13} />}</span>
                    <span><span className="flex flex-wrap items-center gap-2"><span className="text-sm font-bold text-slate-900">{task.title}</span><span className="rounded bg-orange-100 px-1.5 py-0.5 font-mono text-[9px] font-bold text-orange-700">#{task.number}</span><span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[9px] text-slate-500">{task.id}</span></span><span className="mt-1 block line-clamp-2 text-xs leading-5 text-slate-500">{task.trigger}</span></span>
                    <span className="hidden self-center text-xs font-bold uppercase text-slate-500 lg:block">{task.domain}</span>
                    <span className="hidden self-center text-xs text-slate-500 lg:flex lg:items-center lg:gap-1.5" title="Outils communs exposés aux agents / outils officiels de la tâche"><Wrench size={13} />{task.runtimeToolCount} outils · {task.toolCount} officiels</span>
                    <span className="hidden self-center text-xs text-slate-500 lg:flex lg:items-center lg:gap-1.5" title="Nombre de vérifications automatiques appliquées au résultat"><ShieldCheck size={13} />{task.assertionCount} vérifications</span>
                    <span role="button" tabIndex={0} title="Voir l’énoncé, les outils et les vérifications de cette tâche"
                      onClick={(event) => { event.stopPropagation(); openTaskDetails(task.id); }}
                      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); openTaskDetails(task.id); } }}
                      className="hidden self-center text-xs font-bold text-orange-600 lg:flex lg:items-center lg:gap-1.5"><Eye size={13} />Détails</span>
                  </button>
                );
              })}
            </div>
          )}
        </section>

        <ErrorBoundary label="régime de comparaison">
          <RegimePicker
            regimes={health?.regimes ?? []} value={regime} onChange={setRegime}
            disabled={running} taskId={selectedTasks[0] || tasks[0]?.id}
          />
        </ErrorBoundary>

        <section className="sticky bottom-4 z-20 flex flex-col gap-3 rounded-2xl border border-slate-700 bg-slate-950 p-4 text-white shadow-2xl lg:flex-row lg:items-center lg:justify-between">
          <div className="flex items-center gap-3">
            <Activity className="text-orange-500" />
            <div>
              <p className="flex items-center gap-1 text-sm font-bold">
                {executionCount} exécution(s) réelle(s) · {durationLabel(estimatedMs)}
                <Tooltip content={`${help('serialized')} Estimation calculée sur la durée moyenne réellement observée dans l’historique${averageRunMs == null ? ' (aucune mesure disponible pour l’instant : estimation par défaut)' : ''}.`} side="top" />
              </p>
              <p className="text-xs text-slate-400">
                {regimeName(health?.regimes, regime)} · même modèle · raisonnement {health?.modelConfig.thinkingLevel || 'low'} · {health?.modelConfig.maxOutputTokens || 8192} tokens max · appels facturés au fournisseur
              </p>
            </div>
          </div>
          <button
            onClick={() => (executionCount > 4 || estimatedMs > 10 * 60_000 ? setConfirming(true) : launch())}
            disabled={running || executionCount === 0 || executionCount > 40}
            className="flex min-w-52 items-center justify-center gap-2 rounded-xl bg-orange-500 px-5 py-3 text-sm font-black transition hover:bg-orange-400 disabled:cursor-not-allowed disabled:bg-slate-700"
          >
            {running ? <><Loader2 size={17} className="animate-spin" />Comparaison en cours…</> : <><Play size={17} fill="currentColor" />Lancer la comparaison</>}
          </button>
        </section>

        {confirming && (
          <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/70 p-4 backdrop-blur-sm" onMouseDown={() => setConfirming(false)}>
            <div role="dialog" aria-modal="true" aria-label="Confirmer le lancement" className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
              <h2 className="text-lg font-black text-slate-950">Lancer {executionCount} exécutions réelles ?</h2>
              <ul className="mt-3 space-y-2 text-sm text-slate-600">
                <li>• Durée estimée : <span className="font-bold text-slate-900">{durationLabel(estimatedMs)}</span>, les exécutions étant faites une par une pour respecter le quota du fournisseur.</li>
                <li>• Chaque exécution déclenche de <span className="font-bold text-slate-900">vrais appels au modèle</span>, qui sont facturés.</li>
                <li>• Vous pouvez arrêter la campagne à tout moment ; les exécutions déjà terminées sont conservées.</li>
                <li>• Fermer cet onglet n’interrompt rien : le résultat reste récupérable au retour.</li>
                <li>• Régime : <span className="font-bold text-slate-900">{regimeName(health?.regimes, regime)}</span>. Les résultats ne se compareront qu’aux campagnes du même régime.</li>
              </ul>
              <div className="mt-5 flex justify-end gap-2">
                <button onClick={() => setConfirming(false)} className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-600 hover:bg-slate-50">Annuler</button>
                <button onClick={launch} className="rounded-lg bg-orange-500 px-4 py-2 text-sm font-black text-white hover:bg-orange-400">Lancer quand même</button>
              </div>
            </div>
          </div>
        )}

        <ErrorBoundary label="exécution en direct">
          <LiveExecutionPanel
            events={liveEvents} running={running} connected={liveConnected}
            frameworkNames={frameworkNames} onCancel={cancel} cancelling={cancelling} startedAt={startedAt}
          />
        </ErrorBoundary>

        {campaign && (
          <section className="space-y-4">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-500">Résultat de la campagne</p>
                <h2 className="mt-1 text-xl font-black text-slate-950">{campaign.name}</h2>
              </div>
              <p className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
                <span className={`rounded px-2 py-1 text-[10px] font-bold uppercase tracking-wide ${campaign.regime === 'plan_parity' ? 'bg-violet-100 text-violet-700' : 'bg-slate-100 text-slate-600'}`}>
                  {regimeName(health?.regimes, campaign.regime || 'prompt_only')}
                </span>
                <span>
                  {campaign.status === 'cancelled' ? 'Campagne arrêtée avant la fin' : campaign.status === 'error' ? 'Campagne interrompue par une erreur' : 'Campagne terminée'}
                  {' · '}{new Date(campaign.createdAt).toLocaleString('fr-FR')}
                </span>
              </p>
            </div>

            <div className="flex items-start gap-3 rounded-2xl border border-sky-200 bg-sky-50 p-4">
              <Trophy className="mt-0.5 shrink-0 text-sky-600" size={18} />
              <p className="text-sm font-bold leading-6 text-sky-950">{campaignReading(campaign)}</p>
            </div>

            {campaign.status === 'cancelled' && (
              <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
                Cette campagne a été arrêtée : les moyennes ci-dessous ne portent que sur les exécutions
                qui ont eu le temps de se terminer.
              </p>
            )}

            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {Object.entries(campaign.matrixSummary || {}).map(([id, summary]) => (
                <div key={id} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <div className="flex items-center justify-between"><p className="font-black text-slate-950">{summary.frameworkName}</p><span className="text-xs text-slate-400">{summary.validRuns}/{summary.totalRuns} valide(s)</span></div>
                  <p className="mt-4 text-4xl font-black tracking-tight text-slate-950">{pct(summary.averagePartialCredit)}</p>
                  <p className="flex items-center gap-1 text-xs text-slate-400">crédit officiel moyen<Tooltip content={help('partialCredit')} /></p>
                  {summary.invalidRuns > 0 && <p className="mt-2 rounded-lg bg-violet-50 px-2 py-1 text-[10px] font-bold text-violet-700">{summary.invalidRuns} exécution(s) invalide(s), exclue(s) des moyennes</p>}
                  <div className="mt-4 grid grid-cols-4 gap-2 text-center">
                    <div className="rounded-lg bg-emerald-50 p-2"><p className="text-sm font-black text-emerald-700">{pct(summary.officialSuccessRate)}</p><p className="text-[9px] uppercase text-emerald-600">succès</p></div>
                    <div className="rounded-lg bg-slate-50 p-2"><p className="text-sm font-black">{avg(summary.averageToolCalls)}</p><p className="text-[9px] uppercase text-slate-500">actions</p></div>
                    <div className="rounded-lg bg-sky-50 p-2"><p className="text-sm font-black text-sky-700">{avg(summary.averageLlmCalls)}</p><p className="text-[9px] uppercase text-sky-600">LLM</p></div>
                    <div className="rounded-lg bg-slate-50 p-2"><p className="text-sm font-black">{ms(summary.averageTimeMs)}</p><p className="text-[9px] uppercase text-slate-500">durée</p></div>
                  </div>
                </div>
              ))}
            </div>

            <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              {runRows.map((run) => (
                <button key={run.id} onClick={() => inspectRow(run.id)} disabled={openingRunId !== null}
                  className={`grid w-full grid-cols-[1fr_auto] items-center gap-4 border-b border-slate-100 p-4 text-left last:border-0 hover:bg-slate-50 disabled:opacity-60 ${inspectedRun?.id === run.id ? 'bg-orange-50' : ''}`}>
                  <span>
                    <span className="flex flex-wrap items-center gap-2"><span className="font-bold text-slate-950">{run.frameworkName}</span><span className="text-sm font-semibold text-slate-700">· {run.taskTitle}</span><span className="font-mono text-[10px] text-slate-400">{run.taskId}</span></span>
                    <span className="mt-1 flex flex-wrap gap-3 text-xs text-slate-500">
                      <span className="font-black text-slate-800">{pct(run.partialCredit)} de crédit</span>
                      {run.assertionsEvaluated != null && run.assertionsFailed != null && (
                        <span className={run.assertionsFailed > 0 ? 'font-semibold text-rose-600' : 'font-semibold text-emerald-600'}>
                          {run.assertionsEvaluated - run.assertionsFailed}/{run.assertionsEvaluated} validées
                          {run.assertionsFailed > 0 ? ` · ${run.assertionsFailed} à corriger` : ''}
                        </span>
                      )}
                      <span>{run.toolCallCount ?? 'N/D'} actions</span>
                      <span>{run.llmCallCount ?? 'N/D'} LLM</span>
                      <span>{ms(run.executionTimeMs)}</span>
                      {run.status === 'invalid' && <span className="font-bold text-violet-600">invalide · exclu</span>}
                      {run.error && <span className="text-rose-600">{run.error}</span>}
                    </span>
                  </span>
                  <span className="flex items-center gap-3">
                    <span className="hidden text-xs font-bold text-orange-600 sm:inline">{run.success ? 'Voir les preuves' : 'Comprendre pourquoi'}</span>
                    {openingRunId === run.id ? <Loader2 size={18} className="animate-spin text-slate-400" />
                      : run.success ? <CheckCircle2 className="text-emerald-500" size={19} />
                        : <CircleAlert className={run.status === 'invalid' ? 'text-violet-500' : run.error ? 'text-rose-500' : 'text-amber-500'} size={19} />}
                    <ChevronRight size={17} className="text-slate-300" />
                  </span>
                </button>
              ))}
            </div>
          </section>
        )}

        {inspectedRun && (
          <ErrorBoundary label="détail d’exécution">
            <RunInspectorModal
              run={inspectedRun}
              availableTools={tasks.find((task) => task.id === inspectedRun.taskId)?.runtimeTools}
              onClose={() => setInspectedRun(null)}
            />
          </ErrorBoundary>
        )}

        <ErrorBoundary label="bilan global"><StatsPanel refreshKey={campaign?.campaignId || ''} /></ErrorBoundary>
        <ErrorBoundary label="historique"><HistoryPanel refreshKey={campaign?.campaignId || ''} onInspect={setInspectedRun} /></ErrorBoundary>

        <TaskDetailsModal detail={taskDetails} loading={taskDetailsLoading} onClose={() => setTaskDetails(null)} />
        <FrameworkCodeModal bundle={frameworkCode} loading={frameworkCodeLoading} onClose={() => { setFrameworkCode(null); setFrameworkCodeLoading(false); }} />
        </>}
      </main>

      <footer className="mt-10 border-t border-slate-200 bg-white px-5 py-5 text-xs text-slate-400">
        <div className="mx-auto flex max-w-[1500px] flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <span className="flex items-center gap-2"><Database size={13} />Données de test publiques AutomationBench</span>
          <span className="flex items-center gap-1.5"><Clock3 size={13} />Une mesure absente reste « N/D » — elle n’est jamais estimée<Tooltip content={help('nd')} /></span>
        </div>
      </footer>
    </div>
  );
}
