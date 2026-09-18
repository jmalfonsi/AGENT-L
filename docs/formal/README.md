# Modèle formel du noyau

`AgentLKernel.tla` est un modèle TLA+ du **protocole d'exécution** du noyau
(v1.9) — pas de l'interpréteur, pas du langage. Il est vérifié
exhaustivement par TLC, et le script qui le vérifie s'assure aussi qu'il
**attrape** des défauts plausibles.

```
python3 tools/check_formal.py          # Java 11+ dans le PATH
```

## Ce qui est modélisé

| Action TLA+ | Code | Rôle |
|---|---|---|
| `Decide` | `Kernel.authorize` | politique déterministe, approbation journalisée, servie à la reprise |
| `Tamper` | `require_permit` | un hôte enveloppant réécrit les arguments après la décision |
| `Intent` | `DurableJournal.dispatch`, `_settle` | intention écrite avant l'appel ; à la reprise, résultat servi ou action tranchée |
| `Dispatch` | `Host.invoke` sous permis | un hôte idempotent ignore une seconde requête de même clé |
| `Result` | enregistrement du résultat | |
| `Crash` | mort du processus à n'importe quel pas | la mémoire disparaît, le journal et le monde restent |

L'hôte est tiré parmi trois promesses — aucune, idempotence, réconciliation —
et la politique parmi toutes les fonctions `action → {ALLOW, DENY, APPROVE}`.
Toutes les combinaisons sont explorées, avec jusqu'à deux crashs par
exécution placés à tous les pas possibles.

## Ce qui est démontré (sur ce modèle, N = 3, deux crashs)

| Propriété | Énoncé |
|---|---|
| `NoUnauthorizedEffect` (I1) | aucun effet sans autorisation de la politique ou approbation journalisée |
| `OnlyJudgedArgs` (I6) | l'hôte n'agit qu'avec les arguments que la politique a jugés |
| `AtMostOnce` (I9) | aucune reprise ne double un effet |
| `ExactlyOnceWhenPromised` | exactement une fois quand l'hôte honore la clé ou réconcilie |
| `NothingLostSilently` | une action autorisée a eu lieu une fois, ou elle est déclarée indéterminée |
| `InDoubtOnlyWithoutPromise` | l'indétermination n'existe que faute de promesse de l'hôte |
| `OneResult` | au plus un résultat journalisé par action |
| `Termination` (vivacité) | la reprise ne cale jamais sur son propre journal |

## Mutants — le modèle a des dents

`tools/check_formal.py` injecte cinq défauts réalistes et exige que TLC
trouve la violation attendue : relance aveugle d'une action indéterminée
(`AtMostOnce`), hôte qui ne contrôle pas le permis (`OnlyJudgedArgs`),
résultat journalisé pris pour une action en suspens (`OneResult`), reprise
qui ne sait pas servir un résultat (`Termination`), approbation refusée qui
émet quand même un permis (`NoUnauthorizedEffect`).

## Ce qui ne l'est pas

- **Le code Python n'est pas prouvé** : le modèle décrit le protocole que le
  code est censé suivre. La correspondance est défendue par les tests —
  crash injecté à chaque écriture du journal (`tests/test_durable_aaa.py`),
  suites aléatoires d'opérations sur les permis (`P8`), invariants du noyau.
- **Les gardes ne sont pas modélisées** : leur sémantique (Kleene, fail-closed)
  est vérifiée par les propriétés `P1`–`P4`, le solveur par `P5`–`P6`.
- **Bornes** : trois actions, deux crashs. Le protocole traite chaque action
  indépendamment, ce qui rend ces bornes représentatives — ce n'est pas une
  preuve pour N quelconque.
- **L'hôte est cru sur sa promesse** : un hôte qui se déclare idempotent sans
  l'être sort du modèle.
