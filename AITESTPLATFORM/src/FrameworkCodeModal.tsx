import { useEffect, useState } from 'react';
import { Check, Clipboard, Code2, FileCode2, Loader2, X } from 'lucide-react';
import type { FrameworkCodeBundle } from './types';
import { useModal } from './useModal';

interface FrameworkCodeModalProps {
  bundle: FrameworkCodeBundle | null;
  loading: boolean;
  onClose: () => void;
}

export function FrameworkCodeModal({ bundle, loading, onClose }: FrameworkCodeModalProps) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [copied, setCopied] = useState<'idle' | 'done' | 'error'>('idle');

  useEffect(() => {
    setActiveIndex(0);
    setCopied('idle');
  }, [bundle]);

  const open = loading || Boolean(bundle);
  const containerRef = useModal<HTMLDivElement>(open, onClose);

  if (!open) return null;
  const activeFile = bundle?.files[activeIndex] || bundle?.files[0];

  const copySource = async () => {
    if (!activeFile) return;
    try {
      // `navigator.clipboard` est absent hors contexte sécurisé : la promesse
      // rejetée passait sans être capturée et le bouton restait muet.
      await navigator.clipboard.writeText(activeFile.content);
      setCopied('done');
    } catch {
      setCopied('error');
    }
    window.setTimeout(() => setCopied('idle'), 2000);
  };

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/70 p-3 backdrop-blur-sm sm:p-6" onMouseDown={onClose}>
      <div
        ref={containerRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={bundle ? `Code exécuté par ${bundle.name}` : 'Code du framework'}
        className="flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-2xl border border-slate-700 bg-[#111827] shadow-2xl outline-none"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-700 px-5 py-4 text-white">
          <div className="flex min-w-0 items-start gap-3">
            <div className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-orange-500"><Code2 size={20} /></div>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-black">{bundle ? `Code exécuté · ${bundle.name}` : 'Chargement du code…'}</h2>
                {bundle && (
                  <span className={`rounded px-2 py-1 text-[9px] font-black uppercase tracking-wider ${bundle.scope === 'task_specific' ? 'bg-orange-500/20 text-orange-300' : 'bg-sky-500/20 text-sky-300'}`}>
                    {bundle.scope === 'task_specific' ? 'spécifique à la tâche' : 'adaptateur partagé'}
                  </span>
                )}
              </div>
              {bundle && <p className="mt-1 text-xs leading-5 text-slate-400">{bundle.description}</p>}
              {bundle?.taskId && <p className="mt-1 font-mono text-[10px] text-orange-300">{bundle.taskId}</p>}
            </div>
          </div>
          <button onClick={onClose} className="shrink-0 rounded-lg p-2 text-slate-400 transition hover:bg-slate-800 hover:text-white" aria-label="Fermer (Échap)" title="Fermer (Échap)"><X size={19} /></button>
        </div>

        {loading || !bundle || !activeFile ? (
          <div className="grid min-h-80 place-items-center text-slate-400"><Loader2 className="animate-spin" /></div>
        ) : (
          <>
            <div className="flex gap-2 overflow-x-auto border-b border-slate-700 bg-slate-900 px-4 pt-3">
              {bundle.files.map((file, index) => (
                <button key={`${file.name}-${index}`} onClick={() => { setActiveIndex(index); setCopied('idle'); }} className={`flex shrink-0 items-center gap-2 rounded-t-lg border-x border-t px-3 py-2 text-xs font-bold transition ${index === activeIndex ? 'border-slate-600 bg-[#111827] text-orange-300' : 'border-transparent text-slate-500 hover:text-slate-300'}`}>
                  <FileCode2 size={14} />{file.name}
                </button>
              ))}
            </div>
            <div className="flex items-center justify-between gap-3 border-b border-slate-800 px-5 py-3">
              <div>
                <p className="text-xs font-bold text-slate-200">{activeFile.label}</p>
                <p className="mt-0.5 text-[10px] uppercase tracking-wider text-slate-500">{activeFile.language} · source non modifiée</p>
              </div>
              <button onClick={copySource} title="Copier le contenu du fichier affiché" className="flex shrink-0 items-center gap-2 rounded-lg border border-slate-700 px-3 py-2 text-xs font-bold text-slate-300 transition hover:border-slate-600 hover:text-white">
                {copied === 'done' ? <Check size={14} className="text-emerald-400" /> : <Clipboard size={14} />}
                {copied === 'done' ? 'Copié' : copied === 'error' ? 'Copie refusée par le navigateur' : 'Copier'}
              </button>
            </div>
            <pre className="min-h-0 flex-1 overflow-auto p-5 font-mono text-[12px] leading-6 text-slate-300"><code>{activeFile.content}</code></pre>
          </>
        )}
      </div>
    </div>
  );
}
