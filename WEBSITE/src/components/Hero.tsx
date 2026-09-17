import React from 'react';
import { ShieldCheck, Play, ArrowRight, Terminal, Cpu, CheckCircle2, Lock, Sparkles, Layers, FileCode, Server, Shield, Activity, Landmark, Plane } from 'lucide-react';
import { QuickstartWorkflowAnimation } from './QuickstartWorkflowAnimation';
import { AgentLLogo, AgentLText } from './AgentLLogo';

interface HeroProps {
  setActiveTab: (tab: string) => void;
}

export function Hero({ setActiveTab }: HeroProps) {
  // Critical domains data
  const criticalDomains = [
    {
      title: "SOC & Cyberdéfense",
      icon: Shield,
      color: "text-rose-400 border-rose-500/30 bg-rose-500/10",
      description: "Isolez un poste compromis ou suspendez un compte suspect, sans exposer les serveurs critiques à une action automatique.",
      guarantee: "Les actifs critiques restent protégés"
    },
    {
      title: "SRE & Infra Critique",
      icon: Server,
      color: "text-amber-400 border-amber-500/30 bg-amber-500/10",
      description: "Automatisez la remédiation, l'éviction de nœuds et les basculements, avec des règles d'arrêt explicites.",
      guarantee: "Les opérations sensibles restent encadrées"
    },
    {
      title: "FinOps & Transactions",
      icon: Landmark,
      color: "text-emerald-400 border-emerald-500/30 bg-emerald-500/10",
      description: "Détectez les fraudes et encadrez les virements par des plafonds et des approbations vérifiés avant l'exécution.",
      guarantee: "Plafonds et validations vérifiables"
    },
    {
      title: "Santé & Dispositifs Médicaux",
      icon: Activity,
      color: "text-cyan-400 border-cyan-500/30 bg-cyan-500/10",
      description: "Analysez des paramètres physiologiques et formulez des recommandations sans contourner les protocoles déclarés.",
      guarantee: "Les protocoles priment sur le modèle"
    },
    {
      title: "Défense & Systèmes Autonomes",
      icon: Plane,
      color: "text-purple-400 border-purple-500/30 bg-purple-500/10",
      description: "Décidez malgré l'incertitude des capteurs, puis rejouez chaque décision à partir d'un journal scellé.",
      guarantee: "Chaque décision peut être rejouée"
    }
  ];

  return (
    <div className="relative overflow-hidden pt-10 pb-16 md:pt-12 md:pb-20">
      {/* Ambient Glows from Sophisticated Dark design */}
      <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-blue-600/10 rounded-full blur-[100px] pointer-events-none" />
      <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-cyan-600/10 rounded-full blur-[100px] pointer-events-none" />

      <div className="relative mx-auto max-w-7xl px-4 sm:px-6">
        
        {/* Top Badges Row */}
        <div className="flex flex-wrap items-center justify-center gap-2.5 mb-6 text-xs font-mono">
          <span className="inline-flex items-center gap-1.5 px-3 py-1 border border-cyan-500/40 bg-cyan-500/10 text-cyan-300 text-[11px] uppercase tracking-widest font-bold">
            <AgentLLogo size="xs" /> v1.8 // SPEC §32
          </span>
          <span className="inline-block px-3 py-1 border border-white/10 bg-white/5 text-white/70 text-[10px] uppercase tracking-widest">
            Aucune dépendance externe
          </span>
          <span className="inline-block px-3 py-1 border border-white/10 bg-white/5 text-white/70 text-[10px] uppercase tracking-widest">
            9 Théorèmes formels
          </span>
          <span className="inline-block px-3 py-1 border border-white/10 bg-white/5 text-white/70 text-[10px] uppercase tracking-widest">
            Journal scellé & signé
          </span>
          <button
            onClick={() => setActiveTab('whatsnew')}
            className="inline-flex items-center gap-1.5 px-3 py-1 border border-amber-500/40 bg-amber-500/10 text-amber-300 text-[10px] uppercase tracking-widest font-bold hover:bg-amber-500/20 transition-colors"
          >
            <Sparkles className="h-3 w-3" />
            Nouveau : NEVER SEND · ON UNKNOWN · autoloop
          </button>
        </div>

        {/* Hero Central Header with Enlarged Logo */}
        <div className="text-center max-w-5xl mx-auto">
          {/* Prominent Hero Logo Showcase */}
          <div className="flex justify-center mb-6">
            <div className="relative group cursor-pointer" onClick={() => setActiveTab('boundary')}>
              <div className="absolute -inset-2 bg-gradient-to-r from-cyan-500 via-blue-500 to-indigo-500 rounded-xl blur-xl opacity-50 group-hover:opacity-80 transition duration-500 animate-pulse" />
              <AgentLLogo size="xl" glow={true} className="relative shadow-2xl scale-125" />
            </div>
          </div>

          <h1 className="text-3xl sm:text-6xl font-light leading-[1.05] text-white tracking-tight mb-6 font-sans">
            Des agents qui <span className="italic font-serif text-cyan-400">raisonnent</span> librement.
            <span className="block text-cyan-300 font-sans font-semibold text-xl sm:text-3xl mt-3 tracking-wide">
              Des actions qui restent sous contrôle.
            </span>
          </h1>

          <p className="text-base sm:text-lg text-white/70 max-w-3xl mx-auto mb-8 leading-relaxed">
            <AgentLText textClassName="text-cyan-300" /> sépare ce que le modèle interprète de ce que le système peut exécuter. Objectifs, outils et règles de sécurité vivent dans un contrat déclaratif, lisible et vérifiable avant le premier appel réseau.
            <strong className="text-white block mt-3 font-sans font-semibold text-sm sm:text-base tracking-wide uppercase">
              Le LLM propose. Le moteur vérifie. La politique décide.
            </strong>
          </p>

          {/* CTAs in Sophisticated Dark Rectangular High-Contrast Style */}
          <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-4">
            <button
              onClick={() => setActiveTab('boundary')}
              className="w-full sm:w-auto px-6 py-3.5 bg-white text-black text-xs font-bold uppercase tracking-wider rounded-none hover:bg-slate-200 transition-all flex items-center justify-center gap-2 shadow-xl shadow-cyan-500/10"
            >
              <Cpu className="h-4 w-4 text-cyan-600" />
              Comprendre l'architecture
              <ArrowRight className="h-4 w-4" />
            </button>

            <button
              onClick={() => setActiveTab('playground')}
              className="w-full sm:w-auto px-6 py-3.5 border border-white/20 bg-white/5 text-white text-xs font-bold uppercase tracking-wider rounded-none hover:bg-white/10 transition-all flex items-center justify-center gap-2"
            >
              <FileCode className="h-4 w-4 text-cyan-400" />
              Essayer le simulateur
            </button>
          </div>
        </div>

        {/* DOMAINES DE NIVEAU CRITIQUE SECTION */}
        <div className="mt-14 pt-10 border-t border-slate-800/80">
          <div className="text-center max-w-3xl mx-auto mb-8">
            <div className="inline-flex items-center gap-2 rounded-full border border-cyan-500/40 bg-cyan-500/10 px-3 py-1 text-xs font-mono font-bold text-cyan-300 mb-2">
              <ShieldCheck className="h-3.5 w-3.5 text-cyan-400" />
              POUR LES ENVIRONNEMENTS OÙ CHAQUE ACTION COMPTE
            </div>
            <h2 className="text-2xl sm:text-3xl font-bold text-white font-sans">
              Des garde-fous adaptés aux systèmes critiques
            </h2>
            <p className="text-sm text-slate-400 mt-2 leading-relaxed">
              Le modèle peut se tromper ; les règles déclarées continuent de s'appliquer, hors de son contexte.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {criticalDomains.map((domain, idx) => {
              const DomainIcon = domain.icon;
              return (
                <div 
                  key={idx}
                  className="bg-slate-950/80 border border-slate-800/90 hover:border-cyan-500/40 p-5 rounded-xl transition-all duration-300 flex flex-col justify-between group"
                >
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <div className={`p-2.5 rounded-xl border ${domain.color} flex items-center gap-2`}>
                        <DomainIcon className="h-4 w-4" />
                        <span className="font-bold text-xs font-sans text-white">{domain.title}</span>
                      </div>
                      <AgentLLogo size="xs" />
                    </div>

                    <p className="text-[13px] text-slate-300 leading-relaxed mb-4">
                      {domain.description}
                    </p>
                  </div>

                  <div className="pt-3 border-t border-slate-900 font-mono text-[11px] text-cyan-400 font-bold flex items-center gap-1.5">
                    <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                    <span>{domain.guarantee}</span>
                  </div>
                </div>
              );
            })}

            {/* General Banner for All Critical Systems */}
            <div className="md:col-span-2 lg:col-span-1 bg-gradient-to-br from-cyan-950/40 to-blue-950/40 border border-cyan-500/30 p-5 rounded-xl flex flex-col justify-between">
              <div>
                <div className="flex items-center gap-2 text-cyan-300 font-mono text-xs font-bold mb-2">
                  <Sparkles className="h-4 w-4 text-cyan-400" />
                  Règle de sécurité fondamentale
                </div>
                <p className="text-[13px] text-slate-200 leading-relaxed">
                  La politique est fermée par défaut : seules les actions explicitement autorisées entrent dans le plan. Une règle <code className="text-amber-300 font-mono">NEVER</code> reste prioritaire, quel que soit le texte produit par le modèle.
                </p>
              </div>

              <div className="mt-4 pt-3 border-t border-cyan-500/20 font-mono text-[11px] text-emerald-300 font-bold flex items-center gap-1.5">
                <Lock className="h-3.5 w-3.5" />
                <span>Politique fermée par défaut · DEFAULT DENY</span>
              </div>
            </div>
          </div>
        </div>

        {/* ANIMATED QUICKSTART WORKFLOW COMPONENT - VISIBLE IMMEDIATELY ON OPENING */}
        <div className="mt-14">
          <QuickstartWorkflowAnimation onNavigateToTab={setActiveTab} />
        </div>

        {/* Code / Terminal Feature Showcase Grid */}
        <div className="mt-10 grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
          
          {/* Left: AGENT-L Code Snippet */}
          <div className="lg:col-span-7 flex flex-col bg-[#080808] border border-white/10 rounded-lg p-5 shadow-2xl">
            <div className="flex items-center justify-between border-b border-white/10 pb-3 mb-4">
              <div className="flex items-center gap-2">
                <div className="h-2.5 w-2.5 rounded-full bg-rose-500/80" />
                <div className="h-2.5 w-2.5 rounded-full bg-amber-500/80" />
                <div className="h-2.5 w-2.5 rounded-full bg-emerald-500/80" />
                <span className="ml-2 font-mono text-xs font-semibold text-white/60 flex items-center gap-1.5">
                  <AgentLLogo size="xs" /> soc_analyst.agent
                </span>
              </div>
              <span className="text-[10px] uppercase tracking-widest text-cyan-400 font-mono bg-cyan-500/10 px-2 py-0.5 border border-cyan-500/20">
                Grammaire EBNF
              </span>
            </div>

            <pre className="font-mono text-xs text-white/80 leading-relaxed overflow-x-auto p-3 bg-black/60 border border-white/5 rounded">
