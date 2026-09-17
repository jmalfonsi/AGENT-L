export interface FactoryFrameworkState {
  frameworkId: string;
  latestVersion: number;
  nextVersion: number;
  latestBuildId: string | null;
  latestPassed: boolean | null;
  validatedVersion: number | null;
  publishedVersion: number | null;
  publishedBuildId: string | null;
  currentVersion: number | null;
  currentBuildId: string | null;
  hasValidatedVersion: boolean;
  upToDate: boolean;
  outdated: boolean;
}

export function projectStatusLabel(
  builds: FactoryFrameworkState[], verdict: 'ready' | 'incomplete',
): string {
  if (builds.some((build) => build.upToDate)) return 'agent à jour';
  if (builds.some((build) => build.outdated)) return 'précisions à intégrer';
  return verdict === 'ready' ? 'prêt à générer' : 'incomplet';
}

export function projectStatusClass(
  builds: FactoryFrameworkState[], verdict: 'ready' | 'incomplete',
): string {
  const base = 'rounded px-1.5 py-0.5 font-bold uppercase ';
  if (builds.some((build) => build.upToDate)) return base + 'bg-emerald-100 text-emerald-700';
  if (builds.some((build) => build.outdated)) return base + 'bg-amber-100 text-amber-800';
  return base + (verdict === 'ready'
    ? 'bg-sky-100 text-sky-700'
    : 'bg-rose-100 text-rose-700');
}

export function buildStatusLabel(build: FactoryFrameworkState): string {
  const version = build.validatedVersion ?? build.latestVersion;
  const status = build.upToDate
    ? 'validé'
    : build.outdated
      ? 'à mettre à jour'
      : 'refusé';
  return `v${version} · ${status}`;
}

export function buildStatusClass(build: FactoryFrameworkState): string {
  const base = 'rounded px-1.5 py-0.5 font-bold ';
  if (build.upToDate) return base + 'bg-emerald-50 text-emerald-700';
  if (build.outdated) return base + 'bg-amber-50 text-amber-800';
  return base + 'bg-rose-50 text-rose-700';
}

export function generationLabel(label: string, state?: FactoryFrameworkState): string {
  if (!state) return `Générer ${label}`;
  if (state.upToDate) {
    return `Créer v${state.nextVersion} — version actuelle v${state.currentVersion}`;
  }
  if (state.hasValidatedVersion) {
    return `Générer v${state.nextVersion} avec les précisions`;
  }
  return `Générer v${state.nextVersion}`;
}
