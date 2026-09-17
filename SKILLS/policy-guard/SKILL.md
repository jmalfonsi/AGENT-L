# Policy Guard

Concevoir des agents AGENT-L dont chaque action risquée est gouvernée.

## Invariants

- Toute action qui touche le monde déclare `RISK`, `SIDE_EFFECT` et `EFFECT`.
- Le refus est l'état par défaut : sans preuve, sans capacité ou sans
  approbation, l'action ne part pas.
- Un secret, un jeton de preuve ou une donnée brute ne franchissent jamais la
  frontière du modèle.

## Procédure

1. Nommer l'effet observable de chaque outil, puis le capteur indépendant qui
   le confirme. Un `EFFECT` que rien n'observe n'est pas réfutable.
2. Déclarer les préconditions dans la `POLICY`, jamais dans le corps du `PLAN` :
   une garde écrite dans le plan n'est pas vérifiable statiquement.
3. Écrire l'approbation comme une donnée vérifiable — expéditeur attendu,
   jeton de preuve, fenêtre de validité — et non comme un booléen.
4. Ajouter au moins un `SCENARIO` positif et un `SCENARIO` de refus par règle
   `NEVER`.

## Pièges

- Une `POLICY` qui autorise par défaut et interdit par exception : l'ordre
  inverse est le seul sûr.
- Un `EFFECT` déclaré sur une croyance que l'agent écrit lui-même : la boucle
  se confirme toute seule.
- Une approbation dont l'expéditeur n'est pas comparé à l'expéditeur attendu.

## Validations attendues

- `agentl check` sans W ni E.
- `agentl verify` : T3 (action gouvernée) et T6 (provenance) démontrés.
- `agentl boundary` sans B008/B010/B012 sur les outils destructifs.
- `agentl test` : le scénario de refus échoue si la garde est retirée.