<code>{`AGENT SOC_ANALYST {
    VERSION "1.8"

    GOAL containment { MAINTAIN threat.status == contained }

    OBSERVE {
        wazuh.alert_count   network.anomaly_score
        asset.criticality ON UNKNOWN ESCALATE   // capteur muet : alerte explicite
        threat.status                           // recouvre l'EFFECT (T9)
    }

    TOOL isolate_endpoint {
        INPUT       { host: String BIND suspected_host }
        REQUIRES    { endpoint.inspected == yes }
        EFFECT      { threat.status = contained }
        DURATION    45s
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
        NEVER SEND credentials                  // ne sort jamais vers le modèle
        ALLOW isolate_endpoint IF P(credential_attack) >= 0.95
        NEVER isolate_endpoint WHEN asset.criticality == CRITICAL
        NEVER isolate_endpoint WHEN reason.degraded == true
    }
}`}</code>
            </pre>
          </div>

          {/* Right: Terminal Command Execution & Audit Output */}
          <div className="lg:col-span-5 flex flex-col bg-[#080808] border border-white/10 rounded-lg p-5 shadow-2xl justify-between">
            <div>
              <div className="flex items-center justify-between border-b border-white/10 pb-3 mb-4">
                <div className="flex items-center gap-2">
                  <Terminal className="h-4 w-4 text-cyan-400" />
                  <span className="font-mono text-xs font-semibold text-white/80 flex items-center gap-1.5">
                    Terminal CLI <AgentLText textClassName="text-white" />
                  </span>
                </div>
                <span className="text-[10px] uppercase tracking-widest text-emerald-400 bg-emerald-500/10 px-2 py-0.5 border border-emerald-500/20 font-mono">
                  Vérification formelle
                </span>
              </div>

              <div className="space-y-3 font-mono text-xs">
                <div className="p-3 bg-white/5 border border-white/10 rounded relative">
                  <div className="text-white/40 text-[11px]">$ python -m agentl verify soc_analyst.agent</div>
                  <div className="text-emerald-400 font-bold mt-1">✔ T1–T7, T9 : 8 théorèmes démontrés hors ligne</div>
                  <div className="text-white/40 text-[10px] mt-0.5">Point fixe atteint. Aucune exposition d'outil critique.</div>
                </div>

                <div className="p-3 bg-white/5 border border-white/10 rounded relative">
                  <div className="text-white/40 text-[11px]">$ python -m agentl autoloop soc_analyst.agent</div>
                  <div className="text-cyan-400 mt-1">⟳ 42 cas dérivés · lot de contrôle 30 % préservé</div>
                  <div className="text-emerald-400 mt-0.5">✔ Invariants vérifiés sur tous les cas</div>
                </div>

                <div className="p-3 bg-white/5 border border-white/10 rounded relative">
                  <div className="text-white/40 text-[11px]">$ python -m agentl run soc_analyst.agent</div>
                  <div className="text-cyan-400 mt-1">∿ credential_attack: 0.05 → 0.970 (≥ seuil 0.90)</div>
                  <div className="text-emerald-400 mt-0.5">✅ quarantine_account exécuté (coût optimal 4 vs 20)</div>
                </div>

                <div className="p-3 bg-white/5 border border-white/10 rounded relative">
                  <div className="text-white/40 text-[11px]">$ python -m agentl replay run.json</div>
                  <div className="text-emerald-400 font-bold mt-1">✔ Rejeu conforme et scellé (sha256 9f8a3c4e...)</div>
                </div>
              </div>
            </div>

            <div className="mt-4 pt-4 border-t border-white/10 text-[11px] text-white/50 flex items-center justify-between font-mono">
              <span>Banc AutomationBench :</span>
              <span className="font-bold text-emerald-400">Score Zapier 1.00/1.00</span>
            </div>
          </div>

        </div>

        {/* Core Metrics Banner */}
        <div className="mt-12 grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-white/5 border border-white/10 p-5 rounded-none text-center">
            <div className="text-3xl font-light font-mono text-emerald-400">0 / 10</div>
            <div className="text-[11px] uppercase tracking-widest text-white/40 mt-1">Violations (ResilienceBench)</div>
          </div>

          <div className="bg-white/5 border border-white/10 p-5 rounded-none text-center">
            <div className="text-3xl font-light font-mono text-cyan-400">9</div>
            <div className="text-[11px] uppercase tracking-widest text-white/40 mt-1">Théorèmes formels hors ligne</div>
          </div>

          <div className="bg-white/5 border border-white/10 p-5 rounded-none text-center">
            <div className="text-3xl font-light font-mono text-amber-400">0</div>
            <div className="text-[11px] uppercase tracking-widest text-white/40 mt-1">Dépendance externe du cœur</div>
          </div>

          <div className="bg-white/5 border border-white/10 p-5 rounded-none text-center">
            <div className="text-3xl font-light font-mono text-blue-400">100%</div>
            <div className="text-[11px] uppercase tracking-widest text-white/40 mt-1">Rejeu déterministe certifié</div>
          </div>
        </div>

      </div>
    </div>
  );
}
