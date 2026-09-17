import React, { useState } from 'react';
import { EXAMPLES } from '../data/examples';
import { CodeExample } from '../types';
import { Terminal, Play, ShieldCheck, CheckCircle2, RotateCcw, Copy, Check, FileCode, Layers, Cpu, AlertTriangle } from 'lucide-react';

export function Playground() {
  const [selectedExampleId, setSelectedExampleId] = useState<string>('soc_analyst');
  const [activeCodeTab, setActiveCodeTab] = useState<'agent' | 'python'>('agent');
  const [activeAction, setActiveAction] = useState<'check' | 'verify' | 'boundary' | 'run' | 'replay'>('verify');
  const [copiedCode, setCopiedCode] = useState(false);

  const example = EXAMPLES.find(e => e.id === selectedExampleId) || EXAMPLES[0];

  const handleCopyCode = () => {
    const code = activeCodeTab === 'agent' ? example.agentCode : example.hostCode;
    navigator.clipboard.writeText(code);
    setCopiedCode(true);
    setTimeout(() => setCopiedCode(false), 2000);
  };

  const activeOutputLines = example.simulateOutput[activeAction] || [];

  return (
    <section className="my-16">
      <div className="mb-8 text-center max-w-3xl mx-auto">
        <div className="inline-flex items-center gap-2 rounded-full border border-cyan-500/30 bg-cyan-950/40 px-3 py-1 text-xs font-mono font-semibold text-cyan-400 mb-3">
          <Terminal className="h-3.5 w-3.5" />
          Simulateur interactif
        </div>
        <h2 className="text-3xl sm:text-4xl font-black text-white tracking-tight">
          Explorez un agent, puis vérifiez-le
        </h2>
        <p className="mt-3 text-slate-400 text-sm sm:text-base leading-relaxed">
          Choisissez un exemple, comparez le contrat <code className="text-cyan-300">.agent</code> à son hôte Python <code className="text-amber-300">.py</code>, puis parcourez les résultats de chaque vérification.
        </p>
      </div>

      {/* Preset Selector Header */}
      <div className="mb-6 flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-mono text-slate-400">Exemples :</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex.id}
              onClick={() => {
                setSelectedExampleId(ex.id);
                setActiveAction('verify');
              }}
              className={`rounded-lg px-3 py-1.5 font-mono text-xs transition-all ${
                selectedExampleId === ex.id
                  ? 'bg-cyan-500 text-slate-950 font-bold shadow-md shadow-cyan-500/20'
                  : 'bg-slate-900 border border-slate-800 text-slate-300 hover:border-slate-700'
              }`}
            >
              {ex.title.split('—')[0]}
            </button>
          ))}
        </div>

        <div className="text-xs font-mono text-slate-400 italic">
          Règle de structure : <code className="text-cyan-300">X.agent</code> ↔ <code className="text-amber-300">X.py</code>
        </div>
      </div>

      {/* Code & Terminal Dual Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
        
        {/* Left: Code Viewer */}
        <div className="lg:col-span-7 flex flex-col rounded-2xl border border-slate-800 bg-slate-950 shadow-2xl overflow-hidden">
          
          {/* Code Viewer Tab Header */}
          <div className="flex items-center justify-between border-b border-slate-800 bg-slate-900/80 px-4 py-2.5">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setActiveCodeTab('agent')}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1 font-mono text-xs transition-all ${
                  activeCodeTab === 'agent'
                    ? 'bg-cyan-950 text-cyan-300 font-bold border border-cyan-500/30'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <FileCode className="h-3.5 w-3.5 text-cyan-400" />
                {example.id}.agent · contrat
              </button>

              <button
                onClick={() => setActiveCodeTab('python')}
                className={`flex items-center gap-1.5 rounded-lg px-3 py-1 font-mono text-xs transition-all ${
                  activeCodeTab === 'python'
                    ? 'bg-amber-950 text-amber-300 font-bold border border-amber-500/30'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                <Cpu className="h-3.5 w-3.5 text-amber-400" />
                {example.id}.py · hôte
              </button>
            </div>

            <button
              onClick={handleCopyCode}
              className="flex items-center gap-1.5 text-xs font-mono text-slate-400 hover:text-white transition-colors"
            >
              {copiedCode ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
              <span>Copier</span>
            </button>
          </div>

          {/* Description banner */}
          <div className="bg-slate-900/40 p-3 border-b border-slate-800/60 text-xs text-slate-300 flex items-center gap-2 font-sans">
            <Layers className="h-4 w-4 text-cyan-400 shrink-0" />
            <span>{example.description}</span>
          </div>

          {/* Code Area */}
          <pre className="flex-1 p-4 font-mono text-xs text-slate-200 overflow-x-auto leading-relaxed bg-slate-950/90 max-h-[480px]">
<code>{activeCodeTab === 'agent' ? example.agentCode : example.hostCode}</code>
          </pre>
        </div>

        {/* Right: Terminal CLI Console */}
        <div className="lg:col-span-5 flex flex-col rounded-2xl border border-slate-800 bg-slate-950 shadow-2xl overflow-hidden">
          
          {/* CLI Action Selector Bar */}
          <div className="border-b border-slate-800 bg-slate-900/90 p-3 flex items-center gap-1.5 overflow-x-auto">
            <button
              onClick={() => setActiveAction('check')}
              className={`px-2.5 py-1 rounded text-xs font-mono font-semibold transition-all ${
                activeAction === 'check'
                  ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                  : 'bg-slate-800 text-slate-400 hover:text-slate-200'
              }`}
            >
              check
            </button>

            <button
              onClick={() => setActiveAction('verify')}
              className={`px-2.5 py-1 rounded text-xs font-mono font-semibold transition-all ${
                activeAction === 'verify'
                  ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                  : 'bg-slate-800 text-slate-400 hover:text-slate-200'
              }`}
            >
              verify (Sûreté)
            </button>

            <button
              onClick={() => setActiveAction('boundary')}
              className={`px-2.5 py-1 rounded text-xs font-mono font-semibold transition-all ${
                activeAction === 'boundary'
                  ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                  : 'bg-slate-800 text-slate-400 hover:text-slate-200'
              }`}
            >
              boundary
            </button>

            <button
              onClick={() => setActiveAction('run')}
              className={`px-2.5 py-1 rounded text-xs font-mono font-semibold transition-all ${
                activeAction === 'run'
                  ? 'bg-emerald-500 text-slate-950 font-bold shadow'
                  : 'bg-slate-800 text-slate-400 hover:text-slate-200'
              }`}
            >
              run
            </button>

            <button
              onClick={() => setActiveAction('replay')}
              className={`px-2.5 py-1 rounded text-xs font-mono font-semibold transition-all ${
                activeAction === 'replay'
                  ? 'bg-blue-500 text-slate-950 font-bold shadow'
                  : 'bg-slate-800 text-slate-400 hover:text-slate-200'
              }`}
            >
              replay
            </button>
          </div>

          {/* Terminal Output Screen */}
          <div className="flex-1 p-4 font-mono text-xs bg-black/90 text-slate-200 overflow-y-auto space-y-2 min-h-[380px]">
            <div className="text-slate-500 pb-2 border-b border-slate-800">
              $ python -m agentl {activeAction} {example.id}.agent {activeAction === 'run' ? '--record run.json' : ''}
            </div>

            {activeOutputLines.map((line, idx) => {
              const isError = line.includes('✗') || line.includes('⛔') || line.includes('RÉFUTÉ') || line.includes('DENIED');
              const isSuccess = line.includes('✔') || line.includes('✅') || line.includes('PROUVÉ');
              const isWarning = line.includes('!') || line.includes('ℹ') || line.includes('∿') || line.includes('🛑');

              return (
                <div
                  key={idx}
                  className={`leading-relaxed ${
                    isError
                      ? 'text-rose-400 font-bold'
                      : isSuccess
                      ? 'text-emerald-400 font-bold'
                      : isWarning
                      ? 'text-amber-300'
                      : 'text-slate-300'
                  }`}
                >
                  {line}
                </div>
              );
            })}
          </div>

          {/* Console Footer Status */}
          <div className="border-t border-slate-800 bg-slate-900/60 p-3 text-[11px] font-mono text-slate-400 flex items-center justify-between">
            <span>Moteur AGENT-L v1.8</span>
            <span className="text-emerald-400">Empreinte SHA-256 validée</span>
          </div>

        </div>

      </div>
    </section>
  );
}
