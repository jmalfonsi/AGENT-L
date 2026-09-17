import React from 'react';
import { Shield, GitBranch, BookOpen, Heart, Code2 } from 'lucide-react';
import { AgentLLogo, AgentLText } from './AgentLLogo';

export function Footer() {
  return (
    <footer className="border-t border-white/10 bg-[#050505] py-12 text-white/50 text-xs font-mono">
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-8 mb-8">
          
          <div className="md:col-span-2">
            <div className="flex items-center gap-3 text-white font-bold text-base mb-2">
              <AgentLLogo size="md" glow={true} />
              <span className="font-sans text-xl font-black tracking-tight text-white">AGENT-L</span>
            </div>
            <p className="text-white/50 text-sm leading-relaxed max-w-md">
              Un langage déclaratif et compilé pour séparer le raisonnement du LLM des décisions d'exécution. Le cœur repose uniquement sur la bibliothèque standard de Python 3.10+.
            </p>
            <div className="mt-3 flex items-center gap-3 text-[10px] uppercase tracking-widest text-white/40">
              <span>Licence AGPL-3.0-or-later</span>
              <span>•</span>
              <span>Langage v1.8 · paquet 1.8.0 · contrat d'écriture 2.4.0</span>
            </div>
          </div>

          <div>
            <h4 className="font-bold text-white text-[11px] uppercase tracking-widest mb-3">Ressources et spécifications</h4>
            <ul className="space-y-2 text-white/60 text-xs">
              <li className="hover:text-cyan-400 transition-colors">Grammaire EBNF (<code className="text-cyan-400">docs/agentl.ebnf</code>)</li>
              <li className="hover:text-cyan-400 transition-colors">Spécification Sémantique (<code className="text-cyan-400">docs/SPEC.md</code>)</li>
              <li className="hover:text-cyan-400 transition-colors">Journal des changements (<code className="text-cyan-400">CHANGELOG.md</code>)</li>
              <li className="hover:text-cyan-400 transition-colors">Banc AutomationBench (<code className="text-cyan-400">bench/</code>)</li>
              <li className="hover:text-cyan-400 transition-colors">Studio Web Local (<code className="text-cyan-400">agentl studio</code>)</li>
              <li className="hover:text-cyan-400 transition-colors">Skill d'écriture (<code className="text-cyan-400">SKILLS/agentl-author</code>)</li>
            </ul>
          </div>

          <div>
            <h4 className="font-bold text-white text-[11px] uppercase tracking-widest mb-3">Théorèmes formels · v1.8</h4>
            <ul className="space-y-1.5 text-white/50 text-[11px]">
              <li>T1 — Aucun appel interdit n'aboutit</li>
              <li>T2 — Impasse sans escalade démontrée</li>
              <li>T3 — Capacités mortes décelées</li>
              <li>T4 — Surface LLM sous garde d'état</li>
              <li>T5 — Scénarios d'acceptation vérifiés</li>
              <li>T6 / T7 — Provenance & terminaison</li>
              <li className="text-white/70">T8 — Vivacité de la société d'agents</li>
              <li className="text-white/70">T9 — Modèle d'effets réfutable</li>
            </ul>
          </div>

        </div>

        <div className="border-t border-white/10 pt-6 flex flex-col sm:flex-row items-center justify-between gap-4 text-white/40 text-[11px]">
          <div>
            © 2026 Projet AGENT-L · Agents autonomes sous politiques vérifiables.
          </div>
          <div className="flex items-center gap-2">
            <span>Conçu avec un cœur sans dépendance externe.</span>
          </div>
        </div>

      </div>
    </footer>
  );
}
