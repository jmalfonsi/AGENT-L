# Sécurité d’auteur AGENT-L (v1.5, provenance v1.9)

Lire cette référence pour tout agent qui consomme du texte non fiable, agit sur
une cible externe, modifie un système ou peut clore un cycle de traitement.

## Contrat minimal

1. **Le LLM qualifie ; il ne choisit jamais la cible.** Une cible d’action
   vient d’un capteur déterministe. W119 signale les sorties REASON (y compris
   les aliases reason/SET) vers les paramètres de cible reconnus d'un outil
   HIGH/CRITICAL sans ATTESTS. La reconnaissance des cibles reste heuristique :
   l'absence d'avertissement ne prouve pas la provenance.
2. **Toute sortie décisionnelle a un repli explicite et inoffensif.** Déclarer
   `DEFAULT unknown`, `DEFAULT other` ou `DEFAULT yes` pour une détection
   d’injection. Le repli ne doit jamais autoriser une action.
3. **La preuve est distincte de la cible.** Déclarer
   `evidence_token: String ATTESTS target`. L’hôte émet ce jeton depuis la
   perception déterministe, le consomme une seule fois, vérifie sa fraîcheur
   et revérifie l’identité de la cible juste avant l’effet.
4. **Fort impact = approbation, opt-in réel et retour arrière.** Tout outil
   HIGH/CRITICAL porte une REQUIRE APPROVAL inconditionnelle ; une règle
   conditionnelle ne supprime pas W120. L’hôte démarre en dry-run,
   exige un opt-in distinct et journalise une recette de rollback avant effet.
5. **Une promesse d’outil n’est pas une observation.** Toute condition lue
   par `VERIFY` après un `EFFECT` doit aussi être dans `OBSERVE` (`W121`).
6. **Le texte brut est hostile.** Après `raw_log`, `message`, `body` ou
   `content` dans `REASON`, un appel risqué exige une garde déterministe de
   confiance/injection de polarité sûre (`injection_detected == no`). Exécuter
   dans la branche THEN de `injection_detected == yes`, ou dans ELSE de
   `injection_detected == no`, ne constitue pas une protection. W125 reste un
   signal heuristique, pas une validation du détecteur. Le repli d'une
   détection d'injection est `yes`.
7. **Une probabilité globale ne prouve pas une cible locale.** Dans
   `FOREACH`, chaque action transporte une attestation corrélée à l’élément
   courant ; un postérieur global ne suffit pas (`W122`).
8. **La terminaison dépend du backlog.** L’outil satisfaisant le `LOOP UNTIL`
   est autorisé seulement si les compteurs `pending`/`remaining`/`unresolved`
   observés sont nuls (`W123`).
9. **Chaque effet risqué a un scénario de sûreté.** Le scénario touche sa
   postcondition et possède une mutation connue qui le fait échouer (`W124`).

## Portes automatiques

- `agentl check` : W119–W125.
- `agentl verify` : T6 (provenance corrélée) et T7 (terminaison sûre).
- `agentl boundary` : B008–B014 — dry-run, shell, revalidation, curseur,
  rollback, opt-in LLM externe et registres exacts.
- `agentl test` : invariants avec état initial explicite, EXPECT NEVER CALL,
  EXPECT BLOCKED et mutations de politique. Déclarer les OUTPUT et choix
  d'oracle ; voir [scenarios.md](scenarios.md).

`ATTESTS` rend la liaison vérifiable ; il ne remplace jamais la fraîcheur,
l’usage unique et la revalidation d’identité dans l’hôte.


## Provenance portée par les valeurs (v1.9)

Jusqu'en v1.8, la provenance était une propriété **statique** : `W119`,
`W125` et T6 reconnaissent des motifs de noms dans l'AST. Depuis la v1.9,
chaque valeur de l'état porte aussi une **étiquette** : l'ensemble des
sources qui ont servi à la calculer. La politique peut la lire au moment
d'autoriser l'action, quel que soit le chemin suivi par la valeur (recopie,
arithmétique, branche `IF`, plan choisi par le modèle).

### Les sources

| Source | Posée par | Non fiable ? |
|---|---|---|
| `DECLARED` | littéral ou déclaration du programme | non |
| `OBSERVED` | capteur déclaré dans `OBSERVE` | non |
| `HUMAN` | réponse d'opérateur (`ASK`) | non |
| `RUNTIME`, `EFFECT`, `INFERRED`, `FALLBACK` | faits du runtime, `EFFECT` prédit, postérieur bayésien, repli d'un capteur muet | non |
| `TOOL` | sortie d'outil (frontière externe) | **oui** |
| `LLM` | sortie de `REASON`, plan choisi par le modèle | **oui** |
| `MESSAGE`, `EVENT`, `DELEGATE` | charges utiles et retours de sous-agent | **oui** |
| `SHARED`, `MEMORY` | mémoire écrite par un autre agent, mémoire rechargée | **oui** |
| `EXTERNAL` | valeur que l'hôte déclare non fiable (`untrusted(...)`) | **oui** |
| `UNKNOWN` | aucune étiquette connue | **oui** (fail-closed) |

L'union ne retire jamais rien : aucune transformation ne blanchit une valeur.
Le **flux implicite** est suivi : `IF payload.urgent == yes THEN { SET env =
"prod" }` donne à `env` l'étiquette du message, même si `"prod"` est un
littéral. Sans cela, une injection choisirait une constante « de confiance ».

### Les fonctions de garde

