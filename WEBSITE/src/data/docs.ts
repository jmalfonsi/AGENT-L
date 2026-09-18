import { DocSection, ErrorCodeInfo } from '../types';

export const DOC_SECTIONS: DocSection[] = [
  {
    id: 'v19-kernel-durable-provenance',
    title: 'v1.9 — Noyau, reprise durable, provenance, asynchrone',
    iconName: 'ShieldCheck',
    summary: 'Ce que la v1.9 ajoute : un noyau à permis, l’exécution durable sans doublon, la provenance portée par les valeurs et l’exécution asynchrone. Documentation complète sur doc.agent-l.integria.app.',
    content: `
### Noyau à permis (SPEC §34)
L'autorisation et l'appel à l'hôte vivent dans un **noyau** (\`agentl/kernel/\`). Il fige la proposition, évalue la politique, montre une copie à l'approbateur puis émet un **permis** à usage unique lié au condensat de l'action. \`Host.invoke\` sans permis lève \`PermitError\`.

### Exécution durable (SPEC §36)
\`agentl run X.agent --durable DIR\` écrit l'intention de chaque action avant l'appel. Après un crash, la même commande reprend : l'action interrompue est relancée avec la même clé d'idempotence (\`idempotent=True\`), réconciliée, ou déclarée **indéterminée** — \`tools.<outil>.in_doubt\` le dit à la politique.

### Provenance des valeurs (SPEC §35)
Chaque valeur porte ses sources : \`OBSERVED\`, \`LLM\`, \`TOOL\`, \`MESSAGE\`… Les gardes \`UNTRUSTED(x)\`, \`LLM_DERIVED(x)\`, \`ATTESTED(x, outil)\` et \`ORIGIN(x)\` la lisent. Une valeur sans étiquette connue est non fiable. \`E015\` et \`E016\` refusent les fonctions inconnues ou mal employées.

### Exécution asynchrone (SPEC §37)
\`AsyncHost\`, \`AsyncRuntime\`, \`AsyncSociety\` et \`Limits\` : concurrence bornée, délais, annulation. Un outil qui dépasse son délai est indéterminé, jamais réussi.

### Documentation
https://doc.agent-l.integria.app/ — pages « Noyau et permis », « Provenance des valeurs », « Exécution durable », « Exécution asynchrone » et « Banc comparatif ».
    `,
    codeSnippet: `POLICY {
    DEFAULT ALLOW
    REQUIRE APPROVAL FOR wipe_host
    NEVER wipe_host WHEN UNTRUSTED(host) AND NOT ATTESTED(host, check_wipeable)
    NEVER transfer  WHEN tools.transfer.in_doubt == true
}`
  },
  {
    id: 'ebnf-grammar',
    title: 'Grammaire EBNF & Modèle Formel (v1.8)',
    iconName: 'FileText',
    summary: 'Spécification formelle de la syntaxe AGENT-L v1.8 : structure déclarative, gestion explicite des pannes (ON UNKNOWN) et protection des données sensibles (NEVER SEND).',
    content: `
Un programme AGENT-L définit un agent autonome sous la forme d'un tuple formel à 9 composantes :
A = (S, G, B, O, T, P, M, V, π)

### Règles clés de la grammaire :
1. **Mots-clés réservés en majuscules** : \`AGENT\`, \`GOAL\`, \`OBSERVE\`, \`TOOL\`, \`POLICY\`, \`HYPOTHESIS\`, \`PLANNER\`, \`SCENARIO\`, \`FOREACH\`.
2. **Vocabulaire métier libre** : Les identifiants comme \`healthy\`, \`degraded\`, \`unknown\`, \`logs\` constituent le vocabulaire du domaine de l'agent.
3. **Paire de fichiers indissociable** : \`X.agent\` (contrat déclaratif) et \`X.py\` (hôte Python fournissant les capteurs et l'exécution matérielle des outils).
4. **Stabilité d'API** : La grammaire et les codes de diagnostic formels (\`E\`, \`W\`, \`V\`, \`B\`) suivent un versionnage sémantique strict.

### Évolutions majeures (v1.6 → v1.8) :
- \`OBSERVE … ON UNKNOWN ESCALATE | DEGRADE <valeur>\` — déclaration explicite de la conduite à tenir face à un capteur muet (§31).
- \`POLICY { NEVER SEND <chemin> }\` — interdiction formelle de transmission vers l'API du modèle de langage sur toutes les requêtes (§32).
- \`TOOL … DURATION 45s\`, \`PLANNER { DEADLINE … TIME_WEIGHT … }\` — prise en compte de la dimension temporelle dans la synthèse de plans A* (§17.1).
- \`EFFECT … INTERNAL\` — distinction des effets internes ou comptables pour la réfutabilité des postconditions (§30).
- **Simplification de syntaxe** : remplacement de l'ancienne clause \`EVERY\` par des gardes conditionnelles explicites \`WHEN\`.
    `,
    codeSnippet: `AGENT NomAgent {
    VERSION "1.8"
    DESCRIPTION "Description de l'agent"

    GOAL nom { MAINTAIN <condition> WEIGHT 1.0 }

    OBSERVE {
        chemin.capteur
        asset.criticality  ON UNKNOWN ESCALATE      // alerte explicite en cas de panne
        disk.usage_percent ON UNKNOWN DEGRADE 100   // repli déclaré, confiance c=0.3
    }

    BELIEF { chemin = valeur CONFIDENCE 0.80 SOURCE source }

    TOOL nom_outil {
        INPUT       { arg: String BIND chemin }
        SIDE_EFFECT { chemin.effet }
        RISK        HIGH
        REQUIRES    { condition_prealable }
        EFFECT      { chemin = valeur }
        DURATION    45s
        COST        5
    }

    POLICY {
        DEFAULT DENY
        NEVER SEND credentials              // masquage absolu vers le LLM
        ALLOW  nom_outil IF condition
        NEVER  nom_outil WHEN interdit_absolu
        REQUIRE APPROVAL FOR nom_outil WHEN P(hypothese) < 0.95
    }

    HYPOTHESIS nom_hypothese {
        PRIOR 0.05
        EVIDENCE {
            GROUP nom_groupe {
                chemin > seuil LIKELIHOOD 0.90 GIVEN_NOT 0.05
            }
        }
        MAX_EVIDENCE 8
        THRESHOLD 0.90
        EXPLAINS chemin.derive
    }

    PLANNER { ENABLE ACHIEVE condition DEADLINE 5m TIME_WEIGHT 0.1 APPROVAL_COST 10 }

    SCENARIO nom_test {
        GIVEN { chemin = valeur operator.approval = yes }
        EXPECT { chemin.but == atteint } WITHIN 6
    }

    LOOP UNTIL goal.satisfied MAX 8 {
        OBSERVE UPDATE_BELIEFS UPDATE_HYPOTHESES EVALUATE_GOALS
        SELECT_PLAN EXECUTE VERIFY UPDATE_MEMORY
    }
}`
  },
  {
    id: 'agentl-author-skill',
    title: 'Génération via Assistants IA (Claude, ChatGPT, Cursor) & Skill agentl-author',
    iconName: 'Bot',
    summary: 'Skill officiel agentl-author (contrat d\'écriture 2.4.0 scellé) pour concevoir, vérifier et exécuter des agents AGENT-L avec un assistant IA.',
    content: `
Pour concevoir, mettre à jour ou adapter un agent AGENT-L avec un assistant IA, le **Skill officiel \`agentl-author\`** (\`SKILL.md\`) est disponible dans le dépôt.

### Rôle du Skill agentl-author :
Les modèles de langage ont naturellement tendance à coder la logique décisionnelle directement dans Python. Le Skill impose les principes fondamentaux d'AGENT-L :

1. **Règle de séparation stricte** :
   > *"L'hôte Python fournit des FAITS. Le fichier .agent prend les DÉCISIONS."*
2. **Double fichier indissociable** : génération conjointe dans le même dossier :
   - \`nom_agent.agent\` — définition déclarative (objectifs, croyances, politiques, hypothèses bayésiennes, plans).
   - \`nom_agent.py\` — hôte Python jumeau (\`build()\` connectant capteurs et implémentations d'outils).
3. **Chaîne de validation complète** :
   \`agentl check\` ➔ \`agentl test\` ➔ \`agentl verify\` ➔ \`agentl boundary\` ➔ \`agentl autoloop\` ➔ \`agentl run\` ➔ \`agentl replay\`.

### Contrat d'écriture certifié :
Le fichier \`grammar-lock.json\` scelle par empreinte SHA-256 la grammaire et les outils. Le **contrat d'écriture 2.4.0** garantit que l'assistant IA dispose d'une référence exhaustive et rigoureuse couvrant l'ensemble des 119 mots-clés et 16 blocs du langage.

### Intégration dans votre assistant IA :
- **Claude Code / Opus** : chargez \`SKILL.md\` dans votre configuration de skills.
- **ChatGPT / Custom GPT** : incluez le contenu de \`SKILL.md\` dans les instructions système.
- **Cursor / Windsurf** : ajoutez la référence au Skill dans \`.cursorrules\`.
    `,
    codeSnippet: `# Exemple d'instruction pour votre assistant IA :

"Crée un agent AGENT-L code_fixer avec son hôte Python :
 - il lit les rapports de tests en échec dans ~/MONPROJET
 - il synthétise un correctif, l'applique et relance les tests.
 - Il ne doit en aucun cas modifier de fichiers hors de ~/MONPROJET/src ni pousser sur git,
   et doit s'arrêter après 5 tentatives.
 - Vérifie la conformité formelle avec agentl check, verify et autoloop."`
  },
  {
    id: 'policy-engine',
    title: 'Moteur de Politiques & Logique Trivalente',
    iconName: 'ShieldCheck',
    summary: 'Algorithme d\'évaluation déterministe P(s) : règles NEVER irrévocables, politique fermée DEFAULT DENY, validation humaine et logique trivalente de Kleene.',
    content: `
Le moteur de politiques d'AGENT-L est le garant de la sûreté opérationnelle. **Aucun appel d'outil ne peut être exécuté sans validation préalable par la politique**, y compris lors d'une délégation (\`DELEGATE\`) vers un sous-agent.

### Algorithme d'évaluation pour une action 'a' dans l'état 's' :
1. **Règle NEVER correspondante (garde vraie ou indéterminée)** ➔ \`DENIED\` (irrévocable : aucun ALLOW ni hallucination LLM ne peut l'outrepasser).
2. **Règle DENY explicite correspondante** ➔ \`DENIED\`.
3. **Si des règles ALLOW existent** : au moins une règle ALLOW doit être strictement **vraie**, sinon \`DENIED\`.
4. **Si aucune règle ALLOW n'est déclarée** : application de la politique par défaut (\`DEFAULT DENY\` ➔ DENIED ; \`DEFAULT ALLOW\` ➔ continue).
5. **Règle REQUIRE APPROVAL correspondante** ➔ \`APPROVAL_REQUIRED\` (en l'absence d'approbateur humain enregistré, rejet par défaut ; seule une approbation explicite permet l'exécution).
6. **Sinon** ➔ \`ALLOWED\`.

### Évaluation sécurisée par défaut (Logique de Kleene forte, §7.1) :
Lorsqu'une variable ou un capteur est indisponible, l'évaluation suit une **logique trivalente** (vrai, faux, indéterminé). Une condition indéterminée déclenche immédiatement les règles préventives (\`NEVER\` / \`DENY\`), ne valide pas un \`ALLOW\` et redirige les actions sensibles vers une demande d'approbation humaine.

### Typage et correspondances booléennes (§7.7) :
Le moteur assure une correspondance exacte entre les booléens du runtime Python (ex: \`sensors.<nom>.available\`) et les symboles du langage \`.agent\` (\`true\` / \`yes\` et \`false\` / \`no\`), avec un contrôle strict des types (\`1 == true\` reste faux).

### Élagage préventif lors de la planification :
Le planificateur A* intègre les politiques directement lors de la recherche : les branches d'actions interdites par un \`NEVER\` sont éliminées dès la synthèse du plan, empêchant toute génération d'action non autorisée.
    `,
    codeSnippet: `POLICY {
    DEFAULT DENY

    // Protection des données sensibles : masquage vers le LLM
    NEVER SEND credentials

    // Autorisation conditionnée par l'inférence bayésienne
    ALLOW isolate_endpoint IF P(credential_attack) >= 0.95

    // Interdiction absolue sur les serveurs critiques
    NEVER isolate_endpoint WHEN asset.criticality == CRITICAL

    // Prise en compte explicite de l'état des capteurs et des outils
    NEVER isolate_endpoint WHEN sensors.asset.criticality.available == false
    NEVER close_batch      WHEN tools.update_row.available == false
    NEVER apply_change     WHEN reason.degraded == true

    // Approbation humaine obligatoire en cas d'incertitude
    REQUIRE APPROVAL FOR isolate_endpoint WHEN P(credential_attack) < 0.99
}`
  },
  {
    id: 'never-send',
    title: 'NEVER SEND — Protection des Données Confidentielles (v1.8)',
    iconName: 'EyeOff',
    summary: 'Masquage structurel des données confidentielles sur l\'ensemble des communications transmises au modèle LLM.',
    content: `
La directive \`NEVER SEND\` interdit formellement la sortie de données sensibles vers le fournisseur de modèle de langage. Contrairement à la clause locale \`USING\` qui s'applique uniquement à un bloc \`REASON\`, \`NEVER SEND\` s'applique à **toutes les requêtes** émises vers le LLM (y compris \`select_plan\`).

### Fonctionnement du masquage structurel :
- **Portée globale** : s'applique à l'ensemble des échanges vers l'API du modèle de langage.
- **Filtrage par chemin** : \`NEVER SEND credentials\` couvre automatiquement les sous-champs comme \`credentials.token\`.
- **Masquage structurel en profondeur** : interdire \`credentials.token\` masque la valeur correspondante dans toutes les structures composites. Le modèle reçoit \`{'token': '⟦retenu⟧', 'user': 'alice'}\`.
- **Transparence sans fuite** : la clé reste identifiable pour que le modèle sache que l'information existe, mais sa valeur confidentielle est protégée par \`⟦retenu⟧\`.
- **Traçabilité complète** : chaque opération de masquage est consignée dans le journal d'audit.

### Limites et bonnes pratiques :
\`NEVER SEND\` protège les échanges générés par le moteur AGENT-L vers le modèle. Les interactions directes de l'hôte avec des services tiers relèvent de la configuration de l'hôte. L'avertissement \`W130\` signale toute tentative d'un bloc \`USING\` faisant référence à une donnée masquée par \`NEVER SEND\`.
    `,
    codeSnippet: `POLICY {
    // Masquage universel des identifiants et données de santé
    NEVER SEND credentials, patient.identite
    NEVER SEND approval.evidence_token
}

// Contexte transmis au modèle de langage :
// { 'credentials': {'token': '⟦retenu⟧', 'user': 'alice'},
//   'approval':    {'evidence_token': '⟦retenu⟧'} }`
  },
  {
    id: 'sensor-unknown',
    title: 'Gestion Déclarative des Pannes & Résilience (v1.8 / v1.8.1)',
    iconName: 'PlugZap',
    summary: 'ON UNKNOWN ESCALATE / DEGRADE, disjoncteur d\'outils et détection des modes dégradés : la gestion des pannes devient un contrat explicite.',
    content: `
En cas d'indisponibilité d'un capteur ou d'un service externe, AGENT-L applique un principe de sécurité par défaut tout en permettant de déclarer la conduite exacte à tenir.

### Conduite face à un capteur muet — \`ON UNKNOWN\` (§31) :
- *(Par défaut)* : le chemin reste indéterminé, la politique applique la sécurité par défaut et le fait \`sensors.<chemin>.available = false\` est enregistré.
- \`ESCALATE\` : l'anomalie déclenche une alerte explicite unique auprès de l'opérateur pour éviter la saturation d'alertes répétées.
- \`DEGRADE <valeur>\` : une valeur de repli déclarée est assignée avec un niveau de confiance réduit (\`0.3\`) sous la source \`fallback\`.

### Disjoncteur d'outils (Circuit Breaker, v1.8.1) :
Pour protéger les services externes en cas de panne récurrente, l'outil est temporairement mis en pause après trois échecs consécutifs. Un mécanisme de réouverture progressive (demi-ouverture) permet de vérifier la disponibilité du service à intervalles réguliers. L'état est publié sous forme de faits (\`tools.<nom>.available\` et \`tools.<nom>.failures\`).

### Résilience des appels LLM (\`reason.degraded\`) :
Les requêtes vers le LLM sont protégées par un plafond de tentatives et un budget de temps avec recul exponentiel et respect des en-têtes \`Retry-After\`. Les erreurs de contrat (ex: 401, 403) ne sont pas rejouées inutilement. L'état dégradé éventuel est publié sous \`reason.degraded\`, permettant aux politiques de sécurité d'interdire les actions critiques en mode partiel.
    `,
    codeSnippet: `OBSERVE {
    asset.criticality  ON UNKNOWN ESCALATE    // alerte explicite unique
    disk.usage_percent ON UNKNOWN DEGRADE 100 // valeur de repli avec c=0.3
}

POLICY {
    // Les pannes constituent des faits évaluables par la politique
    NEVER close_batch  WHEN tools.update_row.available == false
    NEVER apply_change WHEN reason.degraded == true
}

# Configuration du disjoncteur côté hôte Python :
Runtime(tool_breaker=3, tool_cooldown=5)`
  },
  {
    id: 'autoloop',
    title: 'Évaluation de Robustesse & Généralisation — agentl autoloop',
    iconName: 'Infinity',
    summary: 'Validation sur cas dérivés : rejoue chaque scénario sous variations de données avec un lot de contrôle préservé pour éliminer le surapprentissage.',
    content: `
Tandis que \`check\`, \`test\`, \`verify\` et \`boundary\` confirment la conformité formelle d'un agent, la commande \`agentl autoloop\` valide sa **robustesse face à des variations de conditions réelles**.

### Mécanisme de validation sur cas dérivés :
La commande génère automatiquement des variations à partir du bloc \`GIVEN\` de chaque \`SCENARIO\` (capteurs indisponibles, valeurs limites, refus d'approbation) et vérifie le maintien de quatre invariants universels :
1. Aucune exception d'exécution non gérée ;
2. Aucun échec de postcondition \`VERIFY\` ;
3. Aucune exécution d'outil interdit par une règle absolue ;
4. Aucun contournement d'une demande d'approbation obligatoire.

### Lot de contrôle et détection du surapprentissage :
L'option \`--holdout\` (30 % par défaut) réserve un sous-ensemble de cas dérivés qui n'est jamais utilisé lors des cycles de correction. Le code de sortie \`3\` identifie spécifiquement les cas où l'agent réussit sur les données d'entraînement mais échoue sur le lot de contrôle, garantissant une évaluation impartiale.

### Validation en conditions réelles (\`--host-pass\`) :
L'option \`--host-pass\` permet d'exécuter la validation contre l'hôte réel en **mode lecture seule**, en neutralisant les effets de bord pour valider le comportement en environnement connecté.
    `,
    codeSnippet: `# Évaluation de robustesse sans modification :
agentl autoloop examples/soc_analyst.agent

# Correction automatique guidée par un modèle :
agentl autoloop examples/soc_analyst.agent \\
    --model gemini-3.1-flash-lite \\
    -o soc_analyst.fixed.agent \\
    --holdout 0.3 --max-attempts 6 --patience 2

# Validation contre l'hôte réel en lecture seule :
agentl autoloop examples/soc_analyst.agent --host-pass

# Codes de sortie : 0 = conforme · 1 = échec d'invariant · 3 = surapprentissage détecté`
  },
  {
    id: 'bayes-inference',
    title: 'Inférence Bayésienne Calibrée',
    iconName: 'Calculator',
    summary: 'Calcul exact des log-cotes en bits d\'information, gestion des corrélations (GROUP) et plafonnement des évidences (MAX_EVIDENCE).',
    content: `
Dans AGENT-L, l'indice de confiance est calculé par un moteur bayésien déterministe et vérifiable, éliminant tout risque d'auto-évaluation biaisée par le modèle de langage.

### Formule de révision des cotes :
O(h | e) = O(h) * ∏ (LR_i)
p(h | e) = O(h | e) / (1 + O(h | e))

### Propriétés du calcul bayésien :
- **GROUP** : Regroupe les observations corrélées (ex: multiples capteurs d'une même alerte). Seule l'évidence apportant le plus d'information (en bits) est retenue pour éviter les surévaluations.
- **MAX_EVIDENCE** : Plafonne la somme totale des bits d'information pour prémunir le système contre les corrélations cachées.
- **Apport en bits d'information** : Chaque observation quantifiable déplace les log-cotes d'une valeur exacte en bits.
- **Indéterminé neutre** : Une mesure absente n'altère pas la probabilité (LR = 1).

### Actualisation empirique de la probabilité a priori :
La clause \`PRIOR FROM <clé>\` permet d'ajuster dynamiquement la probabilité de base à partir des fréquences historiques observées via un lissage de Jeffreys. La commande \`agentl infer X.agent\` détaille l'apport contributif de chaque indice en bits d'information.
    `,
    codeSnippet: `HYPOTHESIS credential_attack {
    PRIOR 0.05
    EVIDENCE {
        GROUP rafale {
            wazuh.alert_count > 20        LIKELIHOOD 0.92 GIVEN_NOT 0.06
            network.anomaly_score > 0.75  LIKELIHOOD 0.85 GIVEN_NOT 0.20
        }
        endpoint.integrity == compromised LIKELIHOOD 0.75 GIVEN_NOT 0.08
    }
    MAX_EVIDENCE 8
    THRESHOLD 0.90
    EXPLAINS threat.kind
}`
  },
  {
    id: 'host-boundary',
    title: 'Frontière Hôte / Agent (Règle de Séparation)',
    iconName: 'ArrowLeftRight',
    summary: 'L\'hôte fournit les FAITS (capteurs et outils) ; le fichier .agent prend les DÉCISIONS. Analyse statique B000–B015 sur le code Python.',
    content: `
### Principe de séparation des responsabilités :
> **L'hôte Python fournit des FAITS. Le fichier .agent prend les DÉCISIONS.**

Critère de conception à appliquer sur le code hôte (\`X.py\`) :
*« Si la politique métier de l'entreprise change, cette ligne de code Python doit-elle être modifiée ? »*
Si la réponse est oui, la logique doit être déplacée dans le fichier \`.agent\`.

### Analyseur de frontière \`agentl boundary\` :
L'outil analyse l'arbre syntaxique (AST) de \`X.py\` pour détecter les écarts d'architecture :
- \`B000\` : Absence du fichier hôte jumeau \`X.py\`.
- \`B001\` : Comparaison métier dans le code Python (ex: \`if status == "Blocked"\`).
- \`B002\` : Filtrage d'une collection dans Python plutôt que dans l'agent.
- \`B003\` : Interruption de boucle (\`break\` / \`continue\`) conditionnée par une logique métier.
- \`B004\` : Tri ou priorisation arbitraire dans l'hôte.
- \`B005\` : Application de seuils chiffrés directement dans Python.
- \`B006\` : Nom de fonction suggérant une décision métier (\`should_\`, \`classify_\`).
- \`B015\` : Description d'outil contenant des consignes impératives susceptibles d'influencer le modèle.

Un commentaire \`# BOUNDARY-OK: motif\` permet de justifier les opérations techniques légitimes (sérialisation, formatage bas niveau).
    `,
    codeSnippet: `# Python (X.py) - CONFORME : Fourniture du fait brut
def check_status():
    return {"blocklist_status": Symbol("blocked"), "raw_score": 92}

# Python (X.py) - NON CONFORME (Déclencherait B001) :
# def check_status():
#     if raw_score > 80:
#         return {"should_skip": Symbol("yes")} # DÉCISION MÉTIER DÉPLACÉE`
  },
  {
    id: 'foreach-collections',
    title: 'Traitement Sécurisé des Collections — FOREACH (v1.3)',
    iconName: 'ListFilter',
    summary: 'Parcours itératif des collections avec projection scalaire étanche et réévaluation des politiques par élément.',
    content: `
Pour traiter des collections d'objets (e-mails, tickets, enregistrements) de manière prévisible, la directive \`FOREACH\` projette chaque élément sous forme de variables scalaires temporaires.

### Propriétés de sûreté du bloc FOREACH :
1. **Projection scalaire étanche** : Chaque élément expose ses attributs scalaires (\`item.id\`, \`item.status\`). Les sous-listes fournissent leur cardinalité (\`item.messages.count\`).
2. **Isolation entre itérations** : Les liaisons de variables sont réinitialisées à chaque tour de boucle pour éviter toute contamination de données entre éléments successifs.
3. **Réévaluation des politiques par élément** : Les politiques de sécurité sont évaluées indépendamment pour chaque élément. Dans un lot de 100 enregistrements comportant une entrée interdite, 99 sont traitées et 1 est bloquée en toute conformité.
    `,
    codeSnippet: `FOREACH e IN emails MAX 100 {
    REASON {
        TASK "Classer le sentiment et l'intention de l'expéditeur."
        USING { e.subject, e.body }
        PRODUCE { sentiment IN [positive, negative, neutral, unknown]
                           DEFAULT unknown }
    }
    IF sentiment == negative AND e.sender_internal == no THEN {
        escalate_feedback(ticket_id = e.id)
    }
}`
  },
  {
    id: 'effect-contracts',
    title: 'Théorème T9 : Réfutabilité des Effets d\'Outils (v1.7)',
    iconName: 'Crosshair',
    summary: 'Le théorème T9 garantit que chaque effet sur le monde réel est associé à une observation permettant de vérifier son accomplissement.',
    content: `
La chaîne de preuve formelle s'appuie sur les contrats \`EFFECT\` déclarés pour les outils. Le théorème T9 assure la cohérence empirique du modèle en vérifiant que chaque effet annoncé est effectivement observable.

### Principe du théorème T9 :
Un effet (\`EFFECT\`) modifiant l'état du système doit être associé à un capteur (\`OBSERVE\`) correspondant. Une postcondition qui ne peut être observée ne peut être vérifiée en production. Les diagnostics \`V150\` (avertissement) et \`V151\` (information) signalent les effets non observables.

### Effets internes (\`INTERNAL\`) :
Le mot-clé \`INTERNAL\` identifie les effets portant sur la gestion interne de l'agent (ex: \`cycle.done\`, \`report.written\`), les distinguant des actions modifiant l'environnement externe.

### Suivi de la crédibilité des effets :
Le registre de dérive évalue la fiabilité historique des outils via un lissage de Jeffreys. Les données de fiabilité peuvent être conservées entre exécutions via \`MEMORY { LONG_TERM { effect_drift } }\`.
    `,
    codeSnippet: `TOOL restart_pod {
    INPUT  { pod: String }
    RISK   HIGH
    EFFECT { app.status = healthy }      // doit être associé à une OBSERVE (T9)
    EFFECT { cycle.done = yes INTERNAL } // effet interne : exempté
    COST   3
}

OBSERVE {
    app.status      // observation permettant de vérifier l'effet réel
}

MEMORY { LONG_TERM { effect_drift } }    // conservation de l'historique de fiabilité`
  },
  {
    id: 'planner-time',
    title: 'Prise en Compte du Temps dans la Planification A* (v1.6)',
    iconName: 'Timer',
    summary: 'Déclaration des durées d\'outils (DURATION), échéances maximales (DEADLINE) et pondération coût/temps dans la synthèse de plans.',
    content: `
Le planificateur A* intègre la dimension temporelle de manière explicite dans l'arbitrage des plans d'action :

- \`TOOL … DURATION 45s\` — déclaration de la durée estimée de l'outil avec unité obligatoire (\`s\`, \`m\`, \`h\`).
- \`PLANNER { DEADLINE 5m }\` — échéance maximale au-delà de laquelle un plan trop long est rejeté.
- \`PLANNER { TIME_WEIGHT 0.1 }\` — coefficient d'arbitrage convertissant le temps d'exécution en coût équivalent (valeur par défaut : \`0.0\`).

### Optimisation sur le front de Pareto (Coût / Durée) :
Le planificateur maintient un **front de Pareto** sur le couple (coût, durée) pour chaque état exploré. Une séquence d'actions n'est écartée que si une route alternative s'avère à la fois moins coûteuse et plus rapide. La commande \`agentl plan X.agent\` permet d'inspecter le plan optimal synthétisé sans l'exécuter.
    `,
    codeSnippet: `TOOL failover_region {
    RISK     HIGH
    EFFECT   { service.status = healthy }
    DURATION 4m
    COST     12
}

TOOL restart_service {
    RISK     MEDIUM
    EFFECT   { service.status = healthy }
    DURATION 45s
    COST     5
}

PLANNER {
    ENABLE
    ACHIEVE     service.status == healthy
    DEADLINE    5m       // échéance maximale autorisée
    TIME_WEIGHT 0.1      // pondération du temps d'exécution dans le coût
}`
  },
  {
    id: 'society-liveness',
    title: 'Théorème T8 : Preuve d\'Absence d\'Interblocage (Multi-Agents)',
    iconName: 'Network',
    summary: 'Vérification formelle sur l\'architecture multi-agents : calcul du point fixe sur le graphe d\'attente des signaux (MESSAGE et SHARED).',
    content: `
Le théorème **T8 (« Aucun agent n'attend un signal qui ne sera jamais produit »)** évalue la vivacité globale des architectures composées de plusieurs agents.

### Détection formelle des interblocages :
Lors de l'analyse avec \`agentl verify\`, le solveur calcule le point fixe de productibilité sur le graphe des échanges (\`MESSAGE\` et \`SHARED\`). Tout cycle d'attente mutuel sans amorce initiale est formellement détecté et identifié :

| Code | Diagnostic |
|---|---|
| \`V130\` | Interblocage détecté : cycle d'attente mutuel identifié |
| \`V131\` | Signal non émis car son émetteur potentiel est lui-même bloqué |
| \`V132\` | Signal attendu qui ne figure dans aucun contexte d'émission |
| \`V133\` | Boucle de messages sans progression |
| \`V134\` | Théorème T8 démontré avec succès |
| \`V136\` | Décision non prouvable statiquement en présence d'un REASON non contraint |
| \`V137\` | Clé partagée écrite mais lue par aucun agent |

### Lecture explicite de la mémoire partagée :
Les clés partagées sont accessibles via la syntaxe explicite \`SHARED.<clé>\` (avec support de \`.count\`, \`.version\` et \`.last\`), garantissant la transparence des données échangées entre agents.
    `,
    codeSnippet: `AGENT collecteur {
    MEMORY { SHARED { incidents } }
    PLAN pousser {
        STEP s1 { MESSAGE analyste "incident" { id = incident.id } }
    }
}

AGENT analyste {
    ON MESSAGE "incident" {
        IF SHARED.incidents.count > 0 THEN { trier(id = payload.id) }
    }
}

// agentl verify confirme la vivacité :
//   T8 — Aucun agent n'attend un signal que nul ne produira
//     ✔ DÉMONTRÉ (V134)`
  },
  {
    id: 'mcp-bridge',
    title: 'Passerelle MCP Sécurisée & Typée (v1.6)',
    iconName: 'Plug',
    summary: 'Conversion des outils du protocole Model Context Protocol (MCP) en contrats TOOL formels et scellés.',
    content: `
Les commandes \`agentl mcp list\` et \`agentl mcp import\` permettent d'importer le catalogue d'un serveur MCP et de le convertir en contrats formels \`TOOL\` pour AGENT-L.

### Principes de sécurité lors de l'import MCP :
- **Niveau de risque explicite** : Le niveau \`RISK\` est initialisé à \`UNSET\`. L'erreur \`E011\` exige que le développeur définisse explicitement le niveau de risque avant toute utilisation en production.
- **Réfutabilité des effets** : Un outil importé sans postcondition \`EFFECT\` ne peut être inclus dans un plan automatique tant que ses effets ne sont pas déclarés.
- **Neutralisation des descriptions tierces** : L'option \`--strip-descriptions\` permet de neutraliser les textes fournis par des tiers susceptibles d'introduire des consignes cachées.
- **Scellement du catalogue** : \`MCPHost\` vérifie l'empreinte cryptographique du catalogue au démarrage et refuse l'exécution si les outils du serveur distant ont été modifiés sans mise à jour du contrat.
    `,
    codeSnippet: `# Lister les outils d'un serveur MCP :
agentl mcp list --server mon-serveur

# Importer et convertir le catalogue en contrats TOOL sécurisés :
agentl mcp import --server mon-serveur -o outils.agent --strip-descriptions

# Contrat généré exigeant la définition du niveau de risque :
TOOL create_issue {
    INPUT  { title: String, body: String }
    RISK   UNSET      // E011 : définition du niveau de risque requise
}`
  },
  {
    id: 'deterministic-replay',
    title: 'Rejeu Déterministe Certifié & Scellement d\'Audit',
    iconName: 'Repeat',
    summary: 'Enregistrement complet des 8 points de frontière, chaîne de hachage par événement et signature cryptographique (Ed25519 / HMAC).',
    content: `
Le runtime AGENT-L isole rigoureusement toutes les sources de variabilité externe à travers 8 points de frontière bien définis :
1. \`Host.read()\` (lecture de capteur)
2. \`Host.invoke()\` (appel d'outil)
3. \`Host.ask()\` (demande d'information)
4. \`Host.approve()\` (approbation humaine)
5. \`Host.drain()\` (consommation d'événements)
6. \`DELEGATE\` (délégation à un sous-agent)
7. \`LLM.reason()\` (inférence contextuelle)
8. \`LLM.select_plan()\` (sélection de plan)

### Fonctionnalités de certification du journal :
- \`agentl run --record run.json\` enregistre l'ensemble des perceptions et décisions.
- \`agentl replay run.json\` rejoue l'exécution de manière 100% déterministe **hors ligne sans réseau ni capteur**.
- **Chaîne de hachage inviolable** : toute altération ou troncature du journal est immédiatement détectée et localisée.
- **Signature cryptographique** : certification des métadonnées en \`HMAC-SHA256\` ou \`Ed25519\` via \`agentl seal\`.
- **Contrôle d'intégrité par défaut** : \`Journal.load\` refuse systématiquement tout journal corrompu ou modifié a posteriori.
    `,
    codeSnippet: `# Enregistrer l'exécution réelle avec signature cryptographique :
agentl run examples/soc_analyst.agent --record run.json --sign-key ed25519.pem

# Vérifier l'authenticité et l'intégrité du journal :
agentl seal run.json --key ed25519.pub

# Rejouer la décision hors ligne à l'identique :
agentl replay run.json --require-seal
# ✔ Rejeu certifié conforme — trace identique (sha256 9f8a3c4e...)`
  }
];

