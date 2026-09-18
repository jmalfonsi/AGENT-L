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
   `NEVER`. Le refus s'écrit `EXPECT NEVER CALL outil` **et**
   `EXPECT BLOCKED outil` : le second prouve que c'est la politique qui a
   refusé.
5. Pour toute cible venue du modèle, d'un outil, d'un message ou d'un
   événement, ajouter la garde de provenance (v1.9) :
   `NEVER outil WHEN UNTRUSTED(cible) AND NOT ATTESTED(cible, validateur)`.
   Le validateur **lève** pour refuser : un appel réussi atteste.
6. Pour tout outil dont l'effet ne doit pas se produire deux fois, déclarer
   côté hôte `idempotent=True` (et transmettre
   `current_action().idempotency_key`) ou un réconciliateur, et garder
   `NEVER outil WHEN tools.outil.in_doubt == true`.

## Pièges

- Une `POLICY` qui autorise par défaut et interdit par exception : l'ordre
  inverse est le seul sûr.
- Un `EFFECT` déclaré sur une croyance que l'agent écrit lui-même : la boucle
  se confirme toute seule.
- Une approbation dont l'expéditeur n'est pas comparé à l'expéditeur attendu.
- Une protection écrite en `ALLOW outil WHEN TRUSTED(x)` : elle est fermée
  tant qu'elle est seule, mais il suffit qu'une **autre** règle `ALLOW` sur le
  même outil s'applique pour la contourner. Un `NEVER` ne se rachète ni par
  un `ALLOW` ni par une approbation. Écrire l'interdit en `NEVER`.
- Croire qu'une garde `UNTRUSTED` éteint `W119` : T6 exige toujours le motif
  statique `ATTESTS`. Écrire les deux.
- Un validateur qui rend `{"ok": "no"}` au lieu de lever : il atteste la
  valeur qu'il voulait refuser.

## Validations attendues

- `agentl check` sans W ni E.
- `agentl verify` : T3 (action gouvernée) et T6 (provenance) démontrés.
- `agentl boundary` sans B008/B010/B012 sur les outils destructifs.
- `agentl test` : le scénario de refus échoue si la garde est retirée.
- Référence : `agentl-author/references/security-authoring.md`
  (provenance v1.9) et `runtime-semantics.md` §9–§10 (noyau, reprise).
