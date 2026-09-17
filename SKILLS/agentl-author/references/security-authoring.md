# Sécurité d’auteur AGENT-L (v1.5)

Lire cette référence pour tout agent qui consomme du texte non fiable, agit sur
une cible externe, modifie un système ou peut clore un cycle de traitement.

## Contrat minimal

1. **Le LLM qualifie ; il ne choisit jamais la cible.** Une cible d’action
   vient d’un capteur déterministe. Une sortie `REASON` vers un argument d’un
   outil `HIGH`/`CRITICAL` sans preuve explicite déclenche `W119`.
2. **Toute sortie décisionnelle a un repli explicite et inoffensif.** Déclarer
   `DEFAULT unknown`, `DEFAULT other` ou `DEFAULT yes` pour une détection
   d’injection. Le repli ne doit jamais autoriser une action.
3. **La preuve est distincte de la cible.** Déclarer
   `evidence_token: String ATTESTS target`. L’hôte émet ce jeton depuis la
   perception déterministe, le consomme une seule fois, vérifie sa fraîcheur
   et revérifie l’identité de la cible juste avant l’effet.
4. **Fort impact = approbation, opt-in réel et retour arrière.** Tout outil
   `HIGH`/`CRITICAL` porte `REQUIRE APPROVAL`. L’hôte démarre en dry-run,
   exige un opt-in distinct et journalise une recette de rollback avant effet.
5. **Une promesse d’outil n’est pas une observation.** Toute condition lue
   par `VERIFY` après un `EFFECT` doit aussi être dans `OBSERVE` (`W121`).
6. **Le texte brut est hostile.** Après `raw_log`, `message`, `body` ou
   `content` dans `REASON`, un appel risqué exige une garde déterministe de
   confiance/injection. Le repli d’une détection d’injection est `yes`.
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
- `agentl test` : invariants de non-action et mutations de politique.

`ATTESTS` rend la liaison vérifiable ; il ne remplace jamais la fraîcheur,
l’usage unique et la revalidation d’identité dans l’hôte.


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
