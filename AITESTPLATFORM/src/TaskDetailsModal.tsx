import { Database, FileJson, Hash, Loader2, ShieldCheck, Wrench, X } from 'lucide-react';
import type { BenchmarkTaskDetail } from './types';
import { useModal } from './useModal';
import { Tooltip } from './Tooltip';
import { help } from './glossary';

const json = (value: unknown) => JSON.stringify(value, null, 2);

export function TaskDetailsModal({
  detail,
  loading,
  onClose,
}: {
  detail: BenchmarkTaskDetail | null;
  loading: boolean;
  onClose: () => void;
}) {
  const open = Boolean(detail) || loading;
  const containerRef = useModal<HTMLElement>(open, onClose);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-3 backdrop-blur-sm" onMouseDown={onClose}>
      <section
        ref={containerRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={detail ? `Fiche de la tâche ${detail.title}` : 'Fiche AutomationBench'}
        className="max-h-[94vh] w-full max-w-6xl overflow-hidden rounded-2xl bg-white shadow-2xl outline-none"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-slate-200 bg-slate-950 p-5 text-white">
          <div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-slate-400">
              <span className="flex items-center gap-1 rounded bg-orange-500/15 px-2 py-1 font-bold text-orange-300"><Hash size={12} />{detail?.number ?? '—'}</span>
              <span className="font-mono">{detail?.id || 'Chargement…'}</span>
            </div>
            <h2 className="mt-2 text-xl font-black">{detail?.title || 'Fiche AutomationBench'}</h2>
            {detail && <p className="mt-1 text-xs text-slate-400">Énoncé public complet. Rien de cette fiche n’est transmis aux agents pendant l’exécution : ni l’état attendu, ni les assertions.</p>}
          </div>
          <button onClick={onClose} className="shrink-0 rounded-lg p-2 text-slate-400 hover:bg-white/10 hover:text-white" aria-label="Fermer (Échap)" title="Fermer (Échap)"><X size={20} /></button>
        </header>

        {loading || !detail ? (
          <div className="grid min-h-80 place-items-center text-slate-400"><Loader2 className="animate-spin" /></div>
        ) : (
          <div className="max-h-[calc(94vh-96px)] space-y-5 overflow-auto p-5">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-xl border border-slate-200 p-4"><p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Domaine</p><p className="mt-1 font-bold uppercase text-slate-900">{detail.domain}</p></div>
              <div className="rounded-xl border border-slate-200 p-4"><p className="text-[10px] font-black uppercase tracking-widest text-slate-400">Outils officiels</p><p className="mt-1 text-xl font-black text-slate-900">{detail.toolCount}</p></div>
              <div className="rounded-xl border border-orange-200 bg-orange-50 p-4"><p className="flex items-center gap-1 text-[10px] font-black uppercase tracking-widest text-orange-500">Outils communs<Tooltip content={help('facade')} side="bottom" /></p><p className="mt-1 text-xl font-black text-orange-700">{detail.runtimeToolCount}</p></div>
              <div className="rounded-xl border border-slate-200 p-4"><p className="flex items-center gap-1 text-[10px] font-black uppercase tracking-widest text-slate-400">Assertions<Tooltip content={help('assertion')} side="bottom" /></p><p className="mt-1 text-xl font-black text-slate-900">{detail.assertionCount}</p></div>
            </div>

            <section className="rounded-xl border border-slate-200 p-4">
              <div className="mb-3 flex items-center gap-2"><FileJson size={17} className="text-orange-500" /><h3 className="font-black text-slate-900">Prompt original</h3></div>
              <div className="space-y-2">
                {detail.prompt.map((message, index) => (
                  <div key={index} className="rounded-lg bg-slate-50 p-3"><p className="text-[10px] font-bold uppercase text-slate-400">{message.role}</p><p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-700">{message.content}</p></div>
                ))}
              </div>
            </section>

            <section className="rounded-xl border border-orange-200 bg-orange-50/50 p-4">
              <div className="mb-3 flex items-center gap-2"><Wrench size={17} className="text-orange-600" /><h3 className="font-black text-slate-900">Façade métier commune aux quatre runtimes</h3></div>
              <div className="flex flex-wrap gap-2">{detail.runtimeTools.map((name) => <span key={name} className="rounded-lg border border-orange-200 bg-white px-2 py-1 font-mono text-[10px] font-bold text-orange-700">{name}</span>)}</div>
            </section>

            <section className="rounded-xl border border-slate-200 p-4">
              <div className="mb-3 flex items-center gap-2"><Wrench size={17} className="text-sky-600" /><h3 className="font-black text-slate-900">Outils et contrats</h3></div>
              <div className="grid gap-3 lg:grid-cols-2">
                {detail.toolDefinitions.map((tool) => (
                  <details key={tool.name} className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                    <summary className="cursor-pointer font-mono text-xs font-bold text-slate-900">{tool.name}</summary>
                    <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-500">{tool.doc || 'Documentation non fournie.'}</p>
                    <div className="mt-3 space-y-1">{tool.params.map((param) => <p key={param.name} className="font-mono text-[10px] text-slate-600">{param.name}: {param.type}{param.required ? ' · requis' : ' · optionnel'}</p>)}</div>
                  </details>
                ))}
              </div>
            </section>

            <section className="rounded-xl border border-slate-200 p-4">
              <div className="mb-3 flex items-center gap-2"><ShieldCheck size={17} className="text-emerald-600" /><h3 className="font-black text-slate-900">Assertions officielles</h3></div>
              <div className="grid gap-2 lg:grid-cols-2">
                {detail.assertions.map((assertion, index) => (
                  <div key={index} className="rounded-lg bg-slate-50 p-3"><p className="font-mono text-xs font-bold text-slate-900">#{index + 1} · {assertion.type || 'assertion'}</p><pre className="mt-2 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-5 text-slate-500">{json(assertion)}</pre></div>
                ))}
              </div>
            </section>

            <details className="rounded-xl border border-slate-200 p-4">
              <summary className="flex cursor-pointer items-center gap-2 font-black text-slate-900"><Database size={17} className="text-violet-600" />État initial original</summary>
              <pre className="mt-3 max-h-[500px] overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-4 text-[10px] leading-5 text-slate-300">{json(detail.initialState)}</pre>
            </details>

            <details className="rounded-xl border border-slate-200 p-4">
              <summary className="flex cursor-pointer items-center gap-2 font-black text-slate-900"><FileJson size={17} className="text-orange-500" />Objet AutomationBench brut</summary>
              <pre className="mt-3 max-h-[600px] overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-4 text-[10px] leading-5 text-slate-300">{json(detail.original)}</pre>
            </details>
          </div>
        )}
      </section>
    </div>
  );
}
