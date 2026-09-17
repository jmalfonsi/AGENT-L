import { AlertTriangle, CheckCircle2, CircleAlert, ShieldAlert, Wrench } from 'lucide-react';
import type { LiveRunResult } from './types';
import { tallyAssertions } from './assertions';
import { diagnoseRun } from './runDiagnosis';

const TONES = {
  success: {
    panel: 'border-emerald-200 bg-emerald-50', icon: 'text-emerald-600', title: 'text-emerald-950',
  },
  warning: {
    panel: 'border-amber-200 bg-amber-50', icon: 'text-amber-600', title: 'text-amber-950',
  },
  error: {
    panel: 'border-rose-200 bg-rose-50', icon: 'text-rose-600', title: 'text-rose-950',
  },
  invalid: {
    panel: 'border-violet-200 bg-violet-50', icon: 'text-violet-600', title: 'text-violet-950',
  },
} as const;

/** Lecture immédiate du run avant les preuves détaillées. */
export function RunDiagnosisPanel({ run }: { run: LiveRunResult }) {
  const tally = tallyAssertions(run.assertions || []);
  const diagnosis = diagnoseRun(run);
  const tone = TONES[diagnosis.tone];
  const Icon = diagnosis.tone === 'success' ? CheckCircle2
    : diagnosis.tone === 'error' ? AlertTriangle
      : diagnosis.tone === 'invalid' ? ShieldAlert
        : CircleAlert;

  return (
    <section className={`rounded-2xl border p-5 ${tone.panel}`}>
      <div className="flex items-start gap-3">
        <Icon size={22} className={`mt-0.5 shrink-0 ${tone.icon}`} />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-black uppercase tracking-[.18em] text-slate-500">Diagnostic de l’exécution</p>
          <h3 className={`mt-1 text-lg font-black ${tone.title}`}>{diagnosis.title}</h3>
          <p className="mt-1 max-w-4xl text-sm leading-6 text-slate-700">{diagnosis.explanation}</p>
        </div>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <div className="rounded-xl border border-white/80 bg-white/75 px-3 py-3">
          <p className="text-[10px] font-black uppercase tracking-wide text-slate-400">Validé</p>
          <p className="mt-1 text-lg font-black text-emerald-700">{tally.satisfied.length} / {tally.evaluated}</p>
          <p className="text-[11px] text-slate-500">vérifications notées</p>
        </div>
        <div className="rounded-xl border border-white/80 bg-white/75 px-3 py-3">
          <p className="text-[10px] font-black uppercase tracking-wide text-slate-400">À corriger</p>
          <p className={`mt-1 text-lg font-black ${tally.failed.length ? 'text-rose-700' : 'text-emerald-700'}`}>{tally.failed.length}</p>
          <p className="text-[11px] text-slate-500">conditions non satisfaites</p>
        </div>
        <div className="rounded-xl border border-white/80 bg-white/75 px-3 py-3">
          <p className="flex items-center gap-1 text-[10px] font-black uppercase tracking-wide text-slate-400"><Wrench size={11} />Actions</p>
          <p className="mt-1 text-lg font-black text-slate-900">{diagnosis.actionCount - diagnosis.actionFailures} / {diagnosis.actionCount}</p>
          <p className="text-[11px] text-slate-500">terminées sans erreur</p>
        </div>
        <div className="rounded-xl border border-white/80 bg-white/75 px-3 py-3">
          <p className="text-[10px] font-black uppercase tracking-wide text-slate-400">Hors barème</p>
          <p className="mt-1 text-lg font-black text-slate-700">{tally.notApplicable.length}</p>
          <p className="text-[11px] text-slate-500">écartées de la note</p>
        </div>
      </div>

      {diagnosis.blockedCount != null && diagnosis.blockedCount > 0 && (
        <p className="mt-3 rounded-lg border border-amber-200/80 bg-white/70 px-3 py-2 text-xs leading-5 text-amber-900">
          <span className="font-bold">Signal complémentaire :</span> le runtime AGENT-L a bloqué {diagnosis.blockedCount} tentative(s)
          selon ses politiques. Ce compteur n’est pas un échec en soi, mais peut expliquer une action absente ; les traces internes permettent de l’examiner.
        </p>
      )}
    </section>
  );
}
