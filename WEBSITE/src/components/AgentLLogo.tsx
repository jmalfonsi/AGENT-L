import React from 'react';

interface AgentLLogoProps {
  size?: 'xl' | 'lg' | 'md' | 'sm' | 'xs' | 'inline';
  className?: string;
  glow?: boolean;
}

export function AgentLLogo({ size = 'md', className = '', glow = true }: AgentLLogoProps) {
  // Size mappings
  const dimensions = {
    xl: 'w-16 h-16 text-lg',
    lg: 'w-12 h-12 text-sm',
    md: 'w-9 h-9 text-xs',
    sm: 'w-6 h-6 text-[10px]',
    xs: 'w-4.5 h-4.5 text-[8px]',
    inline: 'w-4 h-4 text-[7px]',
  };

  const dim = dimensions[size] || dimensions.md;

  if (size === 'inline' || size === 'xs') {
    return (
      <span className={`inline-flex items-center gap-1 font-bold ${className}`}>
        <span
          className={`inline-flex items-center justify-center shrink-0 rounded-sm rotate-45 bg-gradient-to-br from-cyan-400 via-cyan-500 to-blue-600 shadow-sm shadow-cyan-500/30 ${dim}`}
          aria-hidden="true"
        >
          <span className="text-black font-extrabold -rotate-45 font-mono leading-none tracking-tighter">
            A-L
          </span>
        </span>
      </span>
    );
  }

  return (
    <div
      className={`relative inline-flex items-center justify-center shrink-0 rounded-sm rotate-45 bg-gradient-to-br from-cyan-400 via-cyan-500 to-blue-600 shadow-lg shadow-cyan-500/30 transition-transform duration-300 hover:scale-110 ${dim} ${
        glow ? 'after:absolute after:inset-0 after:rounded-sm after:bg-cyan-400/20 after:blur-md' : ''
      } ${className}`}
    >
      <span className="text-black font-black -rotate-45 font-mono leading-none tracking-tighter z-10 select-none">
        A-L
      </span>
    </div>
  );
}

/**
 * Helper component that renders the mini logo badge directly in front of "AGENT-L"
 */
export function AgentLText({ className = '', textClassName = '' }: { className?: string; textClassName?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap ${className}`}>
      <AgentLLogo size="inline" />
      <span className={`font-mono font-bold tracking-tight ${textClassName}`}>AGENT-L</span>
    </span>
  );
}
