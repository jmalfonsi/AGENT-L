# Society Architect

Concevoir une société AGENT-L minimale, explicite et vérifiable.

## Invariants

- Chaque agent a un objectif, une responsabilité et un hôte distincts.
- `MESSAGE` pour l'asynchrone, `DELEGATE` uniquement quand l'appelant doit
  attendre le résultat pour continuer.
- Toute clé `SHARED` a un propriétaire logique unique en écriture.

## Procédure

1. Écrire d'abord la carte des échanges : qui envoie quoi à qui, et ce que le
   destinataire doit pouvoir faire sans cette information.
2. Donner à chaque agent un `GOAL` qu'il peut atteindre seul ou en demandant :
   un objectif qui dépend d'un message jamais reçu bloque la société.
3. Borner chaque boucle d'échange. Deux agents qui se relancent sans borne
   consomment tous les ticks disponibles.
4. Nommer les clés `SHARED` par leur propriétaire, pour rendre les conflits
   d'écriture lisibles dans la trace (`⇄`).

## Pièges

- Une charge utile de message qui porte le nom d'un fait déjà observé : elle
  est ignorée, et la trace le dit sans que personne ne la lise.
- Un `DELEGATE` circulaire : A attend B qui attend A.
- Une clé partagée écrite par deux agents au même tick : le dernier gagne en
  silence si aucun propriétaire n'est déclaré.

## Validations attendues

- `agentl verify` sur chaque agent pris isolément, avant la société.
- `agentl run` avec un budget de ticks serré : la société doit converger.
- Un `SCENARIO` par échange, plus un où le message n'arrive jamais.
