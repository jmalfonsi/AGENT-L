# Journal visuel HTML

`agentl run --html <fichier>` écrit un journal d'exécution autonome (un seul
fichier, zéro ressource externe, thème clair/sombre). Rendu par
`agentl/trace_html.py`.

## Ce qu'il montre

- **En-tête** : nom de l'agent, description, outils/objectifs/hypothèses, et
  des tuiles de métriques (les métriques de sûreté — `blocked`,
  `domain_clamps`, `effect_drift`, `verify_fail`, `shared_conflicts` —
  passent au rouge dès qu'elles sont non nulles).
- **Une carte par tick**, sur une colonne vertébrale numérotée.
- **Une ligne par événement réel du runtime** : glyphe + catégorie colorée +
  texte. Les appels d'outil sont éclatés en **paramètres** (clé/valeur) tels
  que passés à l'hôte. Les événements `bloqué`, `approbation`, `vérifié` et
  `inférence` portent un bandeau de couleur.
- **Barre de filtres** : chaque catégorie (perception, inférence,
  raisonnement, planification, action, approbation, bloqué, vérifié, mémoire,
  signal) se masque/affiche.

## Usage programmatique

```python
from agentl import parse_file, Runtime
from agentl.trace_html import render
from pathlib import Path

agent = parse_file("examples/xxx.agent").agents[0]
rt = Runtime(agent, host, llm).run()
Path("run.html").write_text(render(rt, agent), encoding="utf-8")
```

## Étendre le rendu

- Les catégories sémantiques (couleur = sens) sont dans le dict `GROUP` ;
  ajouter un `kind` d'événement = l'y mapper + lui donner un glyphe dans
  `GLYPH`.
- Le gabarit `_PAGE` définit les tokens de couleur par thème sous `:root`,
  `@media (prefers-color-scheme:dark)`, `:root[data-theme=...]`. Modifier une
  couleur = toucher les trois.
- Le fichier utilise des piles de polices système (mono pour les données,
  sans pour le chrome) : c'est délibéré — pas de webfont, donc pas de
  ressource externe ni d'octets superflus dans un fichier régénéré à chaque
  run.
