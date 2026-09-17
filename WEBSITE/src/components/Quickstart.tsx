import React, { useState } from 'react';
import { Terminal, Copy, Check, Play, ShieldCheck, Zap, ArrowRight, FileCode, CheckCircle2, Bot, Sparkles } from 'lucide-react';
import { AgentLLogo, AgentLText } from './AgentLLogo';

export function Quickstart() {
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);

  const copyToClipboard = (text: string, index: number) => {
    navigator.clipboard.writeText(text);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  return (
    <section className="my-16">
      <div className="mb-10 text-center max-w-3xl mx-auto">
        <div className="inline-flex items-center gap-2 rounded-full border border-cyan-500/30 bg-cyan-950/40 px-3 py-1 text-xs font-mono font-semibold text-cyan-400 mb-3">
          <Zap className="h-3.5 w-3.5" />
          Démarrage rapide · 5 min
        </div>
        <h2 className="text-3xl sm:text-4xl font-black text-white tracking-tight">
          Du premier fichier à un agent vérifié
        </h2>
        <p className="mt-3 text-slate-400 text-sm sm:text-base leading-relaxed">
          Installez le cœur d'AGENT-L, écrivez votre contrat puis suivez la chaîne de validation jusqu'au rejeu.
        </p>
      </div>

      {/* Installation Banner */}
      <div className="mb-12 rounded-2xl border border-cyan-500/30 bg-slate-950 p-6 shadow-2xl relative overflow-hidden">
        <div className="flex flex-col md:flex-row items-center justify-between gap-6">
          <div>
            <span className="text-xs font-mono font-bold text-cyan-400 uppercase tracking-wider block mb-1">
              Installation · Python 3.10+
            </span>
            <h3 className="text-xl font-bold text-white">Un cœur sans dépendance externe</h3>
            <p className="text-xs text-slate-400 mt-1 max-w-xl">
              Le cœur repose uniquement sur la bibliothèque standard de Python 3.10+. Ajoutez au besoin <code className="text-cyan-300">studio</code> pour l'interface web, <code className="text-cyan-300">sign</code> pour Ed25519, <code className="text-cyan-300">mcp</code> pour les serveurs MCP ou <code className="text-cyan-300">anthropic</code>.
            </p>
          </div>

          <div className="flex flex-col sm:flex-row gap-3 w-full md:w-auto">
            <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-900 border border-slate-800 px-4 py-3 font-mono text-xs text-cyan-300">
              <code>pip install -e .</code>
              <button
                onClick={() => copyToClipboard('pip install -e .', 101)}
                className="text-slate-400 hover:text-white transition-colors"
              >
                {copiedIndex === 101 ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
              </button>
            </div>

            <div className="flex items-center justify-between gap-3 rounded-xl bg-slate-900 border border-slate-800 px-4 py-3 font-mono text-xs text-cyan-300">
              <code>pip install -e ".[studio]"</code>
              <button
                onClick={() => copyToClipboard('pip install -e ".[studio]"', 102)}
                className="text-slate-400 hover:text-white transition-colors"
              >
                {copiedIndex === 102 ? <Check className="h-4 w-4 text-emerald-400" /> : <Copy className="h-4 w-4" />}
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Skill Banner for AI Coding Agents (Claude Opus, ChatGPT, Cursor) */}
      <div className="mb-12 rounded-2xl border border-purple-500/30 bg-[#080510] p-6 shadow-2xl relative overflow-hidden">
        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-6">
          <div className="space-y-2 max-w-2xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-purple-500/30 bg-purple-500/10 px-3 py-1 text-xs font-mono font-bold text-purple-400">
              <Bot className="h-3.5 w-3.5" />
              Skill officiel · agentl-author
            </div>
            <h3 className="text-xl font-bold text-white font-sans">
              Écrivez des agents avec votre assistant de code
            </h3>
            <p className="text-sm text-white/70 leading-relaxed">
              Le skill <code className="text-purple-300 font-mono">agentl-author</code> aide votre assistant à respecter la règle centrale : l'hôte fournit les faits, le fichier <code className="text-purple-300 font-mono">.agent</code> prend les décisions. Il guide aussi toute la chaîne <code className="text-cyan-400 font-mono">check → test → verify → boundary → autoloop → run → replay</code>.
            </p>
          </div>

          <div className="w-full lg:w-auto bg-black/60 border border-purple-500/20 p-4 rounded-xl font-mono text-xs text-purple-200">
            <div className="text-[10px] uppercase tracking-widest text-purple-400/80 mb-2 font-bold flex items-center gap-1.5">
              <Sparkles className="h-3.5 w-3.5" /> Utiliser avec votre assistant
            </div>
            <div className="space-y-1.5 text-[11px] text-white/80">
              <div><strong className="text-purple-400">Claude Code / Opus :</strong> <code className="text-white/90 bg-white/5 px-1 py-0.5 rounded">/skills/agentl-author/SKILL.md</code></div>
              <div><strong className="text-purple-400">ChatGPT / GPT-4o :</strong> Ajouter <code className="text-white/90 bg-white/5 px-1 py-0.5 rounded">SKILL.md</code> dans les instructions système</div>
              <div><strong className="text-purple-400">Cursor / Windsurf :</strong> Ajouter la référence dans <code className="text-white/90 bg-white/5 px-1 py-0.5 rounded">.cursorrules</code></div>
            </div>
          </div>
        </div>
      </div>

      {/* The 5 Quality Gates Steps */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
        <div className="rounded-2xl border border-slate-800 bg-slate-950 p-5 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="font-mono text-xs font-bold text-cyan-400 bg-cyan-950 px-2 py-0.5 rounded">Porte 1</span>
              <span className="text-[10px] font-mono text-slate-400">Syntaxe EBNF</span>
            </div>
            <h4 className="font-bold text-white text-sm mb-1">1. agentl check</h4>
            <p className="text-xs text-slate-400 leading-relaxed">
              Vérifie la bonne formation du fichier <code className="text-cyan-300">.agent</code>. Détecte les erreurs statiques (<code className="text-rose-400">E001-E010</code>) avant l'exécution.
            </p>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-800/80 font-mono text-[11px] text-slate-300 flex items-center justify-between bg-slate-900 p-2 rounded">
            <code>python -m agentl check X.agent</code>
            <button onClick={() => copyToClipboard('python -m agentl check X.agent', 1)}><Copy className="h-3.5 w-3.5 text-slate-400" /></button>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-950 p-5 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="font-mono text-xs font-bold text-emerald-400 bg-emerald-950 px-2 py-0.5 rounded">Porte 2</span>
              <span className="text-[10px] font-mono text-slate-400">Critères d'acceptation</span>
            </div>
            <h4 className="font-bold text-white text-sm mb-1">2. agentl test</h4>
            <p className="text-xs text-slate-400 leading-relaxed">
              Exécute les critères d'acceptation <code className="text-cyan-300">SCENARIO</code> contre le monde déclaré, de manière étanche et déterministe.
            </p>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-800/80 font-mono text-[11px] text-slate-300 flex items-center justify-between bg-slate-900 p-2 rounded">
            <code>python -m agentl test X.agent</code>
            <button onClick={() => copyToClipboard('python -m agentl test X.agent', 2)}><Copy className="h-3.5 w-3.5 text-slate-400" /></button>
          </div>
        </div>

        <div className="rounded-2xl border border-cyan-500/40 bg-slate-950 p-5 flex flex-col justify-between shadow-lg shadow-cyan-950/30">
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="font-mono text-xs font-bold text-cyan-300 bg-cyan-950 px-2 py-0.5 rounded border border-cyan-500/30">Porte 3 (Sûreté)</span>
              <span className="text-[10px] font-mono text-cyan-400 font-bold">9 Théorèmes formels</span>
            </div>
            <h4 className="font-bold text-white text-sm mb-1">3. agentl verify</h4>
            <p className="text-xs text-slate-300 leading-relaxed">
              Démontre hors ligne que les interdictions <code className="text-rose-400">NEVER</code> sont respectées, qu'aucune impasse sans escalade n'existe, et que chaque <code className="text-cyan-300">EFFECT</code> est vérifiable. Le théorème T8 s'applique aux architectures multi-agents.
            </p>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-800/80 font-mono text-[11px] text-cyan-300 flex items-center justify-between bg-slate-900 p-2 rounded border border-cyan-500/30">
            <code>python -m agentl verify X.agent</code>
            <button onClick={() => copyToClipboard('python -m agentl verify X.agent', 3)}><Copy className="h-3.5 w-3.5 text-slate-400" /></button>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-950 p-5 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="font-mono text-xs font-bold text-amber-400 bg-amber-950 px-2 py-0.5 rounded">Porte 4</span>
              <span className="text-[10px] font-mono text-slate-400">Frontière de l'hôte Python</span>
            </div>
            <h4 className="font-bold text-white text-sm mb-1">4. agentl boundary</h4>
            <p className="text-xs text-slate-400 leading-relaxed">
              Analyse l'AST de <code className="text-amber-300">X.py</code> pour s'assurer qu'aucune décision métier n'a été déplacée dans le code Python (<code className="text-rose-400">B000</code>–<code className="text-rose-400">B015</code>).
            </p>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-800/80 font-mono text-[11px] text-slate-300 flex items-center justify-between bg-slate-900 p-2 rounded">
            <code>python -m agentl boundary X.agent</code>
            <button onClick={() => copyToClipboard('python -m agentl boundary X.agent', 4)}><Copy className="h-3.5 w-3.5 text-slate-400" /></button>
          </div>
        </div>

        <div className="rounded-2xl border border-purple-500/40 bg-slate-950 p-5 flex flex-col justify-between shadow-lg shadow-purple-950/30">
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="font-mono text-xs font-bold text-purple-300 bg-purple-950 px-2 py-0.5 rounded border border-purple-500/30">Porte 5</span>
              <span className="text-[10px] font-mono text-purple-400 font-bold">Robustesse et invariants</span>
            </div>
            <h4 className="font-bold text-white text-sm mb-1">5. agentl autoloop</h4>
            <p className="text-xs text-slate-300 leading-relaxed">
              Rejoue chaque scénario sur des données dérivées et vérifie le maintien strict des invariants. Un lot de contrôle préservé garantit l'absence de surapprentissage.
            </p>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-800/80 font-mono text-[11px] text-purple-200 flex items-center justify-between bg-slate-900 p-2 rounded border border-purple-500/30">
            <code>python -m agentl autoloop X.agent</code>
            <button onClick={() => copyToClipboard('python -m agentl autoloop X.agent', 7)}><Copy className="h-3.5 w-3.5 text-slate-400" /></button>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-950 p-5 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="font-mono text-xs font-bold text-emerald-400 bg-emerald-950 px-2 py-0.5 rounded">Porte 6</span>
              <span className="text-[10px] font-mono text-slate-400">Exécution et journalisation</span>
            </div>
            <h4 className="font-bold text-white text-sm mb-1">6. agentl run & record</h4>
            <p className="text-xs text-slate-400 leading-relaxed">
              Exécute l'agent avec son hôte et consigne les perceptions comme les décisions dans un journal rejouable.
            </p>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-800/80 font-mono text-[11px] text-slate-300 flex items-center justify-between bg-slate-900 p-2 rounded">
            <code>python -m agentl run X.agent --record run.json</code>
            <button onClick={() => copyToClipboard('python -m agentl run X.agent --record run.json', 5)}><Copy className="h-3.5 w-3.5 text-slate-400" /></button>
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-950 p-5 flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="font-mono text-xs font-bold text-blue-400 bg-blue-950 px-2 py-0.5 rounded">Audit</span>
              <span className="text-[10px] font-mono text-slate-400">Rejeu déterministe</span>
            </div>
            <h4 className="font-bold text-white text-sm mb-1">agentl replay et seal</h4>
            <p className="text-xs text-slate-400 leading-relaxed">
              Rejoue les décisions à l'identique, hors ligne et sans capteur. Une signature Ed25519 ou HMAC peut ensuite sceller le journal.
            </p>
          </div>
          <div className="mt-4 pt-3 border-t border-slate-800/80 font-mono text-[11px] text-slate-300 flex items-center justify-between bg-slate-900 p-2 rounded">
            <code>python -m agentl replay run.json</code>
            <button onClick={() => copyToClipboard('python -m agentl replay run.json', 6)}><Copy className="h-3.5 w-3.5 text-slate-400" /></button>
          </div>
        </div>
      </div>

    </section>
  );
}
