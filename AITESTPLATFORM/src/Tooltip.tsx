import { useId, useState, type ReactNode } from 'react';
import { HelpCircle } from 'lucide-react';

/**
 * Bulle d'aide accessible : survol, focus clavier et Échap pour refermer.
 * Le texte reste dans le DOM (`role="tooltip"` + `aria-describedby`) afin d'être
 * lu par un lecteur d'écran, y compris quand la bulle est masquée visuellement.
 */
export function Tooltip({
  content,
  children,
  side = 'top',
  className = '',
}: {
  content: ReactNode;
  children?: ReactNode;
  side?: 'top' | 'bottom' | 'left' | 'right';
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();

  const position =
    side === 'bottom' ? 'top-full mt-2 left-1/2 -translate-x-1/2'
      : side === 'left' ? 'right-full mr-2 top-1/2 -translate-y-1/2'
        : side === 'right' ? 'left-full ml-2 top-1/2 -translate-y-1/2'
          : 'bottom-full mb-2 left-1/2 -translate-x-1/2';

  return (
    <span className={`relative inline-flex items-center ${className}`}>
      <span
        tabIndex={0}
        role="button"
        aria-describedby={id}
        aria-expanded={open}
        aria-label="Afficher l’explication"
        className="inline-flex cursor-help items-center text-slate-400 outline-none transition hover:text-orange-500 focus-visible:text-orange-500 focus-visible:ring-2 focus-visible:ring-orange-300 focus-visible:ring-offset-1 rounded"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false);
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            setOpen((current) => !current);
          }
        }}
        onClick={(event) => event.stopPropagation()}
      >
        {children ?? <HelpCircle size={13} strokeWidth={2.5} />}
      </span>
      <span
        id={id}
        role="tooltip"
        className={`pointer-events-none absolute z-[70] w-64 rounded-lg bg-slate-950 px-3 py-2 text-left text-[11px] font-normal normal-case leading-5 tracking-normal text-slate-100 shadow-xl ring-1 ring-white/10 transition-opacity duration-100 ${position} ${open ? 'opacity-100' : 'opacity-0'}`}
        hidden={!open}
      >
        {content}
      </span>
    </span>
  );
}

/** Terme accompagné de sa définition, pour le vocabulaire d'initiés. */
export function Term({ label, definition }: { label: ReactNode; definition: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1">
      {label}
      <Tooltip content={definition} />
    </span>
  );
}
