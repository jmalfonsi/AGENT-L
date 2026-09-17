export type ProjectConfig = {
  architecture: 'single' | 'multi';
  authoringAgent: 'codex' | 'claude-code';
  authoringModel: string;
  authoringEffort: string;
  authoringBudgetUsd: number;
  authoringTimeoutSeconds: number;
  generateOnCreate: boolean;
  runtimeProvider: 'mock' | 'google-gemini' | 'anthropic' | 'openai' | 'openai-compatible';
  runtimeModel: string;
  runtimeApiKeyEnv: string;
  runtimeBaseUrl: string;
  runtimeMaxCostUsd: number;
  runtimeMaxOutputTokens: number;
  runtimePricePerMTokIn: number;
  runtimePricePerMTokOut: number;
  maxTicks: number;
  timeoutSeconds: number;
  verifyDepth: number;
  maxAttempts: number;
  maxCases: number;
  autoloopModel: string;
  roles: { name: string; mission: string }[];
  [key: string]: unknown;
};

export type Project = {
  id: string;
  name: string;
  slug: string;
  description: string;
  status: 'active' | 'archived';
  path: string;
  config: ProjectConfig;
  skills: string[];
  created_at: string;
  updated_at: string;
  files?: ProjectFile[];
  runs?: Run[];
  authoring?: { applied: boolean; engine?: string; summary?: string; warning?: string | null; gates?: Gate[] };
  scaffold?: { agents: string[]; rolesApplied: boolean; uncoveredRoles: string[] };
  /** Point d’entrée déclaré par `project.agentl.json`. */
  entrypoint?: string | null;
  /** Run de génération lancé à la création, à suivre dans l’espace de travail. */
  authoringRun?: Run;
};

export type HistoryEntry = { stamp: string; path: string; size: number };

export type RuntimeUsage = {
  calls: number; inputTokens: number; outputTokens: number;
  costUsd: number | null; maxCostUsd: number | null; priced: boolean;
  provider?: string; model?: string;
};

export type Draft = {
  summary?: string;
  agent_source?: string;
  host_source?: string;
  mode?: string;
  model?: string;
  cost_usd?: number;
  cost_measured?: boolean;
  project_cost_usd?: number;
  project_budget_usd?: number;
  current?: { agent: string; host: string };
  application?: { applied: boolean; passed?: boolean; gates?: Gate[]; diagnostics?: Diagnostic[]; validation?: string } | null;
  run_id?: string;
};

export type RuntimeProbe = {
  ok: boolean; error?: string; provider?: string; model?: string;
  latencyMs?: number; result?: Record<string, unknown>; usage?: RuntimeUsage;
};

export type ProjectFile = { path: string; name: string; size: number };

export type Skill = {
  id: string;
  name: string;
  slug: string;
  description: string;
  domain: string;
  content: string;
  source: 'system' | 'custom';
  customized?: number;
  created_at: string;
  updated_at: string;
};

export type TraceEvent = { tick: number; agent: string; kind: string; text: string; detail: string };

export type Gate = { name: string; status: string; code: number | null; warnings?: number; errors?: number };

/** Erreur ou avertissement d’une porte, rattaché à son agent et à sa ligne. */
export type Diagnostic = {
  severity: 'error' | 'warning'; code: string; line: number | null;
  message: string; agent: string; gate?: string;
};

export type Run = {
  id: string;
  /** null pour ce qui n'appartient à aucun projet : la rédaction d'un skill. */
  project_id: string | null;
  command: string;
  /** `interrupted` : le serveur s’est arrêté pendant le run. */
  status: 'running' | 'passed' | 'failed' | 'timeout' | 'cancelled' | 'interrupted';
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  exit_code: number | null;
  output: string;
  metadata: {
    metrics?: Record<string, number>;
    events?: TraceEvent[];
    gates?: Gate[];
    diagnostics?: Diagnostic[];
    artifacts?: { trace?: string | null; record?: string | null; graph?: string | null; corrected?: string | null };
    journal?: { meta: Record<string, unknown>; entries: number; crossings?: Record<string, number> };
    runtimeUsage?: RuntimeUsage;
    autoloopModel?: string | null;
    budgetSeconds?: number;
    summary?: string;
    applied?: boolean | null;
    error?: string;
    engine?: string;
    [key: string]: unknown;
  };
};

export type Dashboard = {
  stats: { projects: number; skills: number; runs: number; passed: number };
  projects: Project[];
  runs: Run[];
};

export type Health = {
  status: string;
  agentlVersion: string;
  contractVersion: string;
  codexAvailable: boolean;
  claudeCodeAvailable: boolean;
  defaultAuthoringAgent: 'codex' | 'claude-code';
  tokenRequired: boolean;
};
