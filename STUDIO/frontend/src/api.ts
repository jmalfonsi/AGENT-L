import type {
  Dashboard, Draft, Health, HistoryEntry, Project, ProjectFile, Run, RuntimeProbe, Skill,
} from './types';

// Jeton de session : le serveur ne l'exige que si AGENTL_STUDIO_TOKEN est posé.
// Il arrive une fois dans l'URL, puis vit dans l'onglet — jamais dans le code.
const TOKEN_KEY = 'agentl-studio-token';
const fromUrl = new URLSearchParams(window.location.search).get('token');
if (fromUrl) {
  sessionStorage.setItem(TOKEN_KEY, fromUrl);
  window.history.replaceState({}, '', window.location.pathname);
}
function token() {
  try { return sessionStorage.getItem(TOKEN_KEY) || ''; } catch { return ''; }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token() ? { 'X-Studio-Token': token() } : {}),
      ...(init?.headers || {}),
    },
  });
  if (!response.ok) {
    let detail = `Erreur ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch { /* réponse sans JSON */ }
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export const api = {
  health: () => request<Health>('/api/health'),
  dashboard: () => request<Dashboard>('/api/dashboard'),
  projects: () => request<Project[]>('/api/projects'),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  createProject: (body: unknown) => request<Project>('/api/projects', { method: 'POST', body: JSON.stringify(body) }),
  updateProject: (id: string, body: unknown) => request<Project>(`/api/projects/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteProject: (id: string) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),
  files: (id: string) => request<ProjectFile[]>(`/api/projects/${id}/files`),
  file: (id: string, path: string) => request<{ path: string; content: string }>(`/api/projects/${id}/file?path=${encodeURIComponent(path)}`),
  saveFile: (id: string, path: string, content: string) => request(`/api/projects/${id}/file?path=${encodeURIComponent(path)}`, { method: 'PUT', body: JSON.stringify({ content }) }),
  history: (id: string) => request<HistoryEntry[]>(`/api/projects/${id}/history`),
  historyFile: (id: string, stamp: string, path: string) => request<{ stamp: string; path: string; content: string }>(`/api/projects/${id}/history/file?stamp=${encodeURIComponent(stamp)}&path=${encodeURIComponent(path)}`),
  restoreHistory: (id: string, stamp: string, path: string) => request<{ restored: boolean }>(`/api/projects/${id}/history/restore`, { method: 'POST', body: JSON.stringify({ stamp, path }) }),
  skills: () => request<Skill[]>('/api/skills'),
  createSkill: (body: unknown) => request<Skill>('/api/skills', { method: 'POST', body: JSON.stringify(body) }),
  updateSkill: (id: string, body: unknown) => request<Skill>(`/api/skills/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteSkill: (id: string) => request<void>(`/api/skills/${id}`, { method: 'DELETE' }),
  skillOrigin: (id: string) => request<{ slug: string; content: string; differs: boolean }>(`/api/skills/${id}/origin`),
  resetSkill: (id: string) => request<Skill>(`/api/skills/${id}/reset`, { method: 'POST', body: '{}' }),
  draftSkill: (body: unknown) => request<Record<string, unknown>>('/api/skills/draft', { method: 'POST', body: JSON.stringify(body) }),
  runs: (projectId?: string) => request<Run[]>(`/api/runs${projectId ? `?project_id=${projectId}` : ''}`),
  run: (projectId: string, command: string, runId?: string) => request<Run>(`/api/projects/${projectId}/runs`, { method: 'POST', body: JSON.stringify({ command, run_id: runId }) }),
  startRun: (projectId: string, command: string, runId?: string) => request<Run>(`/api/projects/${projectId}/runs`, { method: 'POST', body: JSON.stringify({ command, run_id: runId, wait: false }) }),
  cancelRun: (runId: string) => request<{ cancelled: boolean }>(`/api/runs/${runId}`, { method: 'DELETE' }),
  getRun: (runId: string) => request<Run>(`/api/runs/${runId}`),
  corrected: (runId: string) => request<{ content: string }>(`/api/runs/${runId}/corrected`),
  /** Lance l’agent auteur ; rend le run à suivre, la proposition se lit ensuite par `draft`. */
  assistant: (projectId: string, prompt: string, apply: boolean) => request<Run>(`/api/projects/${projectId}/assistant`, { method: 'POST', body: JSON.stringify({ prompt, apply, wait: false }) }),
  draft: (runId: string) => request<Draft>(`/api/runs/${runId}/draft`),
  validateDraft: (projectId: string, body: unknown) => request<{ passed: boolean; gates: unknown[]; validation: string }>(`/api/projects/${projectId}/draft/validate`, { method: 'POST', body: JSON.stringify(body) }),
  applyDraft: (projectId: string, body: unknown) => request<{ applied: boolean; passed: boolean; gates: unknown[]; validation: string }>(`/api/projects/${projectId}/draft/apply`, { method: 'POST', body: JSON.stringify(body) }),
  runtimeTest: (projectId: string) => request<RuntimeProbe>(`/api/projects/${projectId}/runtime-test`, { method: 'POST', body: '{}' }),
  settings: () => request<Record<string, unknown>>('/api/settings'),

  /**
   * Flux SSE d'un run : chaque ligne de sortie, puis le run terminé.
   *
   * Une coupure fermait le flux sans jamais appeler `onEnd` : l'espace de
   * travail attendait une fin qui ne viendrait plus, boutons grisés jusqu'au
   * rechargement. Sur erreur, on relit l'état du run ; s'il tourne encore, on
   * reprend le flux à la dernière position reçue (`?from=`), sans doublon.
   */
  stream(runId: string, onLine: (line: string) => void, onEnd: (run: Run) => void) {
    let source: EventSource | null = null;
    let closed = false;
    let position = '';
    let attempts = 0;
    const finish = (run: Run) => { if (closed) return; closed = true; source?.close(); onEnd(run); };
    const recover = async () => {
      source?.close();
      if (closed) return;
      attempts += 1;
      try {
        const run = await api.getRun(runId);
        if (run.status !== 'running') { finish(run); return; }
      } catch { /* serveur injoignable : on réessaie */ }
      window.setTimeout(open, Math.min(1000 * 2 ** Math.min(attempts - 1, 4), 15000));
    };
    const open = () => {
      if (closed) return;
      source = new EventSource(`/api/runs/${runId}/stream${position ? `?from=${encodeURIComponent(position)}` : ''}`);
      source.onmessage = event => {
        attempts = 0;
        if (event.lastEventId) position = event.lastEventId;
        try { onLine(JSON.parse(event.data)); } catch { /* ligne illisible */ }
      };
      source.addEventListener('end', event => {
        try { finish(JSON.parse((event as MessageEvent).data)); } catch { void recover(); }
      });
      source.onerror = () => { void recover(); };
    };
    open();
    return () => { closed = true; source?.close(); };
  },
};
