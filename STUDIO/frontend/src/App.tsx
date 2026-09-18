import { useEffect, useMemo, useRef, useState } from 'react';
import type { ButtonHTMLAttributes, ReactNode } from 'react';
import {
  Activity, Archive, ArrowLeft, ArrowRight, Bot, Boxes, Braces, Check,
  CheckCircle2, ChevronDown, ChevronRight, CircleGauge,
  Clock3, Code2, Command, Cpu, Database, ExternalLink, FileCode2, FileJson,
  FileText, FlaskConical, Folder, FolderKanban, Gauge, GitBranch, History,
  KeyRound, Layers3, LayoutDashboard, LoaderCircle, LockKeyhole, Menu,
  MessageSquareCode, MoreHorizontal, Network, PackageCheck, PanelLeft,
  Pencil, Play, Plus, RefreshCw, Save, Search, Settings, ShieldCheck,
  Sparkles, SquareTerminal, TestTube2, Trash2, Waypoints, X, Zap,
  Ban, Repeat2, Share2, Undo2, Wand2, Radio, ScrollText, SlidersHorizontal,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from './api';
import type { Dashboard, Diagnostic, Draft, Gate, Health, HistoryEntry, Project, ProjectConfig, Run, RuntimeProbe, Skill, TraceEvent } from './types';

type View = 'dashboard' | 'projects' | 'skills' | 'runs' | 'settings' | 'workspace';
type ToastState = { kind: 'success' | 'error'; message: string } | null;

const dateFormatter = new Intl.DateTimeFormat('fr-FR', {
  day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
});

function shortDate(value?: string | null) {
  return value ? dateFormatter.format(new Date(value)) : '—';
}

/** À qui appartient une exécution — la bibliothèque de skills n'a pas de projet. */
function runOwner(run: Run, projects: Project[]) {
  if (!run.project_id) return run.command === 'skill-draft' ? 'Bibliothèque de skills' : '—';
  return projects.find(p => p.id === run.project_id)?.name || run.project_id.slice(0, 8);
}

function duration(ms?: number | null) {
  if (ms == null) return '—';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function Logo({ compact = false }: { compact?: boolean }) {
  return <div className={`brand ${compact ? 'brand-compact' : ''}`}>
    <div className="brand-mark"><span>L</span></div>
    {!compact && <div><strong>AGENT-L</strong><small>STUDIO</small></div>}
  </div>;
}

function Button({ variant = 'primary', icon: Icon, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'ghost' | 'danger'; icon?: LucideIcon }) {
  return <button className={`button button-${variant}`} {...props}>
    {Icon && <Icon size={15} />}{children}
  </button>;
}

function Pill({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) {
  return <span className={`pill pill-${tone}`}>{children}</span>;
}

function StatusDot({ status }: { status: string }) {
  return <span className={`status-dot status-${status}`} />;
}

function Empty({ icon: Icon = Boxes, title, description, action }: { icon?: LucideIcon; title: string; description: string; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-icon"><Icon size={24} /></div><h3>{title}</h3><p>{description}</p>{action}</div>;
}

const NAV: { id: View; label: string; icon: LucideIcon }[] = [
  { id: 'dashboard', label: 'Vue d’ensemble', icon: LayoutDashboard },
  { id: 'projects', label: 'Projets', icon: FolderKanban },
  { id: 'skills', label: 'Skills', icon: Sparkles },
  { id: 'runs', label: 'Exécutions', icon: Activity },
  { id: 'settings', label: 'Paramètres', icon: Settings },
];

function Sidebar({ view, onNavigate, health, collapsed, onCollapse }: { view: View; onNavigate: (view: View) => void; health: Health | null; collapsed: boolean; onCollapse: () => void }) {
  return <aside className={`sidebar ${collapsed ? 'sidebar-collapsed' : ''}`}>
    <div className="sidebar-head"><Logo compact={collapsed} /><button className="icon-button collapse-button" onClick={onCollapse}><PanelLeft size={17} /></button></div>
    <nav className="nav-list">
      <p className="nav-label">ESPACE</p>
      {NAV.map(item => <button key={item.id} title={item.label} className={`nav-item ${view === item.id || (view === 'workspace' && item.id === 'projects') ? 'active' : ''}`} onClick={() => onNavigate(item.id)}>
        <item.icon size={17} /><span>{item.label}</span>
      </button>)}
    </nav>
    <div className="sidebar-spacer" />
    {!collapsed && <div className="runtime-card">
      <div className="runtime-row"><span><StatusDot status="passed" />Runtime local</span><Pill tone="teal">ACTIF</Pill></div>
      <strong>AGENT-L {health?.agentlVersion || '1.9.0'}</strong>
      <small>contrat {health?.contractVersion || '2.5.3'}</small>
      <div className="runtime-line"><span>Auteurs IA</span><b className={health?.codexAvailable || health?.claudeCodeAvailable ? 'ok' : ''}>{health?.codexAvailable && health?.claudeCodeAvailable ? '2 disponibles' : health?.codexAvailable || health?.claudeCodeAvailable ? '1 disponible' : 'absents'}</b></div>
    </div>}
    <div className="sidebar-profile"><div className="avatar">AL</div>{!collapsed && <div><strong>Workspace local</strong><small>Administrateur</small></div>}<MoreHorizontal size={16} /></div>
  </aside>;
}

function Topbar({ view, onSearch }: { view: View; onSearch: (value: string) => void }) {
  const title = NAV.find(item => item.id === view)?.label || (view === 'workspace' ? 'Espace de travail' : 'Studio');
  return <header className="topbar">
    <div><p className="eyebrow">AGENT-L STUDIO</p><h1>{title}</h1></div>
    <div className="topbar-actions">
      <label className="search-box"><Search size={15} /><input aria-label="Rechercher" placeholder="Rechercher…" onChange={e => onSearch(e.target.value)} /><kbd>⌘ K</kbd></label>
      <button className="icon-button" title="Commandes"><Command size={17} /></button>
      <div className="top-status"><StatusDot status="passed" /> Core connecté</div>
    </div>
  </header>;
}

function StatCard({ icon: Icon, label, value, detail, tone }: { icon: LucideIcon; label: string; value: number | string; detail: string; tone: string }) {
  return <article className="stat-card"><div className={`stat-icon tone-${tone}`}><Icon size={19} /></div><div><span>{label}</span><strong>{value}</strong><small>{detail}</small></div></article>;
}

function ProjectCard({ project, onOpen }: { project: Project; onOpen: (project: Project) => void }) {
  const skills = project.skills.length;
  return <article className="project-card" onClick={() => onOpen(project)}>
    <div className="card-top"><div className="project-icon"><Bot size={19} /></div><Pill tone={project.status === 'active' ? 'teal' : 'neutral'}><StatusDot status={project.status === 'active' ? 'passed' : 'idle'} /> {project.status === 'active' ? 'Actif' : 'Archivé'}</Pill></div>
    <h3>{project.name}</h3><p>{project.description || 'Projet AGENT-L gouverné'}</p>
    <div className="project-tags"><span><Layers3 size={13} />{project.config.architecture === 'multi' ? `${project.config.roles?.length || 2} agents` : '1 agent'}</span><span><Sparkles size={13} />{skills} skill{skills > 1 ? 's' : ''}</span></div>
    <div className="card-bottom"><small>Modifié {shortDate(project.updated_at)}</small><ArrowRight size={16} /></div>
  </article>;
}

function RunStatus({ run }: { run: Run }) {
  const passed = run.status === 'passed';
  const Icon = run.status === 'running' ? LoaderCircle : passed ? CheckCircle2 : run.status === 'interrupted' ? Ban : X;
  return <span className={`run-status run-${run.status}`}><Icon size={13} className={run.status === 'running' ? 'spin' : ''} />{run.status === 'passed' ? 'Réussi' : run.status === 'failed' ? 'Échec' : run.status === 'interrupted' ? 'Interrompu' : run.status}</span>;
}

function DashboardView({ data, onNew, onOpen, onNavigate }: { data: Dashboard | null; onNew: () => void; onOpen: (project: Project) => void; onNavigate: (view: View) => void }) {
  if (!data) return <div className="page-loader"><LoaderCircle className="spin" /></div>;
  const successRate = data.stats.runs ? Math.round(data.stats.passed / data.stats.runs * 100) : 100;
  return <div className="page-content dashboard-page">
    <section className="hero-panel">
      <div className="hero-copy"><Pill tone="teal"><Zap size={12} /> CONTRÔLE AVANT EXÉCUTION</Pill><h2>Construisez des agents<br /><span>100% fiables et sécure.</span></h2><p>Créez, gouvernez et observez des agents AGENT-L. Chaque action passe par des contrats, des portes et une trace rejouable.</p><div className="hero-actions"><Button icon={Plus} onClick={onNew}>Nouveau projet</Button><Button variant="secondary" icon={SquareTerminal} onClick={() => onNavigate('runs')}>Voir les exécutions</Button></div></div>
      <div className="hero-visual"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="hero-core"><Logo compact /><span className="pulse-ring" /></div><div className="hero-node node-a"><ShieldCheck size={15} /> policy</div><div className="hero-node node-b"><PackageCheck size={15} /> verify</div><div className="hero-node node-c"><History size={15} /> replay</div></div>
    </section>
    <section className="stats-grid">
      <StatCard icon={FolderKanban} label="Projets actifs" value={data.stats.projects} detail="espaces gouvernés" tone="cyan" />
      <StatCard icon={Sparkles} label="Skills disponibles" value={data.stats.skills} detail="système + spécialisés" tone="violet" />
      <StatCard icon={Activity} label="Exécutions" value={data.stats.runs} detail="journaux persistés" tone="blue" />
      <StatCard icon={ShieldCheck} label="Taux de réussite" value={`${successRate}%`} detail="portes validées" tone="green" />
    </section>
    <section className="section-block"><div className="section-heading"><div><span>PROJETS RÉCENTS</span><h2>Reprendre le travail</h2></div><button className="text-button" onClick={() => onNavigate('projects')}>Tous les projets <ArrowRight size={14} /></button></div>
      <div className="project-grid">{data.projects.map(project => <ProjectCard key={project.id} project={project} onOpen={onOpen} />)}<button className="new-card" onClick={onNew}><Plus size={21} /><strong>Nouveau projet</strong><span>Configurer agents, skills et budget</span></button></div>
    </section>
    <section className="section-block"><div className="section-heading"><div><span>ACTIVITÉ</span><h2>Dernières exécutions</h2></div></div>
      <div className="table-card"><div className="table-head"><span>Commande</span><span>Projet</span><span>État</span><span>Durée</span><span>Date</span></div>{data.runs.length ? data.runs.slice(0, 5).map(run => <div className="table-row" key={run.id}><span className="command-name"><SquareTerminal size={14} />agentl {run.command}</span><span>{runOwner(run, data.projects)}</span><span><RunStatus run={run} /></span><span>{duration(run.duration_ms)}</span><span>{shortDate(run.started_at)}</span></div>) : <Empty icon={Activity} title="Aucune exécution" description="Lancez une porte depuis un projet." />}</div>
    </section>
  </div>;
}

function ProjectsView({ projects, filter, onNew, onOpen, onRefresh, toast }: { projects: Project[]; filter: string; onNew: () => void; onOpen: (p: Project) => void; onRefresh: () => void; toast: (message: string, kind?: 'success' | 'error') => void }) {
  const [status, setStatus] = useState('all');
  const visible = projects.filter(p => (status === 'all' || p.status === status) && `${p.name} ${p.description}`.toLowerCase().includes(filter.toLowerCase()));
  async function archive(project: Project) {
    try { await api.updateProject(project.id, { status: project.status === 'active' ? 'archived' : 'active' }); onRefresh(); toast(project.status === 'active' ? 'Projet archivé' : 'Projet réactivé'); }
    catch (error) { toast((error as Error).message, 'error'); }
  }
  async function remove(project: Project) {
    if (!window.confirm(`Supprimer définitivement « ${project.name} » et ses fichiers ?`)) return;
    try { await api.deleteProject(project.id); onRefresh(); toast('Projet supprimé'); }
    catch (error) { toast((error as Error).message, 'error'); }
  }
  return <div className="page-content"><div className="page-actions"><div className="segmented"><button className={status === 'all' ? 'active' : ''} onClick={() => setStatus('all')}>Tous</button><button className={status === 'active' ? 'active' : ''} onClick={() => setStatus('active')}>Actifs</button><button className={status === 'archived' ? 'active' : ''} onClick={() => setStatus('archived')}>Archivés</button></div><Button icon={Plus} onClick={onNew}>Nouveau projet</Button></div>
    {visible.length ? <div className="project-list">{visible.map(project => <article className="project-list-row" key={project.id}>
      <button className="project-main" onClick={() => onOpen(project)}><div className="project-icon"><Bot size={19} /></div><div><h3>{project.name}</h3><p>{project.description}</p><div className="project-tags"><span><Cpu size={13} />runtime · {project.config.runtimeModel || 'mock-deterministic'}</span><span><Network size={13} />{project.config.architecture}</span><span><Sparkles size={13} />{project.skills.length} skills</span></div></div></button>
      <div className="project-meta"><Pill tone={project.status === 'active' ? 'teal' : 'neutral'}>{project.status}</Pill><small>{shortDate(project.updated_at)}</small><Button variant="ghost" icon={Archive} onClick={() => archive(project)}>{project.status === 'active' ? 'Archiver' : 'Activer'}</Button><button className="icon-button danger-icon" onClick={() => remove(project)}><Trash2 size={15} /></button><button className="icon-button" onClick={() => onOpen(project)}><ChevronRight size={17} /></button></div>
    </article>)}</div> : <Empty icon={FolderKanban} title="Aucun projet" description="Créez votre premier espace agentique gouverné." action={<Button icon={Plus} onClick={onNew}>Créer un projet</Button>} />}
  </div>;
}

function SkillCard({ skill, selected, onSelect, onEdit, onDelete }: { skill: Skill; selected?: boolean; onSelect?: () => void; onEdit: () => void; onDelete: () => void }) {
  return <article className={`skill-card ${selected ? 'selected' : ''}`} onClick={onSelect}>
    <div className="skill-card-top"><div className={`skill-symbol domain-${skill.domain}`}><Sparkles size={17} /></div><div className="skill-badges">{skill.customized ? <Pill tone="amber"><Pencil size={10} /> MODIFIÉ</Pill> : null}<Pill tone={skill.source === 'system' ? 'blue' : 'violet'}>{skill.source === 'system' ? 'SYSTÈME' : 'SPÉCIALISÉ'}</Pill></div></div>
    <h3>{skill.name}</h3><p>{skill.description}</p><div className="skill-domain"><Braces size={13} />{skill.domain}</div>
    <div className="skill-footer"><small>SKILL.md · {Math.max(1, Math.round(skill.content.length / 1000))}k</small><div><button className="icon-button" title="Modifier ce skill" onClick={e => { e.stopPropagation(); onEdit(); }}><Pencil size={14} /></button>{skill.source !== 'system' && <button className="icon-button danger-icon" title="Supprimer" onClick={e => { e.stopPropagation(); onDelete(); }}><Trash2 size={14} /></button>}</div></div>
  </article>;
}

function SkillsView({ skills, filter, onCreate, onEdit, onDelete }: { skills: Skill[]; filter: string; onCreate: () => void; onEdit: (s: Skill) => void; onDelete: (s: Skill) => void }) {
  const visible = skills.filter(s => `${s.name} ${s.description} ${s.domain}`.toLowerCase().includes(filter.toLowerCase()));
  return <div className="page-content"><section className="library-banner"><div><Pill tone="violet"><Sparkles size={12} /> BIBLIOTHÈQUE DE CAPACITÉS</Pill><h2>Des consignes spécialisées,<br />versionnées avec vos projets.</h2><p>Chaque skill est produit ou édité par Codex / Claude Code, copié dans le projet puis utilisé pour guider la création des agents.</p></div><Button icon={Plus} onClick={onCreate}>Créer un skill</Button></section>
    <div className="section-heading compact"><div><span>{visible.length} SKILLS</span><h2>Bibliothèque</h2></div><div className="legend"><Pill tone="blue">Système</Pill><Pill tone="violet">Spécialisé</Pill></div></div>
    <div className="skills-grid">{visible.map(skill => <SkillCard key={skill.id} skill={skill} onEdit={() => onEdit(skill)} onDelete={() => onDelete(skill)} />)}</div>
  </div>;
}

function RunsView({ runs, projects, filter, onOpen }: { runs: Run[]; projects: Project[]; filter: string; onOpen: (project: Project, run: Run) => void }) {
  const visible = runs.filter(r => `${r.command} ${r.status}`.toLowerCase().includes(filter.toLowerCase()));
  return <div className="page-content"><div className="runs-summary"><div><Activity size={20} /><span><strong>{runs.length}</strong> exécutions persistées</span></div><div><History size={20} /><span><strong>{runs.filter(r => r.metadata?.journal).length}</strong> journaux rejouables</span></div><div><PackageCheck size={20} /><span><strong>{runs.filter(r => r.status === 'passed').length}</strong> réussites</span></div></div>
    <div className="table-card runs-table"><div className="table-head"><span>Commande</span><span>Projet</span><span>État</span><span>Événements</span><span>Durée</span><span>Démarré</span><span /></div>{visible.map(run => {
      const project = projects.find(p => p.id === run.project_id);
      return <button className="table-row" key={run.id} disabled={!project} onClick={() => project && onOpen(project, run)}><span className="command-name"><SquareTerminal size={14} />agentl {run.command}</span><span>{runOwner(run, projects)}</span><span><RunStatus run={run} /></span><span>{run.metadata?.events?.length || run.metadata?.journal?.entries || 0}</span><span>{duration(run.duration_ms)}</span><span>{shortDate(run.started_at)}</span><span><ChevronRight size={15} /></span></button>})}</div>
  </div>;
}

function SettingsView({ health }: { health: Health | null }) {
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  useEffect(() => { api.settings().then(setSettings).catch(() => undefined); }, []);
  const security = settings?.security as Record<string, boolean> | undefined;
  const credentials = settings?.runtimeCredentials as Record<string, boolean> | undefined;
  return <div className="page-content settings-page"><div className="settings-grid">
    <section className="settings-card"><div className="settings-card-head"><div className="settings-icon"><Code2 size={18} /></div><div><h3>Agents auteurs</h3><p>Ils produisent les agents et les SKILL.md, sans servir d’oracle runtime.</p></div></div><div className="setting-row"><div><strong>Codex CLI</strong><small>Mode non interactif, sandbox en lecture seule, message final sur fichier</small></div><Pill tone={health?.codexAvailable ? 'teal' : 'amber'}>{health?.codexAvailable ? 'DISPONIBLE' : 'ABSENT'}</Pill></div><div className="setting-row"><div><strong>Claude Code</strong><small>Mode print, outils désactivés, sortie structurée</small></div><Pill tone={health?.claudeCodeAvailable ? 'teal' : 'amber'}>{health?.claudeCodeAvailable ? 'DISPONIBLE' : 'ABSENT'}</Pill></div></section>
    <section className="settings-card"><div className="settings-card-head"><div className="settings-icon violet"><Cpu size={18} /></div><div><h3>LLM d’exécution</h3><p>Oracles appelés par les agents AGENT-L pendant leur boucle.</p></div></div><div className="setting-row"><div><strong>Google Gemini cloud</strong><small>GEMINI_API_KEY</small></div><Pill tone={credentials?.googleGemini ? 'teal' : 'neutral'}>{credentials?.googleGemini ? 'CONFIGURÉ' : 'VARIABLE ABSENTE'}</Pill></div><div className="setting-row"><div><strong>Anthropic</strong><small>ANTHROPIC_API_KEY</small></div><Pill tone={credentials?.anthropic ? 'teal' : 'neutral'}>{credentials?.anthropic ? 'CONFIGURÉ' : 'VARIABLE ABSENTE'}</Pill></div><div className="setting-row"><div><strong>OpenAI</strong><small>OPENAI_API_KEY</small></div><Pill tone={credentials?.openai ? 'teal' : 'neutral'}>{credentials?.openai ? 'CONFIGURÉ' : 'VARIABLE ABSENTE'}</Pill></div><div className="setting-note"><LockKeyhole size={15} />Le projet ne conserve que le nom de la variable d’environnement, jamais sa valeur.</div></section>
    <section className="settings-card"><div className="settings-card-head"><div className="settings-icon green"><ShieldCheck size={18} /></div><div><h3>Sécurité d’exécution</h3><p>Garanties appliquées par le backend local.</p></div></div><ul className="security-list"><li><CheckCircle2 size={15} />Commandes CLI sur liste blanche <Pill tone="teal">{security?.commandsAllowlisted ? 'ACTIF' : '—'}</Pill></li><li><CheckCircle2 size={15} />Fichiers isolés par workspace <Pill tone="teal">{security?.workspaceIsolated ? 'ACTIF' : '—'}</Pill></li><li><CheckCircle2 size={15} />Historique avant chaque écriture <Pill tone="teal">ACTIF</Pill></li><li><CheckCircle2 size={15} />Journaux chaînés et rejouables <Pill tone="teal">ACTIF</Pill></li><li><CheckCircle2 size={15} />En-tête Host validé <Pill tone="teal">{security?.hostHeaderChecked ? 'ACTIF' : '—'}</Pill></li><li><CheckCircle2 size={15} />Variables de clé sur liste blanche <Pill tone="teal">{security?.apiKeyEnvAllowlisted ? 'ACTIF' : '—'}</Pill></li><li><CheckCircle2 size={15} />Jeton de session <Pill tone={security?.tokenRequired ? 'teal' : 'neutral'}>{security?.tokenRequired ? 'EXIGÉ' : 'DÉSACTIVÉ'}</Pill></li></ul></section>
    <section className="settings-card wide"><div className="settings-card-head"><div className="settings-icon violet"><Database size={18} /></div><div><h3>Stockage local</h3><p>Les métadonnées restent dans SQLite ; les sources et artefacts sont lisibles sur disque.</p></div></div><div className="path-box"><span>Données Studio</span><code>{String(settings?.dataRoot || 'STUDIO/.data')}</code></div><div className="path-box"><span>Dépôt AGENT-L</span><code>{String(settings?.repoRoot || '—')}</code></div></section>
  </div></div>;
}

type WizardData = { name: string; description: string; skills: string[]; config: ProjectConfig };
let defaultConfig: ProjectConfig = { architecture: 'single', authoringAgent: 'codex', authoringModel: '', authoringEffort: 'high', authoringBudgetUsd: 2.5, authoringTimeoutSeconds: 300, generateOnCreate: true, runtimeProvider: 'mock', runtimeModel: 'mock-deterministic', runtimeApiKeyEnv: '', runtimeBaseUrl: '', runtimeMaxCostUsd: 1, runtimeMaxOutputTokens: 4096, runtimePricePerMTokIn: 0, runtimePricePerMTokOut: 0, maxTicks: 6, timeoutSeconds: 90, verifyDepth: 4, maxAttempts: 4, maxCases: 200, autoloopModel: '', roles: [{ name: 'principal', mission: 'Exécuter le workflow gouverné' }] };

// Les défauts font autorité côté serveur : c'est lui qui sait quel CLI auteur
// est installé et si une clé Gemini est disponible.
let RUNTIME_MODELS: Record<string, string[]> = {};
let AUTHORING_AVAILABLE: Record<string, boolean | undefined> = {};

function ProjectWizard({ skills, onClose, onCreate }: { skills: Skill[]; onClose: () => void; onCreate: (data: WizardData) => Promise<void> }) {
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [data, setData] = useState<WizardData>({ name: '', description: '', skills: ['system-agentl-author'], config: structuredClone(defaultConfig) });
  const titles = ['Le projet', 'Les skills', 'Les agents', 'Les limites'];
  const valid = step === 0
    ? data.name.trim().length >= 2
    : step === 3
      ? data.config.runtimeProvider === 'mock' || data.config.runtimeModel.trim().length > 0
      : true;
  function toggleSkill(id: string) { setData(current => ({ ...current, skills: current.skills.includes(id) ? current.skills.filter(item => item !== id) : [...current.skills, id] })); }
  function setConfig<K extends keyof ProjectConfig>(key: K, value: ProjectConfig[K]) { setData(current => ({ ...current, config: { ...current.config, [key]: value } })); }
  function updateRole(index: number, key: 'name' | 'mission', value: string) { const roles = data.config.roles.map((r, i) => i === index ? { ...r, [key]: value } : r); setConfig('roles', roles); }
  async function submit() { setBusy(true); try { await onCreate(data); } finally { setBusy(false); } }
  return <div className="modal-backdrop"><div className="modal wizard-modal">
    <div className="modal-head"><div><p className="eyebrow">NOUVEAU PROJET</p><h2>Créer un espace gouverné</h2></div><button className="icon-button" onClick={onClose}><X size={18} /></button></div>
    <div className="wizard-steps">{titles.map((title, index) => <div key={title} className={`${index === step ? 'active' : ''} ${index < step ? 'done' : ''}`}><span>{index < step ? <Check size={13} /> : index + 1}</span><b>{title}</b></div>)}</div>
    <div className="modal-body wizard-body">
      {step === 0 && <div className="form-screen"><div className="form-intro"><div className="large-step-icon"><FolderKanban size={23} /></div><div><h3>Quel agent voulez-vous construire ?</h3><p>Le Studio crée un couple `.agent` / `.py` prêt à passer les portes.</p></div></div><label>Nom du projet<input autoFocus value={data.name} onChange={e => setData({ ...data, name: e.target.value })} placeholder="ex. Qualification des demandes" /></label><label>Description<textarea rows={4} value={data.description} onChange={e => setData({ ...data, description: e.target.value })} placeholder="Mission, contraintes et résultat attendu…" /></label></div>}
      {step === 1 && <div className="form-screen"><div className="form-intro"><div className="large-step-icon violet"><Sparkles size={23} /></div><div><h3>Choisissez les skills spécialisés</h3><p>Ils seront copiés dans `.agentl/skills` et fournis à l’agent auteur sélectionné.</p></div></div><div className="wizard-skill-grid">{skills.map(skill => <button key={skill.id} className={`wizard-skill ${data.skills.includes(skill.id) ? 'selected' : ''}`} onClick={() => toggleSkill(skill.id)}><span className="skill-symbol"><Sparkles size={16} /></span><div><strong>{skill.name}</strong><small>{skill.description}</small></div><span className="select-check">{data.skills.includes(skill.id) && <Check size={13} />}</span></button>)}</div></div>}
      {step === 2 && <div className="form-screen"><div className="form-intro"><div className="large-step-icon blue"><Network size={23} /></div><div><h3>Définissez l’architecture</h3><p>AGENT-L exécute nativement les sociétés multi-agents.</p></div></div><div className="choice-grid"><button className={data.config.architecture === 'single' ? 'selected' : ''} onClick={() => setConfig('architecture', 'single')}><Bot size={20} /><strong>Agent unique</strong><small>Un objectif, un hôte, une boucle</small></button><button className={data.config.architecture === 'multi' ? 'selected' : ''} onClick={() => setConfig('architecture', 'multi')}><Waypoints size={20} /><strong>Société multi-agents</strong><small>Messages, délégation et mémoire</small></button></div><div className="roles-editor"><div className="form-subhead"><span>RÔLES</span>{data.config.architecture === 'multi' && <button className="text-button" onClick={() => setConfig('roles', [...data.config.roles, { name: `agent-${data.config.roles.length + 1}`, mission: '' }])}><Plus size={13} /> Ajouter</button>}</div>{data.config.roles.slice(0, data.config.architecture === 'single' ? 1 : undefined).map((role, index) => <div className="role-row" key={index}><input value={role.name} onChange={e => updateRole(index, 'name', e.target.value)} placeholder="Nom du rôle" /><input value={role.mission} onChange={e => updateRole(index, 'mission', e.target.value)} placeholder="Mission" />{data.config.architecture === 'multi' && data.config.roles.length > 2 && <button className="icon-button" onClick={() => setConfig('roles', data.config.roles.filter((_, i) => i !== index))}><X size={14} /></button>}</div>)}</div></div>}
      {step === 3 && <div className="form-screen"><div className="form-intro"><div className="large-step-icon green"><Gauge size={23} /></div><div><h3>Choisissez l’auteur et le runtime</h3><p>L’agent de codage construit le projet ; le LLM runtime raisonne dans les boucles AGENT-L.</p></div></div><div className="dual-model-grid"><section><div className="form-subhead"><span>1 · AGENT AUTEUR</span><Pill tone="blue">CRÉATION</Pill></div><label>Agent de codage<select value={data.config.authoringAgent} onChange={e => setConfig('authoringAgent', e.target.value as ProjectConfig['authoringAgent'])}><option value="codex">Codex CLI</option><option value="claude-code">Claude Code</option></select></label><label>Modèle auteur <small>vide = défaut du CLI</small><input value={data.config.authoringModel} onChange={e => setConfig('authoringModel', e.target.value)} placeholder="Configuration du CLI" /></label><label>Effort<select value={data.config.authoringEffort} onChange={e => setConfig('authoringEffort', e.target.value)}><option>medium</option><option>high</option><option>xhigh</option><option>max</option></select></label></section><section><div className="form-subhead"><span>2 · LLM D’EXÉCUTION</span><Pill tone="violet">RUNTIME</Pill></div><label>Fournisseur<select value={data.config.runtimeProvider} onChange={e => { const provider = e.target.value as ProjectConfig['runtimeProvider']; setConfig('runtimeProvider', provider); setConfig('runtimeApiKeyEnv', provider === 'google-gemini' ? 'GEMINI_API_KEY' : provider === 'anthropic' ? 'ANTHROPIC_API_KEY' : provider === 'openai' ? 'OPENAI_API_KEY' : ''); setConfig('runtimeModel', provider === 'mock' ? 'mock-deterministic' : ''); }}><option value="mock">Mock déterministe</option><option value="google-gemini">Google Gemini cloud</option><option value="anthropic">Anthropic</option><option value="openai">OpenAI</option><option value="openai-compatible">Local / compatible OpenAI</option></select></label><label>Modèle runtime <small>{data.config.runtimeProvider === 'mock' ? 'déterministe' : 'requis'}</small><input value={data.config.runtimeModel} onChange={e => setConfig('runtimeModel', e.target.value)} placeholder={data.config.runtimeProvider === 'google-gemini' ? 'ex. gemini-…' : 'Identifiant exact du modèle'} /></label><label>Variable de clé<input value={data.config.runtimeApiKeyEnv} onChange={e => setConfig('runtimeApiKeyEnv', e.target.value)} placeholder="Aucune pour mock/local" /></label></section></div><div className="form-grid limit-grid"><label>Ticks maximum<input type="number" min="1" max="100" value={data.config.maxTicks} onChange={e => setConfig('maxTicks', Number(e.target.value))} /></label><label>Timeout runtime (secondes)<input type="number" min="5" max="300" value={data.config.timeoutSeconds} onChange={e => setConfig('timeoutSeconds', Number(e.target.value))} /></label></div><div className="summary-strip"><ShieldCheck size={16} /><span><strong>{data.config.authoringAgent === 'codex' ? 'Codex écrit' : 'Claude Code écrit'}</strong> · {data.config.runtimeProvider === 'mock' ? 'Mock exécute' : `${data.config.runtimeModel || 'modèle à préciser'} raisonne au runtime`} · {data.skills.length} skills</span></div></div>}
    </div>
    <div className="modal-footer"><Button variant="ghost" onClick={step === 0 ? onClose : () => setStep(step - 1)}>{step === 0 ? 'Annuler' : 'Retour'}</Button><Button disabled={!valid || busy} icon={step === 3 ? (busy ? LoaderCircle : Sparkles) : ArrowRight} onClick={step === 3 ? submit : () => setStep(step + 1)}>{step === 3 ? (busy ? `${data.config.authoringAgent === 'codex' ? 'Codex' : 'Claude Code'} génère…` : 'Générer le projet') : 'Continuer'}</Button></div>
  </div></div>;
}

function SkillEditor({ skill, onClose, onSave, onReset }: { skill?: Skill; onClose: () => void; onSave: (body: { name: string; description: string; domain: string; content: string }) => Promise<void>; onReset: (skill: Skill) => Promise<Skill | null> }) {
  const [busy, setBusy] = useState(false);
  const [origin, setOrigin] = useState<{ content: string; differs: boolean } | null>(null);
  const [showOrigin, setShowOrigin] = useState(false);
  const system = skill?.source === 'system';
  const [generating, setGenerating] = useState(false);
  const [generationNote, setGenerationNote] = useState('');
  // Le défaut suit ce qui est réellement installé : proposer Codex sur une
  // machine qui ne l'a pas fait échouer la génération sans que rien ne le dise.
  const [authoring, setAuthoring] = useState<{ agent: string; model: string; effort: string; instructions: string }>(
    { agent: defaultConfig.authoringAgent, model: '', effort: 'high', instructions: '' });
  const [data, setData] = useState({ name: skill?.name || '', description: skill?.description || '', domain: skill?.domain || 'business', content: skill?.content || '# Mon skill AGENT-L\n\nDécrivez la spécialisation et ses règles.\n\n## Procédure\n\n- Définir le résultat attendu.\n- Déclarer les invariants de sécurité.\n- Exiger les scénarios de validation.\n' });
  async function submit() { setBusy(true); try { await onSave(data); } finally { setBusy(false); } }
  // La version livrée reste dans le dépôt : on peut toujours la comparer,
  // et y revenir sans quitter l'éditeur.
  useEffect(() => {
    if (!skill || !system) return;
    api.skillOrigin(skill.id).then(setOrigin).catch(() => setOrigin(null));
  }, [skill?.id, system]);
  async function revert() {
    if (!skill || !window.confirm(`Revenir à la version livrée de « ${skill.name} » ? Vos modifications seront perdues.`)) return;
    setBusy(true);
    try {
      const restored = await onReset(skill);
      if (restored) { setData(current => ({ ...current, content: restored.content })); setShowOrigin(false); }
    } finally { setBusy(false); }
  }
  async function generate() {
    if (data.name.trim().length < 2) { setGenerationNote('Donnez d’abord un nom au skill.'); return; }
    setGenerating(true); setGenerationNote(`${authoring.agent === 'codex' ? 'Codex' : 'Claude Code'} rédige… cela prend souvent une à trois minutes.`);
    try {
      const result = await api.draftSkill({
        name: data.name, description: data.description, domain: data.domain,
        instructions: authoring.instructions, authoring_agent: authoring.agent,
        authoring_model: authoring.model, authoring_effort: authoring.effort, budget_usd: 1,
      });
      if (typeof result.content === 'string') setData(current => ({ ...current, content: result.content as string }));
      const cost = result.cost_measured === false ? 'coût non mesuré' : `${Number(result.cost_usd || 0).toFixed(4)} $`;
      const seconds = Math.round(Number(result.duration_ms || 0) / 1000);
      // Le résultat n'est pas encore enregistré : le dire, sinon on croit
      // que la génération a échoué alors qu'elle attend un clic.
      setGenerationNote(`${typeof result.summary === 'string' ? result.summary : 'SKILL.md généré.'} — ${seconds}\u00a0s, ${cost}. Relisez, puis Enregistrer : rien n’est encore en base.`);
    } catch (error) { setGenerationNote((error as Error).message); }
    finally { setGenerating(false); }
  }
  return <div className="modal-backdrop"><div className="modal skill-modal">
    <div className="modal-head"><div><p className="eyebrow">SKILL BUILDER</p><h2>{skill ? `Modifier « ${skill.name} »` : 'Nouveau skill spécialisé'}</h2></div><button className="icon-button" onClick={onClose}><X size={18} /></button></div>
    {system && <div className="system-skill-banner"><ShieldCheck size={15} /><span><strong>Skill système.</strong> Votre version remplace celle livrée pour tous les projets qui le sélectionnent, et survit aux redémarrages. Son nom et son identifiant ne changent pas.</span><div>{origin?.differs && <Button variant="ghost" icon={ScrollText} onClick={() => setShowOrigin(value => !value)}>{showOrigin ? 'Masquer la version livrée' : 'Comparer'}</Button>}<Button variant="secondary" icon={Undo2} disabled={busy || !origin?.differs} onClick={revert}>Version livrée</Button></div></div>}
    <div className="skill-editor-layout">
      <div className="skill-fields">
        <label>Nom {system && <small>fixé pour un skill système</small>}<input value={data.name} disabled={system} onChange={e => setData({ ...data, name: e.target.value })} placeholder="ex. incident-responder" /></label>
        <label>Domaine<select value={data.domain} onChange={e => setData({ ...data, domain: e.target.value })}><option value="business">Métier</option><option value="governance">Gouvernance</option><option value="quality">Qualité</option><option value="multi-agent">Multi-agent</option><option value="engineering">Ingénierie</option></select></label>
        <label>Description<textarea rows={3} value={data.description} onChange={e => setData({ ...data, description: e.target.value })} /></label>
        <div className="skill-author-box"><div className="form-subhead"><span>GÉNÉRATION PAR AGENT DE CODAGE</span><Pill tone="blue">AUTEUR</Pill></div><div className="skill-author-grid"><label>Agent<select value={authoring.agent} onChange={e => setAuthoring({ ...authoring, agent: e.target.value })}><option value="codex" disabled={AUTHORING_AVAILABLE.codex === false}>Codex CLI{AUTHORING_AVAILABLE.codex === false ? ' — non installé' : ''}</option><option value="claude-code" disabled={AUTHORING_AVAILABLE['claude-code'] === false}>Claude Code{AUTHORING_AVAILABLE['claude-code'] === false ? ' — non installé' : ''}</option></select></label><label>Effort<select value={authoring.effort} onChange={e => setAuthoring({ ...authoring, effort: e.target.value })}><option>medium</option><option>high</option><option>xhigh</option><option>max</option></select></label></div><label>Modèle auteur <small>vide = défaut du CLI</small><input value={authoring.model} onChange={e => setAuthoring({ ...authoring, model: e.target.value })} placeholder="Configuration du CLI" /></label><label>Consignes spécialisées<textarea rows={4} value={authoring.instructions} onChange={e => setAuthoring({ ...authoring, instructions: e.target.value })} placeholder="Règles métier, invariants, tests attendus…" /></label><Button variant="secondary" icon={generating ? LoaderCircle : Sparkles} disabled={generating || data.name.length < 2} onClick={generate}>{generating ? 'Génération…' : `Générer avec ${authoring.agent === 'codex' ? 'Codex' : 'Claude Code'}`}</Button>{generationNote && <p className="generation-note">{generationNote}</p>}</div>
        <div className="setting-note"><Sparkles size={15} />L’agent auteur rédige ce skill ; il ne devient pas le LLM runtime des projets.</div>
      </div>
      <div className="markdown-editor"><div className="editor-toolbar"><span>SKILL.md</span><Pill tone="teal">MARKDOWN</Pill></div>{showOrigin && origin
        ? <div className="skill-origin-diff"><DiffView before={origin.content} after={data.content} title="livrée → la vôtre" /></div>
        : <textarea spellCheck={false} value={data.content} onChange={e => setData({ ...data, content: e.target.value })} />}</div>
    </div>
    <div className="modal-footer"><Button variant="ghost" onClick={onClose}>Annuler</Button><Button icon={busy ? LoaderCircle : Save} disabled={busy || generating || data.name.length < 2 || data.content.length < 20} onClick={submit}>{busy ? 'Enregistrement…' : 'Enregistrer'}</Button></div>
  </div></div>;
}

const TRACE_FILTERS = ['perception', 'reasoning', 'planning', 'plan', 'action', 'approval', 'blocked', 'verified', 'memory', 'message', 'goal', 'info'];
const TRACE_LABELS: Record<string, string> = { perception: 'perception', reasoning: 'raisonnement', planning: 'planification', plan: 'plan', action: 'action', approval: 'approbation', blocked: 'bloqué', verified: 'vérifié', memory: 'mémoire', message: 'message', goal: 'objectif', info: 'info', tick: 'tick', step: 'étape', belief: 'croyance', error: 'erreur', 'verification-failed': 'échec' };

function TracePanel({ run, onReplay, busy }: { run: Run | null; onReplay: () => void; busy: boolean }) {
  const events = run?.metadata?.events || [];
  const [filters, setFilters] = useState<string[]>([]);
  const [agent, setAgent] = useState('');
  // Dans une société, chaque agent redémarre ses ticks à 1 : sans filtre par
  // agent, les deux traces se lisent l'une dans l'autre.
  const agents = useMemo(() => Array.from(new Set(events.map(e => e.agent).filter(Boolean))), [events]);
  const scoped = agent ? events.filter(event => event.agent === agent) : events;
  const visible = filters.length ? scoped.filter(event => filters.includes(event.kind) || event.kind === 'tick' || event.kind === 'agent') : scoped;
  const metrics = run?.metadata?.metrics || {};
  const usage = run?.metadata?.runtimeUsage;
  const crossings = run?.metadata?.journal?.crossings || {};
  function toggle(kind: string) { setFilters(current => current.includes(kind) ? current.filter(item => item !== kind) : [...current, kind]); }
  if (!run) return <Empty icon={Activity} title="Aucune trace" description="Lancez l’agent pour observer sa boucle de décision." />;
  return <div className="trace-panel"><div className="trace-toolbar"><div className="trace-cycle">OBSERVE <ArrowRight size={12} /> PLANIFIE <ArrowRight size={12} /> EXÉCUTE <ArrowRight size={12} /> VÉRIFIE</div><div className="trace-actions">{run.metadata?.artifacts?.trace && <a className="button button-ghost" href={`/api/runs/${run.id}/trace-html`} target="_blank" rel="noreferrer"><ExternalLink size={14} /> HTML</a>}{run.metadata?.journal && <Button variant="secondary" icon={busy ? LoaderCircle : RefreshCw} onClick={onReplay} disabled={busy}>Rejouer</Button>}</div></div>
    {agents.length > 1 && <div className="trace-agents"><span>AGENT</span><button className={agent === '' ? 'active' : ''} onClick={() => setAgent('')}>tous ({agents.length})</button>{agents.map(name => <button key={name} className={agent === name ? 'active' : ''} onClick={() => setAgent(name)}><Bot size={12} />{name}</button>)}</div>}
    <div className="trace-filters"><span>FILTRER</span>{TRACE_FILTERS.map(kind => <button key={kind} className={`${filters.includes(kind) ? 'active' : ''} filter-${kind}`} onClick={() => toggle(kind)}><i />{TRACE_LABELS[kind]}</button>)}</div>
    <div className="trace-layout"><div className="timeline">{visible.length ? visible.map((event, index) => <TraceRow key={`${index}-${event.agent}-${event.tick}`} event={event} showAgent={!agent && agents.length > 1} />) : <Empty icon={Search} title="Aucun événement" description="Aucun événement ne correspond aux filtres." />}</div>
      <aside className="trace-metrics"><p className="eyebrow">MESURES</p>{Object.entries(metrics).slice(0, 12).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{value}</strong></div>)}
        {usage && <><p className="eyebrow spaced">RUNTIME</p><div><span>appels</span><strong>{usage.calls}</strong></div><div><span>tokens</span><strong>{usage.inputTokens + usage.outputTokens}</strong></div><div><span>coût</span><strong>{usage.priced ? `${(usage.costUsd || 0).toFixed(4)} $` : 'non tarifé'}</strong></div></>}
        <div className="journal-proof"><ShieldCheck size={16} /><strong>Journal de frontière</strong><span>{run.metadata?.journal?.entries || 0} franchissements</span><small>{Object.entries(crossings).map(([k, v]) => `${k} ${v}`).join(' · ')}</small></div></aside></div></div>;
}

function TraceRow({ event, showAgent }: { event: TraceEvent; showAgent?: boolean }) {
  if (event.kind === 'agent') return <div className="agent-row"><Bot size={13} /><span>{event.text}</span></div>;
  if (event.kind === 'tick') return <div className="tick-row"><span>tick {event.tick}{showAgent && event.agent ? ` · ${event.agent}` : ''}</span></div>;
  const icons: Record<string, LucideIcon> = { perception: Activity, reasoning: Sparkles, planning: GitBranch, plan: Play, action: Zap, approval: KeyRound, blocked: LockKeyhole, verified: CheckCircle2, memory: Database, message: MessageSquareCode, goal: CircleGauge, error: X, 'verification-failed': X };
  const Icon = icons[event.kind] || ChevronRight;
  return <div className={`trace-row trace-${event.kind}`}><div className="trace-glyph"><Icon size={13} /></div><div><div className="trace-title"><span>{TRACE_LABELS[event.kind] || event.kind}</span><strong>{event.text}</strong></div>{event.detail && <small>{event.detail}</small>}</div></div>;
}

type WorkspaceMode = 'build' | 'tests' | 'trace' | 'graph' | 'optimize' | 'history' | 'config';
type ChatMessage = { role: 'user' | 'assistant'; text: string; meta?: string };

const COMMAND_LABEL: Record<string, string> = {
  'quality-suite': 'les quatre portes', run: 'l’exécution', replay: 'le rejeu',
  autoloop: 'la boucle de correction', viz: 'le graphe', check: 'check',
  test: 'test', verify: 'verify', boundary: 'boundary',
};

/** Diff par lignes : LCS classique, suffisant pour deux sources d’agent. */
function diffLines(before: string, after: string) {
  const a = before.split('\n'), b = after.split('\n');
  const table: number[][] = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1)
    for (let j = b.length - 1; j >= 0; j -= 1)
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  const rows: { kind: 'same' | 'add' | 'remove'; text: string }[] = [];
  let i = 0, j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { rows.push({ kind: 'same', text: a[i] }); i += 1; j += 1; }
    else if (table[i + 1][j] >= table[i][j + 1]) { rows.push({ kind: 'remove', text: a[i] }); i += 1; }
    else { rows.push({ kind: 'add', text: b[j] }); j += 1; }
  }
  while (i < a.length) { rows.push({ kind: 'remove', text: a[i] }); i += 1; }
  while (j < b.length) { rows.push({ kind: 'add', text: b[j] }); j += 1; }
  return rows;
}

function DiffView({ before, after, title }: { before: string; after: string; title: string }) {
  const rows = useMemo(() => diffLines(before, after), [before, after]);
  const added = rows.filter(r => r.kind === 'add').length;
  const removed = rows.filter(r => r.kind === 'remove').length;
  return <div className="diff-block">
    <div className="diff-head"><FileCode2 size={13} /><strong>{title}</strong><span className="diff-add">+{added}</span><span className="diff-remove">−{removed}</span></div>
    <pre className="diff-body">{rows.map((row, index) => <span key={index} className={`diff-line diff-${row.kind}`}>{row.kind === 'add' ? '+' : row.kind === 'remove' ? '−' : ' '} {row.text || ' '}</span>)}</pre>
  </div>;
}

function GateStrip({ gates }: { gates: Gate[] }) {
  if (!gates.length) return null;
  return <div className="gate-strip">{gates.map(gate => <span key={gate.name} className={`gate-chip gate-${gate.status}`}><StatusDot status={gate.status} />agentl {gate.name}</span>)}</div>;
}

/** Revue d’une proposition avant qu’elle ne remplace le programme. */
function DraftReview({ draft, busy, onApply, onDiscard }: { draft: Draft; busy: boolean; onApply: () => void; onDiscard: () => void }) {
  const [tab, setTab] = useState<'agent' | 'host'>('agent');
  const current = draft.current || { agent: '', host: '' };
  const gates = (draft.application?.gates || []) as Gate[];
  return <div className="draft-review">
    <div className="draft-head"><div><p className="eyebrow">PROPOSITION</p><h3>{draft.summary || 'Modification proposée'}</h3></div><Pill tone="violet">{(draft.mode || '').toUpperCase() || 'AUTEUR'}</Pill></div>
    <GateStrip gates={gates} />
    <div className="draft-tabs"><button className={tab === 'agent' ? 'active' : ''} onClick={() => setTab('agent')}>.agent</button><button className={tab === 'host' ? 'active' : ''} onClick={() => setTab('host')}>.py</button></div>
    {tab === 'agent'
      ? <DiffView before={current.agent} after={draft.agent_source || ''} title="programme AGENT-L" />
      : <DiffView before={current.host} after={draft.host_source || ''} title="hôte Python" />}
    <div className="draft-actions"><Button variant="ghost" icon={X} onClick={onDiscard} disabled={busy}>Rejeter</Button><Button icon={busy ? LoaderCircle : Check} onClick={onApply} disabled={busy}>{busy ? 'Les quatre portes tournent…' : 'Valider puis appliquer'}</Button></div>
  </div>;
}

/** Sortie du CLI au fil de l’eau, avec le bouton qui arrête vraiment. */
function LiveTerminal({ title, lines, running, onCancel }: { title: string; lines: string[]; running: boolean; onCancel: () => void }) {
  return <section className="terminal-output live">
    <div className="terminal-bar"><div><i /><i /><i /></div><span>{title}</span>{running ? <><Pill tone="amber"><Radio size={11} /> EN COURS</Pill><button className="icon-button" title="Arrêter" onClick={onCancel}><Ban size={14} /></button></> : <small>{lines.length} lignes</small>}</div>
    <pre>{lines.length ? lines.join('\n') : 'Les résultats apparaîtront ici, ligne par ligne.'}</pre>
  </section>;
}

function GraphPanel({ run, busy, onBuild }: { run: Run | null; busy: boolean; onBuild: () => void }) {
  return <div className="graph-page">
    <section className="quality-hero"><div><Pill tone="violet"><Share2 size={12} /> GRAPHE</Pill><h2>La forme de l’agent, pas seulement son texte.</h2><p>Produit par <code>agentl viz</code> : capteurs, croyances, objectifs, plans, outils et vérifications, dans leurs sept lanes.</p></div><Button icon={busy ? LoaderCircle : Share2} disabled={busy} onClick={onBuild}>{busy ? 'Génération…' : 'Générer le graphe'}</Button></section>
    {run?.metadata?.artifacts?.graph
      ? <iframe className="graph-frame" title="Graphe de l’agent" src={`/api/runs/${run.id}/graph-html`} />
      : <Empty icon={Share2} title="Aucun graphe" description="Générez le graphe pour visualiser la structure de l’agent." />}
  </div>;
}

function OptimizePanel({ project, run, busy, lines, running, onRun, onCancel, onAdopt, toast }: {
  project: Project; run: Run | null; busy: boolean; lines: string[]; running: boolean;
  onRun: () => void; onCancel: () => void; onAdopt: (source: string) => Promise<void>;
  toast: (message: string, kind?: 'success' | 'error') => void;
}) {
  const [corrected, setCorrected] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [adopting, setAdopting] = useState(false);
  const model = project.config.autoloopModel || run?.metadata?.autoloopModel || '';
  const hasCorrection = Boolean(run?.metadata?.artifacts?.corrected);
  useEffect(() => { setCorrected(null); }, [run?.id]);
  async function load() {
    if (!run) return;
    setLoading(true);
    try { setCorrected((await api.corrected(run.id)).content); }
    catch (error) { toast((error as Error).message, 'error'); }
    finally { setLoading(false); }
  }
  return <div className="optimize-page">
    <section className="quality-hero"><div><Pill tone="amber"><Wand2 size={12} /> AUTOLOOP</Pill><h2>Corriger, puis chercher où ça casse.</h2><p>La boucle dérive des cas, corrige tant que l’agent ne tient pas, et garde un lot jamais montré à la correction — la seule mesure de l’apprentissage par cœur.</p></div><Button icon={busy ? LoaderCircle : Repeat2} disabled={busy} onClick={onRun}>{busy ? 'Boucle en cours…' : 'Lancer la boucle'}</Button></section>
    <div className="optimize-facts">
      <div><span>Rédacteur des corrections</span><strong>{model || 'aucun — diagnostic seul'}</strong><small>{model ? 'gemini-* ou claude-*, hérité du runtime ou déclaré' : 'Déclarez un modèle d’autoloop dans la configuration pour que la boucle réécrive.'}</small></div>
      <div><span>Tentatives</span><strong>{project.config.maxAttempts}</strong><small>plafond avant abandon</small></div>
      <div><span>Cas dérivés</span><strong>{project.config.maxCases}</strong><small>dont 30 % retenus hors correction</small></div>
      <div><span>Budget</span><strong>{Math.round(project.config.timeoutSeconds * 0.6)} s</strong><small>strictement sous le timeout, pour que le rapport survive</small></div>
    </div>
    {hasCorrection && <div className="correction-box">
      <div><PackageCheck size={16} /><strong>La boucle a réécrit le programme.</strong><small>Rien n’a été appliqué : relisez le diff avant d’adopter.</small></div>
      <div className="correction-actions"><Button variant="secondary" icon={loading ? LoaderCircle : ScrollText} onClick={load} disabled={loading}>Voir le diff</Button>{corrected && <Button icon={adopting ? LoaderCircle : Check} disabled={adopting} onClick={async () => { setAdopting(true); try { await onAdopt(corrected); } finally { setAdopting(false); } }}>{adopting ? 'Les quatre portes tournent…' : 'Valider puis adopter'}</Button>}</div>
    </div>}
    {corrected && <DiffView before="" after={corrected} title="programme corrigé par la boucle" />}
    <LiveTerminal title={`AGENT-L · autoloop · ${project.slug}`} lines={lines.length ? lines : (run?.output || '').split('\n').filter(Boolean)} running={running} onCancel={onCancel} />
  </div>;
}

function HistoryPanel({ project, entries, onRestore, toast }: {
  project: Project; entries: HistoryEntry[];
  onRestore: (stamp: string, path: string) => Promise<void>;
  toast: (message: string, kind?: 'success' | 'error') => void;
}) {
  const [preview, setPreview] = useState<{ stamp: string; path: string; content: string } | null>(null);
  const [current, setCurrent] = useState('');
  async function open(entry: HistoryEntry) {
    try {
      const archived = await api.historyFile(project.id, entry.stamp, entry.path);
      let live = '';
      try { live = (await api.file(project.id, entry.path)).content; } catch { live = ''; }
      setCurrent(live); setPreview(archived);
    } catch (error) { toast((error as Error).message, 'error'); }
  }
  if (!entries.length) return <Empty icon={History} title="Aucune version archivée" description="Chaque enregistrement archive la version précédente ; elles apparaîtront ici." />;
  return <div className="history-page">
    <aside className="history-list"><div className="panel-heading"><span>VERSIONS</span><Pill tone="neutral">{entries.length}</Pill></div>
      {entries.map(entry => <button key={`${entry.stamp}-${entry.path}`} className={preview?.stamp === entry.stamp && preview?.path === entry.path ? 'active' : ''} onClick={() => open(entry)}>
        <Clock3 size={13} /><span><strong>{entry.path}</strong><small>{entry.stamp.replace('T', ' ').replace('Z', '')}</small></span></button>)}
    </aside>
    <section className="history-detail">{preview
      ? <><div className="history-head"><div><p className="eyebrow">{preview.stamp}</p><h3>{preview.path}</h3></div><Button icon={Undo2} onClick={() => onRestore(preview.stamp, preview.path)}>Restaurer cette version</Button></div>
          <DiffView before={preview.content} after={current} title="archivée → courante" /></>
      : <Empty icon={ScrollText} title="Choisissez une version" description="La colonne de gauche liste chaque sauvegarde faite avant écrasement." />}</section>
  </div>;
}

function Workspace({ base, skills, initialRun, onBack, onChanged, toast }: { base: Project; skills: Skill[]; initialRun?: Run | null; onBack: () => void; onChanged: () => void; toast: (message: string, kind?: 'success' | 'error') => void }) {
  const [project, setProject] = useState(base);
  const [mode, setMode] = useState<WorkspaceMode>(initialRun?.metadata?.events?.length ? 'trace' : 'build');
  const [selectedFile, setSelectedFile] = useState('');
  const [content, setContent] = useState('');
  const [savedContent, setSavedContent] = useState('');
  const [loadingFile, setLoadingFile] = useState(false);
  const [busyCommand, setBusyCommand] = useState('');
  const [currentRun, setCurrentRun] = useState<Run | null>(initialRun || null);
  const [liveLines, setLiveLines] = useState<string[]>([]);
  const [runningRunId, setRunningRunId] = useState('');
  const [prompt, setPrompt] = useState('');
  const [applyDraft, setApplyDraft] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [applyingDraft, setApplyingDraft] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: 'assistant', text: 'Projet chargé. Je peux modifier le programme avec les skills sélectionnés, puis laisser les quatre portes AGENT-L valider le résultat avant qu’il ne remplace quoi que ce soit.', meta: `${base.config.authoringAgent === 'claude-code' ? 'CLAUDE CODE' : 'CODEX'} · PRÊT` }]);
  const [assistantBusy, setAssistantBusy] = useState(false);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const [pendingLine, setPendingLine] = useState<number | null>(null);
  const followedInitialRun = useRef('');
  const authorName = project.config.authoringAgent === 'claude-code' ? 'Claude Code' : 'Codex';
  const authorLabel = authorName.toUpperCase();
  const runtimeName = project.config.runtimeProvider === 'google-gemini' ? 'Gemini cloud' : project.config.runtimeProvider === 'mock' ? 'Mock local' : project.config.runtimeProvider;
  const selectedSkills = skills.filter(skill => project.skills.includes(skill.id));
  const gates = (project.runs?.find(run => run.command === 'quality-suite')?.metadata?.gates || []) as Gate[];
  const autoloopRun = currentRun?.command === 'autoloop' ? currentRun : project.runs?.find(r => r.command === 'autoloop') || null;
  const graphRun = currentRun?.command === 'viz' ? currentRun : project.runs?.find(r => r.metadata?.artifacts?.graph) || null;

  async function reload() {
    const fresh = await api.project(project.id); setProject(fresh); onChanged(); return fresh;
  }
  useEffect(() => { api.project(base.id).then(fresh => { setProject(fresh); const first = fresh.entrypoint || fresh.files?.find(file => file.path.endsWith('.agent'))?.path || fresh.files?.[0]?.path; if (first) loadFile(first); }).catch(error => toast(error.message, 'error')); }, [base.id]);
  // Un run peut déjà tourner à l’ouverture : la génération d’un projet tout
  // juste créé, ou un run choisi dans la liste des exécutions. On le suit.
  useEffect(() => {
    if (initialRun?.status !== 'running' || followedInitialRun.current === initialRun.id) return;
    followedInitialRun.current = initialRun.id;
    if (initialRun.command.startsWith('authoring')) {
      setMessages(current => [...current, {
        role: 'assistant',
        text: initialRun.command === 'authoring-create'
          ? 'Génération de l’implémentation initiale à partir du nom, de la description, de l’architecture, des rôles et des skills. Les quatre portes l’éprouveront avant qu’elle ne remplace le gabarit.'
          : 'Une proposition est en cours de rédaction.',
        meta: `${authorLabel} · EN COURS`,
      }]);
      followAuthoring(initialRun);
    } else {
      setBusyCommand(initialRun.command);
      follow(initialRun).then(finished => settle(initialRun.command, finished)).finally(() => setBusyCommand(''));
    }
  }, [initialRun?.id]);
  // Un diagnostic cite une ligne : l’ouvrir mène à cette ligne, pas en haut du fichier.
  useEffect(() => {
    const editor = editorRef.current;
    if (!pendingLine || !editor || loadingFile) return;
    const lines = content.split('\n');
    const start = lines.slice(0, pendingLine - 1).reduce((total, line) => total + line.length + 1, 0);
    editor.focus();
    editor.setSelectionRange(start, start + (lines[pendingLine - 1]?.length || 0));
    editor.scrollTop = Math.max(0, (pendingLine - 4) * (parseFloat(getComputedStyle(editor).lineHeight) || 22));
    setPendingLine(null);
  }, [pendingLine, content, loadingFile, mode]);
  async function openDiagnostic(diagnostic: Diagnostic) {
    const entry = project.entrypoint;
    if (!entry || !diagnostic.line) return;
    setMode('build');
    if (selectedFile !== entry) await loadFile(entry);
    setPendingLine(diagnostic.line);
  }
  useEffect(() => { if (mode === 'history') api.history(project.id).then(setHistory).catch(() => undefined); }, [mode, project.id]);
  async function loadFile(path: string) { setLoadingFile(true); try { const file = await api.file(base.id, path); setSelectedFile(path); setContent(file.content); setSavedContent(file.content); } catch (error) { toast((error as Error).message, 'error'); } finally { setLoadingFile(false); } }
  async function saveFile() { try { await api.saveFile(project.id, selectedFile, content); setSavedContent(content); toast('Fichier enregistré'); await reload(); } catch (error) { toast((error as Error).message, 'error'); } }

  // Le run vit côté serveur : la requête ne le porte plus. On suit sa sortie
  // ligne à ligne ; si le flux se coupe, `api.stream` relit l’état du run au
  // lieu de laisser l’espace de travail attendre une fin qui ne viendrait pas.
  function follow(run: Run) {
    setRunningRunId(run.id); setLiveLines([]);
    return new Promise<Run>(resolve => {
      api.stream(run.id,
        line => setLiveLines(current => [...current, line]),
        finished => { setRunningRunId(''); resolve(finished); });
    });
  }

  async function settle(command: string, finished: Run) {
    setCurrentRun(finished);
    await reload();
    const label = COMMAND_LABEL[command] || command;
    if (finished.status === 'cancelled') toast(`${label} arrêté`, 'error');
    else if (finished.status === 'interrupted') toast(`${label} interrompu : le serveur s’est arrêté pendant le run`, 'error');
    else toast(finished.status === 'passed' ? `agentl ${command} réussi` : `agentl ${command} a échoué`, finished.status === 'passed' ? 'success' : 'error');
    if ((command === 'run' || command === 'replay') && finished.status === 'passed') setMode('trace');
  }

  async function execute(command: string, runId?: string) {
    setBusyCommand(command);
    if (command === 'quality-suite') setMode('tests');
    else if (command === 'autoloop') setMode('optimize');
    else if (command === 'viz') setMode('graph');
    try {
      const started = await api.startRun(project.id, command, runId);
      setCurrentRun(started);
      await settle(command, await follow(started));
    } catch (error) { toast((error as Error).message, 'error'); setRunningRunId(''); }
    finally { setBusyCommand(''); }
  }
  async function cancel() { if (runningRunId) { try { await api.cancelRun(runningRunId); } catch (error) { toast((error as Error).message, 'error'); } } }

  // L’agent auteur est un run : on suit sa progression, on peut l’arrêter,
  // puis on relit la proposition qu’il a rangée dans ses artefacts.
  async function followAuthoring(run: Run) {
    setAssistantBusy(true); setDraft(null);
    try {
      const finished = await follow(run);
      const fresh = await reload();
      if (finished.status === 'cancelled' || finished.status === 'interrupted') {
        setMessages(current => [...current, {
          role: 'assistant',
          text: finished.status === 'cancelled' ? 'Génération arrêtée : rien n’a été écrit.' : 'Le serveur s’est arrêté pendant la génération : rien n’a été écrit. Relancez la demande.',
          meta: `${authorLabel} · ${finished.status === 'cancelled' ? 'ARRÊTÉ' : 'INTERROMPU'}`,
        }]);
        return;
      }
      let result: Draft;
      try { result = await api.draft(finished.id); }
      catch {
        const reason = finished.metadata?.error || finished.output.split('\n').filter(Boolean).slice(-4).join('\n') || 'Aucune proposition rendue.';
        setMessages(current => [...current, { role: 'assistant', text: reason, meta: 'ERREUR' }]);
        return;
      }
      const summary = result.summary || 'Proposition générée.';
      const application = result.application;
      const cost = result.cost_measured === false ? 'coût non mesuré' : `${(result.cost_usd || 0).toFixed(4)} $`;
      setMessages(current => [...current, {
        role: 'assistant', text: summary,
        meta: application
          ? (application.applied ? `${authorLabel} · APPLIQUÉ · 4 PORTES OK · ${cost}` : `${authorLabel} · REFUSÉ PAR LES PORTES · ${cost}`)
          : `${authorLabel} · À RELIRE · ${cost}`,
      }]);
      if (application?.applied) {
        const path = fresh.entrypoint || fresh.files?.find(file => file.path.endsWith('.agent'))?.path;
        if (path) loadFile(path);
      } else {
        setDraft(result);
        if (application) setMessages(current => [...current, { role: 'assistant', text: (application.validation || '').slice(-1500) || 'Les portes ont refusé la proposition.', meta: 'SORTIE DES PORTES' }]);
      }
    } catch (error) {
      setMessages(current => [...current, { role: 'assistant', text: (error as Error).message, meta: 'ERREUR' }]);
    } finally { setAssistantBusy(false); }
  }

  async function sendPrompt() {
    if (!prompt.trim() || assistantBusy || runningRunId) return;
    const value = prompt.trim();
    setMessages(current => [...current, { role: 'user', text: value }]);
    setPrompt('');
    try { await followAuthoring(await api.assistant(project.id, value, applyDraft)); }
    catch (error) { setMessages(current => [...current, { role: 'assistant', text: (error as Error).message, meta: 'ERREUR' }]); }
  }

  async function applyCurrentDraft() {
    if (!draft?.agent_source || !draft?.host_source) return;
    setApplyingDraft(true);
    try {
      const result = await api.applyDraft(project.id, { summary: draft.summary || '', agent_source: draft.agent_source, host_source: draft.host_source });
      if (result.applied) {
        toast('Proposition appliquée : les quatre portes passent');
        setDraft(null);
        const fresh = await reload();
        const path = fresh.entrypoint || fresh.files?.find(file => file.path.endsWith('.agent'))?.path;
        if (path) loadFile(path);
      } else {
        toast('Refusée par les portes — rien n’a été écrit', 'error');
        setDraft({ ...draft, application: { applied: false, gates: result.gates as Gate[], validation: result.validation } });
        setMessages(current => [...current, { role: 'assistant', text: (result.validation || '').slice(-1500), meta: 'SORTIE DES PORTES' }]);
      }
    } catch (error) { toast((error as Error).message, 'error'); }
    finally { setApplyingDraft(false); }
  }

  // Adopter la correction d’autoloop passe par les quatre portes, comme toute
  // proposition : l’écrire directement sautait précisément la validation.
  async function adoptCorrection(source: string) {
    const entry = project.entrypoint || project.files?.find(file => file.path.endsWith('.agent'))?.path;
    if (!entry) return;
    try {
      const host = (await api.file(project.id, entry.replace(/\.agent$/, '.py'))).content;
      setLiveLines(['Les quatre portes éprouvent la correction dans une copie du projet…']);
      const result = await api.applyDraft(project.id, { summary: 'Correction proposée par autoloop', agent_source: source, host_source: host });
      setLiveLines((result.validation || '').split('\n'));
      if (result.applied) {
        toast('Correction adoptée : les quatre portes passent. La version précédente est dans l’historique');
        await reload(); loadFile(entry);
      } else toast('Correction refusée par les portes : le programme n’a pas été modifié', 'error');
    } catch (error) { toast((error as Error).message, 'error'); }
  }

  async function restore(stamp: string, path: string) {
    try {
      await api.restoreHistory(project.id, stamp, path);
      toast(`${path} restauré`);
      setHistory(await api.history(project.id));
      await reload();
      if (path === selectedFile) loadFile(path);
    } catch (error) { toast((error as Error).message, 'error'); }
  }

  async function saveConfig(config: ProjectConfig) { try { const updated = await api.updateProject(project.id, { config }); setProject({ ...project, ...updated }); toast('Configuration enregistrée'); onChanged(); } catch (error) { toast((error as Error).message, 'error'); } }

  const tabs: [WorkspaceMode, string, LucideIcon][] = [
    ['build', 'Construire', Code2], ['tests', 'Tests', TestTube2], ['trace', 'Trace', Activity],
    ['graph', 'Graphe', Share2], ['optimize', 'Optimiser', Wand2], ['history', 'Historique', History],
    ['config', 'Configuration', Settings],
  ];

  return <div className="workspace-shell">
    <header className="workspace-header"><div className="workspace-breadcrumb"><button className="icon-button" onClick={onBack}><ArrowLeft size={17} /></button><Logo /><ChevronRight size={14} /><span>{project.name}</span><Pill tone="teal"><StatusDot status="passed" /> {project.status}</Pill></div><div className="workspace-actions"><span className="workspace-model"><Code2 size={14} />Auteur : {authorName}</span><span className="workspace-model runtime"><Cpu size={14} />Runtime : {runtimeName} · {project.config.runtimeModel || 'à définir'}</span>{runningRunId ? <Button variant="danger" icon={Ban} onClick={cancel}>Arrêter</Button> : <><Button variant="secondary" icon={busyCommand === 'quality-suite' ? LoaderCircle : PackageCheck} disabled={!!busyCommand} onClick={() => execute('quality-suite')}>Vérifier</Button><Button icon={busyCommand === 'run' ? LoaderCircle : Play} disabled={!!busyCommand} onClick={() => execute('run')}>Exécuter</Button></>}</div></header>
    <div className="workspace-tabs">{tabs.map(([key, label, Icon]) => <button key={key} className={mode === key ? 'active' : ''} onClick={() => setMode(key)}><Icon size={14} />{label}</button>)}<span className="workspace-tabs-fill" /><span className="dirty-state">{content !== savedContent ? '● modifications non enregistrées' : '✓ synchronisé'}</span></div>
    {mode === 'build' && <div className="build-grid"><aside className="project-panel"><div className="panel-heading"><span>PROJET</span><button className="icon-button" onClick={() => reload()}><RefreshCw size={13} /></button></div><div className="project-root"><Folder size={14} />{project.slug}</div><div className="file-tree">{project.files?.map(file => <button key={file.path} className={selectedFile === file.path ? 'active' : ''} onClick={() => loadFile(file.path)}>{file.path.endsWith('.agent') ? <Bot size={14} /> : file.path.endsWith('.py') ? <FileCode2 size={14} /> : file.path.endsWith('.json') ? <FileJson size={14} /> : <FileText size={14} />}<span>{file.path}</span></button>)}</div><div className="panel-heading spaced"><span>SKILLS</span><Pill tone="neutral">{selectedSkills.length}</Pill></div><div className="active-skills">{selectedSkills.map(skill => <div key={skill.id}><span className="skill-symbol small"><Sparkles size={12} /></span><span><strong>{skill.name}</strong><small>{skill.domain}</small></span><CheckCircle2 size={13} /></div>)}</div><div className="panel-heading spaced"><span>PORTES</span></div><div className="gate-mini-list">{['check', 'test', 'verify', 'boundary'].map(name => { const gate = gates.find(item => item.name === name); return <div key={name}><StatusDot status={gate?.status || 'idle'} /><span>{name}</span><small>{gate ? (gate.status === 'passed' ? (gate.warnings ? `passe · ${gate.warnings} avert.` : 'passe') : gate.status) : 'non lancé'}</small></div>; })}</div></aside>
      <section className="assistant-panel"><div className="assistant-head"><div><span className="codex-dot" /><strong>{authorName}</strong><small>agent auteur de code</small></div><Pill tone="violet"><Sparkles size={11} /> {selectedSkills.length} skills</Pill></div><div className="chat-scroll">{messages.map((message, index) => <div key={index} className={`chat-message ${message.role}`}><div className="chat-meta">{message.role === 'assistant' ? <><Sparkles size={12} />{message.meta || authorLabel}</> : 'VOUS'}</div><p>{message.text}</p></div>)}{assistantBusy && <div className="chat-message assistant authoring-live"><div className="chat-meta"><LoaderCircle size={12} className="spin" />{authorLabel} TRAVAILLE{runningRunId && <button className="text-button stop-author" onClick={cancel}><Ban size={12} /> Arrêter</button>}</div>{liveLines.length ? <pre>{liveLines.slice(-6).join('\n')}</pre> : <div className="typing"><i /><i /><i /></div>}</div>}{draft && <DraftReview draft={draft} busy={applyingDraft} onApply={applyCurrentDraft} onDiscard={() => setDraft(null)} />}</div><div className="prompt-box"><textarea value={prompt} onChange={e => setPrompt(e.target.value)} onKeyDown={e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') sendPrompt(); }} placeholder={`Décrivez à ${authorName} l’agent ou la modification…`} /><div><label className="apply-toggle" title="Sans cette case, la proposition est affichée en diff et n’écrase rien tant que vous ne l’appliquez pas."><input type="checkbox" checked={applyDraft} onChange={e => setApplyDraft(e.target.checked)} />Appliquer directement si les 4 portes passent</label><button disabled={assistantBusy || !!runningRunId || !prompt.trim()} onClick={sendPrompt}><ArrowRight size={16} /></button></div></div></section>
      <section className="code-panel"><div className="editor-head"><div><FileCode2 size={14} /><strong>{selectedFile || 'Aucun fichier'}</strong>{content !== savedContent && <i />}</div><div><span>AGENT-L 1.8</span><Button variant="ghost" icon={Save} disabled={!selectedFile || content === savedContent} onClick={saveFile}>Enregistrer</Button></div></div><div className="code-editor">{loadingFile ? <LoaderCircle className="spin" /> : <><div className="line-numbers">{content.split('\n').map((_, i) => <span key={i}>{i + 1}</span>)}</div><textarea ref={editorRef} aria-label="Éditeur de code" spellCheck={false} value={content} onChange={e => setContent(e.target.value)} /></>}</div><div className="editor-status"><span>UTF-8</span><span>LF</span><span>{content.split('\n').length} lignes</span><span className="fill" /><span><ShieldCheck size={12} /> édition historisée</span></div></section></div>}
    {mode === 'tests' && <TestsPanel project={project} run={currentRun?.command === 'quality-suite' ? currentRun : project.runs?.find(r => r.command === 'quality-suite') || null} busy={!!busyCommand} lines={liveLines} running={!!runningRunId} onCancel={cancel} onRun={() => execute('quality-suite')} onCommand={execute} onOpenDiagnostic={openDiagnostic} />}
    {mode === 'trace' && <TracePanel run={currentRun?.metadata?.events?.length ? currentRun : project.runs?.find(r => r.metadata?.events?.length) || null} busy={!!busyCommand} onReplay={() => currentRun && execute('replay', currentRun.id)} />}
    {mode === 'graph' && <GraphPanel run={graphRun} busy={busyCommand === 'viz'} onBuild={() => execute('viz')} />}
    {mode === 'optimize' && <OptimizePanel project={project} run={autoloopRun} busy={!!busyCommand} lines={liveLines} running={!!runningRunId} onRun={() => execute('autoloop')} onCancel={cancel} onAdopt={adoptCorrection} toast={toast} />}
    {mode === 'history' && <HistoryPanel project={project} entries={history} onRestore={restore} toast={toast} />}
    {mode === 'config' && <ProjectConfigPanel project={project} skills={skills} onSave={saveConfig} onUpdateSkills={async ids => { const updated = await api.updateProject(project.id, { skills: ids }); setProject({ ...project, ...updated }); toast('Skills du projet mis à jour'); }} toast={toast} />}
  </div>;
}

