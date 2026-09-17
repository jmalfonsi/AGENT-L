/**
 * Cycle de vie des campagnes : persistance côté Python, registre des campagnes
 * vivantes dans le processus, et annulation.
 *
 * Pourquoi : une campagne dure jusqu'à dix heures. Tant que son état ne vivait
 * que dans la réponse HTTP ouverte, un rechargement de page ou un reverse-proxy
 * impatient rendait invisible un travail réellement effectué et facturé.
 */

import { callRunner, type RunnerHandle } from './runner_process';

export type CampaignKind = 'benchmark' | 'factory';
export type CampaignStatus = 'running' | 'completed' | 'error' | 'cancelled';

export interface CampaignRecord {
  campaignId: string;
  name: string;
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
  status: CampaignStatus;
  kind: CampaignKind;
  taskIds: string[];
  frameworkIds: string[];
  runIds: string[];
  matrixSummary: Record<string, any> | null;
  protocolVersion: string | null;
  executionMode: string;
  scorer: string | null;
  error: string | null;
  projectId: string | null;
  /** Régime de comparaison de la campagne ; jamais mélangé avec un autre. */
  regime: string;
  /** Résultat complet de la fabrique, récupérable après un rechargement. */
  build?: Record<string, any> | null;
}

export interface ActiveCampaign {
  campaignId: string;
  kind: CampaignKind;
  startedAt: string;
  totalRuns: number;
  completedRuns: number;
  currentRun: { runId?: string; taskId?: string; frameworkId?: string } | null;
  cancelRequested: boolean;
  cancelReason: string | null;
  /** Évite de publier deux fois `campaign_cancelled` sur le flux SSE. */
  cancelAnnounced: boolean;
  projectId: string | null;
  handle: RunnerHandle | null;
}

const active = new Map<string, ActiveCampaign>();

const CAMPAIGN_IO_TIMEOUT_MS = Number(process.env.AGENT_CAMPAIGN_IO_TIMEOUT_MS || 120_000);

// --- Persistance -----------------------------------------------------------

// Les écritures sont sérialisées : `campaign-save` fusionne avec l'existant, et
// deux sauvegardes concurrentes de la même campagne pourraient se doubler.
let saveQueue: Promise<unknown> = Promise.resolve();

/**
 * Persiste l'enregistrement de campagne. Ne relance jamais l'erreur vers la
 * campagne en cours : perdre la trace est ennuyeux, interrompre des runs réels
 * déjà payés le serait bien davantage.
 */
export function persistCampaign(record: CampaignRecord): Promise<CampaignRecord | null> {
  const payload = JSON.stringify({ ...record, updatedAt: new Date().toISOString() });
  const next = saveQueue.then(
    () => saveCampaignNow(payload),
    () => saveCampaignNow(payload),
  );
  saveQueue = next.then(() => undefined, () => undefined);
  return next;
}

async function saveCampaignNow(payload: string): Promise<CampaignRecord | null> {
  try {
    return await callRunner(
      ['campaign-save'], CAMPAIGN_IO_TIMEOUT_MS, undefined, {}, 'benchmark_runner.py', payload,
    );
  } catch (error: any) {
    console.error('[campagne] échec de persistance :', error?.message || error);
    return null;
  }
}

export async function listCampaigns(limit: number, offset: number): Promise<any> {
  return await callRunner(
    ['campaigns', '--limit', String(limit), '--offset', String(offset)], CAMPAIGN_IO_TIMEOUT_MS,
  );
}

export async function fetchCampaign(campaignId: string): Promise<any> {
  return await callRunner(['campaign', '--id', campaignId], CAMPAIGN_IO_TIMEOUT_MS);
}

// --- Registre des campagnes vivantes ---------------------------------------

export function registerActive(entry: {
  campaignId: string;
  kind: CampaignKind;
  totalRuns: number;
  projectId?: string | null;
}): ActiveCampaign {
  const record: ActiveCampaign = {
    campaignId: entry.campaignId,
    kind: entry.kind,
    startedAt: new Date().toISOString(),
    totalRuns: entry.totalRuns,
    completedRuns: 0,
    currentRun: null,
    cancelRequested: false,
    cancelReason: null,
    cancelAnnounced: false,
    projectId: entry.projectId ?? null,
    handle: null,
  };
  active.set(entry.campaignId, record);
  return record;
}

export function getActive(campaignId: string): ActiveCampaign | undefined {
  return active.get(campaignId);
}

export function unregisterActive(campaignId: string): void {
  active.delete(campaignId);
}

/** Vue publique : ce que doit connaître une page qui vient d'être rechargée. */
export function listActive(): Array<Record<string, unknown>> {
  return [...active.values()].map((entry) => ({
    campaignId: entry.campaignId,
    kind: entry.kind,
    startedAt: entry.startedAt,
    totalRuns: entry.totalRuns,
    completedRuns: entry.completedRuns,
    currentRun: entry.currentRun,
    progress: `${entry.completedRuns}/${entry.totalRuns}`,
    cancelRequested: entry.cancelRequested,
    projectId: entry.projectId,
  }));
}

/** Erreur d'annulation portant le code HTTP à renvoyer. */
export class CampaignCancelError extends Error {
  readonly statusCode: number;
  constructor(message: string, statusCode = 409) {
    super(message);
    this.name = 'CampaignCancelError';
    this.statusCode = statusCode;
  }
}

/**
 * Demande l'arrêt : tue le sous-processus en cours et empêche le démarrage des
 * runs restants. Les runs déjà terminés sont conservés — ils sont réels et déjà
 * écrits dans l'historique, les jeter serait un mensonge.
 */
export function requestCancel(campaignId: string, kind?: CampaignKind): ActiveCampaign {
  const entry = active.get(campaignId);
  if (!entry) {
    throw new CampaignCancelError('Campagne inconnue ou déjà terminée : rien à annuler.');
  }
  if (kind && entry.kind !== kind) {
    throw new CampaignCancelError(`La campagne ${campaignId} n'est pas de type « ${kind} ».`);
  }
  if (entry.cancelRequested) {
    throw new CampaignCancelError('Annulation déjà demandée pour cette campagne.');
  }
  entry.cancelRequested = true;
  entry.cancelReason = 'Campagne annulée à la demande de l’utilisateur.';
  entry.handle?.cancel(entry.cancelReason);
  return entry;
}

export function isCancelled(campaignId: string): boolean {
  return active.get(campaignId)?.cancelRequested === true;
}

// --- Fabrique de l'enregistrement ------------------------------------------

export function newCampaignRecord(input: {
  campaignId: string;
  name: string;
  kind: CampaignKind;
  taskIds?: string[];
  frameworkIds: string[];
  protocolVersion?: string | null;
  executionMode?: string;
  scorer?: string | null;
  projectId?: string | null;
  regime?: string;
}): CampaignRecord {
  const now = new Date().toISOString();
  return {
    campaignId: input.campaignId,
    name: input.name,
    createdAt: now,
    updatedAt: now,
    completedAt: null,
    status: 'running',
    kind: input.kind,
    taskIds: input.taskIds ?? [],
    frameworkIds: input.frameworkIds,
    runIds: [],
    matrixSummary: null,
    protocolVersion: input.protocolVersion ?? null,
    executionMode: input.executionMode ?? 'real',
    scorer: input.scorer ?? null,
    error: null,
    projectId: input.projectId ?? null,
    regime: input.regime ?? 'prompt_only',
  };
}