export const ERROR_CODES: ErrorCodeInfo[] = [
  {
    code: 'E001',
    type: 'E',
    category: 'Analyse',
    summary: 'Outil non déclaré',
    explanation: 'Appel d\'un outil qui ne figure pas dans les blocs TOOL du fichier .agent.',
    remedy: 'Déclarer l\'outil avec son contrat INPUT / OUTPUT / RISK / EFFECT.'
  },
  {
    code: 'E003',
    type: 'E',
    category: 'Analyse',
    summary: 'Politique sur outil inconnu',
    explanation: 'Une règle POLICY fait référence à un outil non déclaré.',
    remedy: 'Vérifier l\'orthographe du nom de l\'outil dans la section POLICY.'
  },
  {
    code: 'E005',
    type: 'E',
    category: 'Analyse',
    summary: 'EVENT sans corps',
    explanation: 'Documenté depuis la v0.6 et émis nulle part jusqu\'en v1.7 : l\'événement était drainé, la trace affichait un ⚡, et rien ne se déclenchait.',
    remedy: 'Donner un corps à l\'EVENT, ou le retirer si l\'événement n\'a rien à déclencher.'
  },
  {
    code: 'E009',
    type: 'E',
    category: 'Analyse',
    summary: 'Appel d\'outil dans une expression',
    explanation: 'Un outil est appelé dans une affectation ou expression (ex: x = mon_outil()).',
    remedy: 'Exécuter l\'outil en tant qu\'instruction distincte, puis lire les clés de résultat.'
  },
  {
    code: 'E011',
    type: 'E',
    category: 'Analyse',
    summary: 'Outil de risque UNSET (import MCP)',
    explanation: 'Un outil importé d\'un serveur MCP sort avec RISK UNSET : MCP ne déclare pas de risque, et il n\'est pas inventé. Contrairement à W101, aucun ALLOW * ne le fait taire.',
    remedy: 'Trancher le RISK à la main (LOW / MEDIUM / HIGH / CRITICAL) avant toute exécution.'
  },
  {
    code: 'W126',
    type: 'W',
    category: 'Avertissement',
    summary: 'Opérateur sans DURATION alors que le temps est arbitré',
    explanation: 'Le planificateur arbitre sur le temps (TIME_WEIGHT ou DEADLINE) mais l\'outil ne déclare pas de durée. Une durée absente vaut zéro, et un zéro non déclaré est indiscernable d\'une mesure.',
    remedy: 'Déclarer DURATION <n>s|m|h sur l\'outil, unité obligatoire.'
  },
  {
    code: 'W127',
    type: 'W',
    category: 'Avertissement',
    summary: 'Sous-agent délégué sans contrat',
    explanation: 'Un DELEGATE cible un sous-agent qui ne déclare ni risque ni effets de bord ; le risque est alors supposé CRITICAL.',
    remedy: 'Déclarer le contrat du sous-agent, et l\'autoriser explicitement dans la POLICY.'
  },
  {
    code: 'W128',
    type: 'W',
    category: 'Avertissement',
    summary: 'Garde d\'interdit sur un identifiant nu',
    explanation: 'Une garde NEVER / DENY / REQUIRE APPROVAL porte sur un identifiant que rien ne renseigne : il est lu comme une constante symbolique, et la règle ne s\'appliquerait pas.',
    remedy: 'Observer le chemin (OBSERVE) ou le déclarer en BELIEF pour que la garde ait une valeur à juger.'
  },
  {
    code: 'W129',
    type: 'W',
    category: 'Avertissement',
    summary: 'REASON sans USING',
    explanation: 'Un REASON sans USING reçoit tout le contexte : croyances complètes, buts, plans, catalogue d\'outils. Le défaut historique est conservé, mais l\'exposition maximale doit être une décision, pas une omission.',
    remedy: 'Déclarer USING { … } avec les seuls chemins nécessaires au jugement demandé.'
  },
  {
    code: 'W130',
    type: 'W',
    category: 'Avertissement',
    summary: 'USING faisant référence à une donnée masquée (NEVER SEND)',
    explanation: 'La clause USING fait référence à un chemin protégé par NEVER SEND. Le moteur applique le masquage par sécurité, mais le bloc REASON disposera d\'un contexte partiel.',
    remedy: 'Retirer les données sensibles masquées de la clause USING.'
  },
  {
    code: 'W131',
    type: 'W',
    category: 'Avertissement',
    summary: 'Règle de sécurité dépendant d\'un capteur sans gestion de panne',
    explanation: 'Une règle NEVER dépend d\'un capteur qui ne déclare aucune conduite en cas d\'indisponibilité (ON UNKNOWN).',
    remedy: 'Ajouter ON UNKNOWN ESCALATE ou ON UNKNOWN DEGRADE <valeur> sur le capteur.'
  },
  {
    code: 'W132',
    type: 'W',
    category: 'Avertissement',
    summary: 'Valeur de repli DEGRADE sous une règle de sécurité',
    explanation: 'Un capteur avec repli DEGRADE est utilisé dans une règle critique (NEVER, DENY ou REQUIRE APPROVAL). Il convient de vérifier que la valeur de repli active bien la sécurité par défaut.',
    remedy: 'Privilégier ON UNKNOWN ESCALATE ou vérifier que le repli déclenche la protection attendue.'
  },
  {
    code: 'W133',
    type: 'W',
    category: 'Avertissement',
    summary: 'REASON comportant plus de huit champs PRODUCE',
    explanation: 'Un nombre élevé de champs à extraire augmente le risque de troncature par le fournisseur de modèle.',
    remedy: 'Découper le raisonnement en plusieurs blocs REASON successifs ou surveiller reason.degraded.'
  },
  {
    code: 'W134',
    type: 'W',
    category: 'Avertissement',
    summary: 'Valeur DEFAULT ne déclenchant pas la sécurité attendue',
    explanation: 'En cas d\'erreur de parsing ou d\'indisponibilité du LLM, la valeur DEFAULT est assignée. Elle doit donc garantir un comportement sécurisé par défaut.',
    remedy: 'Choisir une valeur DEFAULT qui active la règle de sécurité NEVER en cas de panne.'
  },
  {
    code: 'V101',
    type: 'V',
    category: 'Sûreté (Théorème)',
    summary: 'T1 — Branche morte par politique',
    explanation: 'Le théorème T1 démontre que ce site d\'appel est systématiquement bloqué par un NEVER.',
    remedy: 'Corriger la condition du plan ou ajuster la politique si le cas est légitime.'
  },
  {
    code: 'V102',
    type: 'V',
    category: 'Sûreté (Théorème)',
    summary: 'T1 — Exposition à un état interdit',
    explanation: 'L\'outil peut être tenté dans un état interdit sans repli ni garde d\'état préalable.',
    remedy: 'Ajouter une garde d\'état IF avant l\'appel d\'outil.'
  },
  {
    code: 'V105',
    type: 'V',
    category: 'Sûreté (Théorème)',
    summary: 'T2 — Impasse sans escalade déclarée',
    explanation: 'Sous la règle NEVER, l\'agent n\'a aucune route pour atteindre son but et n\'a pas déclaré d\'escalade.',
    remedy: 'Ajouter une règle d\'escalade : IF planner.exhausted THEN escalate_to_operator.'
  },
  {
    code: 'V109',
    type: 'V',
    category: 'Sûreté (Théorème)',
    summary: 'T3 — Seuil probabiliste inatteignable',
    explanation: 'La politique exige P(h) >= X mais le modèle bayésien plafonne en-dessous de X.',
    remedy: 'Recalibrer les évidences ou abaisser le seuil d\'autorisation.'
  },
  {
    code: 'V130',
    type: 'V',
    category: 'Sûreté (Théorème)',
    summary: 'T8 — Interblocage multi-agents détecté',
    explanation: 'Cycle d\'attente mutuel sans amorce entre plusieurs agents : chaque agent attend un signal qui ne sera jamais produit.',
    remedy: 'Introduire un point d\'amorce initial ou conditionner l\'action sur une garde d\'état.'
  },
  {
    code: 'V136',
    type: 'V',
    category: 'Sûreté (Théorème)',
    summary: 'T8 — Non prouvable statiquement (DECIDE.REASON non contraint)',
    explanation: 'Un bloc DECIDE.REASON permet au modèle de choisir dynamiquement parmi les plans déclarés : la vivacité ne peut être démontrée formellement sans contraintes.',
    remedy: 'Encadrer les transitions par des règles explicites si la preuve formelle est requise.'
  },
  {
    code: 'V137',
    type: 'V',
    category: 'Sûreté (Théorème)',
    summary: 'T8 — Clé de mémoire partagée inutilisée',
    explanation: 'Une clé SHARED est écrite mais n\'est lue par aucun agent du système.',
    remedy: 'Lire explicitement la clé (SHARED.<clé>) ou supprimer l\'écriture superflue.'
  },
  {
    code: 'V150',
    type: 'V',
    category: 'Sûreté (Théorème)',
    summary: 'T9 — Effet non vérifiable par capteur',
    explanation: 'Un effet (EFFECT) modifie le monde réel mais aucun capteur (OBSERVE) ne permet de vérifier son accomplissement.',
    remedy: 'Ajouter une observation pour ce chemin ou marquer l\'effet INTERNAL s\'il est purement comptable.'
  },
  {
    code: 'B001',
    type: 'B',
    category: 'Frontière',
    summary: 'Comparaison métier dans l\'hôte Python',
    explanation: 'L\'hôte Python effectue une décision métier (ex: if status == "Blocked").',
    remedy: 'Transmettre le fait brut à l\'agent et placer la règle dans le fichier .agent (ou ajouter # BOUNDARY-OK: motif).'
  },
  {
    code: 'B002',
    type: 'B',
    category: 'Frontière',
    summary: 'Filtrage de collection dans l\'hôte',
    explanation: 'L\'hôte filtre les éléments avant de les transmettre à l\'agent.',
    remedy: 'Transmettre la collection brute à l\'agent et utiliser FOREACH dans le .agent.'
  },
  {
    code: 'B015',
    type: 'B',
    category: 'Frontière',
    summary: 'Description d\'outil au ton impératif',
    explanation: 'La description d\'un outil contient des consignes impératives susceptibles d\'influencer le modèle de langage.',
    remedy: 'Reformuler la description sur un ton neutre et descriptif ou réimporter avec --strip-descriptions.'
  }
];
