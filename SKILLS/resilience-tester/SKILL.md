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
5. **Tuer le processus** en plein effet (v1.9). Lancer
   `agentl run xxx.agent --durable DIR`, tuer le processus pendant un outil à
   effet, relancer la même commande. Vérifier dans le monde réel que l'effet
   n'a eu lieu qu'une fois, et lire `agentl durable status DIR` : l'action
   doit être relancée (outil `idempotent=True`), réconciliée, ou déclarée
   indéterminée — jamais rejouée à l'aveugle.
6. En asynchrone (`agentl.aio`), fixer des délais (`Limits(tool_timeout=…)`)
   plus courts que l'outil : l'action doit finir **indéterminée**
   (`tools.<outil>.in_doubt`), pas réussie ni relancée.

## Pièges

- Un scénario de refus qui passe parce que le programme échoue plus tôt, pour
  une autre raison : vérifier le motif du refus, pas seulement son occurrence.
- Un capteur simulé toujours disponible : la panne n'est jamais testée.
- Un holdout tiré après correction : il ne mesure plus rien.
- Un outil qui ignore la clé d'idempotence alors qu'il est déclaré
  `idempotent=True` : la promesse est de l'hôte, le runtime ne peut pas la
  vérifier. Le tester avec une panne réelle, pas en lisant le code.
- Un réconciliateur qui rend `NOT_EXECUTED` quand il ne sait pas : il
  transforme « peut-être » en « relancer ». Il doit **lever**.

## Validations attendues

- `agentl test` : au moins un scénario positif et un scénario de refus par
  règle `NEVER`.
- `agentl autoloop` : le lot retenu passe aussi, sinon le code de sortie 3
  signale l'apprentissage par cœur.
- Pour un agent à effets non rejouables : un run durable interrompu puis
  repris, avec un seul effet dans le monde.