function TestsPanel({ project, run, busy, lines, running, onCancel, onRun, onCommand, onOpenDiagnostic }: { project: Project; run: Run | null; busy: boolean; lines: string[]; running: boolean; onCancel: () => void; onRun: () => void; onCommand: (command: string) => void; onOpenDiagnostic: (diagnostic: Diagnostic) => void }) {
  const gates = run?.metadata?.gates || [];
  // Une porte peut passer avec des avertissements : les montrer, avec leur ligne.
  const diagnostics = run?.metadata?.diagnostics || [];
  const errorCount = diagnostics.filter(item => item.severity === 'error').length;
  const warningCount = diagnostics.length - errorCount;
  return <div className="tests-page"><section className="quality-hero"><div><Pill tone="teal"><ShieldCheck size={12} /> QUALITY GATES</Pill><h2>Prouver avant d’exécuter.</h2><p>Les résultats ci-dessous viennent directement du CLI AGENT-L, dans le workspace du projet.</p></div><Button icon={busy ? LoaderCircle : PackageCheck} disabled={busy} onClick={onRun}>{busy ? 'Vérification…' : 'Tout vérifier'}</Button></section><div className="gate-grid">{[
    ['check', 'Analyse statique', 'Syntaxe, contrats, politiques et invariants', Braces],
    ['test', 'Scénarios', 'Chemins nominaux, refus et conditions limites', TestTube2],
    ['verify', 'Vérification bornée', 'Routes sûres, vivacité et postconditions', GitBranch],
    ['boundary', 'Frontières Python', 'Couplage hôte, capteurs et outils', ShieldCheck],
  ].map(([name, title, desc, Icon]) => { const gate = gates.find(item => item.name === name); const I = Icon as LucideIcon; return <article className={`gate-card gate-${gate?.status || 'idle'}`} key={name as string}><div className="gate-icon"><I size={18} /></div><div><span>agentl {name as string}</span><h3>{title as string}</h3><p>{desc as string}</p></div><div className={`gate-result ${gate?.warnings ? 'has-warnings' : ''}`}>{gate ? <><StatusDot status={gate.status} /><strong>{gate.status === 'passed' ? 'PASSE' : gate.status === 'skipped' ? 'NON LANCÉE' : gate.status === 'timeout' ? 'DÉLAI' : gate.status === 'cancelled' ? 'ARRÊTÉE' : 'ÉCHEC'}{gate.warnings ? ` · ${gate.warnings} AVERT.` : ''}</strong></> : <span>NON LANCÉ</span>}</div><button className="icon-button" disabled={busy} onClick={() => onCommand(name as string)}><Play size={14} /></button></article>; })}</div>{diagnostics.length > 0 && <section className="diagnostics" aria-label="Diagnostics des portes"><div className="diagnostics-head"><span>DIAGNOSTICS</span><small>{errorCount} erreur{errorCount > 1 ? 's' : ''} · {warningCount} avertissement{warningCount > 1 ? 's' : ''}</small></div>{diagnostics.map((item, index) => <button key={`${item.gate}-${item.code}-${index}`} className={`diagnostic diagnostic-${item.severity}`} disabled={!item.line} title={item.line ? `Ouvrir la ligne ${item.line}` : undefined} onClick={() => onOpenDiagnostic(item)}><b>{item.code}</b><span className="diagnostic-where">{item.agent || 'programme'}{item.line ? ` · ligne ${item.line}` : ''}</span><span className="diagnostic-message">{item.message}</span></button>)}</section>}<LiveTerminal title={`AGENT-L · ${project.slug}${run ? ` · ${duration(run.duration_ms)}` : ''}`} lines={lines.length ? lines : (run?.output || '').split('\n')} running={running} onCancel={onCancel} /></div>;
}

