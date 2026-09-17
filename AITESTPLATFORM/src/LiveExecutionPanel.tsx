import { useEffect, useMemo, useRef, useState } from 'react';
import { Activity, CheckCircle2, CircleAlert, Clock3, FileCode2, Loader2, Radio, Square, TerminalSquare, Wrench } from 'lucide-react';
import type { ActiveFrameworkId, LiveExecutionEvent } from './types';
import { Tooltip } from './Tooltip';

const terminalTypes = new Set(['run_complete', 'run_error']);

/**
 * Traduction des types d'événements techniques en langage compréhensible.
 * Le flux brut est destiné aux journaux ; l'écran, lui, est lu par des gens qui
 * veulent savoir où en est leur campagne.
 */
const EVENT_LABELS: Record<string, string> = {
  campaign_queued: 'Campagne placée en file',
  campaign_start: 'Campagne démarrée',
  campaign_complete: 'Campagne terminée',
  campaign_error: 'Campagne interrompue',
  campaign_cancelled: 'Campagne annulée',
  run_queued: 'Exécution en attente',
  run_start: 'Exécution démarrée',
  run_complete: 'Exécution terminée',
  run_error: 'Exécution en erreur',
  runtime_start: 'Démarrage du framework',
  runtime_end: 'Framework terminé',
  runtime_error: 'Framework en erreur',
  llm_start: 'Question envoyée au modèle',
  llm_end: 'Réponse du modèle reçue',
  llm_error: 'Appel au modèle en erreur',
  tool_start: 'Action sur le système',
  tool_end: 'Action terminée',
  scoring_start: 'Notation par le barème officiel',
  scoring_end: 'Note officielle calculée',
};

/**
 * Types dont le message porte déjà l'information utile : le traduire par un
 * libellé générique reviendrait à effacer précisément ce qu'on veut montrer.
 * `codegen_step` dit « lit SKILL.md » ; « Étape de rédaction » ne dirait rien.
 */
const SELF_DESCRIBING = new Set(['codegen_step', 'codegen_start', 'codegen_end', 'build_attempt']);

function timeLabel(timestamp: string): string {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleTimeString('fr-FR');
}

