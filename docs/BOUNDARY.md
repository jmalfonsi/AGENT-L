# Boundary : périmètre et corrections de l’audit

`agentl boundary X.agent` est un **linter architectural statique**. Il signale
des décisions prises dans l’hôte Python et des protections dont la structure
n’est pas reconnue. Ce n’est pas une frontière de sécurité ni une preuve
d’absence de décisions dans Python. Des faux positifs et des faux négatifs
restent possibles.

## Surface analysée

L’analyse part de `X.py`, suit transitivement les imports Python locaux et lit
les initialiseurs des packages. Elle gère les cycles, les imports relatifs,
les alias simples, les réexportations et les imports dynamiques dont le nom
est une chaîne statiquement connue. **Aucun module inspecté n’est exécuté.**

La racine est le premier ancêtre contenant `pyproject.toml` ou `.git`, sinon
la racine du package, sinon le répertoire de l’hôte. Les recherches couvrent
le répertoire de l’hôte, la racine et son dossier `src`.

```sh
agentl boundary X.agent --project-root /chemin/projet
agentl boundary X.agent --external-policy error
```

Les chemins résolus hors racine, les fichiers illisibles ou invalides,
`exec`/`eval`, les imports dynamiques non résolus et les imports `*` produisent
`B016 INCOMPLETE / UNVERIFIABLE`. Le processus retourne alors `1`.
Un lien symbolique ne permet pas de lire un module hors du périmètre choisi.

Les bibliothèques tierces sont listées **hors analyse**, sans être importées.
La politique par défaut, `report`, accepte cette exclusion explicite ;
`error` la rend bloquante. La bibliothèque standard et le runtime `agentl`
restent exclus et sont nommés dans le rapport. Une bibliothèque externe peut
donc encore calculer une décision : il faut la revoir séparément ou utiliser
la politique `error`.

Chaque diagnostic porte son fichier et sa ligne. L’API expose
`Report.analyzed_paths`, `external_imports`, `excluded_imports` et `complete`.
`complete` concerne la surface locale et le contrat statiquement reconstructible,
pas la complétude sémantique de Python. `Report.ok()` exige une analyse complète
et aucun diagnostic d’erreur non levé. Un résultat accepté signifie uniquement
**aucun diagnostic bloquant parmi les motifs couverts sur la surface affichée**.

## Corrections BND-A01 à BND-A07

| Constat | Traitement |
| --- | --- |
| A01 — décision déplacée dans un helper | parcours transitif, chemins des diagnostics et politique explicite pour les dépendances externes |
| A02 — vérité nue considérée comme absence | seules les identités explicites avec `None`, leurs négations et combinaisons sont exemptées ; attributs, appels, noms et `filter()` sont contrôlés |
| A03 — comparaison inversée | les deux opérandes et toutes les comparaisons chaînées sont examinées |
| A04 — petit seuil ou constante nommée | suppression du seuil arbitraire de magnitude ; constantes signées, affectations simples, collections et imports locaux résolus |
| A05 — dérogation dans une chaîne | seuls les tokens Python `COMMENT` contenant `# BOUNDARY-OK: raison` sont acceptés |
| A06 — protections reconnues par mots globaux | analyse AST par site d’action, alias des primitives, flot de contrôle local et propagation des données vers les appels LLM |
| A07 — registre dynamique silencieux | `B014 INCOMPLETE / UNVERIFIABLE`, erreur bloquante sans conclusion inventée sur les clés manquantes |

Les registres des modules de construction locaux contribuent au contrat.
Lorsque plusieurs hôtes avec leurs propres `.agent` sont composés, leurs
registres ne sont pas confondus : la composition est déclarée incomplète et
doit être revue avec les contrats des sous-agents. Cette détection ne remplace
pas une analyse interprocédurale des identités des objets `Host`.

## Signaux B008–B013

Chaque action destructive reconnue est contrôlée séparément. Un commentaire,
une chaîne, une fonction jamais appelée ou une vérification d’une autre cible
ne constituent plus une protection.

- B008 reconnaît un chemin gardé par `dry_run=True` ou
  `real_actions_enabled=False`, avec activation explicite de l’action, y compris
  après un retour anticipé. Un paramètre sans défaut sûr ne suffit pas.
- B009 suit les alias de `subprocess` et contrôle `shell=True` sur ses principaux
  points d’entrée, dont `run`, `Popen` et `check_output`.
- B010 reconnaît des appels explicites de revalidation liés à la cible courante
  (`validate_target`, `revalidate_target`, `attest`, `_consume`, etc.) et
  `resolve(strict=True)`. Une réaffectation invalide le signal antérieur.
- B011 recherche une lecture de journal et un positionnement sur curseur du
  même flux ; le mot « cursor » ailleurs dans le fichier ne suffit pas.
- B012 recherche un enregistrement de recette lié à la cible, avant l’action
  (`record_rollback`, `save_rollback`, `persist_rollback`, `journal_rollback`).
- B013 suit les alias de clients externes et la propagation locale des données
  de journal ou de message vers leurs appels. L’opt-in doit garder ce chemin et
  être désactivé par défaut. La construction d’un adaptateur LLM est aussi
  signalée, car il recevra ensuite le contexte du runtime.

Les branches sont fusionnées prudemment : une protection présente sur un seul
chemin ne protège pas les autres. Les fonctions imbriquées et callbacks ne
reçoivent pas automatiquement les protections du chemin qui les crée.

**Ces formes restent des signaux de revue.** Boundary ne démontre pas que le
validateur lève effectivement une exception, que le curseur est persisté, que
le rollback est durable ou exécutable, ni que la cible ne change pas entre
validation et action. Le flot est intraprocédural ; la réflexion, les effets
de bord, le dispatch dynamique, les décisions par arithmétique et les flux
de données indirects peuvent lui échapper. Une analyse générale de taint ou
une preuve d’absence de décision précalculée n’est pas fournie.

## Dérogations et régressions

Une dérogation motivée couvre l’instruction AST adjacente ou celle contenant
le commentaire de fin de ligne, y compris si l’instruction est multiligne.
Les commentaires d’explication peuvent continuer sur les lignes suivantes.
La dérogation ne saute pas par-dessus une ligne vide ou une docstring et ne
s’applique jamais à un autre fichier. Les diagnostics de surface et de contrat
incomplets ne sont pas levés par commentaire.

`tests/test_boundary_audit.py` constitue le corpus adversarial de l’audit.
La CI exécute aussi `python3 tools/check_boundary_examples.py` : le suivi
[boundary-example-debt.json](boundary-example-debt.json) nomme chaque diagnostic
encore présent dans les exemples, y compris ceux nouvellement révélés dans
les helpers. Cette dette contient des décisions à extraire et des signaux à
revoir ou justifier ; elle n’est pas une liste de dérogations. Les commandes
`agentl boundary` correspondantes continuent de retourner `1`.
Tout nouveau diagnostic échoue en CI, même dans un hôte déjà en échec.
