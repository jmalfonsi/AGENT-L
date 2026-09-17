/**
 * Cache mémoire à TTL pour les données statiques du banc.
 *
 * Pourquoi : chaque appel d'API démarre un processus Python qui importe crewai,
 * langgraph et litellm, soit ~3,8 s incompressibles. Le catalogue de tâches, la
 * liste des frameworks et leur code source ne changent pas entre deux
 * redémarrages ; les relire à chaque affichage de page est du temps perdu.
 */

interface CacheEntry {
  value: unknown;
  expiresAt: number;
}

const entries = new Map<string, CacheEntry>();
/** Requêtes en vol : dix onglets simultanés ne doivent lancer qu'un processus. */
const inflight = new Map<string, Promise<unknown>>();

export const CACHE_TTL = {
  tasks: 10 * 60_000,
  frameworks: 10 * 60_000,
  frameworkCode: 10 * 60_000,
  health: 30_000,
} as const;

/**
 * Renvoie la valeur en cache si elle est fraîche, sinon la produit.
 * Une erreur n'est JAMAIS mise en cache : un runner momentanément en panne ne
 * doit pas condamner l'endpoint pour dix minutes.
 */
export function cached<T>(key: string, ttlMs: number, producer: () => Promise<T>, refresh = false): Promise<T> {
  if (!refresh) {
    const entry = entries.get(key);
    if (entry && entry.expiresAt > Date.now()) return Promise.resolve(entry.value as T);
    const pending = inflight.get(key);
    if (pending) return pending as Promise<T>;
  }

  const promise = (async () => {
    const value = await producer();
    entries.set(key, { value, expiresAt: Date.now() + ttlMs });
    return value;
  })();

  // Le rafraîchissement forcé partage lui aussi son vol : deux `?refresh=1`
  // concomitants ne justifient pas deux processus Python.
  inflight.set(key, promise);
  promise
    .catch(() => undefined)
    .finally(() => {
      if (inflight.get(key) === promise) inflight.delete(key);
    });
  return promise;
}

export function invalidateCache(key?: string): void {
  if (key === undefined) entries.clear();
  else entries.delete(key);
}

/** Vrai si `?refresh=1` (ou `true`) est présent dans la requête. */
export function wantsRefresh(value: unknown): boolean {
  return value === '1' || value === 'true' || value === 1 || value === true;
}

export function cacheStats(): { keys: string[]; inflight: string[] } {
  return { keys: [...entries.keys()], inflight: [...inflight.keys()] };
}