function ProjectConfigPanel({ project, skills, onSave, onUpdateSkills, toast }: { project: Project; skills: Skill[]; onSave: (config: ProjectConfig) => Promise<void>; onUpdateSkills: (ids: string[]) => Promise<void>; toast: (message: string, kind?: 'success' | 'error') => void }) {
  const [config, setConfig] = useState<ProjectConfig>(structuredClone(project.config));
  const [selected, setSelected] = useState(project.skills);
  const [busy, setBusy] = useState(false);
  const [probing, setProbing] = useState(false);
  const [probe, setProbe] = useState<RuntimeProbe | null>(null);
  const suggestions = RUNTIME_MODELS[config.runtimeProvider] || [];
  // Un aller-retour réel vaut mieux qu'un identifiant de modèle qu'on
  // découvre faux au premier tick d'un run.
  async function testRuntime() {
    setProbing(true); setProbe(null);
    try { setProbe(await api.runtimeTest(project.id)); }
    catch (error) { toast((error as Error).message, 'error'); }
    finally { setProbing(false); }
  }
  async function save() { setBusy(true); try { await onSave(config); if (selected.join() !== project.skills.join()) await onUpdateSkills(selected); } finally { setBusy(false); } }
  function selectRuntime(provider: ProjectConfig['runtimeProvider']) {
    const defaults: Record<ProjectConfig['runtimeProvider'], { model: string; key: string; base: string }> = {
      mock: { model: 'mock-deterministic', key: '', base: '' },
      'google-gemini': { model: '', key: 'GEMINI_API_KEY', base: '' },
      anthropic: { model: '', key: 'ANTHROPIC_API_KEY', base: '' },
      openai: { model: '', key: 'OPENAI_API_KEY', base: '' },
      'openai-compatible': { model: '', key: '', base: 'http://localhost:11434/v1' },
    };
    setConfig({ ...config, runtimeProvider: provider, runtimeModel: defaults[provider].model, runtimeApiKeyEnv: defaults[provider].key, runtimeBaseUrl: defaults[provider].base });
  }
  return <div className="config-page">
    <div className="config-header"><div><Pill tone="blue"><Settings size={12} /> PROJECT CONTROL</Pill><h2>Configuration du projet</h2><p>L’agent auteur produit le code. Le LLM runtime exécute ensuite les boucles AGENT-L : ce sont deux rôles indépendants.</p></div><Button icon={busy ? LoaderCircle : Save} disabled={busy} onClick={save}>{busy ? 'Enregistrement…' : 'Enregistrer'}</Button></div>
    <div className="model-separation"><div><Code2 size={18} /><span><b>1 · CONSTRUIRE</b><strong>{config.authoringAgent === 'codex' ? 'Codex CLI' : 'Claude Code'}</strong><small>Écrit agents + hôtes + skills</small></span></div><ArrowRight size={18} /><div><PackageCheck size={18} /><span><b>2 · VALIDER</b><strong>Portes AGENT-L</strong><small>check · test · verify · boundary</small></span></div><ArrowRight size={18} /><div className="runtime"><Cpu size={18} /><span><b>3 · EXÉCUTER</b><strong>{config.runtimeProvider === 'google-gemini' ? 'Google Gemini cloud' : config.runtimeProvider}</strong><small>{config.runtimeModel || 'Modèle à définir'}</small></span></div></div>
    <div className="config-grid">
      <section className="config-card author-card"><div className="config-title"><Code2 size={17} /><div><h3>Agent auteur</h3><p>Produit et corrige les sources du projet</p></div><Pill tone="blue">CRÉATION</Pill></div><label>Agent de codage<select value={config.authoringAgent} onChange={e => setConfig({ ...config, authoringAgent: e.target.value as ProjectConfig['authoringAgent'] })}><option value="codex">Codex CLI</option><option value="claude-code">Claude Code</option></select></label><label>Modèle auteur <small>vide = valeur du CLI</small><input value={config.authoringModel} onChange={e => setConfig({ ...config, authoringModel: e.target.value })} placeholder="Configuration du CLI" /></label><div className="price-grid"><label>Effort<select value={config.authoringEffort} onChange={e => setConfig({ ...config, authoringEffort: e.target.value })}><option>low</option><option>medium</option><option>high</option><option>xhigh</option><option>max</option></select></label><label>Budget auteur (USD)<input type="number" min="0" step="0.25" value={config.authoringBudgetUsd} onChange={e => setConfig({ ...config, authoringBudgetUsd: Number(e.target.value) })} /></label></div><label>Timeout auteur (secondes)<input type="number" min="30" max="600" value={config.authoringTimeoutSeconds} onChange={e => setConfig({ ...config, authoringTimeoutSeconds: Number(e.target.value) })} /></label><div className="setting-note"><LockKeyhole size={15} />Mode non interactif, accès au workspace en lecture seule et sortie JSON structurée.</div></section>
      <section className="config-card runtime-card-config"><div className="config-title"><Cpu size={17} /><div><h3>LLM d’exécution</h3><p>Oracle appelé par les agents pendant leurs boucles</p></div><Pill tone="violet">RUNTIME</Pill></div><label>Fournisseur<select value={config.runtimeProvider} onChange={e => selectRuntime(e.target.value as ProjectConfig['runtimeProvider'])}><option value="mock">Mock déterministe</option><option value="google-gemini">Google Gemini cloud</option><option value="anthropic">Anthropic</option><option value="openai">OpenAI</option><option value="openai-compatible">Local / compatible OpenAI</option></select></label><label>Modèle runtime <small>{config.runtimeProvider === 'mock' ? 'déterministe' : 'requis'}</small><input list="runtime-model-list" value={config.runtimeModel} onChange={e => setConfig({ ...config, runtimeModel: e.target.value })} placeholder={config.runtimeProvider === 'google-gemini' ? 'ex. gemini-…' : 'Identifiant exact du modèle'} /><datalist id="runtime-model-list">{suggestions.map(model => <option key={model} value={model} />)}</datalist>{suggestions.length > 0 && <span className="model-chips">{suggestions.map(model => <button key={model} type="button" className={config.runtimeModel === model ? 'active' : ''} onClick={() => setConfig({ ...config, runtimeModel: model })}>{model}</button>)}</span>}</label><label>Variable de clé API<input value={config.runtimeApiKeyEnv} onChange={e => setConfig({ ...config, runtimeApiKeyEnv: e.target.value })} placeholder="ex. GEMINI_API_KEY" /></label><label>URL de base <small>compatible OpenAI uniquement</small><input value={config.runtimeBaseUrl} onChange={e => setConfig({ ...config, runtimeBaseUrl: e.target.value })} placeholder="https://…/v1" /></label><div className="price-grid"><label>Plafond runtime (USD)<input type="number" min="0" step="0.25" value={config.runtimeMaxCostUsd} onChange={e => setConfig({ ...config, runtimeMaxCostUsd: Number(e.target.value) })} /></label><label>Sortie max (tokens)<input type="number" min="64" max="100000" value={config.runtimeMaxOutputTokens} onChange={e => setConfig({ ...config, runtimeMaxOutputTokens: Number(e.target.value) })} /></label></div>
      <div className="price-grid"><label>Tarif entrée <small>USD / M tokens</small><input type="number" min="0" step="0.01" value={config.runtimePricePerMTokIn} onChange={e => setConfig({ ...config, runtimePricePerMTokIn: Number(e.target.value) })} /></label><label>Tarif sortie <small>USD / M tokens</small><input type="number" min="0" step="0.01" value={config.runtimePricePerMTokOut} onChange={e => setConfig({ ...config, runtimePricePerMTokOut: Number(e.target.value) })} /></label></div>
      <div className="setting-note">{config.runtimePricePerMTokIn > 0 || config.runtimePricePerMTokOut > 0 ? <><CircleGauge size={15} />Le plafond est appliqué au runtime : l’appel qui le dépasserait est refusé.</> : <><CircleGauge size={15} />Sans tarif déclaré, appels et tokens sont mesurés mais le plafond ne peut rien borner.</>}</div>
      <div className="runtime-probe"><Button variant="secondary" icon={probing ? LoaderCircle : Radio} disabled={probing || config.runtimeProvider === 'mock'} onClick={testRuntime}>{probing ? 'Appel en cours…' : 'Tester le runtime'}</Button>{probe && <span className={probe.ok ? 'probe-ok' : 'probe-fail'}>{probe.ok ? <><CheckCircle2 size={14} />Réponse en {probe.latencyMs} ms{probe.usage?.calls ? ` · ${(probe.usage.inputTokens || 0) + (probe.usage.outputTokens || 0)} tokens` : ''}</> : <><X size={14} />{probe.error}</>}</span>}</div>
      <div className="setting-note"><KeyRound size={15} />Seul le nom de la variable secrète est enregistré dans le projet.</div></section>
      <section className="config-card"><div className="config-title"><Gauge size={17} /><div><h3>Bornes d’exécution</h3><p>Limites de la boucle et vérification bornée</p></div></div><label>Ticks maximum<input type="number" value={config.maxTicks} min="1" max="100" onChange={e => setConfig({ ...config, maxTicks: Number(e.target.value) })} /></label><label>Timeout runtime (secondes)<input type="number" value={config.timeoutSeconds} min="5" max="300" onChange={e => setConfig({ ...config, timeoutSeconds: Number(e.target.value) })} /></label><label>Profondeur de vérification<input type="number" value={config.verifyDepth} min="1" max="12" onChange={e => setConfig({ ...config, verifyDepth: Number(e.target.value) })} /></label></section>
      <section className="config-card"><div className="config-title"><Wand2 size={17} /><div><h3>Boucle de correction</h3><p>Bornes et rédacteur de <code>agentl autoloop</code></p></div><Pill tone="amber">OPTIMISER</Pill></div><label>Modèle rédacteur <small>gemini-* ou claude-* ; vide = hérité du runtime</small><input value={config.autoloopModel} onChange={e => setConfig({ ...config, autoloopModel: e.target.value })} placeholder={config.runtimeProvider === 'google-gemini' ? config.runtimeModel : 'ex. gemini-…'} /></label><div className="price-grid"><label>Tentatives max<input type="number" min="1" max="10" value={config.maxAttempts} onChange={e => setConfig({ ...config, maxAttempts: Number(e.target.value) })} /></label><label>Cas dérivés max<input type="number" min="10" max="2000" value={config.maxCases} onChange={e => setConfig({ ...config, maxCases: Number(e.target.value) })} /></label></div><div className="setting-note"><SlidersHorizontal size={15} />Sans modèle rédacteur, la boucle diagnostique sans jamais réécrire. Le budget vaut 60 % du timeout, pour que le rapport survive à l’arrêt.</div></section>
      <section className="config-card"><div className="config-title"><Network size={17} /><div><h3>Architecture</h3><p>Un agent ou une société multi-agents</p></div></div><label>Mode<select value={config.architecture} onChange={e => setConfig({ ...config, architecture: e.target.value as ProjectConfig['architecture'] })}><option value="single">Agent unique</option><option value="multi">Société multi-agents</option></select></label><div className="setting-note"><Waypoints size={15} />{config.architecture === 'multi' ? `${config.roles.length} rôles coordonnés` : 'Un objectif, un hôte et une boucle gouvernée'}.</div></section>
      <section className="config-card wide"><div className="config-title"><Sparkles size={17} /><div><h3>Skills actifs</h3><p>Sélection transmise à l’agent auteur et copiée dans le workspace</p></div></div><div className="config-skills">{skills.map(skill => <button key={skill.id} className={selected.includes(skill.id) ? 'selected' : ''} onClick={() => setSelected(current => current.includes(skill.id) ? current.filter(id => id !== skill.id) : [...current, skill.id])}><span className="skill-symbol small"><Sparkles size={12} /></span><span><strong>{skill.name}</strong><small>{skill.description}</small></span><span className="select-check">{selected.includes(skill.id) && <Check size={12} />}</span></button>)}</div></section>
    </div>
  </div>;
}

