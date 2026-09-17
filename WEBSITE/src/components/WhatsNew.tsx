import React, { useState } from 'react';
import {
  Sparkles, ShieldCheck, EyeOff, PlugZap, Infinity as InfinityIcon, Plug,
  Timer, Network, Crosshair, FileSignature, AlertTriangle, Trash2, ArrowRight
} from 'lucide-react';
import { AgentLText } from './AgentLLogo';

interface Entry {
  version: string;
  tag: string;
  tone: 'cyan' | 'amber' | 'rose' | 'emerald' | 'purple';
  icon: React.ElementType;
  title: string;
  spec?: string;
  body: string;
  codes?: string[];
  snippet?: string;
}

interface WhatsNewProps {
  compact?: boolean;
  onViewAll?: () => void;
}

const TONES: Record<Entry['tone'], { chip: string; border: string; icon: string }> = {
  cyan: {
    chip: 'text-cyan-300 bg-cyan-500/10 border-cyan-500/30',
    border: 'hover:border-cyan-500/40',
    icon: 'text-cyan-400'
  },
  amber: {
    chip: 'text-amber-300 bg-amber-500/10 border-amber-500/30',
    border: 'hover:border-amber-500/40',
    icon: 'text-amber-400'
  },
  rose: {
    chip: 'text-rose-300 bg-rose-500/10 border-rose-500/30',
    border: 'hover:border-rose-500/40',
    icon: 'text-rose-400'
  },
  emerald: {
    chip: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/30',
    border: 'hover:border-emerald-500/40',
    icon: 'text-emerald-400'
  },
  purple: {
    chip: 'text-purple-300 bg-purple-500/10 border-purple-500/30',
    border: 'hover:border-purple-500/40',
    icon: 'text-purple-400'
  }
};