function durationLabel(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${String(seconds % 60).padStart(2, '0')} s`;
}

function eventIcon(event: LiveExecutionEvent) {
  if (event.type === 'codegen_step') return <FileCode2 size={13} />;
  if (event.type === 'tool_start' || event.type === 'tool_end') return <Wrench size={14} />;
  if (event.type.includes('error') || event.type === 'campaign_cancelled') return <CircleAlert size={14} />;
  if (event.type === 'run_complete' || event.type === 'campaign_complete') return <CheckCircle2 size={14} />;
  if (event.type === 'llm_start' || event.type === 'llm_end') return <Activity size={14} />;
  return <Clock3 size={14} />;
}

const STATUS_LABELS: Record<string, string> = {
  queued: 'en attente',
  running: 'en cours',
  completed: 'terminée',
  failed: 'en échec',
  cancelled: 'annulée',
};

export function LiveExecutionPanel({
  events,
  running,
  connected,
  frameworkNames,
  onCancel,
  cancelling = false,
  startedAt,
  title,
}: {
  events: LiveExecutionEvent[];
  running: boolean;
  connected: boolean;
  frameworkNames: Partial<Record<ActiveFrameworkId, string>> | Record<string, string>;
  onCancel?: () => void;
  cancelling?: boolean;
  startedAt?: number | null;
  title?: string;
}) {
  const timelineRef = useRef<HTMLDivElement>(null);
  const [now, setNow] = useState(() => Date.now());

  const runs = useMemo(() => {
    // Une fabrique n'émet ni `run_start` ni `run_complete` : son cycle de vie
    // passe par la campagne, qui ne porte pas de `runId`. Sans ce report, la
    // carte affichait « en attente » pendant qu'un agent écrivait, puis restait
    // « en cours » une fois la campagne terminée.
    let outcome: string | null = null;
    for (const event of events) {
      if (event.type === 'campaign_complete') outcome = 'completed';
      if (event.type === 'campaign_error') outcome = 'failed';
      if (event.type === 'campaign_cancelled') outcome = 'cancelled';
    }

    const result = new Map<string, {
      runId: string; taskId: string; frameworkId: string; status: string; message: string;
      attempt?: string; step?: string; turn?: number; maxTurns?: number; steps: number;
    }>();
    for (const event of events) {
      if (!event.runId) continue;
      const current = result.get(event.runId) || {
        runId: event.runId,
        taskId: event.taskId || '—',
        frameworkId: event.frameworkId || '—',
        status: 'queued',
        message: 'En attente',
        steps: 0,
      };
      if (event.type === 'run_start' || event.type === 'runtime_start') current.status = 'running';
      if (event.type === 'run_complete') current.status = event.status === 'failed' ? 'failed' : 'completed';
      if (event.type === 'run_error') current.status = 'failed';
      if (event.type === 'campaign_cancelled' && current.status === 'running') current.status = 'cancelled';

      if (event.type === 'build_attempt') {
        current.status = 'running';
        current.attempt = event.message;
        current.step = undefined;
        current.steps = 0;
      } else if (event.type === 'codegen_start') {
        current.status = 'running';
        current.message = event.message;
        current.maxTurns = event.maxTurns;
        current.step = undefined;
      } else if (event.type === 'codegen_step') {
        // L'opération vit à part du message de phase : elle se remplace au lieu
        // de s'empiler, pour que la ligne de suivi reste une ligne.
        current.status = 'running';
        current.step = event.message;
        current.turn = event.turn;
        current.steps = event.step ?? current.steps + 1;
      } else if (event.type === 'codegen_end') {
        current.message = event.message;
        current.step = undefined;
      } else {
        current.message = event.message;
      }
      result.set(event.runId, current);
    }

    const runsList = [...result.values()];
    if (outcome) {
      for (const run of runsList) {
        if (run.status === 'running' || run.status === 'queued') run.status = outcome;
      }
    }
    return runsList;
  }, [events]);

  useEffect(() => {
    const node = timelineRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [events]);

  // Une campagne dure des dizaines de minutes : sans compteur qui avance,
  // l'utilisateur conclut que la plateforme est bloquée.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  if (!running && events.length === 0) return null;

  const active = runs.filter((run) => run.status === 'running').length;
  const finished = runs.filter((run) => ['completed', 'failed', 'cancelled'].includes(run.status)).length;
  const progress = runs.length ? Math.round((finished / runs.length) * 100) : 0;
  const elapsed = startedAt ? now - startedAt : null;
  const perRun = finished > 0 && elapsed ? elapsed / finished : null;
  const remaining = perRun != null ? perRun * (runs.length - finished) : null;

  return (
    <section className="overflow-hidden rounded-2xl border border-slate-700 bg-slate-950 text-white shadow-xl">
      <div className="flex flex-col gap-3 border-b border-slate-800 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <span className="relative grid h-9 w-9 place-items-center rounded-lg bg-orange-500/15 text-orange-400">
            <Radio size={18} />
            {running && <span className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-pulse rounded-full bg-orange-500" />}
          </span>
          <div>
            <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-400">Exécution en direct</p>
            <h2 className="font-black">{title || (running ? 'Campagne en cours' : 'Journal de la dernière campagne')}</h2>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[10px] font-bold">
          <span className={`flex items-center gap-1 rounded-full px-2.5 py-1 ${connected ? 'bg-emerald-500/15 text-emerald-300' : 'bg-slate-800 text-slate-400'}`}>
            {connected ? 'flux connecté' : 'flux fermé'}
            <Tooltip
              side="bottom"
              content={connected
                ? 'La page reçoit les étapes en direct depuis le serveur.'
                : 'Le flux temps réel est fermé. La campagne continue côté serveur : son résultat sera récupérable même si cette page est rechargée.'}
            />
          </span>
          <span className="rounded-full bg-slate-800 px-2.5 py-1 text-slate-300">{active} en cours</span>
          <span className="rounded-full bg-slate-800 px-2.5 py-1 text-slate-300">{finished}/{runs.length} terminée(s)</span>
          {elapsed != null && <span className="rounded-full bg-slate-800 px-2.5 py-1 text-slate-300">{durationLabel(elapsed)} écoulées</span>}
          {running && onCancel && (
            <button
              onClick={onCancel}
              disabled={cancelling}
              title="Arrêter la campagne. Les exécutions déjà terminées sont conservées."
              className="flex items-center gap-1.5 rounded-full border border-rose-500/40 bg-rose-500/10 px-3 py-1 font-bold text-rose-300 transition hover:bg-rose-500/20 disabled:opacity-50"
            >
              {cancelling ? <Loader2 size={12} className="animate-spin" /> : <Square size={11} fill="currentColor" />}
              {cancelling ? 'Arrêt en cours…' : 'Arrêter'}
            </button>
          )}
        </div>
      </div>

      {runs.length > 0 && (
        <div className="border-b border-slate-800 px-5 py-3">
          <div className="flex items-center justify-between text-[10px] font-bold text-slate-400">
            <span>Progression {progress} %</span>
            {running && remaining != null && finished > 0 && (
              <span>≈ {durationLabel(remaining)} restantes, estimé sur le rythme observé</span>
            )}
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-800">
            <div className="h-full bg-orange-500 transition-all duration-500" style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}

      <div className="grid lg:grid-cols-[.8fr_1.2fr]">
        <div className="max-h-[420px] space-y-2 overflow-auto border-b border-slate-800 p-4 lg:border-b-0 lg:border-r">
          {runs.length === 0 && <p className="p-3 text-xs text-slate-500">Connexion au flux de campagne…</p>}
          {runs.map((run) => {
            const frameworkName = (frameworkNames as Record<string, string>)[run.frameworkId] || run.frameworkId;
            return (
              <div key={run.runId} className={`rounded-xl border p-3 ${run.status === 'running' ? 'border-orange-500/50 bg-orange-500/10' : run.status === 'failed' ? 'border-rose-500/40 bg-rose-500/10' : run.status === 'completed' ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-slate-800 bg-slate-900'}`}>
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs font-black">{frameworkName}</p>
                  <span className="flex items-center gap-1.5 text-[9px] uppercase text-slate-400">
                    {STATUS_LABELS[run.status] || run.status}
                    {run.status === 'running' ? <Loader2 size={14} className="animate-spin text-orange-400" /> : run.status === 'completed' ? <CheckCircle2 size={14} className="text-emerald-400" /> : run.status === 'failed' ? <CircleAlert size={14} className="text-rose-400" /> : <Clock3 size={14} className="text-slate-500" />}
                  </span>
                </div>
                <p className="mt-1 truncate font-mono text-[10px] text-slate-400">{run.taskId}</p>
                {/* Une seule ligne, toujours : la phase, la tentative, puis
                    l'opération en cours. `truncate` garantit qu'aucune cible
                    longue ne provoque de défilement horizontal ; le texte
                    entier reste lisible au survol. */}
                <p
                  className="mt-2 truncate text-[10px] leading-4 text-slate-300"
                  title={[run.message, run.attempt, run.step].filter(Boolean).join(' · ')}
                >
                  <span className="text-slate-300">{run.message}</span>
                  {run.attempt && <span className="text-slate-500"> · {run.attempt}</span>}
                  {run.turn != null && (
                    <span className="text-slate-500"> · tour {run.turn}{run.maxTurns ? `/${run.maxTurns}` : ''}</span>
                  )}
                  {run.step && (
                    <>
                      <span className="text-slate-600"> · </span>
                      <span className="font-mono text-orange-300">{run.step}</span>
                    </>
                  )}
                </p>
                {run.status === 'running' && run.steps > 0 && (
                  <p className="mt-1 text-[9px] text-slate-500">{run.steps} opération(s)</p>
                )}
              </div>
            );
          })}
        </div>

        <div ref={timelineRef} className="max-h-[420px] overflow-auto p-4 font-mono">
          {events.length === 0 && <div className="flex items-center gap-2 p-3 text-xs text-slate-500"><Loader2 size={14} className="animate-spin" />En attente du premier événement…</div>}
          <div className="space-y-1.5">
            {events.map((event) => {
              const error = event.type.includes('error');
              const done = terminalTypes.has(event.type) || event.type === 'campaign_complete';
              const step = event.type === 'codegen_step';
              const label = SELF_DESCRIBING.has(event.type)
                ? event.message
                : EVENT_LABELS[event.type] || event.message;
              return (
                <div key={event.sequence} className={`grid grid-cols-[64px_18px_1fr] gap-2 rounded-lg px-2 py-1.5 text-[11px] ${error ? 'bg-rose-500/10 text-rose-300' : done ? 'bg-emerald-500/5 text-emerald-300' : step ? 'text-slate-400' : 'text-slate-300'}`}>
                  <span className="text-slate-600">{timeLabel(event.timestamp)}</span>
                  <span className={error ? 'text-rose-400' : step ? 'text-slate-600' : 'text-orange-400'}>{eventIcon(event)}</span>
                  {/* `min-w-0` + `truncate` : une cible longue est coupée, elle
                      n'élargit jamais la colonne — pas de défilement horizontal. */}
                  <span className="min-w-0 truncate" title={label}>
                    {step
                      ? <span className="mr-2 text-slate-600">{event.turn != null ? `t${event.turn}` : '·'}</span>
                      : <span className="mr-2 text-slate-500">{(frameworkNames as Record<string, string>)[String(event.frameworkId)] || event.frameworkId || 'campagne'}</span>}
                    {label}
                    {!step && event.tool ? <span className="ml-2 text-slate-400">{String(event.tool)}</span> : null}
                    {event.durationMs != null && <span className="ml-2 text-slate-600">{durationLabel(event.durationMs)}</span>}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="mt-3 flex items-center gap-2 border-t border-slate-800 pt-3 text-[10px] text-slate-600">
            <TerminalSquare size={12} />Étapes opérationnelles uniquement — le contenu des réponses du modèle n’est jamais journalisé.
          </div>
        </div>
      </div>
    </section>
  );
}
