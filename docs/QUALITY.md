# Porte de qualité Grade AAA

`Grade AAA` est le nom d'une **porte de livraison interne** à AGENT-L. Ce
n'est ni une certification externe, ni une preuve formelle du runtime, ni une
promesse qu'un module hôte tiers est sûr. Une révision ne peut revendiquer ce
niveau que si tous les critères obligatoires ci-dessous sont verts sur le
même commit et que leurs sorties sont conservées avec l'artefact de livraison.

## Périmètre

Le périmètre obligatoire du paquet publié est `agentl/`, `tests/`, la SPEC,
les exemples distribués et les métadonnées de paquet. Les laboratoires
`bench/`, `AITESTPLATFORM/`, `AGENTIC_SIMULATOR/`, `WEBSITE/` et
`agentl-vscode/` ont des environnements distincts. Une livraison qui les
inclut doit aussi produire leurs verdicts ; leur absence ne peut pas être
présentée comme un succès.

## Critères obligatoires

| Porte | Critère vérifiable | Commande ou preuve attendue |
|---|---|---|
| Version | `pyproject.toml`, `agentl.__version__`, README, SPEC et CHANGELOG annoncent la même version | contrôle automatisé de cohérence, sans exception manuelle |
| Core | collecte et exécution complètes, sans échec ni erreur de collecte | `python3 -m pytest -q` |
| P0 numérique | aucune valeur non finie ni absence d'oracle ne désarme une politique | `python3 -m pytest -q tests/test_numeric_policy_safety_aaa.py` |
| Sûreté | régressions politique, surface, divulgation, preuve et rejeu vertes | suites `test_*security*`, `test_runtime_aaa.py`, `test_proof_aaa.py`, `test_replay_aaa.py` |
| Couverture | au moins 90 % de lignes sur `agentl`, aucun module de sûreté exclu | `python3 -m pytest --cov=agentl --cov-report=term-missing --cov-fail-under=90` |
| Exemples | chaque exemple livrable franchit `check`, `verify` et `test` | boucle CI sur `examples/*.agent`, exclusions intentionnelles nommées |
| Frontière | aucun nouveau diagnostic `boundary` par rapport au suivi détaillé ; dérogations motivées, dette visible dans `boundary-example-debt.json` | job « Frontière hôte/agent » de la CI |
| Artefact | wheel et sdist se construisent et leurs métadonnées sont valides | `python3 -m build` puis `python3 -m twine check dist/*` |
| Reproductibilité | aucun test obligatoire ne dépend du réseau, d'une clé de modèle ou d'un ordre aléatoire non fixé | exécution dans un environnement propre, clés fournisseur absentes |
| Documentation | toute sémantique modifiée est décrite dans la SPEC et toute limite pertinente reste explicite | revue du diff + liens SPEC/SECURITY/CHANGELOG |

Un test ignoré n'est pas automatiquement un succès. Le rapport de livraison
doit distinguer les skips attendus, avec leur raison, des portes réellement
exécutées. Un verdict `◐ BORNÉ` du vérificateur n'est jamais reformulé en
« démontré ».

## Produits et laboratoires

Pour une revendication **dépôt complet**, ajouter aux portes précédentes :

```bash
# Dans l'environnement AutomationBench compatible
python -m pytest -q AITESTPLATFORM/tests
python bench/test_policy_holds.py

(cd AGENTIC_SIMULATOR && npm ci && npm run check && npm run build)
(cd AITESTPLATFORM && bun install --frozen-lockfile && bun run lint && bun run build)
(cd WEBSITE && npm ci && npm run lint && npm run build)
(cd agentl-vscode && npm ci && npm run selftest && npm run lint-repo)
```

Les campagnes appelant un modèle réel sont des mesures complémentaires. Elles
doivent enregistrer modèle, paramètres, protocole, seed, coût et résultats,
mais ne remplacent aucune porte hors ligne.

## Non-garanties explicites

Même avec toutes les portes vertes, Grade AAA ne signifie pas :

- que le Python d'un hôte est isolé ou sûr ;
- que le solveur est complet ou que tout verdict borné est une preuve ;
- que les `EFFECT` déclarés décrivent fidèlement un outil jamais observé ;
- qu'un journal signé possède un horodatage tiers ;
- que le contrôle syntaxique `boundary` comprend les intentions d'une
  bibliothèque appelée par l'hôte ;
- que le vérificateur constitue une preuve formelle de l'Implémentation Python
  du runtime.

Ces limites sont détaillées dans la SPEC §28 et dans `SECURITY.md`. Une
révision qui les masque ou les reformule en garanties échoue la porte
documentaire, même si tous les tests passent.

## Preuve de livraison

Le rapport associé à une version doit au minimum conserver : commit exact,
versions Python/Node/Bun utilisées, commandes exécutées, codes de sortie,
nombre de tests collectés/exécutés/skippés, couverture, verdicts des exemples,
résultat de construction et liste des dérogations. Les nombres inscrits dans
un ancien README ne valent pas preuve : seul le rapport produit sur la
révision livrée fait foi.


## Exceptions historiques de scénarios

La CI utilise `--allow-empty` uniquement pour les anciens exemples
K8S_HEALER, complex_soc, forensic, llm_soc, maintenance, soc_risk et soc_team.
Ils passent CHECK/VERIFY mais n'ont pas de critères SCENARIO : cette exception
nommée est une dette de couverture. Tous les autres exemples livrables passent
TEST sans cette option. Les oracles des 57 scénarios existants sont explicites.

Deuxième exception nommée, ouverte le 17 septembre 2026 : trois tests du
Studio (`test_real_quality_suite_run_and_replay`,
`test_draft_is_validated_by_the_four_gates`,
`test_project_creation_invokes_selected_authoring_agent`) sont écartés de la
CI par `--deselect`. Cause unique et réelle : le gabarit `runtime_llm.py` du
Studio ne passe pas `agentl boundary` depuis que Boundary suit les modules
locaux (12 diagnostics `B001`/`B006` d'aiguillage de fournisseur), la porte
`boundary` du brouillon rend donc `failed` et rien n'est appliqué derrière.
C'est une dette datée, pas une acceptation : soit le gabarit cesse de comparer
des valeurs métier, soit le Studio se dote d'un suivi de dette explicite comme
`boundary-example-debt.json`.

La commande suivie par Git `python3 tools/check_examples.py` exécute cette
chaîne et porte la liste exacte des exceptions. Le workflow
`.github/workflows/ci.yml` est publié avec le dépôt depuis la v1.8.2 — il
était auparavant ignoré par `.gitignore`, donc jamais exécuté — et il appelle
la même commande.