| Fonction | Rend |
|---|---|
| `UNTRUSTED(x)` | vrai si une source de `x` est non fiable |
| `TRUSTED(x)` | la négation |
| `LLM_DERIVED(x)` | vrai si `LLM` figure parmi les sources de `x` |
| `ATTESTED(x)` · `ATTESTED(x, outil)` | vrai si un outil (cet outil) a **accepté** la valeur de `x` en argument |
| `ORIGIN(x)` | la liste des sources, en symboles |

`x` est un **chemin** : un argument de l'action jugée (`host`, `to`), ou tout
chemin de l'état. `action` désigne l'action entière (ses arguments et la
décision qui l'a produite) : `NEVER wipe WHEN UNTRUSTED(action)`. Il ne se lit
que dans une garde de politique, et `ATTESTED(action)` n'existe pas.

```agentl
NEVER wipe_host WHEN UNTRUSTED(host) AND NOT ATTESTED(host, check_wipeable)
NEVER transfer  WHEN LLM_DERIVED(to) AND NOT ATTESTED(to, resolve_account)
NEVER transfer  WHEN tools.transfer.in_doubt == true
```

La dernière ligne n'est pas une garde de provenance. C'est le fait posé par
l'exécution durable quand un virement est resté indéterminé après une panne
(`runtime-semantics.md` §10). Elle a sa place dans la même politique.

### Les règles à connaître

1. **Une valeur absente rend la garde indéterminée.** Un `NEVER` s'applique
   sous `UNKNOWN`, un `ALLOW` ne compte pas. Écrire la protection en `NEVER` :
   `ALLOW outil WHEN TRUSTED(x)` est fermé tant qu'il est seul, mais une autre
   règle `ALLOW` sur le même outil suffit à le contourner. Un `NEVER` ne se
   rachète ni par un `ALLOW` ni par une approbation.
2. **Attesté ≠ fiable.** `ATTESTED(x, v)` dit que l'outil `v` a été appelé
   avec succès sur cette valeur. L'étiquette de `x` ne change pas.
   **Tout appel réussi atteste ses arguments**, même si l'outil répond
   `{"ok": "no"}`. Un validateur doit donc **lever une exception** pour
   refuser une cible. Un validateur qui « répond non » atteste quand même.
3. **L'hôte peut dégrader, jamais élever.** Un capteur qui lit du texte tiers
   rend `untrusted(valeur)` (`agentl.kernel.provenance`) : la valeur reçoit
   `EXTERNAL` en plus d'`OBSERVED`. Il n'existe aucun moyen de rendre fiable
   une valeur depuis l'hôte.
4. **`E016`** refuse une fonction de provenance mal employée : premier
   argument qui n'est pas un chemin, `action` hors d'une garde de politique,
   `ATTESTED(action)`, second argument qui ne nomme pas un `TOOL` déclaré.
   **`E015`** refuse tout nom de fonction inconnu dans une expression :
   l'expression serait inévaluable, la garde indéterminée pour toujours.
5. **Les scénarios voient les mêmes étiquettes** : un chemin `OBSERVE` posé
   par `GIVEN` est `OBSERVED`, une réponse `REASON` est `LLM`
   (`scenarios.md` §2). Une garde de provenance se teste avec
   `EXPECT NEVER CALL` + `EXPECT BLOCKED`.

### Ce que `check` et `verify` ne voient pas encore

Les gardes de provenance agissent **à l'exécution**. `W119`, `W125` et T6 ne
les créditent pas : un agent protégé par `NEVER … WHEN UNTRUSTED(target)`
reste signalé tant qu'il n'a pas le motif statique (`ATTESTS`, garde
d'injection). Conséquence pratique :

- pour une cible risquée, écrire **les deux** : le motif `ATTESTS` de la
  v1.5, qui rend T6 démontrable, **et** la garde de provenance, qui tient
  même quand la valeur arrive par un chemin que l'analyse statique ne suit
  pas ;
- ne jamais justifier un `W119` par « la garde UNTRUSTED couvre » : c'est
  une défense en profondeur, pas une preuve.

### Ce qu'une garde de provenance ne fait pas

- Elle protège **à qui** et **sur quoi** on agit, pas **ce que contient** un
  champ `String` sortant (principe 9 du SKILL).
- Elle dépend de la politique écrite. Le banc comparatif
  (`bench/frameworks/`) montre qu'un agent AGENT-L **sans** la ligne
  `NEVER … UNTRUSTED(host)` exécute l'injection comme les autres frameworks.


## Périmètre de Boundary après l’audit

Boundary analyse aussi les imports locaux transitifs, sans exécuter Python.
Les dépendances tierces sont affichées hors analyse ; `--external-policy error`
les rend bloquantes. `--project-root` fixe la racine des modules locaux.
B014 rend un registre dynamique `INCOMPLETE / UNVERIFIABLE` et B016 signale
une surface non résolue. Ces états ne sont pas des succès.

Les filtres par vérité nue, les comparaisons inversées, les petits seuils et
les constantes nommées sont contrôlés. Une levée exige un véritable commentaire
Python `# BOUNDARY-OK: raison`. Les signaux B008–B013 portent sur le chemin
local de chaque action, sans prouver le contenu du validateur ni la durabilité
du rollback. Un verdict accepté est limité aux motifs et modules affichés ;
ce n’est pas une preuve d’absence de décision précalculée ou de faille.
Voir `docs/BOUNDARY.md` et le corpus `tests/test_boundary_audit.py`.
