# Politique de sécurité

## Signaler une vulnérabilité

**N'ouvrez pas d'issue publique.** Utilisez l'onglet *Security → Report a
vulnerability* du dépôt (avis de sécurité privé GitHub), ou à défaut un
courriel au mainteneur.

Merci d'inclure : version, programme `.agent` minimal reproduisant le
problème, hôte utilisé, et l'effet observé. Réponse visée sous 7 jours,
correctif ou plan sous 30 jours pour les défauts confirmés.

## Surface d'attaque, telle qu'elle est

Trois zones concentrent le risque, et il vaut mieux les nommer :

1. **Le studio** (`agentl studio`) écoute sur une socket, **exécute du code
   Python arbitraire** — les modules hôtes — et écrit sur le disque. Il est
   confiné à sa racine `--root` et n'est pas conçu pour être exposé sur un
   réseau non fiable. Ne l'exposez pas hors de `localhost` sans placer un
   proxy authentifié devant.
2. **Les modules hôtes** sont du Python que vous fournissez. AGENT-L ne les
   met pas en bac à sable : exécuter un `.agent` tiers avec son hôte revient à
   exécuter du code tiers.
3. **Les fournisseurs de modèles.** L'état observé et les descriptions
   d'outils présentes dans le programme partent chez le fournisseur configuré.
   L'import MCP n'inclut les descriptions tierces qu'avec l'option explicite
   `--unsafe-import-descriptions`. Rien ne part si vous utilisez `MockLLM`.
   Depuis la v1.8, `POLICY { NEVER SEND <chemin> }`
   (SPEC §32) retient un chemin sur **toutes** les sorties, `select_plan`
   compris — `USING` ne bornait que `REASON`. Ce que l'**hôte** envoie de
   son côté reste hors de portée du moteur : c'est la frontière du §28.

## Ce qui est dans le périmètre

- Contournement du moteur de politiques : une action exécutée sans qu'un
  `NEVER`, un `DEFAULT DENY` ou une `REQUIRE APPROVAL` soit honoré.
- Échappement de la racine `--root` du studio (lecture, écriture, exécution).
- Un verdict `verify` « ✔ DÉMONTRÉ » sur un programme qui viole le théorème
  correspondant — un faux négatif du vérificateur est une faille, pas un bug.
- Falsification de trace : une action effectuée mais absente du journal.
- Acceptation comme valide d'un journal dont la chaîne ou la signature HMAC /
  Ed25519 est invalide, lorsqu'une vérification correspondante est demandée.

## Hors périmètre

- Un hôte que vous écrivez et qui agit hors des outils déclarés — c'est
  précisément ce que `agentl boundary` signale, avec ses limites assumées
  (contrôle syntaxique, voir SPEC §28).
- Le comportement d'un modèle de langage tiers.
- L'absence d'**horodatage par un tiers** des journaux : la chaîne et les
  signatures HMAC/Ed25519 attestent l'intégrité et, avec une clé de confiance,
  l'authenticité ; elles n'attestent pas la date de l'exécution (SPEC §26.1).

## Versions supportées

Seule la dernière version publiée reçoit des correctifs. La branche de
développement courante cible `1.8.2` ; il n'existe pas de branche de
maintenance longue.
