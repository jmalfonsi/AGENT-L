export interface CodeExample {
  id: string;
  title: string;
  description: string;
  agentCode: string;
  hostCode: string;
  scenarioName?: string;
  simulateOutput: {
    check: string[];
    verify: string[];
    boundary: string[];
    run: string[];
    replay: string[];
  };
}

export interface ComparisonFeature {
  category: string;
  feature: string;
  description: string;
  agentL: {
    supported: boolean | 'partial' | 'full';
    detail: string;
    badge?: string;
  };
  langchain: {
    supported: boolean | 'partial' | 'full' | 'unknown';
    detail: string;
  };
  crewAi: {
    supported: boolean | 'partial' | 'full' | 'unknown';
    detail: string;
  };
  autogen: {
    supported: boolean | 'partial' | 'full' | 'unknown';
    detail: string;
  };
}

export interface DocSection {
  id: string;
  title: string;
  iconName: string;
  summary: string;
  content: string;
  codeSnippet?: string;
}

export interface ErrorCodeInfo {
  code: string;
  type: 'E' | 'W' | 'V' | 'B';
  category: 'Analyse' | 'Sûreté (Théorème)' | 'Frontière' | 'Avertissement';
  summary: string;
  explanation: string;
  remedy: string;
}

export interface BenchmarkResult {
  task: string;
  agentLScore: number;
  geminiFlashScore: number;
  geminiProScore: number;
  description: string;
}
