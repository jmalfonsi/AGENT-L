import React, { useState } from 'react';
import { DOC_SECTIONS, ERROR_CODES } from '../data/docs';
import { BookOpen, Search, Code, ShieldCheck, AlertTriangle, FileText, ChevronRight, Copy, Check, Filter } from 'lucide-react';

/** Rend le sous-ensemble Markdown employé par DOC_SECTIONS : titres `###`,
 *  listes, citations, tableaux, `**gras**` et `` `code` ``. Volontairement
 *  minimal — ces contenus sont écrits ici, pas importés d'ailleurs. */
function inline(text: string, keyPrefix: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith('**')) {
      parts.push(<strong key={`${keyPrefix}-b${i}`} className="text-white font-semibold">{tok.slice(2, -2)}</strong>);
    } else {
      parts.push(
        <code key={`${keyPrefix}-c${i}`} className="font-mono text-[11px] text-cyan-300 bg-cyan-950/40 px-1 py-0.5 rounded">
          {tok.slice(1, -1)}
        </code>
      );
    }
    last = m.index + tok.length;
    i++;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function renderContent(content: string): React.ReactNode[] {
  const lines = content.split('\n');
  const out: React.ReactNode[] = [];
  let table: string[][] = [];

  const flushTable = (key: string) => {
    if (!table.length) return;
    const [head, ...body] = table;
    out.push(
      <div key={key} className="overflow-x-auto my-3">
        <table className="w-full text-left text-[11px] border border-slate-800">
          <thead className="bg-slate-900/80 text-slate-300">
            <tr>{head.map((c, i) => <th key={i} className="px-2.5 py-1.5 font-semibold border-b border-slate-800">{inline(c, `${key}-h${i}`)}</th>)}</tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 text-slate-400">
            {body.map((row, r) => (
              <tr key={r}>{row.map((c, i) => <td key={i} className="px-2.5 py-1.5 align-top">{inline(c, `${key}-${r}-${i}`)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    );
    table = [];
  };

  lines.forEach((raw, idx) => {
    const line = raw.trim();
    const key = `l${idx}`;

    if (line.startsWith('|')) {
      const cells = line.slice(1, line.endsWith('|') ? -1 : undefined).split('|').map(c => c.trim());
      if (!cells.every(c => /^:?-{2,}:?$/.test(c))) table.push(cells);
      return;
    }
    flushTable(`tbl${idx}`);

    if (!line) return;

    if (line.startsWith('### ')) {
      out.push(<h4 key={key} className="text-cyan-300 font-bold text-[13px] font-sans mt-5 mb-1.5">{inline(line.slice(4), key)}</h4>);
    } else if (line.startsWith('> ')) {
      out.push(
        <blockquote key={key} className="border-l-2 border-cyan-500/50 pl-3 my-2 text-slate-200 italic">
          {inline(line.slice(2), key)}
        </blockquote>
      );
    } else if (/^[-*] /.test(line)) {
      out.push(
        <div key={key} className="flex gap-2 pl-1">
          <span className="text-cyan-500 shrink-0">·</span>
          <span>{inline(line.slice(2), key)}</span>
        </div>
      );
    } else if (/^\d+\.\s/.test(line)) {
      const n = line.slice(0, line.indexOf('.'));
      out.push(
        <div key={key} className="flex gap-2 pl-1">
          <span className="text-cyan-500 font-mono shrink-0">{n}.</span>
          <span>{inline(line.replace(/^\d+\.\s/, ''), key)}</span>
        </div>
      );
    } else {
      out.push(<p key={key}>{inline(line, key)}</p>);
    }
  });

  flushTable('tbl-end');
  return out;
}

export function DocsHub() {
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [activeTab, setActiveTab] = useState<'guides' | 'errors'>('guides');
  const [selectedSectionId, setSelectedSectionId] = useState<string>('ebnf-grammar');
  const [copiedCode, setCopiedCode] = useState(false);

  const filteredSections = DOC_SECTIONS.filter(s => 
    s.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
    s.summary.toLowerCase().includes(searchQuery.toLowerCase()) ||
    s.content.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const filteredErrors = ERROR_CODES.filter(e =>
    e.code.toLowerCase().includes(searchQuery.toLowerCase()) ||
    e.summary.toLowerCase().includes(searchQuery.toLowerCase()) ||
    e.explanation.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const currentSection = DOC_SECTIONS.find(s => s.id === selectedSectionId) || DOC_SECTIONS[0];

  const handleCopyCode = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopiedCode(true);
    setTimeout(() => setCopiedCode(false), 2000);
  };

  return (
    <section className="my-16">
      <div className="mb-10 text-center max-w-3xl mx-auto">
        <div className="inline-flex items-center gap-2 rounded-full border border-cyan-500/30 bg-cyan-950/40 px-3 py-1 text-xs font-mono font-semibold text-cyan-400 mb-3">
          <BookOpen className="h-3.5 w-3.5" />
          Documentation et diagnostics
        </div>
        <h2 className="text-3xl sm:text-4xl font-black text-white tracking-tight">
          Référence technique et grammaire EBNF
        </h2>
        <p className="mt-3 text-slate-400 text-sm sm:text-base leading-relaxed">
          Retrouvez la sémantique du langage, ses blocs déclaratifs et les corrections associées à chaque diagnostic.
        </p>
      </div>

      {/* Search & Main Tab Bar */}
      <div className="mb-8 flex flex-col md:flex-row items-center justify-between gap-4">
        {/* Tab Buttons */}
        <div className="flex items-center gap-2 p-1 bg-slate-900 border border-slate-800 rounded-xl w-full md:w-auto">
          <button
            onClick={() => setActiveTab('guides')}
            className={`flex-1 md:flex-none flex items-center justify-center gap-2 rounded-lg px-4 py-2 font-mono text-xs font-semibold transition-all ${
              activeTab === 'guides'
                ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <FileText className="h-4 w-4" />
            Guides et sémantique
          </button>

          <button
            onClick={() => setActiveTab('errors')}
            className={`flex-1 md:flex-none flex items-center justify-center gap-2 rounded-lg px-4 py-2 font-mono text-xs font-semibold transition-all ${
              activeTab === 'errors'
                ? 'bg-cyan-500 text-slate-950 font-bold shadow'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <AlertTriangle className="h-4 w-4" />
            Codes de diagnostic (E / W / V / B)
          </button>
        </div>

        {/* Search Bar */}
        <div className="relative w-full md:w-80">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Rechercher : NEVER, E009, FOREACH…"
            className="w-full rounded-xl border border-slate-800 bg-slate-950 pl-9 pr-4 py-2 text-xs text-slate-200 placeholder-slate-500 focus:border-cyan-500/50 focus:outline-none focus:ring-1 focus:ring-cyan-500/50 font-mono"
          />
        </div>
      </div>

      {/* TAB CONTENT: GUIDES */}
      {activeTab === 'guides' && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
          
          {/* Left Column: Topic List */}
          <div className="lg:col-span-4 space-y-2">
            {filteredSections.map((sec) => (
              <button
                key={sec.id}
                onClick={() => setSelectedSectionId(sec.id)}
                className={`w-full text-left p-4 rounded-xl border transition-all flex items-start justify-between ${
                  selectedSectionId === sec.id
                    ? 'border-cyan-500/50 bg-cyan-950/30 text-white shadow-lg'
                    : 'border-slate-800 bg-slate-950/60 text-slate-400 hover:border-slate-700 hover:text-slate-200'
                }`}
              >
                <div>
                  <div className="font-bold text-xs font-mono text-cyan-300 mb-1">{sec.title}</div>
                  <div className="text-[11px] text-slate-400 line-clamp-2 leading-relaxed">{sec.summary}</div>
                </div>
                <ChevronRight className="h-4 w-4 text-slate-500 shrink-0 mt-1" />
              </button>
            ))}
          </div>

          {/* Right Column: Article Details */}
          <div className="lg:col-span-8 rounded-2xl border border-slate-800 bg-slate-950 p-6 flex flex-col justify-between">
            <div>
              <h3 className="text-xl font-bold text-white mb-2 font-mono flex items-center gap-2">
                <BookOpen className="h-5 w-5 text-cyan-400" />
                {currentSection.title}
              </h3>
              <p className="text-xs text-cyan-300 font-mono mb-4 bg-cyan-950/40 p-2.5 rounded border border-cyan-500/20">
                {currentSection.summary}
              </p>

              <div className="text-xs text-slate-300 space-y-2 leading-relaxed font-sans border-t border-slate-800/80 pt-4">
                {renderContent(currentSection.content)}
              </div>

              {currentSection.codeSnippet && (
                <div className="mt-6 rounded-xl border border-slate-800 bg-slate-900/90 overflow-hidden">
                  <div className="flex items-center justify-between border-b border-slate-800/80 px-4 py-2 bg-slate-950">
                    <span className="font-mono text-[11px] text-slate-400">Exemple AGENT-L</span>
                    <button
                      onClick={() => handleCopyCode(currentSection.codeSnippet!)}
                      className="text-xs font-mono text-slate-400 hover:text-white flex items-center gap-1"
                    >
                      {copiedCode ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
                      <span>Copier</span>
                    </button>
                  </div>
                  <pre className="p-4 font-mono text-xs text-slate-200 overflow-x-auto leading-relaxed">
<code>{currentSection.codeSnippet}</code>
                  </pre>
                </div>
              )}
            </div>
          </div>

        </div>
      )}

      {/* TAB CONTENT: ERROR CODES */}
      {activeTab === 'errors' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filteredErrors.map((err) => (
            <div 
              key={err.code}
              className={`rounded-2xl border p-5 bg-slate-950 transition-all ${
                err.type === 'E'
                  ? 'border-rose-500/40 shadow-rose-950/20'
                  : err.type === 'V'
                  ? 'border-cyan-500/40 shadow-cyan-950/20'
                  : 'border-amber-500/40 shadow-amber-950/20'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <span className={`font-mono text-xs font-bold px-2.5 py-1 rounded border ${
                  err.type === 'E'
                    ? 'bg-rose-950 text-rose-300 border-rose-500/30'
                    : err.type === 'V'
                    ? 'bg-cyan-950 text-cyan-300 border-cyan-500/30'
                    : 'bg-amber-950 text-amber-300 border-amber-500/30'
                }`}>
                  Code {err.code}
                </span>
                <span className="text-[11px] font-mono text-slate-400">{err.category}</span>
              </div>

              <h4 className="font-bold text-white text-sm mb-1">{err.summary}</h4>
              <p className="text-xs text-slate-300 mb-3 leading-relaxed">{err.explanation}</p>

              <div className="rounded-lg bg-slate-900/90 p-2.5 border border-slate-800 text-xs font-mono">
                <span className="text-emerald-400 font-bold block text-[10px] uppercase">Correction conseillée</span>
                <span className="text-slate-300 text-[11px]">{err.remedy}</span>
              </div>
            </div>
          ))}
        </div>
      )}

    </section>
  );
}
