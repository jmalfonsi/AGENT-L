export type ActiveFrameworkId = 'agent_l' | 'langgraph' | 'crewai' | 'openai_agents';
export interface LiveExecutionEvent {
  sequence: number;
  campaignId: string;
  timestamp: string;
  type: string;
  message: string;
  runId?: string;
  taskId?: string;
  frameworkId?: ActiveFrameworkId | string;
  tool?: string;
  call?: number;
  durationMs?: number;
  status?: string;
  /** Phase de fabrication : `analyze` ou `author`. */
  phase?: string;
  /** Numéro de tentative de la fabrique (1..3). */
  attempt?: number;
  /** Rang de l'opération dans la session de rédaction. */
  step?: number;
  /** Tour de l'agent de codage, borné par `maxTurns`. */
  turn?: number;
  maxTurns?: number;
  elapsedMs?: number;
  [key: string]: unknown;
}

export interface BenchmarkTaskSummary {
  id: string;
  number: number;
  title: string;
  domain: string;
  trigger: string;
  toolCount: number;
  assertionCount: number;
  tools: string[];
  runtimeToolCount: number;
  runtimeTools: string[];
  agentLReady: boolean;
  source: string;
}

export interface AssertionRecord {
  passed?: boolean;
  excluded?: boolean;
  type?: string;
  params?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface TaskToolDefinition {
  name: string;
  doc: string;
  params: Array<{ name: string; type: string; required: boolean }>;
}

export interface BenchmarkTaskDetail extends BenchmarkTaskSummary {
  prompt: Array<{ role: string; content: string }>;
  answer: unknown;
  toolDefinitions: TaskToolDefinition[];
  assertions: AssertionRecord[];
  initialState: Record<string, unknown>;
  original: Record<string, unknown>;
}

export interface FrameworkStatus {
  id: ActiveFrameworkId;
  name: string;
  kind: string;
  distribution: string;
  profile: string;
  installed: boolean;
  configured: boolean;
  available: boolean;
  version: string | null;
  model: string;
  thinkingLevel: string;
  maxOutputTokens: number;
  toolSurface: 'shared_manifest_task_facade';
  protocolVersion: string;
}

export interface FrameworkCodeFile {
  name: string;
  label: string;
  language: 'agentl' | 'python' | 'text';
  content: string;
}

export interface FrameworkCodeBundle {
  id: ActiveFrameworkId;
  name: string;
  taskId: string | null;
  scope: 'task_specific' | 'shared_adapter';
  description: string;
  files: FrameworkCodeFile[];
}

/**
 * Régime de comparaison. Deux régimes ne se moyennent jamais entre eux :
 * ils ne mesurent pas la même chose.
 */
export type RegimeId = 'prompt_only' | 'plan_parity';

export interface RegimeInfo {
  id: RegimeId;
  name: string;
  protocolVersion: string;
  /** Ce que le régime mesure réellement. */
  measures: string;
  /** L'asymétrie qui subsiste, énoncée plutôt que masquée. */
  asymmetry: string;
}

/** Plan transmis aux baselines, dérivé du programme `.agent` de la tâche. */
export interface TaskBriefing {
  taskId: string;
  regime: RegimeId;
  briefing: string;
}

export interface RunnerHealth {
  status: 'ok' | 'error';
  model: string;
  modelConfig: { thinkingLevel: string; maxOutputTokens: number; temperature: number };
  toolSurface: 'shared_manifest_task_facade';
  protocolVersion: string;
  regimes: RegimeInfo[];
  defaultRegime: RegimeId;
  apiKeyConfigured: boolean;
  frameworks: FrameworkStatus[];
  taskCount: number;
  historyCount: number;
  simulation: false;
  error?: string;
}

export interface ToolCallRecord {
  index: number;
  tool?: string;
  event?: string;
  args?: Record<string, unknown>;
  observation?: unknown;
  ok?: boolean;
  error?: string;
  durationMs?: number;
  transportStatus?: 'completed' | 'exception';
  semanticStatus?: 'success' | 'error';
  [key: string]: unknown;
}

export interface LiveRunResult {
  id: string;
  taskId: string;
  taskNumber: number;
  taskTitle: string;
  domain: string;
  frameworkId: ActiveFrameworkId;
  frameworkName: string;
  profile: string;
  model: string;
  status: 'completed' | 'failed' | 'invalid';
  valid: boolean;
  invalidReason: 'output_token_limit' | 'empty_model_output' | 'framework_error' | null;
  success: boolean;
  partialCredit: number;
  taskCompleted: number;
  assertions: AssertionRecord[];
  failedAssertions: AssertionRecord[];
  toolCalls: ToolCallRecord[];
  toolCallCount: number;
  llmCallCount: number | null;
  executionTimeMs: number;
  tokenUsage: {
    promptTokens: number | null;
    completionTokens: number | null;
    totalTokens: number | null;
  };
  finalAnswer: unknown;
  initialState: Record<string, unknown>;
  finalState: Record<string, unknown>;
  runtimeEvidence: Record<string, unknown>;
  modelTelemetry: Array<Record<string, unknown>>;
  modelConfig?: { thinkingLevel: string; maxOutputTokens: number; temperature: number };
  toolSurface: 'shared_manifest_task_facade' | 'shared_prepared_task_facade' | 'legacy_mixed_surface';
  protocolVersion: string;
  regime?: RegimeId;
  briefingChars?: number;
  error: string | null;
  consoleLog: string | null;
  createdAt: string;
  executionMode: 'real';
  scorer: string;
}

/**
 * Ligne d'historique allégée renvoyée par `GET /api/history`.
 * Elle ne contient ni état du monde, ni assertions, ni appels d'outils : ces
 * objets pèsent ~29 Ko par run et n'étaient jamais affichés dans la liste.
 * Le détail complet s'obtient à la demande par `GET /api/history/:runId`.
 */
export interface HistoryRunRow {
  id: string;
  /** Rang stable et croissant dans le journal : curseur de pagination fiable. */
  seq: number;
  campaignId: string | null;
  taskId: string;
  taskNumber: number | null;
  taskTitle: string;
  domain: string;
  frameworkId: ActiveFrameworkId | string;
  frameworkName: string;
  model: string | null;
  status: 'completed' | 'failed' | 'invalid';
  valid: boolean;
  invalidReason: LiveRunResult['invalidReason'];
  success: boolean;
  partialCredit: number | null;
  taskCompleted: number | null;
  toolCallCount: number | null;
  llmCallCount: number | null;
  executionTimeMs: number | null;
  totalTokens: number | null;
  protocolVersion: string | null;
  createdAt: string;
  error: string | null;
  assertionsTotal: number | null;
  assertionsExcluded: number | null;
  assertionsEvaluated: number | null;
  assertionsFailed: number | null;
  regime: RegimeId | null;
  /** Taille du plan réellement transmis ; 0 en régime « énoncé seul ». */
  briefingChars: number | null;
}

export interface HistoryPage {
  runs: HistoryRunRow[];
  total: number;
  limit: number;
  offset: number;
}

export interface FrameworkStats {
  frameworkId: ActiveFrameworkId | string;
  frameworkName: string;
  totalRuns: number;
  validRuns: number;
  invalidRuns: number;
  failedRuns: number;
  successRuns: number;
  officialSuccessRate: number | null;
  averagePartialCredit: number | null;
  averageToolCalls: number | null;
  averageLlmCalls: number | null;
  averageTimeMs: number | null;
  averageTotalTokens: number | null;
  taskCount: number;
}

export interface StatsMatrixCell {
  runs: number;
  validRuns: number;
  averagePartialCredit: number | null;
  successRate: number | null;
  averageToolCalls: number | null;
  averageTimeMs: number | null;
}

export interface StatsMatrixRow {
  taskId: string;
  taskNumber: number | null;
  taskTitle: string;
  cells: Record<string, StatsMatrixCell>;
}

/** Agrégats calculés sur tout l'historique — `GET /api/stats`. */
export interface HistoryStats {
  generatedAt: string;
  filters: Record<string, string | null>;
  totalRuns: number;
  validRuns: number;
  invalidRuns: number;
  failedRuns: number;
  protocolVersions: string[];
  excludedProtocolVersions?: string[];
  /** Régime sur lequel porte ce bilan. */
  regime: RegimeId;
  /** Nombre de runs par régime dans TOUT l'historique, avant filtrage. */
  regimeCounts: Record<string, number>;
  taskCount: number;
  frameworkCount: number;
  byFramework: FrameworkStats[];
  matrix: StatsMatrixRow[];
}

export type CampaignStatus = 'running' | 'completed' | 'error' | 'cancelled';

export interface CampaignSummary {
  campaignId: string;
  name: string;
  createdAt: string;
  completedAt: string | null;
  status: CampaignStatus;
  kind: 'benchmark' | 'factory';
  taskIds: string[];
  frameworkIds: ActiveFrameworkId[];
  runCount: number;
  projectId: string | null;
  regime: RegimeId;
}

/** Campagne persistée — `GET /api/benchmark/campaigns/:campaignId`. */
export interface CampaignRecord extends CampaignSummary {
  updatedAt: string;
  runIds: string[];
  runs: HistoryRunRow[];
  matrixSummary: LiveBenchmarkSuite['matrixSummary'];
  protocolVersion: string;
  executionMode: 'real';
  scorer: string;
  error: string | null;
  build?: unknown;
}

export interface CampaignsPage {
  campaigns: CampaignSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** Campagne en cours dans le processus serveur — `GET /api/benchmark/active`. */
export interface ActiveCampaign {
  campaignId: string;
  kind: 'benchmark' | 'factory';
  startedAt: string;
  completedRuns: number;
  totalRuns: number;
  currentTaskId?: string | null;
  currentFrameworkId?: string | null;
  projectId?: string | null;
}

/** Accusé de réception d'une campagne lancée en tâche de fond (HTTP 202). */
export interface CampaignAccepted {
  campaignId: string;
  status: 'running';
  name?: string;
  taskIds?: string[];
  frameworkIds?: ActiveFrameworkId[];
  runCount?: number;
  projectId?: string;
  frameworkId?: string;
}

export interface LiveBenchmarkSuite {
  id: string;
  name: string;
  createdAt: string;
  taskIds: string[];
  frameworkIds: ActiveFrameworkId[];
  runs: LiveRunResult[];
  matrixSummary: Record<string, {
    frameworkName: string;
    totalRuns: number;
    validRuns: number;
    invalidRuns: number;
    completedRuns: number;
    officialSuccessRate: number | null;
    averagePartialCredit: number | null;
    averageToolCalls: number | null;
    averageLlmCalls: number | null;
    averageTimeMs: number | null;
    failedRuns: number;
  }>;
  executionMode: 'real';
  scorer: string;
  protocolVersion: string;
}
