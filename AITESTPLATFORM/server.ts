import 'dotenv/config';
import express from 'express';
import path from 'path';
import { promises as fs } from 'fs';
import { callRunner, isCancellation, RunnerCancelledError, type RunnerHandle } from "./runner_process";
import { publishLive, scheduleLiveCleanup, subscribeLive, validCampaignId } from "./live_events";
import { cached, CACHE_TTL, wantsRefresh } from "./runner_cache";
import {
  CampaignCancelError,
  fetchCampaign,
  getActive,
  listActive,
  listCampaigns,
  newCampaignRecord,
  persistCampaign,
  registerActive,
  requestCancel,
  unregisterActive,
  type CampaignRecord,
  type CampaignStatus,
} from "./campaign_runtime";

type JsonObject = Record<string, any>;

const app = express();
const PORT = Number(process.env.PORT || 3000);
const projectRoot = process.cwd();

// 32 Mo : un cahier des charges PDF arrive encodé en base64 dans le corps JSON.
app.use(express.json({ limit: '32mb' }));


let queue: Promise<unknown> = Promise.resolve();
function serialized<T>(job: () => Promise<T>): Promise<T> {
  const next = queue.then(job, job);
  queue = next.then(() => undefined, () => undefined);
  return next;
}

function validateIds(taskId: unknown, frameworkId: unknown): void {
  if (typeof taskId !== 'string' || !taskId.includes('.')) {
    throw new Error('taskId AutomationBench invalide.');
  }
  if (typeof frameworkId !== 'string' || !['agent_l', 'langgraph', 'crewai', 'openai_agents'].includes(frameworkId)) {
    throw new Error('frameworkId invalide.');
  }
}

function validateFrameworkId(frameworkId: unknown): asserts frameworkId is string {
  if (typeof frameworkId !== 'string' || !['agent_l', 'langgraph', 'crewai', 'openai_agents'].includes(frameworkId)) {
    throw new Error('frameworkId invalide.');
  }
}

function pageNumber(value: unknown, fallback: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(0, Math.min(Math.trunc(parsed), max));
}

function validateRunId(value: unknown): asserts value is string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{4,80}$/.test(value)) {
    throw new Error('runId invalide.');
  }
}

/** Jeton libre mais borné : sert aux filtres protocole/statut. */
function validateToken(value: string, label: string): string {
  if (!/^[A-Za-z0-9._-]{1,80}$/.test(value)) throw new Error(`${label} invalide.`);
  return value;
}

/**
 * Les régimes de comparaison ne se moyennent jamais entre eux. Une valeur
 * inconnue doit donc être refusée ici et pas silencieusement ignorée, sans
 * quoi la requête retomberait sur le régime par défaut sans le dire.
 * `all` est accepté : c'est le choix explicite de tout afficher.
 */
const REGIMES = ['prompt_only', 'plan_parity', 'all'];

function validateRegime(value: string): string {
  if (!REGIMES.includes(value)) throw new Error(`Régime de comparaison inconnu : ${value}`);
  return value;
}

function validateIsoDate(value: string): string {
  if (Number.isNaN(Date.parse(value))) throw new Error('Paramètre « since » invalide (date ISO attendue).');
  return value;
}

/**
 * Traduit les filtres de requête en arguments du runner. Les valeurs sont
 * validées avant d'être transmises : le runner est appelé sans shell, mais un
 * filtre farfelu doit produire un 400 clair plutôt qu'une trace Python.
 */
function filterArgs(query: JsonObject): string[] {
  const args: string[] = [];
  if (typeof query.framework === 'string' && query.framework) {
    validateFrameworkId(query.framework);
    args.push('--framework', query.framework);
  }
  if (typeof query.task === 'string' && query.task) {
    validateIds(query.task, 'agent_l');
    args.push('--task', query.task);
  }
  if (typeof query.protocol === 'string' && query.protocol) {
    args.push('--protocol', validateToken(query.protocol, 'protocol'));
  }
  if (typeof query.status === 'string' && query.status) {
    args.push('--status', validateToken(query.status, 'status'));
  }
  if (typeof query.since === 'string' && query.since) {
    args.push('--since', validateIsoDate(query.since));
  }
  if (typeof query.regime === 'string' && query.regime) {
    args.push('--regime', validateRegime(query.regime));
  }
  return args;
}

