import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CheckCircle2, CircleAlert, CircleDashed, FileText, FolderOpen, Loader2, Play,
  ShieldCheck, Sparkles, Upload, XCircle,
} from 'lucide-react';
import { LiveExecutionPanel } from './LiveExecutionPanel';
import { Tooltip } from './Tooltip';
import {
  buildStatusClass, buildStatusLabel, generationLabel, projectStatusClass, projectStatusLabel,
} from './factoryVersionUi';
import type { FactoryFrameworkState } from './factoryVersionUi';
import type { LiveExecutionEvent } from './types';

type DimensionStatus = 'present' | 'partial' | 'absent';

interface Dimension {
  id: string; label: string; status: DimensionStatus;
  evidence: string; gap: string; questions: string[];
  required: boolean; blocking: boolean;
}

interface Analysis {
  title: string; summary: string; externalSystems: string[]; risks: string[];
  dimensions: Dimension[]; coverage: number | null; missingRequired: string[];
  blockingQuestions: Array<{ dimension: string; label: string; question: string }>;
  advisoryQuestions: Array<{ dimension: string; label: string; question: string }>;
  ready: boolean; verdict: 'ready' | 'incomplete'; frameworkIds: string[];
}

interface ValidationStep {
  id: string; passed: boolean; blocking: boolean; exitCode: number | null;
  output: string; meaning: string; skipped?: boolean;
}

type FrameworkState = FactoryFrameworkState;

interface Build {
  id: string; frameworkId: string; passed: boolean; forced: boolean;
  version: number; specRevision: number; specHash?: string;
  artifactDir?: string; currentSpec?: boolean; isPublished?: boolean;
  skill?: string; skillResolved?: boolean;
  attemptCount: number; artifacts: string[];
  attempts: Array<{
    attempt: number; report?: string; codegenError?: string;
    validation: { passed: boolean; diagnosis: string; steps: ValidationStep[] } | null;
  }>;
}

interface Project {
  id: string; stem: string; frameworkIds: string[];
  specRevision: number; specHash?: string; specText?: string;
  analysis: Analysis; builds: Build[];
  frameworkStates: Record<string, FrameworkState>;
}

interface FrameworkCapability {
  id: string; label: string; skill: string;
  skillResolved: boolean; statically_verified: boolean;
}

/** Le backend décrit chaque dimension de la grille : de quoi expliquer ce qui est attendu. */
interface DimensionSpec {
  id: string; label: string; seeks: string; requiredFor: string[];
}

interface Capabilities {
  factoryVersion: string; frameworks: FrameworkCapability[]; maxAttempts: number;
  codegen: { available: boolean; reason: string | null; version: string | null };
  dimensions?: DimensionSpec[];
}

interface ProjectSummary {
  id: string; title: string; createdAt: string; updatedAt: string;
  verdict: 'ready' | 'incomplete'; coverage: number | null; frameworkIds: string[];
  specRevision: number;
  builds: FrameworkState[];
}

const FRAMEWORK_LABELS: Record<string, string> = {
  agent_l: 'AGENT-L', langgraph: 'LangGraph', crewai: 'CrewAI', openai_agents: 'OpenAI Agents SDK',
};

const STATUS_STYLE: Record<DimensionStatus, { chip: string; label: string }> = {
  present: { chip: 'bg-emerald-100 text-emerald-700', label: 'complet' },
  partial: { chip: 'bg-amber-100 text-amber-700', label: 'partiel' },
  absent: { chip: 'bg-rose-100 text-rose-700', label: 'muet' },
};

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const response = await fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data as T;
}

function readAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Lecture du fichier impossible.'));
    reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
    reader.readAsDataURL(file);
  });
}

function StepRow({ step }: { step: ValidationStep }) {
  const [open, setOpen] = useState(false);
  const icon = step.skipped
    ? <CircleDashed size={16} className="text-slate-300" />
    : step.passed
      ? <CheckCircle2 size={16} className="text-emerald-500" />
      : <XCircle size={16} className={step.blocking ? 'text-rose-500' : 'text-amber-500'} />;
  return (
    <div className="border-b border-slate-100 last:border-0">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-slate-50">
        {icon}
        <span className="font-mono text-xs font-bold text-slate-900">{step.id}</span>
        <span className="flex-1 text-xs text-slate-500">{step.meaning}</span>
        {step.blocking && <span className="rounded bg-slate-900 px-1.5 py-0.5 text-[9px] font-bold uppercase text-white">bloquant</span>}
        {step.skipped && <span className="text-[10px] uppercase text-slate-400">sautée</span>}
      </button>
      {open && step.output && (
        <pre className="max-h-72 overflow-auto border-t border-slate-100 bg-slate-950 p-3 font-mono text-[11px] leading-5 text-slate-200">{step.output}</pre>
      )}
    </div>
  );
}