const ENTRIES: Entry[] = [
  {
    version: 'v1.8',
    tag: 'Langage',
    tone: 'rose',
    icon: EyeOff,
    title: 'NEVER SEND — protection et masquage des données confidentielles',
    spec: 'SPEC §32',
    body:
      "NEVER SEND empêche toute donnée sensible d'atteindre l'API du modèle, y compris pendant select_plan. " +
      "Le masquage s'applique à toute la hiérarchie concernée : la clé reste visible, sa valeur devient ⟦retenu⟧ " +
      "et l'opération est inscrite dans le journal d'audit.",
    codes: ['W130'],
    snippet: `POLICY { NEVER SEND credentials, patient.identite }
// contexte transmis : {'token': '⟦retenu⟧', 'user': 'alice'}`
  },
  {
    version: 'v1.8',
    tag: 'Langage',
    tone: 'amber',
    icon: PlugZap,
    title: 'ON UNKNOWN — gestion explicite des pannes de capteurs',
    spec: 'SPEC §31',
    body:
      "Quand un capteur ne répond pas, ON UNKNOWN rend la conduite à tenir explicite. ESCALATE consigne l'anomalie " +
      "et alerte un opérateur ; DEGRADE utilise une valeur de repli avec une confiance réduite (0,3). " +
      "L'agent ne se bloque plus en silence.",
    codes: ['W131', 'W132'],
    snippet: `OBSERVE {
    asset.criticality  ON UNKNOWN ESCALATE
    disk.usage_percent ON UNKNOWN DEGRADE 100
}
// sensors.<chemin>.available devient un fait du monde`
  },
  {
    version: 'v1.8.1',
    tag: 'Résilience',
    tone: 'amber',
    icon: PlugZap,
    title: "Disjoncteur d'outils et gestion de la résilience",
    spec: 'SPEC §31',
    body:
      "Après trois échecs consécutifs, le disjoncteur suspend temporairement l'outil afin de ne pas saturer un " +
      "service indisponible, puis teste progressivement sa reprise. Les appels au LLM respectent aussi un nombre " +
      "maximal de tentatives, Retry-After et un mode dégradé explicite.",
    codes: ['W133', 'W134'],
    snippet: `NEVER close_batch  WHEN tools.update_row.available == false
NEVER apply_change WHEN reason.degraded == true
// métriques : tool_failures · circuit_open · reason_degraded`
  },
  {
    version: 'v1.8.1',
    tag: 'Sécurité',
    tone: 'rose',
    icon: AlertTriangle,
    title: 'Évaluation unifiée des conditions booléennes',
    spec: 'SPEC §7.7',
    body:
      "Harmonisation stricte entre les types booléens Python du moteur interne (ex. sensors.*.available) " +
      "et les symboles du langage .agent. L'évaluation traite désormais avec exactitude les équivalences " +
      "(true/yes et false/no) tout en préservant un typage strict. Une variable non observée reste indéterminée " +
      "et déclenche la politique de sécurité fermée par défaut.",
    snippet: `// désormais vrai dans les deux vocabulaires du langage
NEVER close_batch WHEN tools.update_row.available == false
// true/yes ↔ vrai · false/no ↔ faux · 1 == true reste FAUX`
  },
  {
    version: 'v1.8',
    tag: 'Outillage',
    tone: 'cyan',
    icon: InfinityIcon,
    title: 'agentl autoloop — validation de robustesse sur cas dérivés',
    body:
      "La commande autoloop permet d'éprouver la robustesse d'un agent au-delà de ses scénarios de test initiaux. " +
      "Elle génère automatiquement des variations de données (capteurs indisponibles, valeurs limites) tout en " +
      "conservant un lot de contrôle (30 % par défaut) préservé de l'apprentissage. La validation repose exclusivement " +
      "sur la vérification d'invariants universels et d'attentes déclarées.",
    snippet: `agentl autoloop X.agent --model gemini-3.1-flash-lite -o X.fixed.agent
agentl autoloop X.agent --host-pass   # hôte réel, lecture seule
# 0 = tient · 1 = casse · 3 = appris par cœur`
  },
  {
    version: 'v1.8',
    tag: 'Évolution',
    tone: 'rose',
    icon: Trash2,
    title: "Clarification de la cadence d'observation",
    body:
      "Remplacement de l'ancienne clause EVERY au profit de gardes conditionnelles WHEN explicites. " +
      "La planification périodique ou événementielle d'observations s'exprime désormais par des conditions d'état " +
      "claires et vérifiables, garantissant un comportement déterministe et fidèle à l'état du système.",
    snippet: `// la cadence s'exprime de manière transparente par des gardes WHEN :
OBSERVE network WHEN incident.suspected == yes`
  },
  {
    version: 'v1.7',
    tag: 'Preuve',
    tone: 'emerald',
    icon: Crosshair,
    title: 'Théorème T9 — réfutabilité empirique des effets déclarés',
    spec: 'SPEC §30',
    body:
      "Le théorème T9 garantit que chaque effet (EFFECT) modifiant le monde réel est obligatoirement " +
      "associé à un capteur (OBSERVE) permettant de vérifier son accomplissement. Les effets purement comptables " +
      "ou internes sont distingués par le mot-clé INTERNAL, assurant ainsi la cohérence formelle du modèle d'action.",
    codes: ['V150', 'V151'],
    snippet: `EFFECT { app.status = healthy }        // doit être observé (T9)
EFFECT { cycle.done = yes INTERNAL }   // comptabilité : exempté
MEMORY { LONG_TERM { effect_drift } }  // la crédibilité survit au processus`
  },
  {
    version: 'v1.6',
    tag: 'Preuve',
    tone: 'emerald',
    icon: Network,
    title: "Théorème T8 — preuve d'absence d'interblocage (deadlock)",
    spec: 'SPEC §21',
    body:
      "Pour les architectures multi-agents, le théorème T8 calcule le point fixe de productibilité sur " +
      "le graphe d'attente des signaux (échanges de messages et mémoire partagée). Il prouve mathématiquement " +
      "l'absence de cycles d'attente mutuels et garantit la vivacité globale du système.",
    codes: ['V130', 'V136', 'V137'],
    snippet: `T8 — Aucun agent n'attend un signal que nul ne produira
  ✔ DÉMONTRÉ (V134) · graphe d'attente sans cycle sans amorce`
  },
  {
    version: 'v1.6',
    tag: 'Sécurité',
    tone: 'rose',
    icon: ShieldCheck,
    title: 'Renforcement des frontières de sécurité de la politique',
    spec: 'SPEC §7.1 – §7.6',
    body:
      "Généralisation de la logique trivalente de Kleene (fail-closed) à l'ensemble des règles de sécurité, " +
      "encadrement strict des sous-agents délégués (DELEGATE), priorité absolue des arguments d'action sur " +
      "les variables locales et cloisonnement étanche des contextes transmis au LLM.",
    codes: ['W127', 'W128', 'W129'],
    snippet: `# 61 tests de sécurité dédiés, dont un test de mutation par règle.
# Chaque frontière est validée contre les contournements potentiels.`
  },
  {
    version: 'v1.6',
    tag: 'Audit',
    tone: 'purple',
    icon: FileSignature,
    title: 'Scellement cryptographique des journaux de rejeu',
    spec: 'SPEC §26.1',
    body:
      "Génération d'une chaîne de hachage par événement avec signature cryptographique optionnelle " +
      "(HMAC-SHA256 ou Ed25519). Cette structure garantit l'intégrité et la traçabilité complète des " +
      "exécutions lors des audits sans risque de falsification a posteriori.",
    snippet: `agentl run X.agent --record run.json --sign-key ed25519.pem
agentl seal run.json --key ed25519.pub     # sans rejouer
agentl replay run.json --require-seal`
  },
  {
    version: 'v1.6',
    tag: 'Interop',
    tone: 'purple',
    icon: Plug,
    title: 'Passerelle MCP sécurisée et typée',
    spec: 'SPEC §29',
    body:
      "Importation et conversion des outils du protocole Model Context Protocol (MCP) en contrats formels TOOL. " +
      "Le niveau de risque est exigé pour toute action, et les descriptions fournies par des tiers sont " +
      "neutralisées pour prémunir l'agent contre les injections de consignes.",
    codes: ['E011', 'B015'],
    snippet: `agentl mcp import --server srv -o outils.agent --strip-descriptions
// B015 : neutralisation des consignes impératives tierces`
  },
  {
    version: 'v1.6',
    tag: 'Planification',
    tone: 'cyan',
    icon: Timer,
    title: 'Intégration du facteur temps dans la planification A*',
    spec: 'SPEC §17.1',
    body:
      "Prise en compte explicite des durées d'outils (DURATION) et des échéances maximales (DEADLINE) dans " +
      "la synthèse de plans. Le planificateur optimise simultanément le coût des actions et le temps d'exécution " +
      "en recherchant un compromis de Pareto.",
    codes: ['W126'],
    snippet: `PLANNER { ENABLE ACHIEVE service.status == healthy
          DEADLINE 5m  TIME_WEIGHT 0.1 }`
  }
];