export default function App() {
  const [view, setView] = useState<View>('dashboard');
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [initialRun, setInitialRun] = useState<Run | null>(null);
  const [wizard, setWizard] = useState(false);
  const [skillEditor, setSkillEditor] = useState<Skill | null | 'new'>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [search, setSearch] = useState('');
  const [toast, setToast] = useState<ToastState>(null);

  const [ready, setReady] = useState(false);

  async function load() {
    try {
      const [dash, projectRows, skillRows, runRows, healthInfo] = await Promise.all([api.dashboard(), api.projects(), api.skills(), api.runs(), api.health()]);
      setDashboard(dash); setProjects(projectRows); setSkills(skillRows); setRuns(runRows); setHealth(healthInfo);
    } catch (error) { showToast((error as Error).message, 'error'); }
  }
  // Les défauts viennent du serveur : lui seul sait quel CLI auteur est
  // installé et si une clé d’exécution est disponible.
  useEffect(() => {
    api.settings().then(settings => {
      const serverDefaults = settings.defaults as ProjectConfig | undefined;
      if (serverDefaults) defaultConfig = { ...defaultConfig, ...serverDefaults };
      RUNTIME_MODELS = (settings.runtimeModels as Record<string, string[]>) || {};
      AUTHORING_AVAILABLE = { codex: settings.codexCli as boolean, 'claude-code': settings.claudeCode as boolean };
    }).catch(() => undefined).finally(() => setReady(true));
    load();
  }, []);
  function showToast(message: string, kind: 'success' | 'error' = 'success') { setToast({ message, kind }); window.setTimeout(() => setToast(null), 3800); }
  function navigate(next: View) { setView(next); setSelectedProject(null); setInitialRun(null); setSearch(''); }
  function openProject(project: Project, run: Run | null = null) { setSelectedProject(project); setInitialRun(run); setView('workspace'); }
  // La génération est un run : la création revient aussitôt, et l’espace de
  // travail suit l’agent auteur, avec un bouton qui l’arrête.
  async function createProject(data: WizardData) {
    try {
      const project = await api.createProject({ name: data.name, description: data.description, skills: data.skills, config: data.config, wait: false });
      setWizard(false); await load(); openProject(project, project.authoringRun || null);
      const author = data.config.authoringAgent === 'claude-code' ? 'Claude Code' : 'Codex';
      showToast(project.authoringRun ? `${author} génère le projet : la progression s’affiche dans l’espace de travail` : 'Projet créé à partir du gabarit');
    } catch (error) { showToast((error as Error).message, 'error'); throw error; }
  }
  async function saveSkill(body: { name: string; description: string; domain: string; content: string }) { try { if (skillEditor && skillEditor !== 'new') await api.updateSkill(skillEditor.id, body); else await api.createSkill(body); setSkillEditor(null); await load(); showToast('Skill enregistré'); } catch (error) { showToast((error as Error).message, 'error'); throw error; } }
  async function resetSkill(skill: Skill) {
    try { const restored = await api.resetSkill(skill.id); await load(); showToast('Skill ramené à sa version livrée'); return restored; }
    catch (error) { showToast((error as Error).message, 'error'); return null; }
  }
  async function deleteSkill(skill: Skill) { if (skill.source === 'system' || !window.confirm(`Supprimer le skill « ${skill.name} » ?`)) return; try { await api.deleteSkill(skill.id); await load(); showToast('Skill supprimé'); } catch (error) { showToast((error as Error).message, 'error'); } }

  return <div className="app-shell"><Sidebar view={view} onNavigate={navigate} health={health} collapsed={collapsed} onCollapse={() => setCollapsed(!collapsed)} /><main className="main-shell">{view !== 'workspace' && <Topbar view={view} onSearch={setSearch} />}
    {view === 'dashboard' && <DashboardView data={dashboard} onNew={() => setWizard(true)} onOpen={openProject} onNavigate={navigate} />}
    {view === 'projects' && <ProjectsView projects={projects} filter={search} onNew={() => setWizard(true)} onOpen={openProject} onRefresh={load} toast={showToast} />}
    {view === 'skills' && <SkillsView skills={skills} filter={search} onCreate={() => setSkillEditor('new')} onEdit={setSkillEditor} onDelete={deleteSkill} />}
    {view === 'runs' && <RunsView runs={runs} projects={projects} filter={search} onOpen={(p, r) => openProject(p, r)} />}
    {view === 'settings' && <SettingsView health={health} />}
    {view === 'workspace' && selectedProject && <Workspace base={selectedProject} skills={skills} initialRun={initialRun} onBack={() => navigate('projects')} onChanged={load} toast={showToast} />}
  </main>{wizard && ready && <ProjectWizard skills={skills} onClose={() => setWizard(false)} onCreate={createProject} />}{skillEditor && <SkillEditor skill={skillEditor === 'new' ? undefined : skillEditor} onClose={() => setSkillEditor(null)} onSave={saveSkill} onReset={resetSkill} />}{toast && <div className={`toast toast-${toast.kind}`}>{toast.kind === 'success' ? <CheckCircle2 size={16} /> : <X size={16} />}{toast.message}</div>}</div>;
}
