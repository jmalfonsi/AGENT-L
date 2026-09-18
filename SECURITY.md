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

## Ce que l'espace non fiable couvre — et ce qu'il ne couvre pas

Trois entrées de données externes sont liées sous leur nom **nu** dans
`state.untrusted`, consulté **en dernier** : la charge utile d'un événement ou
d'un message, le retour d'un `DELEGATE`, et — depuis la v1.8.2 — le retour
d'un `TOOL`. Aucune ne peut donc masquer une observation, une croyance ou une
entrée de mémoire homonyme, ni éteindre par ce biais un `NEVER` (SPEC §7.3).

Depuis la v1.9, s'y ajoute une **étiquette de provenance portée par chaque
valeur** (SPEC §35) : l'union de ses sources, qui survit aux recopies par
`SET`, aux `EFFECT`, à l'arithmétique et aux branches (flux implicite). Une
garde la lit par `UNTRUSTED(x)`, `LLM_DERIVED(x)`, `ATTESTED(x, outil)`.

Ce que cela ne dit pas :

- l'étiquette ne protège **que** si la politique la lit. Une donnée externe
  recopiée dans une croyance reste étiquetée non fiable, mais une garde qui
  ne consulte pas `UNTRUSTED(...)` décide dessus comme avant. C'est à
  l'auteur d'écrire le `NEVER` ;
- une attestation n'est pas une confiance : `ATTESTED(x, outil)` dit qu'un
  outil a accepté la valeur, pas qu'elle est fiable. Un validateur doit
  **lever** pour refuser ;
- une garde qui lit explicitement une forme préfixée (`payload.x`,
  `<outil>.<clé>`) décide **sur de la donnée externe**, en connaissance de
  cause. Le runtime ne l'interdit pas ;
- `W119` et `W125` restent des heuristiques de **noms** à l'analyse statique
  (`raw`, `log`, `message`, `body`, `content`) : elles signalent des cas
  probables, elles n'établissent pas une provenance, et elles ne créditent
  pas encore les gardes de provenance d'exécution.

## Ce qui est dans le périmètre

- Contournement du moteur de politiques : une action exécutée sans qu'un
  `NEVER`, un `DEFAULT DENY` ou une `REQUIRE APPROVAL` soit honoré.
- Contournement du noyau (v1.9) : un outil ou un sous-agent atteint sans
  permis, ou avec des arguments différents de ceux que la politique et
  l'approbateur ont jugés.
- Blanchiment de provenance : une transformation qui fait perdre à une valeur
  une source non fiable.
- Exécution durable : un effet exécuté deux fois après une reprise, alors
  que l'outil est déclaré idempotent ou réconciliable ; une action
  indéterminée relancée sans promesse de l'hôte.
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
- Un hôte qui se déclare idempotent (`idempotent=True`) sans honorer la clé :
  la promesse est la sienne, le runtime ne peut pas la vérifier.
- La réécriture délibérée d'un journal **durable** par quelqu'un qui peut
  écrire sur son répertoire : sa chaîne est un SHA-256 sans clé, qui détecte
  la corruption mais pas un faussaire qui recalcule toute la chaîne.
- L'absence d'**horodatage par un tiers** des journaux : la chaîne et les
  signatures HMAC/Ed25519 attestent l'intégrité et, avec une clé de confiance,
  l'authenticité ; elles n'attestent pas la date de l'exécution (SPEC §26.1).

## Versions supportées

Seule la dernière version publiée reçoit des correctifs. La branche de
développement courante cible `1.9.0` (le paquet reste numéroté `1.8.2`
jusqu'à la publication) ; il n'existe pas de branche de
maintenance longue.
