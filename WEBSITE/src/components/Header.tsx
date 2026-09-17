import React, { useState } from 'react';
import { Shield, Terminal, Copy, Check, ExternalLink, Cpu, BookOpen, Layers, BarChart3, Code, Zap, Sparkles } from 'lucide-react';
import { AgentLLogo, AgentLText } from './AgentLLogo';

interface HeaderProps {
  activeTab: string;
  setActiveTab: (tab: string) => void;
}

export function Header({ activeTab, setActiveTab }: HeaderProps) {
  const [copiedPip, setCopiedPip] = useState(false);

  const handleCopyPip = () => {
    navigator.clipboard.writeText('pip install -e .');
    setCopiedPip(true);
    setTimeout(() => setCopiedPip(false), 2000);
  };

  return (
    <header className="sticky top-0 z-50 w-full border-b border-white/10 bg-[#050505]/90 backdrop-blur-xl">
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
       <div className="flex items-center justify-between py-4">

        {/* Logo & Version Badge */}
        <div className="flex items-center gap-4">
          <div 
            onClick={() => setActiveTab('overview')}
            className="group flex items-center gap-3.5 cursor-pointer"
          >
            <AgentLLogo size="lg" glow={true} className="shadow-cyan-500/40" />
            <div>
              <div className="flex items-center gap-2">
                <span className="text-2xl font-black tracking-tighter text-white font-sans flex items-center gap-1 whitespace-nowrap">
                  AGENT-L
                </span>
                <span className="inline-block px-2 py-0.5 border border-cyan-500/40 bg-cyan-500/10 text-cyan-300 text-[10px] font-bold uppercase tracking-widest font-mono rounded-none">
                  v1.8
                </span>
              </div>
              <p className="text-[10px] font-mono text-cyan-400/80 hidden sm:block tracking-wide font-semibold">
                Langage déclaratif pour des agents vérifiables
              </p>
            </div>
          </div>
        </div>

        {/* Quick Pip Install Action */}
        <div className="flex items-center gap-3">
          <button
            onClick={handleCopyPip}
            className="flex items-center gap-2 border border-white/20 bg-white/5 px-4 py-2 font-mono text-xs text-white whitespace-nowrap hover:bg-white/10 hover:border-cyan-400/50 transition-all shadow-sm rounded-none"
          >
            <Terminal className="h-3.5 w-3.5 text-cyan-400" />
            <span className="hidden sm:inline">pip install -e .</span>
            <span className="sm:hidden">pip -e .</span>
            {copiedPip ? (
              <Check className="h-3.5 w-3.5 text-emerald-400" />
            ) : (
              <Copy className="h-3.5 w-3.5 text-white/50" />
            )}
          </button>
        </div>

       </div>

        {/* Navigation Tabs — seconde ligne, pour que rien ne déborde */}
        <nav className="hidden lg:flex flex-wrap items-center justify-center gap-0.5 xl:gap-1 pb-2 text-[11px] uppercase tracking-widest font-semibold">
          <button
            onClick={() => setActiveTab('overview')}
            className={`px-2 xl:px-3 py-1.5 whitespace-nowrap transition-all flex items-center gap-1.5 ${
              activeTab === 'overview'
                ? 'text-cyan-400 border-b-2 border-cyan-400 font-bold bg-white/5'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            }`}
          >
            <Shield className="h-3.5 w-3.5" />
            Présentation
          </button>

          <button
            onClick={() => setActiveTab('whatsnew')}
            className={`px-2 xl:px-3 py-1.5 whitespace-nowrap transition-all flex items-center gap-1.5 ${
              activeTab === 'whatsnew'
                ? 'text-cyan-400 border-b-2 border-cyan-400 font-bold bg-white/5'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            }`}
          >
            <Sparkles className="h-3.5 w-3.5" />
            Nouveautés
          </button>

          <button
            onClick={() => setActiveTab('boundary')}
            className={`px-2 xl:px-3 py-1.5 whitespace-nowrap transition-all flex items-center gap-1.5 ${
              activeTab === 'boundary'
                ? 'text-cyan-400 border-b-2 border-cyan-400 font-bold bg-white/5'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            }`}
          >
            <Cpu className="h-3.5 w-3.5" />
            Architecture
          </button>

          <button
            onClick={() => setActiveTab('separation')}
            className={`px-2 xl:px-3 py-1.5 whitespace-nowrap transition-all flex items-center gap-1.5 ${
              activeTab === 'separation'
                ? 'text-cyan-400 border-b-2 border-cyan-400 font-bold bg-white/5'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            }`}
          >
            <Shield className="h-3.5 w-3.5" />
            Rôles & frontière
          </button>

          <button
            onClick={() => setActiveTab('comparison')}
            className={`px-2 xl:px-3 py-1.5 whitespace-nowrap transition-all flex items-center gap-1.5 ${
              activeTab === 'comparison'
                ? 'text-cyan-400 border-b-2 border-cyan-400 font-bold bg-white/5'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            }`}
          >
            <Layers className="h-3.5 w-3.5" />
            Comparatif
          </button>

          <button
            onClick={() => setActiveTab('playground')}
            className={`px-2 xl:px-3 py-1.5 whitespace-nowrap transition-all flex items-center gap-1.5 ${
              activeTab === 'playground'
                ? 'text-cyan-400 border-b-2 border-cyan-400 font-bold bg-white/5'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            }`}
          >
            <Code className="h-3.5 w-3.5" />
            Simulateur
          </button>

          <button
            onClick={() => setActiveTab('quickstart')}
            className={`px-2 xl:px-3 py-1.5 whitespace-nowrap transition-all flex items-center gap-1.5 ${
              activeTab === 'quickstart'
                ? 'text-cyan-400 border-b-2 border-cyan-400 font-bold bg-white/5'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            }`}
          >
            <Zap className="h-3.5 w-3.5" />
            Démarrage
          </button>

          <button
            onClick={() => setActiveTab('docs')}
            className={`px-2 xl:px-3 py-1.5 whitespace-nowrap transition-all flex items-center gap-1.5 ${
              activeTab === 'docs'
                ? 'text-cyan-400 border-b-2 border-cyan-400 font-bold bg-white/5'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            }`}
          >
            <BookOpen className="h-3.5 w-3.5" />
            Référence
          </button>

          <button
            onClick={() => setActiveTab('benchmarks')}
            className={`px-2 xl:px-3 py-1.5 whitespace-nowrap transition-all flex items-center gap-1.5 ${
              activeTab === 'benchmarks'
                ? 'text-cyan-400 border-b-2 border-cyan-400 font-bold bg-white/5'
                : 'text-white/60 hover:text-white hover:bg-white/5'
            }`}
          >
            <BarChart3 className="h-3.5 w-3.5" />
            Résultats
          </button>
        </nav>

      </div>

      {/* Mobile Subnav Row */}
      <div className="flex lg:hidden overflow-x-auto border-t border-white/10 px-4 py-2 gap-1 text-[10px] uppercase tracking-widest font-semibold no-scrollbar bg-black/40">
        <button
          onClick={() => setActiveTab('overview')}
          className={`px-3 py-1 whitespace-nowrap ${
            activeTab === 'overview' ? 'bg-cyan-500/20 text-cyan-400 font-bold border border-cyan-500/30' : 'text-white/60'
          }`}
        >
          Présentation
        </button>
        <button
          onClick={() => setActiveTab('whatsnew')}
          className={`px-3 py-1 whitespace-nowrap ${
            activeTab === 'whatsnew' ? 'bg-cyan-500/20 text-cyan-400 font-bold border border-cyan-500/30' : 'text-white/60'
          }`}
        >
          Nouveautés
        </button>
        <button
          onClick={() => setActiveTab('boundary')}
          className={`px-3 py-1 whitespace-nowrap ${
            activeTab === 'boundary' ? 'bg-cyan-500/20 text-cyan-400 font-bold border border-cyan-500/30' : 'text-white/60'
          }`}
        >
          Architecture
        </button>
        <button
          onClick={() => setActiveTab('separation')}
          className={`px-3 py-1 whitespace-nowrap ${
            activeTab === 'separation' ? 'bg-cyan-500/20 text-cyan-400 font-bold border border-cyan-500/30' : 'text-white/60'
          }`}
        >
          Frontière
        </button>
        <button
          onClick={() => setActiveTab('comparison')}
          className={`px-3 py-1 whitespace-nowrap ${
            activeTab === 'comparison' ? 'bg-cyan-500/20 text-cyan-400 font-bold border border-cyan-500/30' : 'text-white/60'
          }`}
        >
          Comparatif
        </button>
        <button
          onClick={() => setActiveTab('playground')}
          className={`px-3 py-1 whitespace-nowrap ${
            activeTab === 'playground' ? 'bg-cyan-500/20 text-cyan-400 font-bold border border-cyan-500/30' : 'text-white/60'
          }`}
        >
          Simulateur
        </button>
        <button
          onClick={() => setActiveTab('quickstart')}
          className={`px-3 py-1 whitespace-nowrap ${
            activeTab === 'quickstart' ? 'bg-cyan-500/20 text-cyan-400 font-bold border border-cyan-500/30' : 'text-white/60'
          }`}
        >
          Démarrage
        </button>
        <button
          onClick={() => setActiveTab('docs')}
          className={`px-3 py-1 whitespace-nowrap ${
            activeTab === 'docs' ? 'bg-cyan-500/20 text-cyan-400 font-bold border border-cyan-500/30' : 'text-white/60'
          }`}
        >
          Docs
        </button>
        <button
          onClick={() => setActiveTab('benchmarks')}
          className={`px-3 py-1 whitespace-nowrap ${
            activeTab === 'benchmarks' ? 'bg-cyan-500/20 text-cyan-400 font-bold border border-cyan-500/30' : 'text-white/60'
          }`}
        >
          Résultats
        </button>
      </div>
    </header>
  );
}