export function FactoryPanel() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [capabilitiesError, setCapabilitiesError] = useState('');
  const [specText, setSpecText] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [frameworkIds, setFrameworkIds] = useState<string[]>(['agent_l']);
  const [project, setProject] = useState<Project | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [build, setBuild] = useState<Build | null>(null);
  const [busy, setBusy] = useState<'' | 'analyze' | 'answer' | 'generate'>('');
  // Sans compteur qui avance, une fabrication de trente minutes est
  // indiscernable d'une plateforme bloquée.
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [events, setEvents] = useState<LiveExecutionEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [notice, setNotice] = useState('');
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [confirmFramework, setConfirmFramework] = useState<string | null>(null);
  const sourceRef = useRef<EventSource | null>(null);
  const campaignRef = useRef<string | null>(null);

  useEffect(() => {
    // Le corps d'une réponse 503 est `{error}` : le pousser tel quel dans l'état
    // rendait `capabilities.codegen` indéfini et faisait tomber tout l'écran.
    fetch('/api/factory/capabilities')
      .then(async (response) => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data || typeof data !== 'object' || !data.codegen) {
          throw new Error(data?.error || `HTTP ${response.status}`);
        }
        return data as Capabilities;
      })
      .then((data) => { setCapabilities(data); setCapabilitiesError(''); })
      .catch((reason: any) => {
        setCapabilities(null);
        setCapabilitiesError(reason?.message || 'Service de fabrique injoignable.');
      });

    void refreshProjects();

    // Une fabrication lancée avant un rechargement de page continue côté serveur :
    // sans ce rattachement, son résultat resterait invisible.
    fetch('/api/benchmark/active')
      .then((response) => response.json())
      .then((data) => {
        const live = (data?.campaigns || []).find((item: any) => item.kind === 'factory');
        if (!live) return;
        campaignRef.current = live.campaignId;
        setBusy('generate');
        setStartedAt(live.startedAt ? new Date(live.startedAt).getTime() : Date.now());
        setNotice('Une génération lancée précédemment est toujours en cours. L’affichage s’y est rebranché.');
        if (live.projectId) {
          void openProject(live.projectId);
          attachStream(live.campaignId, live.projectId);
        }
      })
      .catch(() => undefined);

    return () => sourceRef.current?.close();
    // Volontairement monté une seule fois : les fonctions référencées sont stables.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshProjects = useCallback(async () => {
    try {
      const response = await fetch('/api/factory/projects?limit=20');
      const data = await response.json().catch(() => ({}));
      if (response.ok && Array.isArray(data.projects)) setProjects(data.projects as ProjectSummary[]);
    } catch {
      // La liste des projets est un confort : son absence ne bloque pas la fabrique.
    }
  }, []);

  const toggleFramework = (id: string) => setFrameworkIds((current) =>
    current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);

  const analyse = async () => {
    setError(''); setBusy('analyze'); setBuild(null);
    try {
      const body: Record<string, unknown> = { frameworkIds };
      if (file) { body.fileName = file.name; body.fileBase64 = await readAsBase64(file); }
      else { body.text = specText; }
      setProject(await postJson<Project>('/api/factory/analyze', body));
      setAnswers({});
    } catch (reason: any) { setError(reason.message); }
    finally { setBusy(''); }
  };

  const submitAnswers = async () => {
    if (!project) return;
    setError(''); setBusy('answer');
    try {
      const payload = (Object.entries(answers) as Array<[string, string]>)
        .filter(([, value]) => value.trim())
        .map(([question, answer]) => ({ question, answer }));
      const previousRevision = project.specRevision || 1;
      const revised = await postJson<Project>('/api/factory/answer', { projectId: project.id, answers: payload });
      setProject(revised);
      setNotice(
        revised.specRevision > previousRevision
          ? 'Cahier des charges r' + revised.specRevision + ' créé. Les versions validées restent conservées ; l’agent est signalé à mettre à jour.'
          : 'Cahier des charges r' + revised.specRevision + ' réanalysé sans changement de contenu.',
      );
      setAnswers({});
    } catch (reason: any) { setError(reason.message); }
    finally { setBusy(''); }
  };

  /** Récupère le résultat consolidé côté serveur : il survit à un rechargement. */
  const collectBuild = useCallback(async (campaignId: string, projectId: string) => {
    try {
      const response = await fetch(`/api/factory/builds/${encodeURIComponent(campaignId)}`);
      const record = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(record.error || `HTTP ${response.status}`);
      if (record.build) setBuild(record.build as Build);
      if (record.status === 'cancelled') setNotice('Génération arrêtée à votre demande.');
      if (record.status === 'error' && record.error) setError(record.error);
      const refreshed = await fetch(`/api/factory/projects/${encodeURIComponent(projectId)}`).then((r) => r.json());
      if (!refreshed.error) setProject(refreshed);
    } catch (reason: any) {
      setError(`Résultat de génération illisible : ${reason.message}`);
    }
  }, []);

  const attachStream = useCallback((campaignId: string, projectId: string) => {
    sourceRef.current?.close();
    const source = new EventSource(`/api/benchmark/live/${encodeURIComponent(campaignId)}`);
    sourceRef.current = source;
    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data) as LiveExecutionEvent;
        setEvents((current) => current.some((item) => item.sequence === event.sequence) ? current : [...current, event]);
        if (['campaign_complete', 'campaign_error', 'campaign_cancelled'].includes(event.type)) {
          source.close();
          setConnected(false);
          setBusy('');
          setCancelling(false);
          void collectBuild(campaignId, projectId);
        }
      } catch { /* ignoré */ }
    };
  }, [collectBuild]);

  const generate = async (frameworkId: string) => {
    if (!project) return;
    setError(''); setNotice(''); setBusy('generate'); setBuild(null); setEvents([]); setConfirmFramework(null);
    setStartedAt(Date.now());
    const campaignId = `factory_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    campaignRef.current = campaignId;
    attachStream(campaignId, project.id);
    try {
      // La requête rend la main tout de suite : une fabrication dure des dizaines
      // de minutes et ne doit plus dépendre d'une connexion HTTP maintenue.
      await postJson('/api/factory/generate', { projectId: project.id, frameworkId, campaignId });
    } catch (reason: any) {
      setError(reason.message);
      setBusy('');
      sourceRef.current?.close();
      setConnected(false);
    }
  };

  const cancelGeneration = async () => {
    const campaignId = campaignRef.current;
    if (!campaignId) return;
    setCancelling(true);
    try {
      await postJson('/api/factory/cancel', { campaignId });
      setNotice('Arrêt demandé.');
    } catch (reason: any) {
      setError(reason.message);
      setCancelling(false);
    }
  };

  const openProject = async (projectId: string) => {
    setError(''); setNotice(''); setBuild(null);
    try {
      const response = await fetch(`/api/factory/projects/${encodeURIComponent(projectId)}`);
      const data = await response.json().catch(() => ({}));
      if (!response.ok || data.error) throw new Error(data.error || `HTTP ${response.status}`);
      setProject(data as Project);
      setAnswers({});
    } catch (reason: any) {
      setError(`Projet illisible : ${reason.message}`);
    }
  };

  const analysis = project?.analysis;
  const codegenReady = capabilities?.codegen?.available === true;
  const dimensionHelp: Record<string, string> = Object.fromEntries(
    (capabilities?.dimensions || []).map((item) => [item.id, item.seeks]),
  );

  return (
    <div className="space-y-6">
      {error && (
        <div className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
          <XCircle className="mt-0.5 shrink-0" size={18} />
          <div><p className="font-bold">Impossible de poursuivre</p><p>{error}</p></div>
        </div>
      )}

      {capabilitiesError && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-900">
          <p className="font-bold">Fabrique indisponible</p>
          <p className="mt-1">{capabilitiesError}</p>
          <p className="mt-1 text-xs text-rose-700">
            L’onglet « Comparer des runtimes » reste utilisable. Vérifiez que le
            service <span className="font-mono">agent_factory.py</span> répond, puis rechargez la page.
          </p>
        </div>
      )}

      {notice && (
        <div className="rounded-xl border border-sky-200 bg-sky-50 p-4 text-sm text-sky-900">{notice}</div>
      )}

      {projects.length > 0 && (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-500">00 · Reprendre</p>
              <h2 className="mt-1 flex items-center gap-2 text-lg font-black text-slate-950">
                <FolderOpen size={18} />Projets déjà commencés
              </h2>
              <p className="mt-1 text-xs text-slate-400">
                Les cahiers des charges et les agents produits sont conservés sur le disque : rien n’est perdu au rechargement.
              </p>
            </div>
            <button onClick={refreshProjects} className="rounded-lg border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50">Actualiser</button>
          </div>
          <div className="mt-4 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
            {projects.map((item) => (
              <button key={item.id} onClick={() => openProject(item.id)} disabled={busy !== ''}
                title="Rouvrir ce projet et sa grille de complétude"
                className={`rounded-xl border p-3 text-left transition hover:border-slate-300 disabled:opacity-50 ${project?.id === item.id ? 'border-orange-500 bg-orange-50' : 'border-slate-200'}`}>
                <p className="truncate text-sm font-bold text-slate-900">{item.title || item.id}</p>
                <p className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
                  <span className={projectStatusClass(item.builds, item.verdict)}>
                    {projectStatusLabel(item.builds, item.verdict)}
                  </span>
                  <span>CdC r{item.specRevision || 1}</span>
                  <span>couverture {item.coverage == null ? 'N/D' : String(Math.round(item.coverage * 100)) + ' %'}</span>
                  <span>{new Date(item.updatedAt).toLocaleDateString('fr-FR')}</span>
                </p>
                {item.builds.length > 0 && (
                  <p className="mt-1.5 flex flex-wrap gap-1.5 text-[10px]">
                    {item.builds.map((build, index) => (
                      <span key={build.frameworkId + '-' + index} className={buildStatusClass(build)}>
                        {FRAMEWORK_LABELS[build.frameworkId] || build.frameworkId} · {buildStatusLabel(build)}
                      </span>
                    ))}
                  </p>
                )}
              </button>
            ))}
          </div>
        </section>
      )}

      {capabilities && !codegenReady && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-bold">Agent de codage indisponible</p>
          <p>{capabilities.codegen?.reason || 'Raison non communiquée par le service.'}</p>
        </div>
      )}

      {/* 01 — cahier des charges */}
      <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-500">01 · Besoin</p>
        <h2 className="mt-1 text-lg font-black text-slate-950">Cahier des charges</h2>
        <p className="mt-1 text-sm text-slate-500">
          Texte ou PDF. Le document est lu tel quel : aucun résumé par un modèle
          avant analyse, pour qu'il reste opposable à l'agent produit.
        </p>

        <textarea
          value={specText} disabled={busy !== '' || !!file}
          onChange={(event) => setSpecText(event.target.value)}
          placeholder="Décrire l'objectif, le déclencheur, les données lues, les actions autorisées, les interdits, les cas d'erreur et les critères de recette."
          className="mt-4 h-48 w-full rounded-xl border border-slate-200 p-3 font-mono text-xs leading-5 outline-none focus:border-orange-400 disabled:bg-slate-50"
        />

        <div className="mt-3 flex flex-wrap items-center gap-3">
          <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs font-bold text-slate-600 hover:border-slate-300">
            <Upload size={14} />{file ? file.name : 'Déposer un PDF, TXT ou MD'}
            <input type="file" accept=".pdf,.txt,.md" className="hidden"
              onChange={(event) => setFile(event.target.files?.[0] || null)} />
          </label>
          {file && <button onClick={() => setFile(null)} className="text-xs font-bold text-rose-600">retirer</button>}
        </div>

        <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {(capabilities?.frameworks || []).map((framework) => (
            <button key={framework.id} onClick={() => toggleFramework(framework.id)} disabled={busy !== ''}
              className={`rounded-xl border p-3 text-left transition ${frameworkIds.includes(framework.id) ? 'border-orange-500 bg-orange-50' : 'border-slate-200 hover:border-slate-300'}`}>
              <p className="text-sm font-black text-slate-950">{framework.label}</p>
              <p className="mt-1 flex items-center gap-1.5 font-mono text-[10px] text-slate-500">
                <span className={`inline-block h-1.5 w-1.5 rounded-full ${framework.skillResolved ? 'bg-emerald-500' : 'bg-rose-500'}`} />
                {framework.skill}{framework.skillResolved ? '' : ' (absent)'}
              </p>
              <p className={`mt-2 text-[10px] font-bold uppercase tracking-wide ${framework.statically_verified ? 'text-emerald-600' : 'text-amber-600'}`}>
                {framework.statically_verified ? 'chaîne de preuve' : 'chargement seul'}
              </p>
            </button>
          ))}
        </div>

        <button onClick={analyse} disabled={busy !== '' || !codegenReady || (!specText.trim() && !file) || !frameworkIds.length}
          className="mt-4 flex items-center gap-2 rounded-xl bg-slate-950 px-5 py-3 text-sm font-black text-white transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300">
          {busy === 'analyze' ? <><Loader2 size={16} className="animate-spin" />Analyse en cours…</> : <><Sparkles size={16} />Analyser le cahier des charges</>}
        </button>
      </section>

      {/* 02 — grille */}
      {analysis && (
        <section className="rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-200 p-5">
            <div>
              <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-500">02 · Complétude</p>
              <h2 className="mt-1 text-lg font-black text-slate-950">{analysis.title}</h2>
              <p className="mt-1 max-w-2xl text-sm text-slate-500">{analysis.summary}</p>
            </div>
            <div className={`rounded-xl px-4 py-3 text-sm font-bold ${analysis.ready ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'}`}>
              {analysis.ready ? 'Génération possible' : 'Cahier des charges incomplet'}
              <p className="text-xs font-normal">
                couverture {analysis.coverage == null ? 'N/D' : `${Math.round(analysis.coverage * 100)} %`}
              </p>
            </div>
          </div>

          <div className="divide-y divide-slate-100">
            {analysis.dimensions.map((dimension) => (
              <div key={dimension.id} className="flex flex-wrap items-start gap-3 px-5 py-3">
                <span className={`rounded px-2 py-0.5 text-[10px] font-bold uppercase ${STATUS_STYLE[dimension.status].chip}`}>
                  {STATUS_STYLE[dimension.status].label}
                </span>
                <div className="min-w-52 flex-1">
                  <p className="flex flex-wrap items-center text-sm font-bold text-slate-900">
                    {dimension.label}
                    {/* Le backend fournit déjà le « ce qui est attendu » de chaque
                        dimension : l'afficher évite de laisser deviner. */}
                    {dimensionHelp[dimension.id] && <Tooltip content={dimensionHelp[dimension.id]} />}
                    {dimension.required && <span className="ml-2 text-[10px] font-bold uppercase text-slate-400">exigé</span>}
                    {dimension.blocking && <span className="ml-2 rounded bg-rose-600 px-1.5 py-0.5 text-[9px] font-bold uppercase text-white">bloquant</span>}
                  </p>
                  {dimension.gap && <p className="mt-0.5 text-xs text-slate-500">{dimension.gap}</p>}
                  {dimension.evidence && <p className="mt-1 border-l-2 border-slate-200 pl-2 text-xs italic text-slate-400">« {dimension.evidence} »</p>}
                </div>
              </div>
            ))}
          </div>

          {(analysis.blockingQuestions.length > 0 || analysis.advisoryQuestions.length > 0) && (
            <div className="border-t border-slate-200 p-5">
              <h3 className="text-sm font-black text-slate-950">Précisions demandées</h3>
              <p className="mt-1 text-xs text-slate-500">
                Les réponses sont ajoutées au cahier des charges, qui est réanalysé
                entièrement : c'est le document complété qui fait foi.
              </p>
              <div className="mt-3 space-y-3">
                {[...analysis.blockingQuestions, ...analysis.advisoryQuestions].map((item, index) => {
                  const blocking = index < analysis.blockingQuestions.length;
                  return (
                    <div key={`${item.dimension}_${index}`} className={`rounded-xl border p-3 ${blocking ? 'border-rose-200 bg-rose-50/50' : 'border-slate-200'}`}>
                      <p className="text-xs font-bold text-slate-900">
                        {blocking && <span className="mr-2 text-rose-600">bloquant</span>}
                        {item.label} — {item.question}
                      </p>
                      <input value={answers[item.question] || ''} disabled={busy !== ''}
                        onChange={(event) => setAnswers({ ...answers, [item.question]: event.target.value })}
                        placeholder="Réponse en une phrase"
                        className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-xs outline-none focus:border-orange-400" />
                    </div>
                  );
                })}
              </div>
              <button onClick={submitAnswers} disabled={busy !== '' || !(Object.values(answers) as string[]).some((value) => value.trim())}
                className="mt-4 flex items-center gap-2 rounded-xl bg-slate-950 px-4 py-2.5 text-xs font-black text-white disabled:bg-slate-300">
                {busy === 'answer' ? <><Loader2 size={14} className="animate-spin" />Réanalyse…</> : <><FileText size={14} />Compléter et réanalyser</>}
              </button>
            </div>
          )}
        </section>
      )}

      {/* 03 — génération */}
      {project && (
        <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <p className="text-[10px] font-black uppercase tracking-[.18em] text-orange-500">03 · Fabrication</p>
          <h2 className="mt-1 text-lg font-black text-slate-950">
            {project.builds.length > 0 ? 'Versions de l’agent' : 'Générer et valider l’agent'}
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Pour AGENT-L, l'agent de codage applique le skill <span className="font-mono text-xs">agentl-author</span>,
            puis la plateforme rejoue de son côté la chaîne check → test → verify → boundary → run
            sur les fichiers du disque. Un agent n'est validé que par cette relecture indépendante.
          </p>
          <p className="mt-2 text-xs font-bold text-sky-800">
            Chaque clic crée une nouvelle version isolée. La version publiée reste intacte tant que la nouvelle n’a pas passé toute la validation.
          </p>
          {project.builds.length > 0 && (
            <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-3">
              <div className="flex items-center justify-between gap-3">
                <p className="text-xs font-black text-slate-900">Historique immuable</p>
                <span className="text-[10px] font-bold uppercase text-slate-500">CdC courant · r{project.specRevision || 1}</span>
              </div>
              <div className="mt-2 space-y-1.5">
                {[...project.builds].sort((left, right) => right.version - left.version).map((version) => (
                  <div key={version.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-[11px]">
                    <span className="font-black text-slate-900">
                      {FRAMEWORK_LABELS[version.frameworkId] || version.frameworkId} · v{version.version}
                    </span>
                    <span className={version.passed ? 'font-bold text-emerald-700' : 'font-bold text-rose-700'}>
                      {version.passed ? 'validée' : 'refusée'}
                    </span>
                    <span className="text-slate-500">CdC r{version.specRevision}</span>
                    {version.isPublished && <span className="rounded bg-sky-100 px-1.5 py-0.5 font-bold text-sky-800">publiée</span>}
                    {version.isPublished && !version.currentSpec && <span className="rounded bg-amber-100 px-1.5 py-0.5 font-bold text-amber-800">à mettre à jour</span>}
                  </div>
                ))}
              </div>
            </div>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {project.frameworkIds.map((id) => (
              <button key={id} onClick={() => setConfirmFramework(id)} disabled={busy !== '' || !project.analysis.ready}
                title="Lancer la rédaction puis la validation de l’agent pour ce framework"
                className="flex items-center gap-2 rounded-xl bg-orange-500 px-4 py-2.5 text-xs font-black text-white transition hover:bg-orange-400 disabled:cursor-not-allowed disabled:bg-slate-300">
                {busy === 'generate' ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} fill="currentColor" />}
                {generationLabel(FRAMEWORK_LABELS[id] || id, project.frameworkStates[id])}
              </button>
            ))}
            {busy === 'generate' && (
              <button onClick={cancelGeneration} disabled={cancelling}
                className="flex items-center gap-2 rounded-xl border border-rose-300 px-4 py-2.5 text-xs font-black text-rose-700 transition hover:bg-rose-50 disabled:opacity-50">
                {cancelling ? <Loader2 size={14} className="animate-spin" /> : null}
                {cancelling ? 'Arrêt en cours…' : 'Arrêter la génération'}
              </button>
            )}
          </div>

          {confirmFramework && (
            <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/70 p-4 backdrop-blur-sm" onMouseDown={() => setConfirmFramework(null)}>
              <div role="dialog" aria-modal="true" aria-label="Confirmer la fabrication" className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
                <h2 className="text-lg font-black text-slate-950">
                  {project.frameworkStates[confirmFramework]?.latestVersion > 0
                    ? 'Créer une nouvelle version de ' + (FRAMEWORK_LABELS[confirmFramework] || confirmFramework) + ' ?'
                    : 'Fabriquer l’agent ' + (FRAMEWORK_LABELS[confirmFramework] || confirmFramework) + ' ?'}
                </h2>
                <ul className="mt-3 space-y-2 text-sm text-slate-600">
                  <li>• L’agent de codage écrit puis corrige le code : comptez <span className="font-bold text-slate-900">plusieurs dizaines de minutes</span>, jusqu’à {capabilities?.maxAttempts ?? 3} tentatives.</li>
                  <li>• L’opération consomme du quota et écrit dans un nouveau répertoire de version.</li>
                  <li>• Une version refusée reste consultable mais ne remplace jamais la dernière version validée.</li>
                  <li>• Vous pouvez l’arrêter à tout moment, et fermer cet onglet sans rien perdre.</li>
                </ul>
                <div className="mt-5 flex justify-end gap-2">
                  <button onClick={() => setConfirmFramework(null)} className="rounded-lg border border-slate-200 px-4 py-2 text-sm font-bold text-slate-600 hover:bg-slate-50">Annuler</button>
                  <button onClick={() => generate(confirmFramework)} className="rounded-lg bg-orange-500 px-4 py-2 text-sm font-black text-white hover:bg-orange-400">
                    {generationLabel(FRAMEWORK_LABELS[confirmFramework] || confirmFramework, project.frameworkStates[confirmFramework])}
                  </button>
                </div>
              </div>
            </div>
          )}
          {!project.analysis.ready && (
            <p className="mt-3 text-xs font-bold text-rose-600">
              Génération bloquée tant que les dimensions exigées restent muettes : {project.analysis.missingRequired.join(', ')}.
            </p>
          )}
        </section>
      )}

      <LiveExecutionPanel
        events={events} running={busy === 'generate'} connected={connected} startedAt={startedAt}
        frameworkNames={FRAMEWORK_LABELS} onCancel={cancelGeneration} cancelling={cancelling}
        title={busy === 'generate' ? 'Fabrication en cours' : 'Journal de la dernière fabrication'}
      />

      {/* 04 — résultat */}
      {build && (
        <section className="space-y-4">
          <div className={`flex flex-wrap items-center justify-between gap-3 rounded-2xl border p-5 ${build.passed ? 'border-emerald-200 bg-emerald-50' : 'border-rose-200 bg-rose-50'}`}>
            <div className="flex items-center gap-3">
              {build.passed ? <ShieldCheck className="text-emerald-600" /> : <CircleAlert className="text-rose-600" />}
              <div>
                <p className="font-black text-slate-950">
                  {build.passed ? 'Agent validé par la chaîne' : 'Agent non validé'}
                </p>
                <p className="text-xs text-slate-600">
                  {FRAMEWORK_LABELS[build.frameworkId] || build.frameworkId} · v{build.version} · CdC r{build.specRevision}
                  {build.skill && <> · skill <span className="font-mono">{build.skill}</span>{build.skillResolved === false && ' (non résolu)'}</>}
                  {' '}· {build.attemptCount} tentative(s) · {build.artifacts.join(', ') || 'aucun fichier'}
                </p>
              </div>
            </div>
            {build.forced && <span className="rounded-lg bg-amber-100 px-3 py-2 text-[11px] font-bold text-amber-800">Généré sur un cahier des charges incomplet, à la demande explicite</span>}
          </div>

          {build.attempts.map((attempt) => (
            <div key={attempt.attempt} className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="flex items-center justify-between border-b border-slate-200 px-5 py-3">
                <p className="text-sm font-black text-slate-950">Tentative {attempt.attempt}</p>
                <span className={`text-xs font-bold ${attempt.validation?.passed ? 'text-emerald-600' : 'text-rose-600'}`}>
                  {attempt.validation?.passed ? 'validée' : 'refusée'}
                </span>
              </div>
              {attempt.codegenError && <p className="px-5 py-3 text-xs text-rose-700">{attempt.codegenError}</p>}
              {attempt.validation && (
                <>
                  <p className="px-5 py-3 text-xs text-slate-600">{attempt.validation.diagnosis}</p>
                  <div className="border-t border-slate-100">
                    {attempt.validation.steps.map((step) => (
                      <div key={step.id}><StepRow step={step} /></div>
                    ))}
                  </div>
                </>
              )}
              {attempt.report && (
                <details className="border-t border-slate-100">
                  <summary className="cursor-pointer px-5 py-3 text-xs font-bold text-slate-600">Rapport de l'agent de codage</summary>
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap bg-slate-50 px-5 py-3 text-[11px] leading-5 text-slate-700">{attempt.report}</pre>
                </details>
              )}
            </div>
          ))}
        </section>
      )}
    </div>
  );
}