const VERSIONS = ['Toutes', 'v1.8.1', 'v1.8', 'v1.7', 'v1.6'];

export function WhatsNew({ compact = false, onViewAll }: WhatsNewProps) {
  const [filter, setFilter] = useState<string>('Toutes');

  const entries = filter === 'Toutes' ? ENTRIES : ENTRIES.filter(e => e.version === filter);
  const visibleEntries = compact ? entries.slice(0, 3) : entries;

  return (
    <section className={compact ? 'my-12' : 'my-16'}>
      <div className="mb-10 text-center max-w-3xl mx-auto">
        <div className="inline-flex items-center gap-2 rounded-full border border-cyan-500/30 bg-cyan-950/40 px-3 py-1 text-xs font-mono font-semibold text-cyan-400 mb-3">
          <Sparkles className="h-3.5 w-3.5" />
          {compact ? 'Dernières évolutions' : 'Journal des évolutions · v1.6 → v1.8.1'}
        </div>
        <h2 className="text-3xl sm:text-4xl font-black text-white tracking-tight">
          {compact ? 'Ce qui change dans AGENT-L' : 'Évolutions majeures du langage et du moteur'}
        </h2>
        <p className="mt-3 text-slate-400 text-sm sm:text-base leading-relaxed">
          <AgentLText textClassName="text-cyan-400" /> fait évoluer sa grammaire comme une API publique : chaque changement est versionné, documenté et traçable.
        </p>
      </div>

      {/* Version filter */}
      {!compact && <div className="mb-8 flex flex-wrap items-center justify-center gap-2">
        {VERSIONS.map(v => (
          <button
            key={v}
            onClick={() => setFilter(v)}
            className={`px-4 py-1.5 font-mono text-[11px] uppercase tracking-widest border transition-all ${
              filter === v
                ? 'border-cyan-500/50 bg-cyan-500/10 text-cyan-300 font-bold'
                : 'border-slate-800 bg-slate-950 text-slate-400 hover:text-slate-200 hover:border-slate-700'
            }`}
          >
            {v}
          </button>
        ))}
      </div>}

      {/* Headline numbers */}
      {!compact && <div className="mb-10 grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { value: '9', label: 'Théorèmes prouvés hors ligne', hint: 'T1–T7 + T9 par agent, T8 sur la société' },
          { value: '6', label: 'Portes de qualité', hint: 'check · test · verify · boundary · autoloop · run' },
          { value: '119', label: 'Mots réservés au contrat', hint: 'contrat d\'écriture 2.4.0, scellé par empreinte' },
          { value: '0', label: 'Dépendance du cœur', hint: 'stdlib Python 3.10+ · extras studio/sign/mcp/anthropic' }
        ].map((m, i) => (
          <div key={i} className="bg-white/5 border border-white/10 p-5 text-center">
            <div className="text-3xl font-light font-mono text-cyan-400">{m.value}</div>
            <div className="text-[11px] uppercase tracking-widest text-white/50 mt-1">{m.label}</div>
            <div className="text-[10px] text-white/30 font-mono mt-2 leading-relaxed">{m.hint}</div>
          </div>
        ))}
      </div>}

      {/* Timeline */}
      <div className="space-y-4">
        {visibleEntries.map((e, idx) => {
          const tone = TONES[e.tone];
          const Icon = e.icon;
          return (
            <article
              key={idx}
              className={`rounded-2xl border border-slate-800 bg-slate-950 p-6 transition-all ${tone.border}`}
            >
              <div className="flex flex-col lg:flex-row lg:items-start gap-6">
                <div className="lg:w-2/3">
                  <div className="flex flex-wrap items-center gap-2 mb-3">
                    <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 border font-mono text-[10px] font-bold uppercase tracking-widest ${tone.chip}`}>
                      <Icon className={`h-3 w-3 ${tone.icon}`} />
                      {e.version}
                    </span>
                    <span className="px-2 py-0.5 border border-white/10 bg-white/5 text-white/60 font-mono text-[10px] uppercase tracking-widest">
                      {e.tag}
                    </span>
                    {e.spec && (
                      <span className="px-2 py-0.5 border border-white/10 text-white/40 font-mono text-[10px]">
                        {e.spec}
                      </span>
                    )}
                    {e.codes?.map(c => (
                      <span key={c} className="px-2 py-0.5 border border-amber-500/30 bg-amber-500/10 text-amber-300 font-mono text-[10px] font-bold">
                        {c}
                      </span>
                    ))}
                  </div>

                  <h3 className="text-white font-bold text-base font-sans mb-2 flex items-start gap-2">
                    <ArrowRight className={`h-4 w-4 mt-1 shrink-0 ${tone.icon}`} />
                    {e.title}
                  </h3>

                  <p className="text-sm text-slate-300 leading-relaxed">
                    {e.body}
                  </p>
                </div>

                {e.snippet && (
                  <div className="lg:w-1/3 w-full">
                    <pre className="h-full font-mono text-[11px] text-slate-300 leading-relaxed bg-black/60 border border-white/10 rounded-lg p-4 overflow-x-auto">
<code>{e.snippet}</code>
                    </pre>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>

      {compact ? (
        <div className="mt-8 text-center">
          <button
            onClick={onViewAll}
            className="inline-flex items-center gap-2 border border-white/20 bg-white/5 px-5 py-2.5 text-xs font-bold uppercase tracking-wider text-white hover:border-cyan-500/40 hover:bg-white/10 transition-colors"
          >
            Voir toutes les évolutions
            <ArrowRight className="h-4 w-4 text-cyan-400" />
          </button>
        </div>
      ) : (
        <p className="mt-8 text-center text-[11px] font-mono text-slate-500">
          Pour le détail de chaque décision, consultez <code className="text-cyan-400">CHANGELOG.md</code> et <code className="text-cyan-400">docs/SPEC.md</code> dans le dépôt.
        </p>
      )}
    </section>
  );
}
