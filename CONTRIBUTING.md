# Contribuer à AGENT-L

Merci de l'intérêt. Ce projet a une contrainte inhabituelle : **le langage est
un contrat de sûreté**. Une contribution qui affaiblit une propriété vérifiée
sans le dire est un défaut, même si tous les tests passent.

## Mise en route

```bash
git clone <dépôt> && cd AGENT-L
python -m pip install -e ".[studio]" pytest pytest-cov
python -m pytest -q            # tests du cœur, aucun réseau
agentl check examples/soc_analyst.agent
agentl verify examples/soc_analyst.agent
```

`pytest` sans argument cible volontairement `tests/`, c'est-à-dire le paquet
publiable et compatible Python 3.10–3.12. Les laboratoires ne sont pas cachés :
ils ont des commandes et des environnements séparés, car AutomationBench exige
Python 3.13 et ses propres dépendances. Leur exécution doit apparaître dans le
rapport d'une livraison qui les inclut ; l'absence d'un job n'est pas un vert.

```bash
# AutomationBench doit être un dépôt voisin, ou être indiqué explicitement.
export AUTOMATIONBENCH_ROOT="$(cd ../AutomationBench && pwd)"
"$AUTOMATIONBENCH_ROOT/.venv/bin/python" -m pytest -q AITESTPLATFORM/tests
"$AUTOMATIONBENCH_ROOT/.venv/bin/python" bench/test_policy_holds.py

# Applications TypeScript
(cd AGENTIC_SIMULATOR && npm ci && npm run check && npm run build)
(cd AITESTPLATFORM && bun install --frozen-lockfile && bun run lint && bun run build)
(cd WEBSITE && npm ci && npm run lint && npm run build)
(cd agentl-vscode && npm ci && npm run selftest)
```

`bench/test_comp_policy_holds.py` appelle un modèle réel : exécutez-le avec
`GEMINI_API_KEY` et `AGENTL_BENCH_MODEL` dans le même venv. Il n'appartient
pas à la CI hors ligne et son absence n'est donc pas comptée comme un succès.

La porte de livraison complète, y compris les régressions P0, la cohérence de
version et les limites de ce que le projet appelle **Grade AAA**, est définie
dans [docs/QUALITY.md](docs/QUALITY.md). « AAA » n'est pas une appréciation :
chaque critère obligatoire doit produire un artefact ou un code de sortie
vérifiable.

Le core n'a **aucune dépendance runtime**. Toute PR qui en ajoute une au core
(hors extras `studio` / `anthropic`) doit justifier pourquoi elle est
inévitable.

## Boucle de travail

`check` (bien formé) → `verify` (sûr) → `boundary` (honnête) → `run`.
C'est la chaîne décrite en [docs/SPEC.md](docs/SPEC.md) §25 ; le code doit la
respecter et les exemples aussi.

## Ce qu'on attend d'une PR

- **Des tests**, dans le style existant : déterministes, sans réseau, sans
  `sleep` arbitraire, sans écriture dans le dépôt. Les tests du studio
  travaillent sur une copie temporaire — gardez cette règle.
- **Pas de régression de vocabulaire.** Ajouter un mot-clé au langage change
  la grammaire de tout le monde : ouvrez d'abord une issue de discussion.
- **La spécification suit le code.** Un changement de sémantique sans mise à
  jour de `docs/SPEC.md` sera refusé — la spec est le produit, pas la doc.
- **Les codes de diagnostic** (`V…`, `W…`, `E…`) sont un contrat public :
  on en ajoute, on n'en recycle jamais un.
- **Aucun chemin ne doit atteindre un outil sans traverser le moteur de
  politiques.** C'est aujourd'hui une propriété du code Python, pas un
  théorème (SPEC §28) : toute PR qui touche `runtime.py`, `policy.py` ou
  `host.py` doit expliquer comment elle la préserve.

## Style

Français dans les commentaires, docstrings et messages utilisateur — c'est la
langue du projet et le mélange coûte plus cher que la traduction. Code en
anglais (identifiants, noms de symboles du langage).

## Licence et CLA

Le core est sous **AGPL-3.0-or-later** (voir [LICENSE](LICENSE)). En
contribuant, vous acceptez que votre contribution soit distribuée sous cette
licence. Un CLA permettant une double licence commerciale pourra être demandé
avant fusion pour les contributions substantielles.

## Sécurité

Ne signalez pas une faille par une issue publique : voir
[SECURITY.md](SECURITY.md).
