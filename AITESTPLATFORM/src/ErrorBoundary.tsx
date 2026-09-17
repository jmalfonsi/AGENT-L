import { Component, type ErrorInfo, type ReactNode } from 'react';
import { RefreshCw, XCircle } from 'lucide-react';

interface State {
  error: Error | null;
}

/**
 * Un défaut d'affichage dans un panneau ne doit jamais faire disparaître toute
 * la plateforme : l'écran blanc est indistinguable d'une panne serveur pour
 * l'utilisateur, et il masque les résultats déjà obtenus.
 */
export class ErrorBoundary extends Component<{ children: ReactNode; label?: string }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('[AITESTPLATFORM] défaut d’affichage', error, info.componentStack);
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="m-5 rounded-2xl border border-rose-200 bg-rose-50 p-5 text-sm text-rose-900">
        <div className="flex items-start gap-3">
          <XCircle className="mt-0.5 shrink-0" size={20} />
          <div className="min-w-0">
            <p className="font-black">Ce panneau n’a pas pu s’afficher</p>
            <p className="mt-1 text-rose-800">
              {this.props.label ? `Section : ${this.props.label}. ` : ''}
              Les mesures déjà enregistrées ne sont pas perdues : elles restent dans l’historique.
            </p>
            <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-white/70 p-3 font-mono text-[11px] leading-5">{error.message}</pre>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={() => this.setState({ error: null })}
                className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-3 py-2 text-xs font-bold text-white hover:bg-slate-800"
              >
                <RefreshCw size={14} />Réessayer d’afficher
              </button>
              <button
                onClick={() => window.location.reload()}
                className="inline-flex items-center gap-2 rounded-lg border border-rose-300 px-3 py-2 text-xs font-bold text-rose-700 hover:bg-white"
              >
                Recharger la page
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }
}
