# Resilience Tester

Renforcer un projet AGENT-L au-delà de son chemin nominal.

## Invariants

- Un chemin nominal qui passe ne prouve rien : la couverture se mesure sur les
  refus, les pannes et les contradictions.
- Un cas retenu (holdout) jamais montré à la boucle de correction est la seule
  mesure de l'apprentissage par cœur.

## Procédure

1. Couvrir les quatre pannes de frontière : capteur inconnu, outil
   indisponible, sortie de modèle invalide, approbation refusée.
2. Écrire un `SCENARIO` qui tente de violer chaque règle `NEVER`, et vérifier
   qu'il échoue pour la bonne raison.
3. Éprouver les bornes : plafond de ticks atteint, disjoncteur ouvert, dérive
   d'effet constatée entre ce qui est déclaré et ce qui est observé.
4. Garder les cas retenus hors de portée de `agentl autoloop` : ce sont eux qui
   distinguent un agent corrigé d'un agent appris par cœur.

## Pièges

- Un scénario de refus qui passe parce que le programme échoue plus tôt, pour
  une autre raison : vérifier le motif du refus, pas seulement son occurrence.
- Un capteur simulé toujours disponible : la panne n'est jamais testée.
- Un holdout tiré après correction : il ne mesure plus rien.

## Validations attendues

- `agentl test` : au moins un scénario positif et un scénario de refus par
  règle `NEVER`.
- `agentl autoloop` : le lot retenu passe aussi, sinon le code de sortie 3
  signale l'apprentissage par cœur.
