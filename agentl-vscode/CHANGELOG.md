# Journal des changements — extension VS Code AGENT-L

## [1.4.0] — 2026-10-17

### Corrigé
- **Les fonctions de garde et les assertions de scénario ne sont plus des
  fautes.** `UNTRUSTED`, `ATTESTED`, `TRUSTED`, `LLM_DERIVED`, `ORIGIN`,
  `POSTERIOR`, `PROBABILITY`, `UNCERTAINTY` (fonctions de garde) et `CALL`,
  `BLOCKED`, `NO`, `ERROR` (`EXPECT NEVER CALL`, `EXPECT NO ERROR`) se lisent
  comme des noms, pas comme des mots-clés du lexeur : la liste miroir les
  ignorait, et un agent v1.11 valide affichait douze erreurs.
- **L'extension installée exécutait un `out/extension.js` de juillet.** La
  source avait suivi le langage, pas le fichier compilé, et le numéro de
  version n'avait pas bougé : VS Code ne remplaçait rien. Le test miroir
  (`tests/test_frontend_aaa.py`) compare désormais aussi le fichier compilé.

## [1.3.0] — 2026-07-28

### Corrigé
- **Les majuscules ne sont plus contrôlées à l'intérieur des chaînes.**
  `DESCRIPTION "Analyste SOC niveau 1"` signalait `SOC` comme mot-clé inconnu.
  Une prose française contient forcément des sigles ; un linter qui les refuse
  dans les libellés est un linter qu'on désactive. Commentaires de ligne
  (`//`, `#`) et de bloc neutralisés de la même façon, en préservant les
  positions pour que les soulignements restent justes.
- **Les chaînes multilignes sont suivies d'une ligne à l'autre.** Les
  `TASK "…"` du dépôt courent sur plusieurs lignes : leur prose était relue
  comme du code dès la deuxième (`NÉGATIVE`, `RH` signalés à tort).
- **Les lettres accentuées comptent comme des lettres.** `NÉGATIF` était
  découpé et le message désignait `GATIF`, un mot absent du fichier.

### Ajouté
- **Contrôle du nom d'agent** : `AGENT SOC_ANALYST` est signalé (avertissement)
  avec une suggestion en snake_case — `soc_analyst`, et
  `SecurityInvestigator` → `security_investigator`. Avertissement et non
  erreur : le parseur accepte ces noms, c'est la convention qui tranche.
  Un nom fautif ne produit qu'**un** diagnostic, pas deux.
- Mots-clés v1.3 et v1.4 : `FOREACH`, puis `SCENARIO`, `GIVEN`, `WITHIN`
  (liste des réservés et coloration). La liste est le miroir exact de
  `agentl/lexer.py` — vérifié.
- `npm run selftest` (17 contrôles) et `npm run lint-repo` : la logique du
  linter est exportée sans dépendance à l'API VS Code, donc exécutable sur le
  dépôt entier.

### Modifié
- Grammaire TextMate : `#comments` et `#strings` passent avant `#keywords`.
