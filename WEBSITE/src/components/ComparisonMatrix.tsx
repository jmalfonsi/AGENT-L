import React, { useState } from 'react';
import { COMPARISON_FEATURES } from '../data/comparison';
import { Check, X, AlertCircle, HelpCircle, Shield, Layers, Filter, Sparkles, ChevronDown, ChevronUp } from 'lucide-react';

export function ComparisonMatrix() {
  const [selectedCategory, setSelectedCategory] = useState<string>('Toutes');
  const [expandedFeature, setExpandedFeature] = useState<string | null>(null);

  const categories = ['Toutes', ...Array.from(new Set(COMPARISON_FEATURES.map(f => f.category)))];

  const filteredFeatures = selectedCategory === 'Toutes'
    ? COMPARISON_FEATURES
    : COMPARISON_FEATURES.filter(f => f.category === selectedCategory);

  return (
    <section className="my-16">
      <div className="mb-8 text-center max-w-3xl mx-auto">
        <div className="inline-block px-3 py-1 border border-cyan-500/30 bg-cyan-500/5 text-cyan-400 text-[10px] uppercase tracking-widest font-mono mb-3">
          Comparatif d'architecture
        </div>
        <h2 className="text-3xl sm:text-5xl font-light text-white tracking-tight font-sans">
          Ce qui change avec <span className="italic font-serif text-cyan-400">AGENT-L</span>
        </h2>
        <p className="mt-3 text-white/60 text-sm sm:text-base leading-relaxed">
          Comparez la séparation des responsabilités d'AGENT-L aux orchestrateurs qui confient davantage de décisions au contexte du modèle.
        </p>
      </div>

      {/* Category Filter Tabs */}
      <div className="mb-6 flex items-center justify-center gap-2 flex-wrap text-xs font-mono">
        <span className="text-white/40 uppercase tracking-widest text-[10px] flex items-center gap-1 mr-2">
          <Filter className="h-3.5 w-3.5" /> Filtrer :
        </span>
        {categories.map((cat) => (
          <button
            key={cat}
            onClick={() => setSelectedCategory(cat)}
            className={`px-3 py-1.5 transition-all text-[11px] uppercase tracking-wider font-semibold rounded-none ${
              selectedCategory === cat
                ? 'bg-white text-black font-bold'
                : 'bg-white/5 border border-white/10 text-white/70 hover:bg-white/10'
            }`}
          >
            {cat}
          </button>
        ))}
      </div>

      {/* Desktop / Large Screen Matrix Table in Sophisticated Dark style */}
      <div className="hidden md:block overflow-hidden rounded-lg border border-white/10 bg-[#080808] shadow-2xl">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-white/10 bg-white/5 text-[11px] font-mono text-white/70 uppercase tracking-widest">
              <th className="p-4 w-1/3">Critère et fonctionnement</th>
              <th className="p-4 w-1/6 text-cyan-400 font-bold bg-cyan-500/10 border-l border-r border-cyan-500/30">
                <div className="flex items-center gap-1.5">
                  <Shield className="h-4 w-4 text-cyan-400" />
                  AGENT-L (v1.8)
                </div>
              </th>
              <th className="p-4 w-1/6 text-white/50 font-normal">LangChain / LangGraph</th>
              <th className="p-4 w-1/6 text-white/50 font-normal">CrewAI</th>
              <th className="p-4 w-1/6 text-white/50 font-normal">AutoGen</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5 text-xs font-mono">
            {filteredFeatures.map((feat, idx) => (
              <React.Fragment key={idx}>
                <tr 
                  onClick={() => setExpandedFeature(expandedFeature === feat.feature ? null : feat.feature)}
                  className="hover:bg-slate-900/50 cursor-pointer transition-colors"
                >
                  <td className="p-4">
                    <div className="font-bold text-slate-100 flex items-center justify-between">
                      <span>{feat.feature}</span>
                      {expandedFeature === feat.feature ? (
                        <ChevronUp className="h-4 w-4 text-cyan-400 ml-2" />
                      ) : (
                        <ChevronDown className="h-4 w-4 text-slate-500 ml-2" />
                      )}
                    </div>
                    <div className="text-[11px] text-slate-400 font-sans mt-0.5 leading-snug">
                      {feat.description}
                    </div>
                  </td>

                  {/* AGENT-L CELL */}
                  <td className="p-4 bg-cyan-950/20 border-l border-r border-cyan-500/20 font-bold">
                    <div className="flex items-start gap-2">
                      <Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                      <div>
                        <span className="text-emerald-300 font-bold">{feat.agentL.badge || 'Oui'}</span>
                        <p className="text-[10px] text-slate-300 font-sans font-normal mt-1 leading-snug">
                          {feat.agentL.detail}
                        </p>
                      </div>
                    </div>
                  </td>

                  {/* LANGCHAIN CELL */}
                  <td className="p-4">
                    <div className="flex items-start gap-2">
                      {feat.langchain.supported === true ? (
                        <Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                      ) : feat.langchain.supported === 'partial' ? (
                        <AlertCircle className="h-4 w-4 text-amber-400 shrink-0 mt-0.5" />
                      ) : (
                        <X className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" />
                      )}
                      <div>
                        <p className="text-[10px] text-slate-400 font-sans leading-snug">
                          {feat.langchain.detail}
                        </p>
                      </div>
                    </div>
                  </td>

                  {/* CREWAI CELL */}
                  <td className="p-4">
                    <div className="flex items-start gap-2">
                      {feat.crewAi.supported === true ? (
                        <Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                      ) : feat.crewAi.supported === 'partial' ? (
                        <AlertCircle className="h-4 w-4 text-amber-400 shrink-0 mt-0.5" />
                      ) : (
                        <X className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" />
                      )}
                      <div>
                        <p className="text-[10px] text-slate-400 font-sans leading-snug">
                          {feat.crewAi.detail}
                        </p>
                      </div>
                    </div>
                  </td>

                  {/* AUTOGEN CELL */}
                  <td className="p-4">
                    <div className="flex items-start gap-2">
                      {feat.autogen.supported === true ? (
                        <Check className="h-4 w-4 text-emerald-400 shrink-0 mt-0.5" />
                      ) : feat.autogen.supported === 'partial' ? (
                        <AlertCircle className="h-4 w-4 text-amber-400 shrink-0 mt-0.5" />
                      ) : (
                        <X className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" />
                      )}
                      <div>
                        <p className="text-[10px] text-slate-400 font-sans leading-snug">
                          {feat.autogen.detail}
                        </p>
                      </div>
                    </div>
                  </td>
                </tr>

                {/* Expanded Detailed Rationale Row */}
                {expandedFeature === feat.feature && (
                  <tr className="bg-slate-900/90 border-b border-cyan-500/30">
                    <td colSpan={5} className="p-4 text-xs font-sans text-slate-300 leading-relaxed">
                      <div className="flex items-start gap-3 rounded-xl bg-slate-950 p-4 border border-cyan-500/30">
                        <Sparkles className="h-5 w-5 text-cyan-400 shrink-0 mt-0.5" />
                        <div>
                          <span className="font-mono font-bold text-cyan-300 block mb-1">
                            À retenir — {feat.feature}
                          </span>
                          <p className="text-slate-300">
                            Dans de nombreux frameworks, les outils et les règles sont décrits dans le contexte du modèle. Une donnée piégée — un e-mail ou un journal contenant une fausse consigne — peut alors influencer son raisonnement.
                          </p>
                          <p className="mt-2 text-slate-300">
                            <strong>Avec AGENT-L</strong>, la règle <code className="text-cyan-300 font-mono">NEVER restart_service WHEN maintenance.window == open</code> est évaluée sur le fait fourni par l'hôte Python, hors du contexte du LLM. Le texte injecté ne peut donc pas modifier cette condition.
                          </p>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile Responsive Cards Matrix */}
      <div className="block md:hidden space-y-4">
        {filteredFeatures.map((feat, idx) => (
          <div key={idx} className="rounded-xl border border-slate-800 bg-slate-950 p-4">
            <div className="font-bold text-slate-100 text-sm mb-1">{feat.feature}</div>
            <p className="text-xs text-slate-400 mb-3">{feat.description}</p>

            <div className="space-y-2 border-t border-slate-800/80 pt-3 text-xs">
              <div className="rounded-lg bg-cyan-950/40 border border-cyan-500/30 p-2.5">
                <span className="font-mono font-bold text-cyan-300 block text-[11px] mb-1">
                  AGENT-L : {feat.agentL.badge || 'Pris en charge'}
                </span>
                <p className="text-slate-300 text-[11px]">{feat.agentL.detail}</p>
              </div>

              <div className="rounded-lg bg-slate-900 border border-slate-800 p-2">
                <span className="font-mono font-bold text-slate-400 block text-[10px]">LangChain / LangGraph :</span>
                <p className="text-slate-400 text-[11px]">{feat.langchain.detail}</p>
              </div>

              <div className="rounded-lg bg-slate-900 border border-slate-800 p-2">
                <span className="font-mono font-bold text-slate-400 block text-[10px]">CrewAI :</span>
                <p className="text-slate-400 text-[11px]">{feat.crewAi.detail}</p>
              </div>
            </div>
          </div>
        ))}
      </div>

    </section>
  );
}
