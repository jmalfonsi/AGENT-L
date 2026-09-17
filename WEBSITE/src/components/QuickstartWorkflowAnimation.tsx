import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  FileText,
  Brain,
  Bot,
  Files,
  ShieldCheck,
  CheckCircle2,
  Play,
  Pause,
  RotateCcw,
  ChevronRight,
  ChevronLeft,
  Sparkles,
  Terminal,
  Cpu,
  Lock,
  FileCode,
  Check,
  Copy,
  ArrowRight,
  Eye,
  SlidersHorizontal,
  FolderGit2
} from 'lucide-react';
import { AgentLLogo, AgentLText } from './AgentLLogo';

interface QuickstartWorkflowAnimationProps {
  onNavigateToTab?: (tab: string) => void;
}

export function QuickstartWorkflowAnimation({ onNavigateToTab }: QuickstartWorkflowAnimationProps) {
  const [activeStep, setActiveStep] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(true);
  const [speed, setSpeed] = useState<number>(4000); // 4 seconds per step
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const [activeFileTab, setActiveFileTab] = useState<'agent' | 'py' | 'env'>('agent');

  // Steps definition
  const steps = [
    {
      id: 0,
      title: '1. Cahier des Charges',
      shortTitle: 'Spécifications',
      subtitle: 'Chargement du document de besoin métier (cahier_des_charges.md)',
      icon: FileText,
      color: 'from-blue-500 to-cyan-500',
      badge: 'ENTRÉE MÉTIER',
      details: {
        filename: 'cahier_des_charges_soc.md',
        content: `# CAHIER DES CHARGES : AGENT DE SURVEILLANCE & REMÉDIATION SOC

## Objectif principal
Maintenir le statut des cyber-menaces au niveau "contained" (consigné) automatique.

## Contraintes de Sécurité & Politiques Inviolables
- INTERDICTION STRICTE d'isoler un serveur tagué CRITICAL.
- Ne déclencher l'isolation QUE SI la probabilité d'attaque de type credential_attack dépasse 95%.
- Par défaut, TOUTE ACTION EST REFUSÉE (DEFAULT DENY).

## Sources de données (Capteurs)
- Wazuh SIEM : wazuh.alert_count, wazuh.rule_level
- Asset Inventory : asset.criticality (LOW, MEDIUM, CRITICAL)
- Network Probe : network.anomaly_score`,
        extractedItems: [
          { label: 'Objectif', val: 'MAINTAIN threat.status == contained', icon: '🎯' },
          { label: 'Règle stricte', val: 'NEVER isolate_endpoint WHEN asset.criticality == CRITICAL', icon: '🛡️' },
          { label: 'Condition d\'action', val: 'ALLOW IF P(credential_attack) >= 0.95', icon: '⚡' },
          { label: 'Politique par défaut', val: 'DEFAULT DENY', icon: '🔒' }
        ]
      }
    },
    {
      id: 1,
      title: '2. Agent de Conception',
      shortTitle: 'Conception',
      subtitle: 'Analyse sémantique, extraction des croyances, politiques et faits',
      icon: Brain,
      color: 'from-cyan-500 to-teal-500',
      badge: 'ANALYSE DU BESOIN',
      details: {
        analysisResults: [
          { category: 'Modèle Inférentiel', desc: 'Hypothèse Bayésienne avec Prior 0.05 & Vraisemblance 0.92' },
          { category: 'Grammaire Déclarative', desc: 'Déclaration EBNF d\'outils avec Preconditions (REQUIRES) & Effets (EFFECT)' },
          { category: 'Politiques de Sécurité', desc: 'Déclaration formelle d\'interdictions universelles (NEVER)' },
          { category: 'Capteurs Perçus', desc: 'Abonnement aux métriques systèmes externes' }
        ],
        agentDesignSchema: `{
  "agent_name": "soc_sentinel",
  "goal": "containment",
  "observe": ["wazuh.alert_count", "network.anomaly_score", "asset.criticality"],
  "tools": ["isolate_endpoint"],
  "hypotheses": ["credential_attack"],
  "policy": ["DEFAULT DENY", "ALLOW IF P >= 0.95", "NEVER IF CRITICAL"]
}`
      }
    },
    {
      id: 2,
      title: '3. Agent de Codage + Skill',
      shortTitle: 'Codage & Skill',
      subtitle: 'Invocations de Claude Opus / ChatGPT / Cursor avec le Skill agentl-author',
      icon: Bot,
      color: 'from-purple-500 to-indigo-500',
      badge: 'SKILL agentl-author',
      details: {
        skillName: 'agentl-author (SKILL.md)',
        systemPrompt: `SYSTEM PROMPT (Skill agentl-author) :
"RÈGLE ABSOLUE : L'Hôte Python fournit les FAITS. Le fichier .agent prend toutes les DÉCISIONS.
Tu dois générer obligatoirement les 3 fichiers :
 1. soc_sentinel.agent (Runtime déclaratif)
 2. soc_sentinel.py (Hôte Python)
 3. .env (Configuration secrets)
Puis exécuter la chaîne de contrôle : check -> verify -> boundary -> run -> studio."`,
        status: 'Génération en cours du code respectant l\'étanchéité Hôte/Agent...'
      }
    },
    {
      id: 3,
      title: '4. Génération des 3 Fichiers',
      shortTitle: '3 Fichiers (.agent, .py, .env)',
      subtitle: 'Création du programme runtime, de l\'hôte Python jumeau et de la config',
      icon: Files,
      color: 'from-amber-500 to-orange-500',
      badge: '3 FICHIERS GÉNÉRÉS',
      details: {
        agentFile: {
          name: 'soc_sentinel.agent',
          type: 'Runtime Déclaratif AGENT-L',
          code: `AGENT soc_sentinel {
    GOAL containment { MAINTAIN threat.status == contained }

    OBSERVE { wazuh.alert_count  network.anomaly_score  asset.criticality }

    TOOL isolate_endpoint {
        INPUT       { host: String BIND suspected_host }
        REQUIRES    { endpoint.inspected == yes }
        EFFECT      { threat.status = contained }
        COST        8
    }

    HYPOTHESIS credential_attack {
        PRIOR 0.05
        EVIDENCE {
            wazuh.alert_count > 20  LIKELIHOOD 0.92 GIVEN_NOT 0.06
        }
        THRESHOLD 0.90  EXPLAINS threat.kind
    }

    POLICY {
        DEFAULT DENY
        ALLOW isolate_endpoint IF P(credential_attack) >= 0.95
        NEVER isolate_endpoint WHEN asset.criticality == CRITICAL
    }
}`
        },
        pyFile: {
          name: 'soc_sentinel.py',
          type: 'Hôte Python Jumeau (Faits Uniquement)',
          code: `import os
from agentl import Host, MockLLM, Sensor, Tool

def build():
    host = Host("soc_sentinel")
    
    # Capteurs de faits bruts (Aucune décision dans l'Hôte!)
    host.add_sensor(Sensor("wazuh.alert_count", lambda: 25))
    host.add_sensor(Sensor("asset.criticality", lambda: "MEDIUM"))
    host.add_sensor(Sensor("endpoint.inspected", lambda: "yes"))
    
    # Outil réel de remédiation
    def do_isolate(args):
        # BOUNDARY-OK: Action d'exécution technique
        print(f"[EXEC] Host {args.get('host')} isolé du réseau")
        return {"status": "success"}

    host.add_tool(Tool("isolate_endpoint", do_isolate))
    
    llm = MockLLM()
    return host, llm`
        },
        envFile: {
          name: '.env',
          type: 'Configuration & Secrets',
          code: `# Fichier de configuration d'environnement pour soc_sentinel
AGENTL_ENV=production
ANTHROPIC_API_KEY=sk-ant-api03-...
WAZUH_ENDPOINT=https://siem.internal.corp:55000
LOG_LEVEL=INFO`
        }
      }
    },
    {
      id: 4,
      title: '5. Validation & Qualification',
      shortTitle: 'Validation & Checks',
      subtitle: 'Séquence automatique : check ➔ verify ➔ boundary ➔ run ➔ studio',
      icon: ShieldCheck,
      color: 'from-emerald-500 to-green-500',
      badge: '5 QUALIFICATIONS',
      details: {
        commands: [
          { cmd: 'python -m agentl check soc_sentinel.agent', label: 'EBNF & Syntax Check', result: '✔ 0 erreur syntaxique', status: 'pass' },
          { cmd: 'python -m agentl verify soc_sentinel.agent', label: 'Théorèmes formels (T1–T7, T9)', result: '✔ 8/8 Théorèmes démontrés sûrs', status: 'pass' },
          { cmd: 'python -m agentl boundary soc_sentinel.agent', label: 'Contrôle AST Hôte/Agent', result: '✔ 0 décision non justifiée dans l\'hôte', status: 'pass' },
          { cmd: 'python -m agentl run soc_sentinel.agent --html /tmp/run.html', label: 'Exécution & Journal Scellé', result: '✔ Trace SHA-256 scellée générée', status: 'pass' },
          { cmd: 'python -m agentl studio soc_sentinel.agent', label: 'Studio N8N & Visualisateur', result: '✔ Studio N8N actif sur :8765', status: 'pass' }
        ]
      }
    }
  ];

  // Timer loop for auto-play
  useEffect(() => {
    let timer: NodeJS.Timeout;
    if (isPlaying) {
      timer = setInterval(() => {
        setActiveStep((prev) => (prev + 1) % steps.length);
      }, speed);
    }
    return () => clearInterval(timer);
  }, [isPlaying, speed, steps.length]);

  const copyToClipboard = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  const currentStepObj = steps[activeStep];

  return (
    <div className="my-6 rounded-2xl border border-cyan-500/30 bg-[#050810] p-4 sm:p-6 shadow-2xl relative overflow-hidden">
      {/* Background glow effects */}
      <div className="absolute top-0 right-0 -mt-12 -mr-12 w-80 h-80 bg-cyan-500/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute bottom-0 left-0 -mb-12 -ml-12 w-80 h-80 bg-purple-500/10 rounded-full blur-3xl pointer-events-none" />

      {/* Title Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-slate-800 pb-5 mb-6">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-cyan-500/40 bg-cyan-500/10 px-3 py-1 text-xs font-mono font-bold text-cyan-300 mb-2">
            <Sparkles className="h-3.5 w-3.5 text-cyan-400" />
            QUICKSTART ANIMÉ : FLUX AUTOMATISÉ D'UN AGENT DE CODAGE
          </div>
          <h2 className="text-xl sm:text-2xl font-bold text-white font-sans flex items-center gap-2">
            Du Cahier des Charges à l'Agent <AgentLText textClassName="text-cyan-300" /> Vérifié
          </h2>
          <p className="text-xs sm:text-sm text-slate-400 font-serif mt-1">
            Visualisez en direct comment un Agent de Codage (Claude Opus, ChatGPT, Cursor) génère les 3 fichiers et valide la frontière d'étanchéité.
          </p>
        </div>

        {/* Animation Control Bar */}
        <div className="flex items-center gap-2 bg-slate-900/90 border border-slate-800 p-2 rounded-xl text-xs font-mono">
          <button
            onClick={() => setIsPlaying(!isPlaying)}
            className={`px-3 py-1.5 rounded-lg flex items-center gap-1.5 font-bold transition-all ${
              isPlaying ? 'bg-amber-500/20 text-amber-300 border border-amber-500/30' : 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/30'
            }`}
            title={isPlaying ? "Mettre en pause l'animation" : "Lancer l'animation automatique"}
          >
            {isPlaying ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
            <span>{isPlaying ? 'Pause' : 'Lecture'}</span>
          </button>

          <button
            onClick={() => setActiveStep(0)}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            title="Redémarrer le flux au début"
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>

          <div className="h-4 w-px bg-slate-800 mx-1" />

          {/* Speed selector */}
          <div className="flex items-center gap-1 text-[11px] text-slate-400">
            <SlidersHorizontal className="h-3.5 w-3.5 text-cyan-400" />
            <button
              onClick={() => setSpeed(5000)}
              className={`px-1.5 py-0.5 rounded ${speed === 5000 ? 'bg-cyan-500/20 text-cyan-300 font-bold' : 'hover:text-slate-200'}`}
            >
              Lent
            </button>
            <button
              onClick={() => setSpeed(3500)}
              className={`px-1.5 py-0.5 rounded ${speed === 3500 ? 'bg-cyan-500/20 text-cyan-300 font-bold' : 'hover:text-slate-200'}`}
            >
              Normal
            </button>
            <button
              onClick={() => setSpeed(2000)}
              className={`px-1.5 py-0.5 rounded ${speed === 2000 ? 'bg-cyan-500/20 text-cyan-300 font-bold' : 'hover:text-slate-200'}`}
            >
              Rapide
            </button>
          </div>
        </div>
      </div>

      {/* Stepper Pipeline Flow Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2 mb-6">
        {steps.map((step, idx) => {
          const StepIcon = step.icon;
          const isActive = idx === activeStep;
          const isPassed = idx < activeStep;

          return (
            <button
              key={step.id}
              onClick={() => {
                setActiveStep(idx);
                setIsPlaying(false);
              }}
              className={`relative p-3 rounded-xl border text-left transition-all duration-300 flex flex-col justify-between overflow-hidden ${
                isActive
                  ? 'border-cyan-400 bg-cyan-950/40 shadow-lg shadow-cyan-500/10 scale-[1.02]'
                  : isPassed
                  ? 'border-emerald-500/40 bg-emerald-950/10 text-slate-300'
                  : 'border-slate-800/80 bg-slate-900/40 text-slate-500 hover:border-slate-700'
              }`}
            >
              {/* Active animated top glow line */}
              {isActive && (
                <motion.div
                  className="absolute top-0 left-0 right-0 h-1 bg-gradient-to-r from-cyan-400 to-emerald-400"
                  layoutId="activeStepGlow"
                  transition={{ type: "spring", stiffness: 300, damping: 30 }}
                />
              )}

              <div className="flex items-center justify-between mb-2">
                <div className={`p-1.5 rounded-lg ${isActive ? 'bg-cyan-500/20 text-cyan-300' : isPassed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-800 text-slate-400'}`}>
                  <StepIcon className="h-4 w-4" />
                </div>
                {isPassed ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                ) : (
                  <span className={`font-mono text-[10px] ${isActive ? 'text-cyan-400 font-bold' : 'text-slate-500'}`}>
                    0{idx + 1}
                  </span>
                )}
              </div>

              <div>
                <div className={`text-xs font-bold font-sans line-clamp-1 ${isActive ? 'text-white' : 'text-slate-300'}`}>
                  {step.shortTitle}
                </div>
                <div className="text-[10px] text-slate-400 font-mono mt-0.5 line-clamp-1">
                  {step.badge}
                </div>
              </div>
            </button>
          );
        })}
      </div>

      {/* Main Animated Stage Area */}
      <div className="relative rounded-xl border border-slate-800 bg-[#070b14] p-4 sm:p-6 min-h-[380px] flex flex-col justify-between">
        <AnimatePresence mode="wait">
          <motion.div
            key={currentStepObj.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.3 }}
            className="space-y-4"
          >
            {/* Step Header */}
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-800/80 pb-3">
              <div className="flex items-center gap-3">
                <div className={`p-2.5 rounded-xl bg-gradient-to-br ${currentStepObj.color} text-white shadow-lg`}>
                  <currentStepObj.icon className="h-5 w-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs uppercase tracking-widest font-mono text-cyan-400 font-bold">
                      {currentStepObj.badge}
                    </span>
                    <span className="text-slate-600">•</span>
                    <span className="text-xs font-mono text-slate-400">Étape {activeStep + 1} sur 5</span>
                  </div>
                  <h3 className="text-lg font-bold text-white font-sans mt-0.5">
                    {currentStepObj.title}
                  </h3>
                </div>
              </div>

              <div className="text-xs text-slate-400 font-mono bg-slate-900 border border-slate-800 px-3 py-1.5 rounded-lg flex items-center gap-2">
                <span className="h-2 w-2 rounded-full bg-cyan-400 animate-pulse" />
                {currentStepObj.subtitle}
              </div>
            </div>

            {/* STEP 1 DETAILS: Cahier des Charges */}
            {activeStep === 0 && (
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
                <div className="lg:col-span-7 bg-slate-950 border border-slate-800 p-4 rounded-xl font-mono text-xs">
                  <div className="flex items-center justify-between pb-2 mb-2 border-b border-slate-800 text-slate-400 text-[11px]">
                    <div className="flex items-center gap-2">
                      <FileText className="h-3.5 w-3.5 text-blue-400" />
                      <span>{currentStepObj.details.filename}</span>
                    </div>
                    <span className="text-blue-400 bg-blue-500/10 px-2 py-0.5 rounded text-[10px]">Markdown Spec</span>
                  </div>
                  <pre className="text-slate-300 whitespace-pre-wrap leading-relaxed text-[11px] max-h-56 overflow-y-auto">
                    {currentStepObj.details.content}
                  </pre>
                </div>

                <div className="lg:col-span-5 space-y-2">
                  <div className="text-xs font-mono uppercase text-cyan-400 font-bold flex items-center gap-1.5 mb-1">
                    <Sparkles className="h-3.5 w-3.5" /> Éléments Métier Détectés :
                  </div>
                  {currentStepObj.details.extractedItems?.map((item, i) => (
                    <motion.div
                      key={i}
                      initial={{ opacity: 0, x: 20 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: i * 0.1 }}
                      className="p-3 bg-slate-900/80 border border-slate-800 rounded-xl flex items-start gap-3"
                    >
                      <span className="text-base">{item.icon}</span>
                      <div>
                        <div className="text-[11px] text-slate-400 font-mono font-bold">{item.label}</div>
                        <div className="text-xs font-mono text-cyan-300 font-bold mt-0.5">{item.val}</div>
                      </div>
                    </motion.div>
                  ))}
                </div>
              </div>
            )}

            {/* STEP 2 DETAILS: Agent de Conception */}
            {activeStep === 1 && (
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
                <div className="lg:col-span-6 space-y-3">
                  <div className="text-xs font-mono uppercase text-cyan-400 font-bold flex items-center gap-1.5">
                    <Brain className="h-3.5 w-3.5" /> Cartographie Formelle de l'Agent :
                  </div>
                  {currentStepObj.details.analysisResults?.map((res, i) => (
                    <div key={i} className="p-3 bg-slate-900 border border-slate-800 rounded-xl">
                      <div className="text-xs font-bold text-white font-sans flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-cyan-400" />
                        {res.category}
                      </div>
                      <div className="text-xs text-slate-300 font-serif mt-1">{res.desc}</div>
                    </div>
                  ))}
                </div>

                <div className="lg:col-span-6 bg-slate-950 border border-slate-800 p-4 rounded-xl font-mono text-xs">
                  <div className="text-[11px] text-slate-400 border-b border-slate-800 pb-2 mb-2 flex items-center justify-between">
                    <span>Structure d'Ingénierie Synthétisée</span>
                    <span className="text-emerald-400 text-[10px] bg-emerald-500/10 px-2 py-0.5 rounded">Valide EBNF</span>
                  </div>
                  <pre className="text-cyan-300 text-[11px] leading-relaxed overflow-x-auto">
                    {currentStepObj.details.agentDesignSchema}
                  </pre>
                </div>
              </div>
            )}

            {/* STEP 3 DETAILS: Agent de Codage + Skill */}
            {activeStep === 2 && (
              <div className="space-y-4">
                <div className="p-4 bg-purple-950/20 border border-purple-500/30 rounded-xl flex items-start gap-4">
                  <div className="p-3 rounded-xl bg-purple-500/20 text-purple-300 shrink-0">
                    <Bot className="h-6 w-6" />
                  </div>
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-bold text-purple-300 font-mono">
                        Skill Officiel Référencé : {currentStepObj.details.skillName}
                      </span>
                      <span className="text-[10px] bg-purple-500/20 text-purple-200 border border-purple-500/30 px-2 py-0.5 rounded font-mono">
                        Claude Opus / ChatGPT / Cursor
                      </span>
                    </div>
                    <pre className="text-xs font-mono text-slate-200 bg-black/60 p-3 rounded-lg border border-purple-500/20 whitespace-pre-wrap leading-relaxed">
                      {currentStepObj.details.systemPrompt}
                    </pre>
                  </div>
                </div>

                <div className="p-3 bg-slate-900 border border-slate-800 rounded-xl flex items-center justify-between font-mono text-xs text-slate-300">
                  <div className="flex items-center gap-2">
                    <Sparkles className="h-4 w-4 text-purple-400 animate-spin" />
                    <span>{currentStepObj.details.status}</span>
                  </div>
                  <span className="text-cyan-400 font-bold">Règle de Partage Validée</span>
                </div>
              </div>
            )}

            {/* STEP 4 DETAILS: 3 Fichiers Générés */}
            {activeStep === 3 && (
              <div className="space-y-3">
                {/* Tabs to switch between the 3 files */}
                <div className="flex items-center gap-2 border-b border-slate-800 pb-2 font-mono text-xs">
                  <button
                    onClick={() => setActiveFileTab('agent')}
                    className={`px-3 py-1.5 rounded-lg flex items-center gap-2 font-bold transition-all ${
                      activeFileTab === 'agent'
                        ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                        : 'text-slate-400 hover:text-white bg-slate-900'
                    }`}
                  >
                    <FileCode className="h-3.5 w-3.5" />
                    1. soc_sentinel.agent (Runtime)
                  </button>

                  <button
                    onClick={() => setActiveFileTab('py')}
                    className={`px-3 py-1.5 rounded-lg flex items-center gap-2 font-bold transition-all ${
                      activeFileTab === 'py'
                        ? 'bg-cyan-500/20 text-cyan-300 border border-cyan-500/40'
                        : 'text-slate-400 hover:text-white bg-slate-900'
                    }`}
                  >
                    <FolderGit2 className="h-3.5 w-3.5" />
                    2. soc_sentinel.py (Hôte Python)
                  </button>

                  <button
                    onClick={() => setActiveFileTab('env')}
                    className={`px-3 py-1.5 rounded-lg flex items-center gap-2 font-bold transition-all ${
                      activeFileTab === 'env'
                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                        : 'text-slate-400 hover:text-white bg-slate-900'
                    }`}
                  >
                    <Lock className="h-3.5 w-3.5" />
                    3. .env (Config)
                  </button>
                </div>

                {/* Tab content viewer */}
                <div className="bg-slate-950 border border-slate-800 p-4 rounded-xl font-mono text-xs relative">
                  {activeFileTab === 'agent' && (
                    <div>
                      <div className="flex items-center justify-between text-slate-400 text-[11px] pb-2 mb-2 border-b border-slate-800">
                        <span>{currentStepObj.details.agentFile?.name} ({currentStepObj.details.agentFile?.type})</span>
                        <button
                          onClick={() => copyToClipboard(currentStepObj.details.agentFile?.code || '', 301)}
                          className="flex items-center gap-1 text-slate-400 hover:text-white transition-colors"
                        >
                          {copiedIndex === 301 ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
                          <span>{copiedIndex === 301 ? 'Copié' : 'Copier'}</span>
                        </button>
                      </div>
                      <pre className="text-amber-300 text-[11px] leading-relaxed max-h-56 overflow-y-auto">
                        {currentStepObj.details.agentFile?.code}
                      </pre>
                    </div>
                  )}

                  {activeFileTab === 'py' && (
                    <div>
                      <div className="flex items-center justify-between text-slate-400 text-[11px] pb-2 mb-2 border-b border-slate-800">
                        <span>{currentStepObj.details.pyFile?.name} ({currentStepObj.details.pyFile?.type})</span>
                        <button
                          onClick={() => copyToClipboard(currentStepObj.details.pyFile?.code || '', 302)}
                          className="flex items-center gap-1 text-slate-400 hover:text-white transition-colors"
                        >
                          {copiedIndex === 302 ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
                          <span>{copiedIndex === 302 ? 'Copié' : 'Copier'}</span>
                        </button>
                      </div>
                      <pre className="text-cyan-300 text-[11px] leading-relaxed max-h-56 overflow-y-auto">
                        {currentStepObj.details.pyFile?.code}
                      </pre>
                    </div>
                  )}

                  {activeFileTab === 'env' && (
                    <div>
                      <div className="flex items-center justify-between text-slate-400 text-[11px] pb-2 mb-2 border-b border-slate-800">
                        <span>{currentStepObj.details.envFile?.name} ({currentStepObj.details.envFile?.type})</span>
                        <button
                          onClick={() => copyToClipboard(currentStepObj.details.envFile?.code || '', 303)}
                          className="flex items-center gap-1 text-slate-400 hover:text-white transition-colors"
                        >
                          {copiedIndex === 303 ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
                          <span>{copiedIndex === 303 ? 'Copié' : 'Copier'}</span>
                        </button>
                      </div>
                      <pre className="text-emerald-300 text-[11px] leading-relaxed max-h-56 overflow-y-auto">
                        {currentStepObj.details.envFile?.code}
                      </pre>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* STEP 5 DETAILS: Validation & Qualification */}
            {activeStep === 4 && (
              <div className="space-y-3">
                <div className="text-xs font-mono text-slate-400 mb-2">
                  La séquence de qualification complète est exécutée pour prouver la sûreté avant tout déploiement :
                </div>

                <div className="grid grid-cols-1 gap-2 font-mono text-xs">
                  {currentStepObj.details.commands?.map((c, i) => (
                    <motion.div
                      key={i}
                      initial={{ opacity: 0, x: -10 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ delay: i * 0.08 }}
                      className="p-3 bg-slate-950 border border-slate-800 rounded-xl flex flex-col sm:flex-row sm:items-center justify-between gap-2"
                    >
                      <div className="space-y-0.5">
                        <div className="text-slate-400 text-[10px] uppercase font-bold text-cyan-400">
                          {c.label}
                        </div>
                        <div className="text-white font-bold text-[11px] flex items-center gap-1.5">
                          <Terminal className="h-3.5 w-3.5 text-cyan-400" />
                          <span>{c.cmd}</span>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 font-bold text-[11px]">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          {c.result}
                        </span>
                      </div>
                    </motion.div>
                  ))}
                </div>
              </div>
            )}
          </motion.div>
        </AnimatePresence>

        {/* Step Navigation Footer */}
        <div className="mt-6 pt-4 border-t border-slate-800 flex items-center justify-between">
          <button
            onClick={() => {
              setActiveStep((prev) => (prev > 0 ? prev - 1 : steps.length - 1));
              setIsPlaying(false);
            }}
            className="px-3 py-1.5 rounded-lg border border-slate-800 bg-slate-900 hover:bg-slate-800 text-xs text-slate-300 font-mono flex items-center gap-1.5 transition-colors"
          >
            <ChevronLeft className="h-4 w-4" />
            Étape précédente
          </button>

          <div className="flex items-center gap-1">
            {steps.map((_, i) => (
              <span
                key={i}
                onClick={() => {
                  setActiveStep(i);
                  setIsPlaying(false);
                }}
                className={`h-2 rounded-full cursor-pointer transition-all ${
                  i === activeStep ? 'w-6 bg-cyan-400' : 'w-2 bg-slate-700 hover:bg-slate-500'
                }`}
              />
            ))}
          </div>

          <button
            onClick={() => {
              setActiveStep((prev) => (prev + 1) % steps.length);
              setIsPlaying(false);
            }}
            className="px-4 py-1.5 rounded-lg bg-cyan-500 text-black font-bold text-xs font-mono flex items-center gap-1.5 hover:bg-cyan-400 transition-colors shadow-lg shadow-cyan-500/20"
          >
            Étape suivante
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
