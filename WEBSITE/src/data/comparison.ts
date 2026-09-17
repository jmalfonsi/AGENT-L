import { ComparisonFeature } from '../types';

export const COMPARISON_FEATURES: ComparisonFeature[] = [
  {
    category: 'Architecture & Sécurité',
    feature: 'Séparation Strict Canaux (LLM vs Runtime)',
    description: 'Isolation physique entre la donnée analysée par le LLM et la décision d’exécution. Le LLM propose, le runtime décide.',
    agentL: {
      supported: true,
      detail: 'Strictement étanche : Le LLM ne lit jamais les politiques et n’accède jamais directement aux outils.',
      badge: 'Garantie Formelle'
    },
    langchain: {
      supported: false,
      detail: 'Canal unique : Prompt système, outils et entrées utilisateur cohabitent dans le même contexte (vulnérable aux injections).'
    },
    crewAi: {
      supported: false,
      detail: 'Canal unique : Les rôles et instructions sont transmis dans le prompt LLM.'
    },
    autogen: {
      supported: false,
      detail: 'Boucle de conversation LLM à LLM sans cloisonnement formel.'
    }
  },
  {
    category: 'Architecture & Sécurité',
    feature: 'Moteur de Politiques Hors-LLM (Policy Engine)',
    description: 'Évaluation déterministe des règles (NEVER, ALLOW, REQUIRE APPROVAL) exécutée par le runtime Python sans passer par un LLM.',
    agentL: {
      supported: true,
      detail: 'Algorithme P(s) déterministe avec règles irrévocables `NEVER`.',
      badge: 'Natif & Déterministe'
    },
    langchain: {
      supported: 'partial',
      detail: 'Nécessite des wrappers de guardrails a posteriori (NeMo, Llama Guard) qui ajoutent de la latence et d’autres LLM.'
    },
    crewAi: {
      supported: false,
      detail: 'Délégué aux consignes du prompt ou à du code Python personnalisé non vérifié.'
    },
    autogen: {
      supported: 'partial',
      detail: 'Fonctions de validation Python personnalisées à coder manuellement.'
    }
  },
  {
    category: 'Vérification Statique & Théorèmes',
    feature: 'Vérification de Sûreté Hors Ligne (agentl verify)',
    description: 'Théorèmes formels prouvés sur l’AST avant tout appel de modèle ou exécution réseau.',
    agentL: {
      supported: true,
      detail: 'Huit théorèmes par agent (T1 appel interdit, T2 impasse & escalade, T3 capacité morte, T4 surface LLM, T5 scénarios, T6/T7 provenance & terminaison, T9 effets réfutables) plus T8 sur le programme entier dès qu\'il y a plusieurs agents.',
      badge: '9 Théorèmes formels'
    },
    langchain: {
      supported: false,
      detail: 'Aucune vérification statique. Détection des failles uniquement en production ou via des bancs de tests.'
    },
    crewAi: {
      supported: false,
      detail: 'Aucune analyse statique de sûreté.'
    },
    autogen: {
      supported: false,
      detail: 'Aucune analyse statique.'
    }
  },
  {
    category: 'Vérification Statique & Théorèmes',
    feature: 'Contrôle d’Honnêteté de l’Hôte (agentl boundary)',
    description: 'Analyse statique de l’AST Python de l’hôte pour empêcher les fuites de logique métier dans le code hôte (B000-B015).',
    agentL: {
      supported: true,
      detail: 'Détecte les comparaisons métier, filtrages et tris dans Python pour garantir que le .agent prend les décisions.',
      badge: 'Analyse AST Hôte'
    },
    langchain: {
      supported: false,
      detail: 'Toute la logique de contrôle est dispersée sans distinction dans le code Python.'
    },
    crewAi: {
      supported: false,
      detail: 'Pas de séparation formelle hôte/agent.'
    },
    autogen: {
      supported: false,
      detail: 'Aucune distinction.'
    }
  },
  {
    category: 'Incertitude & Décision',
    feature: 'Inférence Bayésienne Calibrée en Bits',
    description: 'Calcul exact des log-cotes et des postérieurs P(h) à partir des a priori et vraisemblances déclarés.',
    agentL: {
      supported: true,
      detail: 'Postérieurs calculés mathématiquement, gestion des corrélations (GROUP) et plafonnement MAX_EVIDENCE.',
      badge: 'Bayes Calibré'
    },
    langchain: {
      supported: false,
      detail: 'Confiance basée uniquement sur l’auto-évaluation textuelle du LLM (hallucination fréquente des probabilités).'
    },
    crewAi: {
      supported: false,
      detail: 'Confiance non calculée.'
    },
    autogen: {
      supported: false,
      detail: 'Confiance non calculée.'
    }
  },
  {
    category: 'Incertitude & Décision',
    feature: 'Planificateur STRIPS & Espaces de Croyance',
    description: 'Synthèse de séquences d’actions optimisées par le coût et filtrées par la politique AVANT execution.',
    agentL: {
      supported: true,
      detail: 'Le planificateur ne conçoit pas d’actions interdites. Il préfère les routes autonomes moins chères.',
      badge: 'STRIPS + Policy'
    },
    langchain: {
      supported: 'partial',
      detail: 'Tool-calling réactif coup par coup (ReAct) ou graphes figés manuellement (LangGraph).'
    },
    crewAi: {
      supported: 'partial',
      detail: 'Séquentiel ou hiérarchique basé sur le Tool-Calling du LLM.'
    },
    autogen: {
      supported: false,
      detail: 'Discussion entre agents sans planificateur formel.'
    }
  },
  {
    category: 'Auditabilité & Testabilité',
    feature: 'Rejeu Déterministe Certifié (SHA-256)',
    description: 'Enregistrement des points de franchissement de frontière pour re-dériver la décision hors ligne à l’identique.',
    agentL: {
      supported: true,
      detail: 'Rejeu hors ligne sans capteur, sans outil et sans réseau avec validation d’empreinte cryptographique.',
      badge: 'Rejeu Scellé'
    },
    langchain: {
      supported: 'partial',
      detail: 'Traces d’exécution enregistrables (LangSmith), mais non rejouables de manière déterministe hors ligne.'
    },
    crewAi: {
      supported: false,
      detail: 'Traces verbeuses textuelles uniquement.'
    },
    autogen: {
      supported: false,
      detail: 'Historique de chat brut.'
    }
  },
  {
    category: 'Auditabilité & Testabilité',
    feature: 'Critères d’Acceptation Intégrés (SCENARIO)',
    description: 'Déclaration des attentes et invariants directement dans le fichier .agent avec simulation hermétique (`agentl test`).',
    agentL: {
      supported: true,
      detail: 'GIVEN / EXPECT / WITHIN exécutés contre le monde déclaré, avec support des invariants et tests de mutation.',
      badge: 'Natif dans la Grammaire'
    },
    langchain: {
      supported: false,
      detail: 'Nécessite de développer des suites de tests PyTest externes avec mocks d’API.'
    },
    crewAi: {
      supported: false,
      detail: 'Tests externes uniquement.'
    },
    autogen: {
      supported: false,
      detail: 'Tests externes uniquement.'
    }
  },
  {
    category: 'Empreinte Technique',
    feature: 'Dépendances Logicielles',
    description: 'Taille et complexité du projet et du moteur d’exécution.',
    agentL: {
      supported: true,
      detail: '0 dépendance (Python 3.10+ stdlib uniquement). Fonctionne en environnement isolé/air-gapped.',
      badge: '0 Dépendance'
    },
    langchain: {
      supported: false,
      detail: 'Dizaines de dépendances lourdes, mises à jour fréquentes entraînant des ruptures de compatibilité.'
    },
    crewAi: {
      supported: false,
      detail: 'Dépend de LangChain et d’autres bibliothèques tierces.'
    },
    autogen: {
      supported: false,
      detail: 'Dépendances multiples (OpenAI, Docker, etc.).'
    }
  },
  {
    category: 'Auditabilité & Testabilité',
    feature: 'Généralisation Mesurée (agentl autoloop)',
    description: 'Rejeu de chaque SCENARIO sur des variations de données avec un lot de contrôle préservé.',
    agentL: {
      supported: true,
      detail: 'L’oracle est l’invariant, jamais le modèle. Un lot de contrôle (30 % par défaut) garantit une évaluation impartiale sans surapprentissage.',
      badge: 'Lot de contrôle'
    },
    langchain: {
      supported: false,
      detail: 'Aucune notion de généralisation : les tests couvrent les cas écrits à la main.'
    },
    crewAi: {
      supported: false,
      detail: 'Aucune mesure du surapprentissage.'
    },
    autogen: {
      supported: false,
      detail: 'Aucune mesure du surapprentissage.'
    }
  },
  {
    category: 'Architecture & Sécurité',
    feature: 'Confidentialité Déclarée (NEVER SEND)',
    description: 'Interdiction de sortie vers le fournisseur de modèle, s\'appliquant à toutes les requêtes (y compris le contexte de select_plan).',
    agentL: {
      supported: true,
      detail: 'Masquage structurel par préfixe de chemin : la donnée interdite est remplacée par ⟦retenu⟧, la clé reste visible et la restriction est consignée.',
      badge: 'Données protégées'
    },
    langchain: {
      supported: false,
      detail: 'Tout l’état transmis au modèle est envoyé dans le prompt ; le masquage doit être implémenté manuellement dans le code applicatif.'
    },
    crewAi: {
      supported: false,
      detail: 'Aucune barrière de sortie déclarative.'
    },
    autogen: {
      supported: false,
      detail: 'L’historique de conversation entier est transmis.'
    }
  },
  {
    category: 'Résilience aux Attaques',
    feature: 'Gestion Déclarative des Pannes',
    description: 'Conduite explicite face à l’indisponibilité : ON UNKNOWN ESCALATE / DEGRADE, disjoncteur d’outil et détection de mode dégradé.',
    agentL: {
      supported: true,
      detail: 'La panne est un fait explicite (sensors.*.available, tools.*.available, reason.degraded) : une garde peut l\'évaluer. Disjoncteur à réouverture progressive, reprise LLM bornée.',
      badge: 'Sécurité par défaut'
    },
    langchain: {
      supported: 'partial',
      detail: 'Retry et timeouts configurables, mais l’indisponibilité n’est pas un fait directement évaluable par la logique de décision.'
    },
    crewAi: {
      supported: 'partial',
      detail: 'max_rpm et max_execution_time ; l’échec d’outil retourne au modèle sous forme de texte.'
    },
    autogen: {
      supported: false,
      detail: 'L’erreur d’outil est renvoyée au modèle dans la conversation brute.'
    }
  },
  {
    category: 'Résilience aux Attaques',
    feature: 'Résistance aux Injections Prompt Injection (AutomationBench)',
    description: 'Taux de succès et respect des politiques sous charge d’attaque indirecte (ex: consignes malveillantes dans un e-mail/log).',
    agentL: {
      supported: true,
      detail: '0/10 violations sous charge d’attaque renforcée. L’injection reste confinée dans le canal REASON.',
      badge: '0/10 Violation'
    },
    langchain: {
      supported: false,
      detail: '10/10 violations sous charge d’attaque renforcée (ResilienceBench) quand l’explication piège le LLM.'
    },
    crewAi: {
      supported: false,
      detail: 'Vulnérable dès que l’attaque modifie le contexte du prompt.'
    },
    autogen: {
      supported: false,
      detail: 'Vulnérable aux faux ordres transmis dans la conversation.'
    }
  }
];
