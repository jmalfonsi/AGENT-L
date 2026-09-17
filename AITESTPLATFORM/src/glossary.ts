/**
 * Vocabulaire de la plateforme traduit pour un lecteur non spécialiste.
 * Chaque entrée répond à « qu'est-ce que je regarde, et comment je le lis ».
 * Source unique : l'interface ne doit pas réécrire ces définitions localement.
 */
export interface GlossaryEntry {
  /** Nom court affiché dans l'interface. */
  term: string;
  /** Une phrase, sans jargon, qui suffit à comprendre le chiffre. */
  short: string;
  /** Comment interpréter : ce qui est bon, ce qui est mauvais, les pièges. */
  reading?: string;
}

export const GLOSSARY = {
  partialCredit: {
    term: 'Crédit officiel',
    short: "Part des vérifications de la tâche que l'agent a réellement satisfaites, calculée par le barème officiel d'AutomationBench.",
    reading: '100 % = tout ce qui était attendu a été fait. 60 % = le travail est à moitié fait, pas « presque bon ».',
  },
  taskCompleted: {
    term: 'Tâche complète',
    short: "Oui seulement si l'intégralité de la tâche est réussie : une seule vérification manquée suffit à donner Non.",
    reading: "C'est le critère strict. Un agent peut avoir 90 % de crédit et rester à « Non ».",
  },
  successRate: {
    term: 'Taux de succès',
    short: 'Part des exécutions valides où la tâche a été entièrement réussie.',
    reading: 'Les exécutions techniquement invalides sont retirées du calcul, elles ne pénalisent ni ne favorisent personne.',
  },
  assertion: {
    term: 'Assertion',
    short: "Une vérification automatique de l'énoncé : « ce message a-t-il été envoyé », « ce champ vaut-il bien X ».",
    reading: "Elles sont écrites par AutomationBench, pas par la plateforme, et ne sont jamais montrées à l'agent pendant l'exécution.",
  },
  excludedAssertion: {
    term: 'Assertion non applicable',
    short: "Vérification que le barème officiel écarte pour ce cas précis : elle ne compte ni comme réussie ni comme ratée.",
    reading: 'Le score porte uniquement sur les assertions évaluées.',
  },
  rubric: {
    term: 'Barème officiel',
    short: "Le programme de notation fourni par AutomationBench. La plateforme l'appelle tel quel et n'ajoute aucune note maison.",
  },
  invalidRun: {
    term: 'Exécution invalide',
    short: "L'agent n'a rien produit d'exploitable : aucune action, aucune réponse, ou coupure technique. Il n'y a rien à noter.",
    reading: "Ces exécutions sont exclues des moyennes pour ne pas transformer une panne en mauvaise note. Le compteur reste affiché : beaucoup d'exécutions invalides est en soi un mauvais signe.",
  },
  toolCall: {
    term: 'Appel d’outil',
    short: "Une action concrète de l'agent sur le système simulé : lire une fiche, envoyer un message, modifier un enregistrement.",
    reading: "Moins d'appels pour un même résultat = agent plus direct. Beaucoup d'appels sans résultat = agent qui tâtonne.",
  },
  llmCall: {
    term: 'Appel LLM',
    short: 'Nombre de fois où le modèle de langage a été sollicité pendant une exécution.',
    reading: "C'est le principal facteur de coût et de durée.",
  },
  tokens: {
    term: 'Tokens',
    short: 'Unité de facturation du modèle : le texte lu et écrit, découpé en fragments.',
    reading: 'Deux agents au même score mais du simple au double en tokens ne coûtent pas la même chose en production.',
  },
  worldState: {
    term: 'État du système',
    short: "Les données simulées sur lesquelles l'agent travaille : employés, messages, tickets, selon la tâche.",
    reading: "Chaque exécution repart d'une copie neuve et identique, pour que la comparaison soit équitable.",
  },
  facade: {
    term: 'Outils communs',
    short: "Le même jeu d'outils est exposé aux quatre frameworks, avec les mêmes noms et les mêmes contrats.",
    reading: "Sans cela, un framework pourrait gagner grâce à de meilleurs outils plutôt qu'à un meilleur raisonnement.",
  },
  regime: {
    term: 'Régime de comparaison',
    short: "Ce que la campagne met à l'épreuve : « énoncé seul » demande aux frameworks de trouver ET d'exécuter la marche à suivre ; « parité de plan » leur donne le plan et n'évalue plus que l'exécution.",
    reading: "Deux régimes ne se moyennent jamais entre eux. Un score de 40 % dans l'un et de 40 % dans l'autre ne disent pas la même chose.",
  },
  protocolVersion: {
    term: 'Version de protocole',
    short: "Règles du jeu utilisées pour l'exécution. Deux versions différentes ne se comparent pas entre elles.",
    reading: '« legacy-v1 » désigne d’anciennes mesures conservées pour mémoire, tenues à l’écart des campagnes actuelles.',
  },
  nd: {
    term: 'N/D',
    short: "Non disponible : le runtime n'a pas fourni cette mesure.",
    reading: "La plateforme préfère l'afficher vide plutôt que de l'estimer. N/D ne veut pas dire zéro.",
  },
  agentFile: {
    term: 'Programme .agent',
    short: "Programme écrit spécifiquement pour AGENT-L, compilé et vérifié avant d'être exécuté.",
    reading: "Les trois autres frameworks ne le reçoivent pas : ils travaillent depuis l'énoncé seul. L'asymétrie est voulue et assumée.",
  },
  thinkingLevel: {
    term: 'Niveau de raisonnement',
    short: "Effort de réflexion demandé au modèle avant qu'il réponde. Identique pour tous les frameworks.",
  },
  maxOutputTokens: {
    term: 'Plafond de sortie',
    short: "Longueur maximale que le modèle peut produire en une fois. Atteindre ce plafond sans avoir agi rend l'exécution invalide.",
  },
  serialized: {
    term: 'Exécutions sérialisées',
    short: "Les exécutions se suivent une par une, jamais en parallèle, pour rester sous le quota de l'API et rendre les durées comparables.",
  },
} satisfies Record<string, GlossaryEntry>;

export type GlossaryKey = keyof typeof GLOSSARY;

/** Texte prêt à poser dans une bulle d'aide. */
export function help(key: GlossaryKey): string {
  const entry: GlossaryEntry = GLOSSARY[key];
  return entry.reading ? `${entry.short} ${entry.reading}` : entry.short;
}
