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
5. Traiter ce qu'un autre agent envoie comme **non fiable** (v1.9) : une
   charge utile `MESSAGE`, un retour `DELEGATE` et une lecture `SHARED`
   portent des sources non fiables. Le destinataire re-valide toute cible par
   un outil à lui : `NEVER outil WHEN UNTRUSTED(cible) AND NOT ATTESTED(cible, validateur)`.
6. Pour une exécution réellement concurrente, `agentl.aio.AsyncSociety` ;
   borner les boîtes de réception (`Limits(inbox_capacity=…)`). Pour survivre
   à un crash, `agentl run … --durable DIR` : un seul journal pour toute la
   société.

## Pièges

- Une charge utile de message qui porte le nom d'un fait déjà observé : elle
  est ignorée, et la trace le dit sans que personne ne la lise.
- Un `DELEGATE` circulaire : A attend B qui attend A.
- Une clé partagée écrite par deux agents au même tick : le dernier gagne en
  silence si aucun propriétaire n'est déclaré.
- Un destinataire qui agit sur la cible d'un message sans la re-valider : un
  agent compromis pilote alors la société.

## Validations attendues

- `agentl verify` sur chaque agent pris isolément, avant la société.
- `agentl run` avec un budget de ticks serré : la société doit converger.
- Un `SCENARIO` par échange, plus un où le message n'arrive jamais, plus un
  où il vient d'un expéditeur hostile (`GIVEN MESSAGE … FROM attacker`).
