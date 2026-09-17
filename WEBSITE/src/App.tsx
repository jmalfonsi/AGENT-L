import React, { useState } from 'react';
import { Header } from './components/Header';
import { Hero } from './components/Hero';
import { BoundaryVisualizer } from './components/BoundaryVisualizer';
import { ComparisonMatrix } from './components/ComparisonMatrix';
import { Quickstart } from './components/Quickstart';
import { Playground } from './components/Playground';
import { DocsHub } from './components/DocsHub';
import { BenchmarkSection } from './components/BenchmarkSection';
import { Footer } from './components/Footer';

import { SeparationBoundaryExplainer } from './components/SeparationBoundaryExplainer';
import { WhatsNew } from './components/WhatsNew';

export default function App() {
  const [activeTab, setActiveTab] = useState<string>('overview');

  const navigateTo = (tab: string) => {
    setActiveTab(tab);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <div className="min-h-screen bg-[#050505] text-[#e0e0e0] font-sans selection:bg-cyan-500 selection:text-black antialiased">
      {/* Sticky Header */}
      <Header activeTab={activeTab} setActiveTab={navigateTo} />

      {/* Main Content Area */}
      <main className="mx-auto max-w-7xl px-4 sm:px-6">
        
        {activeTab === 'overview' && (
          <>
            <Hero setActiveTab={navigateTo} />
            <WhatsNew compact onViewAll={() => navigateTo('whatsnew')} />
            <BoundaryVisualizer />
            <ComparisonMatrix />
            <Quickstart />
            <Playground />
            <DocsHub />
            <BenchmarkSection />
          </>
        )}

        {activeTab === 'whatsnew' && (
          <div className="py-8">
            <WhatsNew />
          </div>
        )}

        {activeTab === 'boundary' && (
          <div className="py-8">
            <BoundaryVisualizer />
            <Playground />
          </div>
        )}

        {activeTab === 'separation' && (
          <div className="py-8">
            <SeparationBoundaryExplainer />
          </div>
        )}

        {activeTab === 'comparison' && (
          <div className="py-8">
            <ComparisonMatrix />
            <BenchmarkSection />
          </div>
        )}

        {activeTab === 'playground' && (
          <div className="py-8">
            <Playground />
          </div>
        )}

        {activeTab === 'quickstart' && (
          <div className="py-8">
            <Quickstart />
          </div>
        )}

        {activeTab === 'docs' && (
          <div className="py-8">
            <DocsHub />
          </div>
        )}

        {activeTab === 'benchmarks' && (
          <div className="py-8">
            <BenchmarkSection />
          </div>
        )}

      </main>

      {/* Footer */}
      <Footer />
    </div>
  );
}