app.get('/api/health', async (req, res) => {
  try {
    res.json(await cached('health', CACHE_TTL.health,
      () => callRunner(['health'], 60_000), wantsRefresh(req.query.refresh)));
  } catch (error: any) {
    res.status(503).json({ status: 'error', error: error.message, simulation: false });
  }
});

app.get('/api/tasks', async (req, res) => {
  try {
    res.json(await cached('tasks', CACHE_TTL.tasks,
      () => callRunner(['tasks'], 60_000), wantsRefresh(req.query.refresh)));
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/api/tasks/:taskId', async (req, res) => {
  try {
    const taskId = decodeURIComponent(req.params.taskId);
    validateIds(taskId, 'agent_l');
    res.json(await callRunner(['task', '--task', taskId], 60_000));
  } catch (error: any) {
    res.status(404).json({ error: error.message });
  }
});

/**
 * Plan transmis aux baselines en régime « parité de plan ». Consultable sans
 * lancer la moindre exécution : personne ne doit avoir à dépenser du quota
 * pour vérifier ce que les frameworks vont recevoir.
 */
app.get('/api/tasks/:taskId/briefing', async (req, res) => {
  try {
    const taskId = decodeURIComponent(req.params.taskId);
    validateIds(taskId, 'agent_l');
    res.json(await cached(`briefing:${taskId}`, CACHE_TTL.tasks,
      () => callRunner(['briefing', '--task', taskId], 60_000), wantsRefresh(req.query.refresh)));
  } catch (error: any) {
    res.status(404).json({ error: error.message });
  }
});

app.get('/api/frameworks', async (req, res) => {
  try {
    res.json(await cached('frameworks', CACHE_TTL.frameworks,
      () => callRunner(['frameworks'], 60_000), wantsRefresh(req.query.refresh)));
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/api/frameworks/:frameworkId/code', async (req, res) => {
  try {
    const frameworkId = decodeURIComponent(req.params.frameworkId);
    validateFrameworkId(frameworkId);
    const taskId = typeof req.query.taskId === 'string' ? req.query.taskId : undefined;
    if (taskId !== undefined) validateIds(taskId, frameworkId);
    const args = ['framework-code', '--framework', frameworkId];
    if (taskId) args.push('--task', taskId);
    res.json(await cached(`framework-code:${frameworkId}:${taskId || ''}`, CACHE_TTL.frameworkCode,
      () => callRunner(args, 60_000), wantsRefresh(req.query.refresh)));
  } catch (error: any) {
    res.status(400).json({ error: error.message });
  }
});

// L'historique, les statistiques, les exports et les campagnes ne sont jamais
// mis en cache : ces données changent à chaque run.
app.get('/api/history', async (req, res) => {
  try {
    const limit = pageNumber(req.query.limit, 50, 200) || 50;
    const offset = pageNumber(req.query.offset, 0, 1_000_000);
    const args = ['history', '--limit', String(limit), '--offset', String(offset), ...filterArgs(req.query as JsonObject)];
    if (req.query.beforeSeq !== undefined && req.query.beforeSeq !== '') {
      args.push('--before-seq', String(pageNumber(req.query.beforeSeq, 0, Number.MAX_SAFE_INTEGER)));
    }
    res.json(await callRunner(args, 60_000));
  } catch (error: any) {
    res.status(400).json({ error: error.message });
  }
});

app.get('/api/history/:runId', async (req, res) => {
  try {
    const runId = decodeURIComponent(req.params.runId);
    validateRunId(runId);
    res.json(await callRunner(['run-detail', '--id', runId], 60_000));
  } catch (error: any) {
    res.status(404).json({ error: error.message });
  }
});

app.get('/api/stats', async (req, res) => {
  try {
    res.json(await callRunner(['stats', ...filterArgs(req.query as JsonObject)], 120_000));
  } catch (error: any) {
    res.status(400).json({ error: error.message });
  }
});

app.get('/api/export', async (req, res) => {
  try {
    const format = typeof req.query.format === 'string' ? req.query.format : 'csv';
    const scope = typeof req.query.scope === 'string' ? req.query.scope : 'runs';
    // Liste blanche stricte : le format et la portée finissent dans un nom de
    // fichier et un Content-Type renvoyés au navigateur.
    if (!['csv', 'json'].includes(format)) throw new Error('Format d’export invalide (csv ou json).');
    if (!['runs', 'stats'].includes(scope)) throw new Error('Portée d’export invalide (runs ou stats).');

    const result = await callRunner(
      ['export', '--format', format, '--scope', scope, ...filterArgs(req.query as JsonObject)], 180_000,
    );
    const filename = String(result?.filename || `export.${format}`).replace(/[^A-Za-z0-9._-]/g, '_');
    const contentType = format === 'csv' ? 'text/csv; charset=utf-8' : 'application/json; charset=utf-8';
    res.setHeader('Content-Type', String(result?.contentType || contentType));
    res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
    res.send(typeof result?.content === 'string' ? result.content : JSON.stringify(result?.content ?? null));
  } catch (error: any) {
    res.status(400).json({ error: error.message });
  }
});

app.get("/api/benchmark/live/:campaignId", (req, res) => {
  const campaignId = req.params.campaignId;
  if (!validCampaignId(campaignId)) {
    res.status(400).json({ error: "campaignId invalide." });
    return;
  }
  res.status(200);
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();
  res.write("retry: 1500\n\n");
  const afterSequence = Number(req.headers["last-event-id"] || 0);
  const unsubscribe = subscribeLive(campaignId, res, Number.isFinite(afterSequence) ? afterSequence : 0);
  const heartbeat = setInterval(() => res.write(": heartbeat\n\n"), 15_000);
  req.on("close", () => {
    clearInterval(heartbeat);
    unsubscribe();
  });
});

app.post('/api/benchmark/run-single', async (req, res) => {
  try {
    const { taskId, frameworkId } = req.body;
    validateIds(taskId, frameworkId);
    const regime = typeof req.body.regime === 'string' && req.body.regime
      ? validateRegime(req.body.regime) : 'prompt_only';
    if (regime === 'all') throw new Error('« all » n’est pas un régime d’exécution.');
    const result = await serialized(() => callRunner([
      'run', '--task', taskId, '--framework', frameworkId, '--regime', regime,
    ]));
    res.json(result);
  } catch (error: any) {
    res.status(400).json({ error: error.message });
  }
});

const DEFAULT_PROTOCOL_VERSION = "automationbench-native-prompt-facade-v3.1";
// Doit rester aligné sur `REGIME_PROTOCOLS` de benchmark_runner.py : la version
// n'est reprise ici que pour la campagne restée sans run exploitable.
const PROTOCOL_BY_REGIME: Record<string, string> = {
  prompt_only: DEFAULT_PROTOCOL_VERSION,
  plan_parity: "automationbench-plan-parity-facade-v4",
};
const SCORER = "AutomationBench official rubric";

interface RunSpec { taskId: string; frameworkId: string; runId: string }

/**
 * Matrice par framework. Règles conservées telles quelles : les runs
 * `valid === false` ou `status === "invalid"` ne comptent dans aucune moyenne,
 * et une moyenne sur ensemble vide vaut `null` — pas zéro, qui se lirait comme
 * une mesure alors qu'il n'y en a pas.
 */
function buildMatrixSummary(runs: JsonObject[], frameworkIds: string[]): JsonObject {
  const matrixSummary: JsonObject = {};
  for (const frameworkId of frameworkIds) {
    const own = runs.filter((run) => run.frameworkId === frameworkId);
    const valid = own.filter((run) => run.valid !== false && run.status !== "invalid");
    const average = (field: string): number | null => {
      const values = valid.map((run) => run[field]).filter((value) => typeof value === "number" && Number.isFinite(value));
      return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    };
    matrixSummary[frameworkId] = {
      frameworkName: own[0]?.frameworkName || frameworkId,
      totalRuns: own.length,
      validRuns: valid.length,
      invalidRuns: own.filter((run) => run.status === "invalid").length,
      completedRuns: valid.filter((run) => run.success).length,
      officialSuccessRate: valid.length ? valid.filter((run) => run.success).length / valid.length : null,
      averagePartialCredit: average("partialCredit"),
      averageToolCalls: average("toolCallCount"),
      averageLlmCalls: average("llmCallCount"),
      averageTimeMs: average("executionTimeMs"),
      failedRuns: own.filter((run) => run.status === "failed").length,
    };
  }
  return matrixSummary;
}

/**
 * Déroule la campagne en tâche de fond. Ne rejette JAMAIS : toute panne est
 * publiée en SSE et persistée, sans quoi elle deviendrait un unhandledRejection
 * qui ferait tomber le processus et emporterait les campagnes voisines.
 */
async function runBenchmarkCampaign(campaignId: string, record: CampaignRecord, runSpecs: RunSpec[]): Promise<void> {
  const regime = record.regime || 'prompt_only';
  const entry = getActive(campaignId);
  const runs: JsonObject[] = [];
  let status: CampaignStatus = "completed";
  let failure: string | null = null;

  try {
    publishLive(campaignId, { type: "campaign_start", message: "Campagne démarrée" });
    for (const spec of runSpecs) {
      if (entry?.cancelRequested) { status = "cancelled"; break; }
      if (entry) entry.currentRun = spec;
      publishLive(campaignId, { type: "run_start", message: "Lancement du processus agent", ...spec });
      try {
        const result = await callRunner(
          ["run", "--task", spec.taskId, "--framework", spec.frameworkId, "--regime", regime],
          15 * 60_000,
          (event) => publishLive(campaignId, { ...event, runId: spec.runId, taskId: spec.taskId, frameworkId: spec.frameworkId } as any),
          {
            AGENT_BENCH_LIVE: "1",
            AGENT_BENCH_LIVE_RUN_ID: spec.runId,
            AGENT_BENCH_LIVE_TASK_ID: spec.taskId,
            AGENT_BENCH_LIVE_FRAMEWORK_ID: spec.frameworkId,
          },
          "benchmark_runner.py",
          undefined,
          (handle: RunnerHandle) => {
            if (!entry) return;
            entry.handle = handle;
            // Annulation demandée pendant le lancement : on rattrape ici.
            if (entry.cancelRequested) handle.cancel(entry.cancelReason ?? undefined);
          },
        );
        runs.push(result);
        if (entry) { entry.completedRuns = runs.length; entry.handle = null; }
        // Persistance après CHAQUE run : un plantage du serveur ne doit pas
        // effacer des exécutions réellement facturées.
        record.runIds.push(String(result?.id || spec.runId));
        record.updatedAt = new Date().toISOString();
        void persistCampaign(record);
        publishLive(campaignId, {
          type: "run_complete",
          message: result.success ? "Run validé par le rubric" : result.status === "invalid" ? "Run techniquement invalide" : result.status === "failed" ? "Run terminé en erreur" : "Run terminé avec résultat partiel",
          ...spec, status: result.status, success: result.success, partialCredit: result.partialCredit,
        });
      } catch (error: any) {
        if (entry) entry.handle = null;
        if (isCancellation(error) || entry?.cancelRequested) {
          publishLive(campaignId, { type: "run_cancelled", message: "Run interrompu par l’annulation", ...spec, status: "cancelled" });
          status = "cancelled";
          break;
        }
        publishLive(campaignId, { type: "run_error", message: "Processus agent interrompu", errorType: error?.name || "Error", ...spec, status: "error" });
        throw error;
      }
    }
  } catch (error: any) {
    status = "error";
    failure = error?.message || String(error);
  }

  if (entry) entry.currentRun = null;
  // La matrice est calculée même sur campagne annulée : les runs déjà terminés
  // sont réels, les jeter serait faux.
  record.matrixSummary = buildMatrixSummary(runs, record.frameworkIds);
  record.protocolVersion = runs[0]?.protocolVersion || PROTOCOL_BY_REGIME[regime] || DEFAULT_PROTOCOL_VERSION;
  record.status = status;
  record.error = failure;
  record.completedAt = new Date().toISOString();
  record.updatedAt = record.completedAt;
  await persistCampaign(record);

  if (status === "cancelled") {
    if (!entry?.cancelAnnounced) {
      publishLive(campaignId, { type: "campaign_cancelled", message: "Campagne annulée", completedRuns: runs.length });
    }
  } else if (status === "error") {
    publishLive(campaignId, { type: "campaign_error", message: "Campagne interrompue", error: failure, completedRuns: runs.length });
  } else {
    publishLive(campaignId, { type: "campaign_complete", message: "Campagne terminée", completedRuns: runs.length });
  }
  scheduleLiveCleanup(campaignId);
  unregisterActive(campaignId);
}

app.post("/api/benchmark/run-suite", async (req, res) => {
  let campaignId: string | undefined;
  try {
    campaignId = req.body.campaignId ?? `server_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    if (!validCampaignId(campaignId)) throw new Error("campaignId invalide.");
    if (getActive(campaignId)) throw new Error("Une campagne porte déjà cet identifiant.");
    const taskIds: string[] = Array.isArray(req.body.taskIds) ? req.body.taskIds : [];
    const frameworkIds: string[] = Array.isArray(req.body.frameworkIds) ? req.body.frameworkIds : [];
    if (!taskIds.length || !frameworkIds.length) {
      throw new Error("Sélectionnez au moins une tâche et un framework.");
    }
    if (taskIds.length * frameworkIds.length > 40) {
      throw new Error("Une suite est limitée à 40 exécutions réelles.");
    }
    for (const taskId of taskIds) {
      for (const frameworkId of frameworkIds) validateIds(taskId, frameworkId);
    }

    const runSpecs: RunSpec[] = taskIds.flatMap((taskId: string, taskIndex: number) =>
      frameworkIds.map((frameworkId: string, frameworkIndex: number) => ({
        taskId, frameworkId, runId: `live_${taskIndex}_${frameworkIndex}_${Date.now()}`,
      })));

    const regime = typeof req.body.regime === 'string' && req.body.regime
      ? validateRegime(req.body.regime) : 'prompt_only';
    if (regime === 'all') throw new Error('« all » n’est pas un régime d’exécution.');

    const regimeLabel = regime === 'plan_parity' ? ' · parité de plan' : '';
    const name = `${taskIds.length} tâche(s) × ${frameworkIds.length} framework(s)${regimeLabel}`;
    const record = newCampaignRecord({
      campaignId, name, kind: "benchmark", taskIds, frameworkIds, regime,
      protocolVersion: PROTOCOL_BY_REGIME[regime], scorer: SCORER,
    });
    registerActive({ campaignId, kind: "benchmark", totalRuns: runSpecs.length });
    void persistCampaign(record);

    publishLive(campaignId, { type: "campaign_queued", message: `${runSpecs.length} exécution(s) placée(s) dans la file` });
    for (const spec of runSpecs) {
      publishLive(campaignId, { type: "run_queued", message: "Exécution en attente", ...spec });
    }

    // La campagne part dans la file sérialisée existante ; la requête HTTP, elle,
    // rend la main tout de suite. Le double filet `.catch` garantit qu'aucune
    // promesse rejetée ne remonte jusqu'à `unhandledRejection`.
    const id = campaignId;
    void serialized(() => runBenchmarkCampaign(id, record, runSpecs))
      .catch((error) => console.error(`[campagne ${id}] échec non rattrapé :`, error));

    res.status(202).json({
      campaignId, status: "running", name, taskIds, frameworkIds, regime, runCount: runSpecs.length,
    });
  } catch (error: any) {
    if (campaignId && validCampaignId(campaignId)) unregisterActive(campaignId);
    res.status(400).json({ error: error.message });
  }
});

app.get("/api/benchmark/campaigns", async (req, res) => {
  try {
    const limit = pageNumber(req.query.limit, 50, 200) || 50;
    const offset = pageNumber(req.query.offset, 0, 1_000_000);
    res.json(await listCampaigns(limit, offset));
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

// Placé avant `/campaigns/:campaignId` n'est pas nécessaire (chemins distincts),
// mais cet endpoint est la clé du rattachement : une page rechargée y lit les
// campagnes vivantes puis rouvre le flux SSE correspondant.
app.get("/api/benchmark/active", (_req, res) => {
  res.json({ campaigns: listActive() });
});

app.get("/api/benchmark/campaigns/:campaignId", async (req, res) => {
  try {
    const campaignId = decodeURIComponent(req.params.campaignId);
    if (!validCampaignId(campaignId)) throw new Error("campaignId invalide.");
    const campaign = await fetchCampaign(campaignId);
    if (!campaign || campaign.error === "not_found" || campaign.campaignId === undefined) {
      res.status(404).json({ error: "Campagne introuvable." });
      return;
    }
    res.json(campaign);
  } catch (error: any) {
    res.status(404).json({ error: error.message });
  }
});

/** Annulation commune au banc et à la fabrique. */
function cancelEndpoint(kind: "benchmark" | "factory") {
  return (req: express.Request, res: express.Response): void => {
    try {
      const campaignId = req.body?.campaignId;
      if (!validCampaignId(campaignId)) throw new CampaignCancelError("campaignId invalide.", 400);
      const entry = requestCancel(campaignId, kind);
      entry.cancelAnnounced = true;
      publishLive(campaignId, {
        type: "campaign_cancelled", message: "Campagne annulée", completedRuns: entry.completedRuns,
      });
      res.json({ campaignId, status: "cancelled", completedRuns: entry.completedRuns });
    } catch (error: any) {
      const statusCode = error instanceof CampaignCancelError ? error.statusCode : 409;
      res.status(statusCode).json({ error: error.message });
    }
  };
}

app.post("/api/benchmark/cancel", cancelEndpoint("benchmark"));

// --- Fabrique d'agents -----------------------------------------------------
// File d'attente distincte de celle du banc : une génération dure des minutes
// et ne doit pas retenir une campagne de mesure derrière elle.
let factoryQueue: Promise<unknown> = Promise.resolve();
function serializedFactory<T>(job: () => Promise<T>): Promise<T> {
  const next = factoryQueue.then(job, job);
  factoryQueue = next.then(() => undefined, () => undefined);
  return next;
}

const FACTORY_TIMEOUT_MS = Number(process.env.AGENT_FACTORY_HTTP_TIMEOUT_MS || 90 * 60_000);

function callFactory(
  args: string[],
  timeoutMs = 5 * 60_000,
  onProgress?: (event: Record<string, unknown>) => void,
  extraEnv: Record<string, string> = {},
  stdinText?: string,
  // Relaie la poignée du sous-processus : sans elle, une génération de 90
  // minutes ne serait pas annulable.
  onChild?: (handle: RunnerHandle) => void,
): Promise<any> {
  return callRunner(args, timeoutMs, onProgress, extraEnv, 'agent_factory.py', stdinText, onChild);
}

function validateProjectId(value: unknown): asserts value is string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]{4,64}$/.test(value)) {
    throw new Error('projectId invalide.');
  }
}

function validateFrameworkList(value: unknown): string[] {
  const list = Array.isArray(value) ? value : [];
  if (!list.length) throw new Error('Choisir au moins un framework cible.');
  for (const item of list) validateFrameworkId(item);
  return list as string[];
}

app.get('/api/factory/capabilities', async (_req, res) => {
  try {
    res.json(await callFactory(['capabilities'], 60_000));
  } catch (error: any) {
    res.status(503).json({ error: error.message });
  }
});

app.get('/api/factory/projects', async (req, res) => {
  try {
    const limit = pageNumber(req.query.limit, 50, 200) || 50;
    const offset = pageNumber(req.query.offset, 0, 1_000_000);
    res.json(await callFactory(['projects', '--limit', String(limit), '--offset', String(offset)], 60_000));
  } catch (error: any) {
    res.status(500).json({ error: error.message });
  }
});

app.get('/api/factory/projects/:projectId', async (req, res) => {
  try {
    const projectId = decodeURIComponent(req.params.projectId);
    validateProjectId(projectId);
    res.json(await callFactory(['project', '--project', projectId], 60_000));
  } catch (error: any) {
    res.status(404).json({ error: error.message });
  }
});

app.post('/api/factory/analyze', async (req, res) => {
  try {
    const frameworkIds = validateFrameworkList(req.body.frameworkIds);
    const { text, fileName, fileBase64 } = req.body;
    const args = ['analyze', '--frameworks', frameworkIds.join(',')];
    let stdinText: string | undefined;

    if (typeof fileBase64 === 'string' && fileBase64) {
      const safeName = String(fileName || 'cahier-des-charges.pdf').replace(/[^A-Za-z0-9._-]/g, '_');
      if (!/\.(pdf|txt|md)$/i.test(safeName)) throw new Error('Format accepté : PDF, TXT ou MD.');
      const data = Buffer.from(fileBase64, 'base64');
      if (data.length > 20_000_000) throw new Error('Fichier trop volumineux (20 Mo maximum).');
      const uploadDir = path.join(projectRoot, 'data', 'uploads');
      await fs.mkdir(uploadDir, { recursive: true });
      const target = path.join(uploadDir, `${Date.now()}_${safeName}`);
      await fs.writeFile(target, data);
      args.push('--file', target);
    } else if (typeof text === 'string' && text.trim()) {
      // Le cahier des charges passe par stdin : il n'apparaît ni dans la ligne
      // de commande ni dans la table des processus.
      args.push('--stdin');
      stdinText = text;
    } else {
      throw new Error('Fournir un cahier des charges (texte ou fichier).');
    }

    res.json(await serializedFactory(() => callFactory(args, 10 * 60_000, undefined, {}, stdinText)));
  } catch (error: any) {
    res.status(400).json({ error: error.message });
  }
});

app.post('/api/factory/answer', async (req, res) => {
  try {
    const { projectId, answers } = req.body;
    validateProjectId(projectId);
    if (!Array.isArray(answers) || !answers.length) throw new Error('Aucune réponse fournie.');
    res.json(await serializedFactory(() => callFactory(
      ['answer', '--project', projectId, '--answers', JSON.stringify(answers)], 10 * 60_000)));
  } catch (error: any) {
    res.status(400).json({ error: error.message });
  }
});

/**
 * Génération en tâche de fond. Comme pour le banc, la génération peut durer
 * 90 minutes : la réponse HTTP ne peut pas servir de canal de résultat.
 */
async function runFactoryCampaign(
  campaignId: string, record: CampaignRecord, args: string[], projectId: string, frameworkId: string,
): Promise<void> {
  const entry = getActive(campaignId);
  let status: CampaignStatus = 'completed';
  let failure: string | null = null;
  let build: JsonObject | null = null;

  try {
    if (entry?.cancelRequested) throw new RunnerCancelledError('Génération annulée avant démarrage.');
    publishLive(campaignId, { type: 'campaign_start', message: 'Agent de codage démarré' });
    build = await callFactory(args, FACTORY_TIMEOUT_MS,
      (event) => publishLive(campaignId, { ...event, projectId, frameworkId } as any),
      {
        AGENT_BENCH_LIVE: '1',
        AGENT_BENCH_LIVE_RUN_ID: campaignId,
        AGENT_BENCH_LIVE_FRAMEWORK_ID: frameworkId,
      },
      undefined,
      (handle: RunnerHandle) => {
        if (!entry) return;
        entry.handle = handle;
        if (entry.cancelRequested) handle.cancel(entry.cancelReason ?? undefined);
      });
    if (entry) { entry.handle = null; entry.completedRuns = 1; }
  } catch (error: any) {
    if (entry) entry.handle = null;
    if (isCancellation(error) || entry?.cancelRequested) {
      status = 'cancelled';
    } else {
      status = 'error';
      failure = error?.message || String(error);
    }
  }

  // Le résultat complet vit dans l'enregistrement : c'est ce qui le rend
  // récupérable après un rechargement de page.
  record.build = build;
  record.status = status;
  record.error = failure;
  record.completedAt = new Date().toISOString();
  record.updatedAt = record.completedAt;
  if (build && typeof build.protocolVersion === 'string') record.protocolVersion = build.protocolVersion;
  await persistCampaign(record);

  if (status === 'cancelled') {
    if (!entry?.cancelAnnounced) {
      publishLive(campaignId, { type: 'campaign_cancelled', message: 'Génération annulée' });
    }
  } else if (status === 'error') {
    publishLive(campaignId, { type: 'campaign_error', message: 'Génération interrompue', error: failure });
  } else {
    publishLive(campaignId, { type: 'campaign_complete', message: build?.passed ? 'Agent validé' : 'Agent non validé' });
  }
  scheduleLiveCleanup(campaignId);
  unregisterActive(campaignId);
}

app.post('/api/factory/generate', async (req, res) => {
  let campaignId: string | undefined;
  try {
    const { projectId, frameworkId, force } = req.body;
    validateProjectId(projectId);
    validateFrameworkId(frameworkId);
    campaignId = req.body.campaignId ?? `factory_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    if (!validCampaignId(campaignId)) throw new Error('campaignId invalide.');
    if (getActive(campaignId)) throw new Error('Une campagne porte déjà cet identifiant.');

    const args = ['generate', '--project', projectId, '--framework', frameworkId];
    if (force === true) args.push('--force');

    const record = newCampaignRecord({
      campaignId, name: `Fabrique ${projectId} · ${frameworkId}`, kind: 'factory',
      frameworkIds: [frameworkId], projectId, executionMode: 'real',
    });
    registerActive({ campaignId, kind: 'factory', totalRuns: 1, projectId });
    void persistCampaign(record);

    publishLive(campaignId, { type: 'campaign_queued', message: 'Génération en attente' });
    const id = campaignId;
    void serializedFactory(() => runFactoryCampaign(id, record, args, projectId, frameworkId))
      .catch((error) => console.error(`[fabrique ${id}] échec non rattrapé :`, error));

    res.status(202).json({ campaignId, projectId, frameworkId, status: 'running' });
  } catch (error: any) {
    if (campaignId && validCampaignId(campaignId)) unregisterActive(campaignId);
    res.status(400).json({ error: error.message });
  }
});

app.get('/api/factory/builds/:campaignId', async (req, res) => {
  try {
    const campaignId = decodeURIComponent(req.params.campaignId);
    if (!validCampaignId(campaignId)) throw new Error('campaignId invalide.');
    const campaign = await fetchCampaign(campaignId);
    if (!campaign || campaign.campaignId === undefined) {
      res.status(404).json({ error: 'Génération introuvable.' });
      return;
    }
    res.json(campaign);
  } catch (error: any) {
    res.status(404).json({ error: error.message });
  }
});

app.post('/api/factory/cancel', cancelEndpoint('factory'));

async function startServer() {
  if (process.env.NODE_ENV !== 'production') {
    const { createServer: createViteServer } = await import('vite');
    const vite = await createViteServer({ server: { middlewareMode: true }, appType: 'spa' });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(projectRoot, 'dist');
    app.use(express.static(distPath));
    app.get('*', (_req, res) => res.sendFile(path.join(distPath, 'index.html')));
  }
  app.listen(PORT, '127.0.0.1', () => {
    console.log(`AITESTPLATFORM réelle sur http://127.0.0.1:${PORT}`);
  });
}

startServer().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
