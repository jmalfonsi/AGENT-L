# Journal des changements

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.3/).
Versionnage sémantique : la **grammaire du langage** est l'API publique, au
même titre que les codes de diagnostic `V…` / `W…` / `E…` / `B…`.

## [1.9.0] — non publié · le noyau, la reprise, la provenance

Cinq chantiers, un fil : garder le caractère déclaratif et fail-closed du
langage tout en le rendant exploitable là où LangGraph et PydanticAI étaient
en avance. Le paquet est numéroté 1.9.0 ; le contrat d'auteur passe à
**2.10.1**. La trace est inchangée : les
journaux dorés de la v1.8.2 (`tests/golden/v1.8.2`) se rejouent à l'octet, en
synchrone comme en asynchrone.

### Ajouté

- **Noyau de confiance** (`agentl/kernel/`, SPEC §34). L'autorisation et
  l'appel à l'hôte quittent `runtime.py`. Le noyau fige la proposition,
  évalue la politique, soumet une copie à l'approbateur, puis émet un
  **permis** à usage unique lié au condensat de l'action. `Host.invoke`,
  `AsyncHost.invoke` et `host.subagents` exigent ce permis. Dix invariants
  numérotés (`tests/test_kernel_invariants_aaa.py`), dont une vérification
  **sur le code source** que seul le noyau appelle l'hôte. Liste des fichiers
  de confiance : `agentl.kernel.TCB_FILES`, bornée par un test.
- **Exécution durable** (`agentl/durable.py`, SPEC §36). Intention écrite et
  synchronisée avant chaque appel, résultat après, point de contrôle à chaque
  tick, reprise par re-dérivation du journal. Identifiant d'action et clé
  d'idempotence stables (`current_action()`). Une intention sans résultat est
  relancée avec la même clé (`@host.tool(…, idempotent=True)`), réconciliée
  (`@host.reconciler`), ou déclarée **indéterminée** :
  `tools.<outil>.in_doubt = true`, lisible par la politique. CLI :
  `agentl run --durable DIR [--run-id]`, `agentl durable status|export DIR`.
  Stockages : fichier (fsync), SQLite, mémoire.
- **Exécution asynchrone** (`agentl/aio.py`, SPEC §37) : `AsyncHost`,
  `AsyncRuntime`, `AsyncSociety` (agents réellement concurrents par tours
  synchronisés), `Limits` (concurrence bornée, contre-pression, délais,
  capacité des boîtes de réception), annulation coopérative. Un outil qui
  dépasse son délai est **indéterminé**, jamais réussi. Pont MCP
  asynchrone (`AsyncMCPHost`, `connect_async`).
- **Provenance portée par les valeurs** (`agentl/kernel/provenance.py`, SPEC
  §35). Chaque valeur porte l'union de ses sources (`OBSERVED`, `LLM`,
  `TOOL`, `MESSAGE`, `EVENT`, `DELEGATE`, `SHARED`, `EXTERNAL`, `UNKNOWN`…),
  y compris par flux implicite. Fonctions de garde `UNTRUSTED`, `TRUSTED`,
  `LLM_DERIVED`, `ATTESTED(x[, outil])`, `ORIGIN`, et le chemin `action`.
  L'hôte peut marquer une lecture non fiable (`untrusted(...)`), jamais
  l'inverse.
- **Validation externe** (SPEC §38) : tests de propriétés sur la sémantique
  de Kleene, la politique, le solveur, le parseur et les permis
  (`AGENTL_PROPERTY_RUNS`) ; compatibilité des journaux entre versions ;
  modèle TLA+ du protocole du noyau vérifié par TLC avec mutants
  (`docs/formal/`, `tools/check_formal.py`) ; **banc comparatif** contre
  LangGraph, PydanticAI et CrewAI (`bench/frameworks/`, `run.py --check`).
- Diagnostics `E015` (fonction inconnue dans une expression) et `E016`
  (fonction de provenance mal employée).
- Skill `agentl-author` : `references/scenarios.md` (il était référencé et
  absent, ce qui rendait `sync_grammar.py --check` rouge), sections v1.9 dans
  `security-authoring.md`, `runtime-semantics.md` (§9–§11), `authoring.md`
  et `composition.md` ; le contrat généré liste les fonctions reconnues,
  extraites de l'analyseur.

### Modifié — à lire avant de mettre à jour

- **`host.invoke(...)` hors du noyau lève `PermitError`.** Un test qui
  appelait l'hôte directement doit appeler la fonction enregistrée
  (`host.tools["x"](**args)`) ou `agentl.kernel.testing.dispatch`.
- **`E009` ne vise plus que les outils.** `CONFIDENCE(x)`, `P(h)`, `len(xs)`
  étaient signalés à tort. En contrepartie, un nom de fonction inconnu, qui
  passait sans un mot, est désormais `E015`.
- La sémantique d'approbation (`approval_granted`, `APPROVAL_WORDS`) est
  réexportée par `agentl.host` depuis le noyau.

### Corrigé

- **Comparaison indécidable dans une garde.** `cpu.load > 90` avec un
  capteur à `unavailable`, `"N/A"` ou `NaN` s'évaluait à faux, ce qui
  **désarmait** un `NEVER`. Une comparaison d'ordre entre valeurs non
  ordonnables, ou un `IN` sur un non-conteneur, est désormais indéterminée.
  Trouvé par le test de propriété P1.
- **Fonction épistémique sur une croyance ou une hypothèse inconnue** :
  `CONFIDENCE(x)` / `P(h)` rendaient une valeur par défaut au lieu d'une
  garde indéterminée.
- **Solveur** : les rangs ordinaux (`LOW`…`CRITICAL`) et les nombres forment
  deux échelles disjointes, comme dans l'évaluateur. `LOW <= 7` était traité
  comme `2 <= 7`, ce qui faussait des verdicts de satisfiabilité.
- **Fidélité du rejeu** : une exception journalisée est reconstruite dans sa
  classe d'origine (y compris `KeyError`) ; le verdict « réponse absente »
  de l'oracle est journalisé, et `reason.degraded` se rejoue à l'identique.
- **Studio : deux courses sur « run en cours ».** L'état suivait
  `thread.is_alive()` : faux entre la création du thread et son démarrage
  (un second `run()` concurrent passait, deux runs partageaient la session),
  encore vrai après l'émission de `run.finished` (une relance immédiate était
  refusée — l'échec intermittent de la CI). Un indicateur explicite, posé et
  levé sous verrou, les ferme ; deux tests déterministes les reproduisent.
- **CI rouge depuis la publication du dépôt.** Starlette 1.x ne retombe plus
  sur un `httpx` transitif pour `TestClient` : 24 tests du Studio échouaient
  et la couverture passait sous 90 %. Nouvel extra `test` (`pytest`,
  `pytest-cov`, `httpx2`, `httpx`), utilisé par la CI.
- **EBNF** : la grammaire, passée en v1.9, nomme les fonctions de provenance
  (`ORIGIN`, `UNTRUSTED`, `TRUSTED`, `LLM_DERIVED`, `ATTESTED`) comme elle
  nommait les fonctions épistémiques. Contrat d'auteur 2.10.1.
- **Agents de banc mis au niveau des durcissements antérieurs.**
  `hr_employee_request_routing` figeait l'adresse par `SET route_to` : depuis
  la v1.8.2 la sortie de `route_for` ne la masquait plus, et toute demande
  suivant une demande de paie partait vers la paie (RIB et accès IT compris).
  L'agent de cave d'AITESTPLATFORM déclare désormais les accusés de ses
  outils dans `GIVEN` (TST-05) et justifie ses levées `boundary`.

### Ajouté — outillage de livraison

- **Porte CI du modèle formel** : un job lance `tools/check_formal.py` (TLC
  épinglé par empreinte) — le modèle et ses cinq mutants sont vérifiés à
  chaque commit.

### Limites connues

- `W119`, `W125` et T6 ne créditent pas encore les gardes de provenance :
  garder le motif statique `ATTESTS` en plus.
- La chaîne du journal durable est un SHA-256 sans clé : elle détecte la
  corruption, pas un faussaire qui recalcule toute la chaîne.
- Tout appel d'outil réussi atteste ses arguments : un validateur doit lever
  pour refuser.
- Un `SET` sur un chemin qu'un `OUTPUT` d'outil écrit aussi fige ce chemin :
  les sorties suivantes ne le masquent plus (v1.8.2), et aucun diagnostic ne
  le signale encore.
- La propagation des étiquettes de provenance (`_label_args`, contexte de
  contrôle) vit dans `runtime.py`, hors de la TCB mesurée : une politique de
  provenance dépend aussi de ce code.

## [1.8.2] — non publié · la preuve cesse de se taire

### Suite de l'audit général comparatif — contrat auteur 2.9.0

Cinq constats d'un audit externe, vérifiés puis corrigés. Un seul touchait la
sémantique.

**Le retour d'un outil était la dernière frontière externe traitée comme de la
donnée fiable.** Les charges utiles d'événement et le retour d'un `DELEGATE`
allaient déjà dans `state.untrusted` — consulté en dernier — depuis la v1.6 ;
les clés `OUTPUT` d'un `TOOL`, elles, étaient liées sous leur **nom nu** dans
les locales, c'est-à-dire en tête de résolution. Or c'est par là qu'arrive
l'injection **indirecte** : page web, ticket, corps de courriel, RAG. Un outil
qui rapportait une clé `criticality` valant `LOW` éteignait donc un
`NEVER … WHEN criticality == CRITICAL` gardé par une observation contraire.
Le contrat `OUTPUT` bornait la forme de la réponse, jamais sa provenance. Les
formes préfixées restent inchangées — `result.<outil>.<clé>` dans les locales,
`<outil>.<clé>` dans le monde — et gardent leur provenance lisible ; seul le
nom nu change d'espace. Test de mutation joint : rétablir l'ancienne liaison
ramène l'exploit. Contrat d'auteur `2.9.0` : la grammaire ne bouge pas, mais
une garde écrite sur le nom nu d'une sortie d'outil ne lit plus la même chose.

**La CI n'était pas publiée.** `.gitignore` contenait `.github/` : le workflow
existait en local, complet, et n'avait donc jamais tourné sur la révision
publiée. Il est publié. Les étapes qui visaient `AGENTIC_SIMULATOR`, absent du
dépôt, sont retirées — une CI ne contrôle que ce que la révision contient.

**La dette de revue Boundary était désynchronisée** : `examples/gemini_llm.py`
avait bougé de quatorze lignes, ce qui faisait sortir
`tools/check_boundary_examples.py` en erreur sur 33 « régressions » qui
n'étaient que des décalages, et signalait 33 diagnostics résolus à retirer.
Les mêmes diagnostics, aux lignes actuelles : aucune dette n'est levée.

**Défaut découvert en publiant la CI, non corrigé et nommé** : le gabarit
`runtime_llm.py` du Studio ne passe pas `agentl boundary` depuis que Boundary
suit les modules locaux (12 diagnostics `B001`/`B006` d'aiguillage de
fournisseur). La porte `boundary` du brouillon rend donc `failed` et trois
tests du Studio échouent. Ils sont écartés de la CI par un `--deselect`
explicite, daté et documenté dans `docs/QUALITY.md` — une dette, pas une
acceptation.

`SECURITY.md` dit maintenant ce que l'espace non fiable couvre **et ce qu'il
ne couvre pas** : ce n'est pas un marquage de teinte porté par les valeurs, et
`W119`/`W125` restent des heuristiques de noms. Le modèle Anthropic par défaut
passe à `claude-sonnet-5`.

### Suite des audits CHECK et TEST — contrat auteur 2.8.1

Constats vérifiés par reproduction : E012/E013, kwargs dupliqués, analyses de
flot W102/W135, approbations conditionnelles et provenance LLM corrigés.
TEST respecte la boucle runtime, UNKNOWN et les invariants entre actions.
OUTPUT, OUTCOME multiples et choix de plan deviennent explicites. Ajout de
GIVEN EVENT/MESSAGE et des assertions de trace ; suite vide en erreur sauf
--allow-empty. Exemples migrés avec fixtures explicites. La portée des INPUT
dans les EFFECT runtime est également corrigée. Voir docs/audit-followup.md
pour les nuances de l'audit et les limites conservées.


Cinq points relevés par un **audit externe** conduit sur la v1.8. Trois
tenaient tels quels, un tenait à moitié, un tapait à côté de sa cible — et
c'est le premier qui comptait. La grammaire ne bouge pas ; le contrat d'auteur
passe à `2.5.0` pour les nouveaux codes `W135` et `V114`, puis à `2.5.1`
pour rendre explicite la sémantique de panne P0 de `REASON`, à `2.5.2`
pour aligner Scenario et Autoloop sur le même état initial, et à `2.5.3`
parce qu'un `TOOL` doit désormais déclarer dans son `OUTPUT` toute clé qu'il
renvoie.

Le fil commun des cinq : **une garantie qui disparaît sans un seul signal**.
Le vérificateur affichait DÉMONTRÉ sur un site qu'il n'avait pas su ranger, le
solveur renonçait en silence, un capteur muet laissait passer une
vérification, une typo déclenchait un plan, et l'import MCP prenait par défaut
le texte d'un tiers. Aucun de ces cinq n'*ouvrait* une action interdite — le
moteur de politiques évalue sur l'état vivant et bloquait dans tous les cas —
mais tous rendaient l'agent moins lisible que ce qu'il prétendait être.

Un sixième défaut, découvert ensuite par exécution différentielle, était en
revanche un **P0 de sûreté** : une sortie numérique `NaN` et le silence d'un
oracle pouvaient rendre fausse une garde numérique de `NEVER` et laisser
aboutir l'action. La v1.8.2 ne doit pas être publiée sans le correctif et les
régressions ci-dessous.

### Audit Boundary — BND-A01 à BND-A07

- Analyse transitive des imports locaux, provenance fichier/ligne, alias et
  constantes résolues sans exécution de Python ; exclusions affichées et
  options `--project-root` / `--external-policy`.
- Filtres booléens non exemptés, comparaisons symétriques, petits seuils et
  collections de constantes contrôlés ; dérogations limitées aux commentaires.
- B008–B013 utilisent des signaux AST et un flot local par site d'action,
  au lieu de recherches textuelles globales. Leurs limites restent explicites.
- B014 signale les registres inconnus comme `INCOMPLETE / UNVERIFIABLE` ; B016
  rend visibles les imports/code dynamiques et les sources non analysables.
- Corpus adversarial `tests/test_boundary_audit.py`, suivi exact des diagnostics
  d'exemples en CI et documentation `docs/BOUNDARY.md`. Le suivi constitue une
  dette de revue, sans modifier le verdict bloquant de Boundary.
- Contrat d'auteur 2.7.1 : nouvelles options et nouveau diagnostic, sans
  changement de grammaire ; modules d'analyse inclus dans les empreintes.

### Sécurité P0 — une sortie numérique invalide ne désarme plus une politique

- `NaN`, `+inf` et `-inf`, sous forme numérique ou textuelle, sont rejetés à
  la coercition et au contrôle de domaine. Ils ne sont jamais conservés dans
  l'état du runtime.
- Un champ `PRODUCE` absent sans `DEFAULT` explicite devient `UNDEFINED` ; le
  moteur de politiques l'évalue comme `UNKNOWN`, donc applique un
  `NEVER`/`DENY`, refuse d'en déduire un `ALLOW` et demande l'approbation pour
  `REQUIRE APPROVAL`.
- Un `DEFAULT` explicite et valide reste le comportement déclaré en panne. Si
  le champ garde une action, `W134` signale désormais aussi l'absence de ce
  contrat explicite, en plus d'un repli qui ne déclenche pas l'interdit.
- `tests/test_numeric_policy_safety_aaa.py` couvre l'appel réel au seam
  Runtime/Policy pour les valeurs non finies, l'oracle muet et le repli sûr.

Le vérificateur et le runtime n'ont pas été déclarés équivalents pour autant :
la garantie est bornée par les tests différentiels et les limites du §28.

### Sécurité P0 — la sortie d'un outil ne peut plus forger l'état

- Le runtime n'admet dans l'état que les clés déclarées par `OUTPUT`, après
  validation du type et rejet des nombres non finis. Les clés supplémentaires
  sont supprimées et comptées.
- Une réponse absente, scalaire, incomplète ou mal typée produit une panne de
  contrat ; ses `EFFECT` ne sont jamais présumés vrais.
- La régression provient d'un outil qui renvoyait
  `operator.confirmed=yes`, masquait l'observation humaine et ouvrait une
  alerte publique. `tests/test_tool_output_safety_aaa.py` verrouille cette
  frontière ainsi que la panne de la file d'événements.
- `AITESTPLATFORM/adversarial_protocol.py` applique une matrice de fautes à
  un agent réel produit par la fabrique et refuse la certification tant que
  trois exigences de reprise durable restent insatisfaites.
- Contrat d'auteur `2.5.3` : la contrainte est visible côté écriture — toute
  clé qu'un outil renvoie doit figurer, typée, dans son `OUTPUT`.

### Architecture agentique — une seule autorisation, un seul tick zéro

- Outil et délégation traversent le même Module d'autorisation : politique
  évaluée une fois, exception fermée, approbation isolée par copie défensive,
  métriques et état d'audit cohérents. Les Adapters gardent leurs contrats
  propres d'invocation et de résultat.
- Une approbation ne peut plus muter les arguments après leur contrôle puis
  faire invoquer une action différente de celle jugée par la politique.
- Scenario et Autoloop ne rappellent plus `_bootstrap`. Le routage d'un état
  initial vers croyance ou monde est localisé dans le Runtime.
- Un `GIVEN` qui vise une `BELIEF` remplace désormais réellement sa valeur au
  tick zéro, y compris pendant `initial_expectations` ; classification et
  exécution du scénario observent donc le même état.
- `tests/test_governed_action_aaa.py` et la régression `GIVEN`/`BELIEF`
  verrouillent ces Seams par des appels réels.

### Corrigé — le vérificateur suit les écritures d'état (`agentl/verifier.py`)

Le défaut de preuve le plus sérieux. Une condition de chemin dit « pour
arriver ici, ceci était vrai » ; `_walk_guarded` la lisait comme « ceci est
vrai ici ». Un
`SET` intercalé entre la garde et l'appel n'était pas vu :

```agentl
IF asset.criticality == LOW THEN {
    SET asset.criticality = CRITICAL
    isolate_endpoint()          // NEVER … WHEN criticality == CRITICAL
}
```

Le solveur voyait `LOW ∧ CRITICAL`, concluait à l'insatisfiabilité, et le site
ne tombait **ni** dans « branche morte » **ni** dans « exposition ». T1
s'affichait DÉMONTRÉ, sans un mot, pendant que le runtime bloquait l'appel à
chaque tick. La preuve était muette exactement là où l'agent calait.

Le parcours est désormais sensible au flot, dans les deux sens et sans jamais
renforcer les prémisses à tort :

- une garde est **datée** du rang d'écriture en vigueur quand elle a été
  franchie, et retirée dès qu'un chemin qu'elle mentionne est réécrit après
  cette date — affaiblir les prémisses ne peut que réduire ce qu'on démontre ;
- un `SET` d'une constante sur un chemin **durable** devient au contraire un
  fait invocable. Durable exclut tout ce qu'une re-perception peut contredire :
  chemins observés, postconditions d'`EFFECT`, capteurs qu'un `REQUIRES` ou un
  `VERIFY` rafraîchit, champs `PRODUCE`, retours de `DELEGATE` ;
- les conditions d'entrée d'un plan subissent la même péremption que ses
  gardes internes — un `SET` invalide aussi le `WHEN` qui a déclenché le plan ;
- à la sortie d'un `IF`, ce que l'une ou l'autre branche écrit devient inconnu ;
  dans un corps de boucle, ce que le corps écrit est inconnu **dès l'entrée**,
  une itération précédente ayant pu le faire.

**T1 rend aussi ses comptes.** Un site permis — celui dont la condition de
chemin *réfute* la garde du `NEVER` — est le bon cas, et il est désormais
compté. La somme boucle : `sites examinés = morts + exposés + permis`. Un site
qui ne tombait dans aucune case disparaissait du rapport.

### Ajouté — `V114`, l'abandon du solveur devient un fait (`agentl/solver.py`)

Au-delà de `MAX_CLAUSES` clauses en DNF, le solveur renonce et répond
« satisfiable ». C'est **sûr** — il ne réfute jamais à tort — mais ce n'est pas
neutre : à partir de là il ne réfute plus rien, et le théorème qui s'appuie
dessus n'est plus démontré, seulement non contredit. Afficher DÉMONTRÉ dessus
était le mensonge que tout le reste du projet cherche à éviter.

`watch_overflow()` observe les abandons d'un bloc ; T1 émet `V114` sur le site
concerné et rend **◐ BORNÉ** au lieu de **✔ DÉMONTRÉ**. Aucun agent livré ne
déclenche l'abandon : la borne n'est pas serrée, elle est seulement muette.

### Ajouté — `W135`, le piège du `!=` sur un chemin absent (`agentl/analyzer.py`)

Les gardes de **politique** passent par Kleene : un chemin absent y rend
`UNKNOWN`, et `NEVER`/`DENY`/`REQUIRE APPROVAL` s'appliquent quand même. Les
gardes de **déclenchement** — `WHEN` de plan, `IF`, règle `DECIDE` — non :
elles passent par l'évaluateur ordinaire, où `!=` est le complément de `==`.
Donc `PLAN escalader WHEN incidnet.severity != low` se déclenche à chaque tick
sur l'ignorance.

Le sens du défaut fait sa gravité : une typo dans un `==` ne déclenche rien et
se voit au premier essai ; dans un `!=` elle déclenche *tout*, et ressemble à
un agent qui marche.

Le contrôle est serré volontairement — un seul opérateur, un chemin pointé
comparé à une constante, et aucun préfixe que le runtime publie ou qu'un
`FOREACH` projette. Zéro déclenchement sur l'ensemble des agents livrés : un
avertissement qui crie à tort n'est plus lu.

### Sécurité — un capteur muet ne valide plus une action (`agentl/runtime.py`)

`_refresh_for` re-perçoit les capteurs avant un `VERIFY` ou un `REQUIRES`. Si
la relecture ne rendait rien, la croyance d'**avant l'action** subsistait et la
vérification statuait dessus : un échec d'outil déguisé en succès, au moment
précis où l'on cherchait à savoir si l'action avait mordu.

Le chemin est désormais invalidé (`State.invalidate`, distinct de
`set_world(path, None)` — `None` est une valeur d'état légitime, l'absence
n'en est pas une), `sensors.<chemin>.available` passe à faux, et le régime
strict de `VERIFY` échoue fermé. Le repli `ON UNKNOWN DEGRADE` ne s'applique
délibérément **pas** ici : vérifier une action contre une valeur qu'on a
soi-même posée revient à se donner raison. La trace le dit quand le cas se
présente.

### Sécurité — l'import MCP ne prend plus les descriptions (`agentl/cli.py`)

Les descriptions d'outils MCP sont du texte rédigé par un tiers qui atteint le
contexte du modèle : la surface d'injection indirecte du protocole.
`suspicious_description` en attrape les formes grossières et n'a jamais
prétendu davantage — une consigne tournée en persona, encodée en homoglyphes
ou simplement polie passe au travers. Une heuristique faillible peut signaler ;
elle ne peut pas être ce qui décide.

`agentl mcp import` n'importe donc plus que les noms. Importer les descriptions
demande `--unsafe-import-descriptions`, dont le nom dit ce qu'il engage.
`--strip-descriptions` reste accepté comme synonyme du défaut : les scripts
existants continuent de marcher, et personne ne passe en silence du mode sûr
au mode risqué.

### Ajouté — `run --events`, la trace lisible par un outil (`agentl/cli.py`)

- `agentl run X.agent --events F.jsonl` et `agentl replay J.json --events
  F.jsonl` écrivent chaque événement de trace sur une ligne JSON — `seq`,
  `agent`, `tick`, `kind`, `text`, `detail` — vidée aussitôt écrite.
- Le Studio (`STUDIO/`) relisait la trace texte au glyphe près : un changement
  de mise en forme cassait sa chronologie sans bruit. Il consomme désormais ce
  fichier, et ne garde le découpage du texte qu'en repli.
- Le fichier se branche sur le `sink` des traces sans remplacer celui d'un
  hôte, recopie ce qui a été journalisé avant le branchement, et s'ouvre avant
  le premier tick : un chemin inutilisable est refusé (code 2) avant que
  l'agent n'agisse. Le runtime n'est pas modifié.

## [1.8.1] — non publié · résilience

Trois défauts trouvés par une **épreuve de panne** — oracle coupé, outils en
503 sur une tâche réelle — et non par une relecture. Le runtime survivait déjà
aux deux pannes, ce qui était l'acquis ; ce qui manquait, c'est ce qu'il en
faisait. La grammaire ne bouge pas ; le contrat d’auteur passe à `2.4.0` pour
les nouveaux codes `W133` et `W134`.

Une **relecture d'après-coup** a ensuite montré que le premier correctif
n'était pas utilisable : les faits de panne étaient publiés, et l'idiome
évident pour les lire ne mordait pas. C'est l'objet de la section « Corrigé ».

### Ajouté
- **Reprise sur panne transitoire** (`agentl/llm.py` : `is_transient`,
  `retry_after`, `call_with_retry` ; branchée sur `AnthropicLLM` et
  `examples/gemini_llm.py`). Un 429 ou un 503 passager dégradait aussitôt le
  `REASON` vers ses défauts : sûr, mais on perdait le raisonnement pour rien.
  Le rejeu est **borné deux fois** — nombre d'essais *et* budget de temps,
  vérifié avant de dormir — avec recul exponentiel plafonné et bruit
  décorrélé. Il ne rejoue **jamais** une erreur de contrat (400/401/403/404) :
  insister sur un 401 ne fait que retarder l'échec en brûlant du quota. Le
  délai demandé par le fournisseur est respecté, en-tête `Retry-After` d'abord
  puis indication dans le corps. Réglable par `AGENTL_LLM_RETRIES` et
  `AGENTL_LLM_RETRY_BUDGET`.
- **Disjoncteur d'outil** (`Runtime(tool_breaker=…, tool_cooldown=…)`). Un
  service mort ne devient pas vivant parce qu'on insiste : un programme à
  curseur martelait une API tombée à chaque tick — 122 tentatives observées,
  ramenées à 32 sur la même épreuve. Au-delà de trois échecs consécutifs
  l'outil n'est plus appelé, et la **demi-ouverture** laisse repasser un seul
  sondage tous les cinq ticks pour que l'agent reparte seul quand le service
  revient. `tool_breaker=0` restitue le comportement historique.
- **L'indisponibilité d'un outil est un fait**, publié comme l'est déjà celle
  d'un capteur — `tools.<nom>.available` et `tools.<nom>.failures`, lisibles
  par une garde : `NEVER close_batch WHEN tools.update_row.available == false`.
  Le disjoncteur ne décide rien à la place du programme ; il cesse de marteler
  et publie l'état.
- **Un oracle muet ne passe plus pour un oracle neutre** : `reason.degraded`
  et `reason.missing` posés après chaque `REASON`, métrique `reason_degraded`,
  ligne nommée dans la trace — de type `LLM` et **non** `ERROR`, parce qu'un
  oracle qui se tait n'est pas une faute du runtime et que `autoloop` juge son
  invariant « aucune erreur d'exécution » sur ce type.
  Avant le correctif P0 de la v1.8.2, `_coerce` comblait les champs absents par
  des valeurs implicites et une réponse **tronquée** devenait indiscernable
  d'une réponse complète et neutre. Désormais, pour un champ absent ou
  numérique non fini, seuls les `DEFAULT` explicites sont substitués ; sans
  eux, la valeur devient `UNDEFINED`.
- **`W133`** — avertissement statique au-delà de huit champs `PRODUCE`, avec
  la conduite à tenir : découper le `REASON` ou garder les seuils par
  `reason.degraded`.
- **`W134`** — le `DEFAULT` explicite d'un champ `PRODUCE` doit
  **déclencher** l'interdit qu'il garde. Depuis la v1.8.2, le diagnostic
  signale également un champ gardant une action sans `DEFAULT` : l'exécution
  reste fermée grâce à `UNDEFINED`/`UNKNOWN`, mais le contrat de panne doit
  être écrit. Le contrôle simule le repli déclaré et tombe dès qu'il devient
  permissif.
- Métriques `tool_failures`, `circuit_open`, `reason_degraded`.
- `tests/test_resilience_aaa.py` couvre notamment le budget de temps qui prime
  sur le nombre d'essais, le refus de rejouer un 401, la demi-ouverture, et le
  fait qu'un refus de disjoncteur ne se compte **pas** comme un refus de
  politique : `blocked` compte les interdits, les confondre fausse l'audit.

### Corrigé
- **Une garde sur un fait booléen ne mordait pas** (`agentl/state.py`,
  SPEC §7.7). Le moteur pose `sensors.<chemin>.available`,
  `tools.<nom>.available`, `reason.degraded` et `last_action.blocked` en `bool`
  Python ; un `.agent` ne sait écrire que des symboles ; l'égalité comparait
  les deux par leur texte. `str(False)` valant `"False"`, jamais `"false"`,
  l'idiome évident — celui que la SPEC elle-même donnait en exemple —

      NEVER close_batch WHEN tools.update_row.available == false

  était **faux alors que l'outil était bien indisponible**. `check` ne
  signalait rien, l'auteur avait toutes les raisons de croire la panne gardée,
  et l'interdit ne s'appliquait pas. C'est le mode de défaillance le plus
  coûteux que le langage puisse produire — une garantie qui disparaît sans
  émettre un seul signal — et il touchait `sensors.*.available` **depuis son
  introduction en v1.6**. L'égalité fait désormais le pont dans les deux
  vocabulaires du langage (`true`/`yes`, `false`/`no`) ; un symbole hors de ce
  vocabulaire n'est égal à aucun booléen ; `bool` héritant de `int`, le test de
  type reste strict pour que `1 == true` ne devienne pas vrai au passage ; et
  un chemin jamais posé garde son statut d'indéterminé, donc appliquant
  l'interdit.
- `AnthropicLLM` plafonnait sa sortie à 1024 jetons en dur — trop court dès
  qu'un `PRODUCE` dépassait quelques champs, ce qui déclenchait précisément la
  troncature ci-dessus. Désormais `max_tokens` en paramètre, défaut 4096,
  réglable par `AGENTL_MAX_OUTPUT_TOKENS`.

## [1.8.0] — non publié · outillage

La grammaire ne bouge pas d'un caractère — le niveau de langage reste `1.8`,
d'où l'absence de version neuve : c'est l'**outillage** qui gagne une porte.
Les quatre contrôles existants disent si un programme est **recevable** ; aucun
ne disait s'il tient sur **d'autres données que celles que l'auteur a
écrites**.

### Ajouté
- **`agentl autoloop X.agent`** (`agentl/autoloop.py`, README §« Tenir sur
  d'autres données », `SKILLS/agentl-author/references/autoloop.md`) : franchit
  la barrière (analyse, preuve, frontière, scénarios), puis rejoue chaque
  `SCENARIO` sur des mondes dérivés de son `GIVEN` — capteur muet, valeur au
  bord d'un seuil que le programme nomme lui-même, opérateur qui refuse. Avec
  `--model gemini-*|claude-*`, la boucle corrige elle-même ; elle s'arrête sur
  un plafond de tentatives, un budget de temps **ou** une absence de progrès,
  parce que « boucler jusqu'à 100 % » n'est pas une condition d'arrêt. Sans
  `-o`, rien n'est écrit.
- **L'oracle est l'invariant**, jamais le modèle qu'on corrige : quatre
  invariants universels (aucune erreur d'exécution, aucun `VERIFY` en échec,
  aucun outil interdit *sans condition* exécuté, aucune approbation contournée)
  plus les `EXPECT` déjà vraies au tick 0, que `agentl test` traite déjà en
  invariants. Une éventualité n'est jamais jugée sur un cas dérivé : son
  avènement dépend des données qu'on vient de changer.
- **Lot retenu** (`--holdout`, 0,3 par défaut) : jamais exécuté pendant la
  boucle, jamais cité dans une consigne de correction, ouvert à la fin. D'où un
  **troisième code de sortie** : `3` = appris par cœur — 100 % sur les cas vus,
  des ruptures sur le lot retenu. Confondre ce cas avec un simple échec ferait
  passer le plus dangereux des deux pour le plus bénin.
- **Cinquième barrière, « invariants (monde déclaré) »** : `agentl test` juge
  l'attente, pas la façon d'y arriver. Un scénario peut être vert alors que
  l'exécution a levé une erreur en chemin — rien ne le disait jusqu'ici.
- **Second temps `--host-pass`** : exécution contre l'hôte réel, en lecture
  seule (tout outil à effet de bord ou `RISK HIGH`/`CRITICAL` neutralisé,
  approbation jamais accordée). Le rapport nomme les outils neutralisés : leur
  `EFFECT` n'ayant pas été produit, la dérive d'effet **n'est pas jugée** dans
  cette passe, et un vert ne doit pas se lire « les `EFFECT` sont vrais ».

### Modifié
- `SKILLS/agentl-author` : contrat d'écriture **2.1.0** (grammaire inchangée,
  surface auteur élargie d'une commande et d'une porte) ; `agentl/autoloop.py`
  entre dans l'outillage verrouillé par `grammar-lock.json`.

## [1.8.0] — non publié · langage

Deux faiblesses relevées par la revue du 2026-08-02, toutes deux issues d'un
**trou de déclaration** plutôt que d'un contournement : le programme n'avait
aucun moyen de dire ce qu'il voulait, et le runtime choisissait à sa place.

### Ajouté
- **`POLICY { NEVER SEND <chemin> }`** (SPEC §32) : interdiction de sortie
  vers le fournisseur de modèle, portant sur **toutes** les sorties. `USING`
  bornait ce qu'un `REASON` montre ; il n'a jamais rien borné de
  `select_plan`, qui transmet l'intégralité des croyances. Un programme ne
  pouvait donc pas retenir un secret : la seule barrière disponible ne
  couvrait pas le point de sortie le plus large. Correspondance par préfixe de
  segment (`credentials` couvre `credentials.token`, pas
  `credentials_publics`), valeur remplacée par `⟦retenu⟧` — la clé reste
  visible, pour que le modèle sache qu'il raisonne sur un état amputé sans
  apprendre ce qui lui manque — et retenue **tracée** au rang d'un blocage de
  politique.
- Diagnostic `W130` : `USING` listant un chemin qu'un `NEVER SEND` retient.
  Deux déclarations qui se contredisent ; le runtime tranche du côté sûr,
  mais le `REASON` raisonne alors sur un trou dont son auteur se croit
  protégé.
- **`OBSERVE … ON UNKNOWN ESCALATE | DEGRADE <valeur>`** (SPEC §31) : la
  conduite face à un capteur muet devient déclarée. Jusqu'ici le silence d'un
  capteur ne produisait aucun événement — le chemin restait indéfini, les
  gardes échouaient fermé (bonne direction) et l'agent se bloquait sans qu'une
  ligne de trace dise pourquoi. Une panne de capteur et un chemin mal
  orthographié produisaient le même silence. `ESCALATE` nomme la panne et
  réveille l'humain **une fois par panne** — une alerte répétée à chaque tick
  n'est plus lue. `DEGRADE` substitue un repli déclaré, à la confiance `0.3`
  et sous la source `fallback` : une garde peut distinguer une hypothèse d'une
  mesure. Ne rien déclarer garde exactement le comportement d'avant.
- `sensors.<chemin>.available` : la panne d'un capteur est un fait du monde,
  lisible par une garde — le programme peut en décider lui-même.
- Diagnostics `W131` (chemin gardant un interdit sans conduite `ON UNKNOWN` —
  la disponibilité mérite d'être décidée, pas subie) et `W132`, plus grave :
  un `DEGRADE` sous un interdit fait juger la règle sur une valeur déclarée,
  et un repli mal choisi rouvre exactement la faille §7.1, cette fois avec la
  bénédiction du programme. Le repli reste permis ; il ne doit pas être
  silencieux. `W131` se déclenche sur **onze exemples du dépôt**.
- `tests/test_security_disclosure_aaa.py` (22 tests), avec un test de mutation
  par correctif — on rétablit l'ancien comportement et l'on vérifie que le
  défaut revient.
- Métriques `redactions`, `sensor_unknown`, `sensor_degraded`.

### Retiré — `EVERY` quitte la grammaire
- **`OBSERVE … EVERY <durée>` n'a jamais rien cadencé.** Le corps de la
  condition de cadence était un `pass`, et l'unité mentait par-dessus : le
  lexer rendait `EVERY 60s` en **secondes**, que le runtime comparait à
  `state.tick`, un compteur qui n'a aucune durée déclarée nulle part dans le
  langage. Un contrat de cadence qu'aucune exécution n'honore vaut moins que
  pas de contrat du tout. Aucun programme livré ne l'employait.
- **Le mot reste réservé et le programme est refusé**, avec la conduite à
  tenir. Le rendre au vocabulaire libre aurait été pire que le no-op :
  `OBSERVE logs EVERY 10s` se serait relu en silence comme deux chemins
  observés, `logs` et `EVERY`, et l'agent aurait tourné en attendant un
  capteur inexistant. La cadence s'exprime par une garde `WHEN`, qui elle est
  honorée.
- Contrat du skill porté à **2.0.0** : la surface déclarée a rétréci, et le
  versionnage du dépôt fait de la grammaire l'API publique. Un retrait s'y
  annonce en majeur, même quand la construction retirée n'a jamais rien fait.
  `EVERY` figure dans les écarts déclarés (`COVERAGE_WAIVERS`) avec sa raison,
  et ne paraît plus dans aucune section du contrat.

### Corrigé — toute garde passe par l'évaluation sûre
- **Une garde pouvait faire tomber la boucle de l'agent.** `sensor.value + 1 >
  2` sur un capteur rendant une chaîne laissait remonter une `EvalError` hors
  du tick : le processus s'arrêtait. `_safe_test` existait mais ne couvrait
  que trois sites ; les gardes de `PLAN`, `DECIDE`, `IF`, `EVENT`,
  `ON MESSAGE`, `OBSERVE`, d'écriture `MEMORY` et de score de but appelaient
  `Evaluator.test` en direct. Les neuf y passent désormais, repli **fermé** et
  ligne de trace nommant le site. C'est un défaut de disponibilité et non de
  sûreté — mais un agent régulé qui s'arrête sur une donnée inattendue ne
  surveille plus rien.

### Corrigé — coercition booléenne stricte
- **`bool("false")` valait `True`**, comme `bool("no")` et `bool("off")`. Un
  modèle qui répond « false » en toutes lettres — ce que font la plupart quand
  la sortie n'est pas strictement contrainte — produisait l'inverse de sa
  réponse, en silence, et une garde `WHEN injection_detected` se déclenchait
  sur une négation. Le contrat `PRODUCE { x: Bool }` porte sur une valeur de
  vérité, pas sur la vérité de Python.
- Vocabulaire explicite (`true/false`, `yes/no`, `on/off`, `1/0`, `oui/non`,
  `vrai/faux`, casse et espaces indifférents). Hors vocabulaire, le repli
  **déclaré** (`DEFAULT`) l'emporte : une réponse illisible n'est pas une
  réponse négative, c'est une absence de réponse — et c'est exactement le cas
  que `DEFAULT` prévoit. Symétrique du traitement déjà en place pour un
  booléen reçu là où un `Number` est attendu.

### Sécurité — deux absences ne se comparent pas
- **`UNDEFINED == UNDEFINED` valait vrai.** `_eq` repliait sur `a is b`, vrai
  sur le singleton interne : `PLAN p WHEN missing.a == missing.b` se
  déclenchait, et sous `DEFAULT ALLOW` cela conduisait à une action réelle.
  Une garde se satisfaisait de sa propre ignorance, ce que la SPEC interdit
  explicitement. Les deux opérateurs valent désormais faux quand **les deux**
  opérandes sont absents — traiter `!=` par simple complément de `==` aurait
  rouvert le défaut à l'envers.
- **La correction s'arrête là où l'ignorance est unilatérale.** Une absence
  face à une valeur connue reste « différente ». La recommandation reçue —
  « toute comparaison avec `UNDEFINED` vaut faux » — a été essayée : elle
  casse `request.sent != yes`, `folder != unknown`, `service.status !=
  healthy`, c'est-à-dire l'idiome d'amorçage de presque tous les programmes
  livrés. Mesuré : 9 tests rouges, `agentl test` de 26/27 à 23/27, et la
  société d'agents entière inerte. Un agent qui ne démarre jamais n'est pas
  le sens sûr, c'est une panne.
- **`VERIFY` passe en régime strict** (`Evaluator(strict_undefined=True)`) :
  là, une comparaison portant sur une absence vaut faux dans les deux sens.
  `VERIFY { incident.resolved != open }` réussissait tant que le chemin
  n'existait pas — l'absence de preuve valait preuve. Une vérification
  affirme que le monde a atteint un état ; une garde de plan demande s'il
  faut agir. Deux questions, deux régimes. Aucun programme livré n'en pâtit.
- Les gardes de **politique** ne passent pas par là : `trivalent.evaluate`
  rend `UNKNOWN` dès qu'un opérande est absent, et un `NEVER` indéterminé
  s'applique. La sûreté des interdits ne dépendait pas de ce défaut.
- Cinq régressions dans `tests/test_security_authoring.py`, dont le régime
  strict de `VERIFY` et le piège symétrique de `!=`.
- Signalé par le même audit externe du 2026-08-03.

### Sécurité — `NEVER SEND` retenait la feuille, pas le parent
- **Un secret fuyait par le composite qui le contient.** `_redacted` ne
  comparait que dans le sens descendant : interdire `credentials.token` ne
  disait rien de `credentials`, et `USING { credentials }` envoyait le
  dictionnaire entier, token compris, à un modèle distant. La seule
  fonctionnalité de confidentialité du langage se contournait en désignant le
  nœud du dessus — sans redaction, sans trace, et sans que `W130` ne dise
  quoi que ce soit. C'était une faille directe de la v1.8.
- **Redaction structurelle** : le runtime descend désormais dans les
  composites et ne retient que **la feuille interdite**. Le modèle reçoit
  `{'token': '⟦retenu⟧', 'user': 'alice'}`. Retenir tout `credentials`
  aurait fermé la fuite en amputant le raisonnement de données que le
  programme n'a jamais protégées ; l'imbrication est suivie à tous les
  niveaux. Une valeur que le runtime ne sait pas parcourir est retenue
  **entière** : une interdiction qu'on ne sait pas appliquer finement
  s'applique en grand.
- Le contrôle couvre les deux points de sortie, `REASON … USING` et le
  contexte complet transmis à `select_plan`.
- **`W130` vaut dans les deux sens** : un `USING` nommant le parent d'un
  chemin retenu est signalé, avec la conduite à tenir — nommer ce qui doit
  sortir plutôt que le nœud au-dessus du secret. Contrat du skill
  `agentl-author` porté à 1.6.2 (`analyzer.py`, aucun changement de
  grammaire ni de code de diagnostic).
- Huit régressions dans `tests/test_security_authoring.py`, dont un test de
  mutation : rétablir le contrôle descendant seul doit rendre la suite rouge.
- Signalé par le même audit externe du 2026-08-03.

### Sécurité — l'argument prime sur la locale homonyme
- **Un `NEVER` se contournait par une affectation antérieure.** Dans
  `_ActionScope`, les arguments de l'appel étaient ajoutés par `setdefault`
  après la copie des locales : une locale du même nom l'emportait, et la garde
  jugeait une autre valeur que celle transmise à l'outil.

      SET target = safe
      delete(target=protected)
      POLICY { NEVER delete WHEN target == protected }

  L'hôte recevait `protected`, la garde lisait `safe`, l'appel aboutissait —
  et `verify` annonçait pourtant T1 « démontré », son diagnostic `V102`
  affirmant même que « le runtime bloquera l'appel ». L'invariant central
  était faux, et le certificat le contredisait. Aucune malveillance n'était
  requise : deux noms qui se rencontrent suffisaient, et le nom court étant
  l'idiome canonique de la SPEC §7.1, la portée du défaut était celle de
  l'usage recommandé. Une garde de politique juge **l'appel en cours** :
  dans cette portée, le nom d'un paramètre désigne ce qui part vers l'outil.
  La portée reste celle des gardes de politique — `_ActionScope` n'existe que
  le temps d'un `PolicyEngine.check`.
- **La collision de noms est tracée** (`POLICY`, `« target » masque la locale
  safe`) et lisible dans `PolicyDecision.shadowed_args`. Deux déclarations qui
  se rencontrent en silence sont ce qui a produit le défaut ; le cas courant
  — aucun homonyme — ne produit aucune ligne.
- Cinq régressions dans `tests/test_security_authoring.py`, dont un test de
  mutation : rétablir le `setdefault` doit rendre la suite rouge.
- Signalé par un audit externe du 2026-08-03 sur `6ab85b8`.

### Modifié
- Mots-clés `SEND`, `UNKNOWN`, `DEGRADE` (grammaire, EBNF, coloration VS
  Code). Contrat du skill `agentl-author` porté à 1.5.1.

### Corrigé — contrat d'auteur
- **`sync_grammar.py` ne voyait pas la moitié optionnelle de la grammaire.**
  L'extracteur ne lisait que `at_kw`/`expect_kw` et les comparaisons à une
  constante : `accept_kw` — donc `APPROVAL` dans `REQUIRE APPROVAL FOR`, et
  `ESCALATE` dans `ON UNKNOWN` — lui échappait, tout comme
  `effect in ("NEVER", "DENY", "ALLOW")`, dont le membre droit est un tuple.
  `ALLOW`, `DENY` et `APPROVAL` manquaient donc au contrat depuis toujours.
  Le résultat est en outre intersecté avec `KEYWORDS` : les genres de jeton
  (`NAME`, `KW`, `STR`) ne sont pas de la grammaire de surface.
- **`POLICY` et `OBSERVE` n'étaient pas énumérés** dans le contrat généré.
  Ce sont les deux blocs qui portent des interdits — sur l'action et la
  sortie pour le premier, sur la conduite face à un capteur muet pour le
  second. Un `--check` vert ne prouvait leur conformité que par empreinte,
  jamais par contenu.
- L'exemple canonique déclare `ON UNKNOWN ESCALATE` sur les quatre chemins
  dont dépendent ses interdits, et `NEVER SEND approval.evidence_token` : la
  référence exécutable du skill démontre la grammaire au lieu de la subir.
  Elle repasse à **aucun diagnostic**, 8/8 théorèmes.
- **Le contrat énumère désormais les seize blocs du langage**, engendrés
  depuis les méthodes du parseur (`CONTRACT_SECTIONS`) : `44` mots réservés
  sur `119` étaient nommés, ils le sont tous.
- **Contrôle de couverture exécutable** (`validate_coverage`) : `--check` et
  `--write` échouent sur un mot réservé qu'aucune section ne nomme et
  qu'aucun `COVERAGE_WAIVERS` n'écarte avec sa raison. C'est le contrôle qui
  manquait — les empreintes disent que les artefacts *suivent* les sources,
  jamais qu'ils les *décrivent*, et c'est pour cela que trois mots-clés ont
  pu traverser toutes les versions sans figurer nulle part. Aucun écart
  n'est déclaré aujourd'hui.
- `tests/test_authoring_contract_aaa.py` (11 tests) : le contrat n'était
  couvert par aucun test. Test de mutation compris — un faux mot réservé
  ajouté au lexer doit rendre le contrôle rouge.

## [1.7.0] — non publié

Une seule vague de publication : les entrées marquées `(v1.6)` et `(v1.7)`
dans la spécification désignent l'ordre dans lequel le travail a été fait, pas
deux versions distribuées. Rien entre `1.5.0` et celle-ci n'a été publié.

### Ajouté
- **Le temps dans la planification** (SPEC §17.1) : `TOOL … DURATION 45s`
  (unité obligatoire, normalisée en secondes) et, côté `PLANNER`,
  `DEADLINE <durée>` (borne dure — un plan trop long n'est pas engendré) et
  `TIME_WEIGHT <n>` (taux de change secondes → coût, `0.0` par défaut donc
  strictement rétrocompatible). Une action de 45 s et une de 4 min ne
  coûtaient jusqu'ici pas plus l'une que l'autre.
- Diagnostic `W126` : opérateur sans `DURATION` alors que le planificateur
  arbitre sur le temps. Une durée absente vaut zéro, et un zéro non déclaré
  est indiscernable d'une mesure.
- **Théorème T8 — vivacité de la société** (`agentl/liveness.py`, SPEC §21) :
  le premier théorème portant sur le **programme entier**, évalué par
  `agentl verify` dès qu'il y a plus d'un agent. Plus petit point fixe sur un
  graphe d'attente de signaux (`MESSAGE` et `SHARED`) : un cycle d'attente sans
  point d'amorce est réfuté et **nommé** (`V130`), les contextes morts sont
  pointés (`V135`). Comble un trou réel : T2 concluait « le but reste
  atteignable » pour chaque agent d'une société pourtant interbloquée.
- Diagnostics `V130`–`V137`. `V136` rend le verdict « ◐ non prouvé » quand un
  `DECIDE.REASON` permet au modèle de proposer n'importe quel plan déclaré —
  l'absence de preuve n'est pas la preuve d'absence.
- **Lecture de la mémoire partagée** : `SHARED.<clé>` est un chemin
  d'expression, avec `.count`, `.version` et `.last[.champ]` (SPEC §21).
- **Pont MCP** (`agentl/mcp.py`, SPEC §29) : `agentl mcp list` inspecte un
  serveur, `agentl mcp import` traduit son `tools/list` en contrats `TOOL`.
  Ce que MCP ne dit pas n'est pas inventé — `RISK` sort à `UNSET`, sans
  `EFFECT` l'outil n'est pas planifiable, et les annotations du serveur sont
  citées en commentaire, jamais promues en valeur.
- Diagnostic `E011` : outil de risque `UNSET`. Erreur, donc `check` et `run`
  refusent — et, contrairement à `W101`, aucun `ALLOW *` ne le fait taire.
  `UNSET` entre dans l'échelle ordinale **au-dessus** de `CRITICAL`, pour que
  toute garde échoue fermée en seconde barrière.
- Contrôle de frontière `B015` : `DESCRIPTION` d'outil au ton impératif — du
  texte écrit par un tiers qui atteint le contexte du modèle.
  `agentl mcp import --strip-descriptions` n'importe alors que les noms.
- `MCPHost` : exécute les outils importés et **refuse de démarrer** si le
  serveur ne rend plus le catalogue scellé, en nommant les écarts.
  `MCPHost.from_agent_file()` relit l'empreinte depuis le `.agent` produit.
- **Scellement des journaux de rejeu** (`agentl/seal.py`, SPEC §26.1) : chaîne
  de hachage par entrée — l'altération d'un journal est détectée *et
  localisée* — et signature facultative de la méta scellée, en `HMAC-SHA256`
  (stdlib) ou `Ed25519` (extra `sign`). Le journal passe au format 2 ; les
  journaux de format 1 restent lisibles mais ne sont jamais présentés comme
  intègres.
- Sous-commande `agentl seal` : vérifie une pièce sans la rejouer, et
  `--keygen` produit une paire Ed25519 (privée en 0600).
- `run --sign-key`, `replay --key` et `replay --require-seal` ; à défaut, la
  variable d'environnement `AGENTL_JOURNAL_KEY` est consultée.
- Extras optionnels `sign` (`cryptography`) et `mcp` (`mcp`). Le cœur reste à
  zéro dépendance.

### Ajouté — les contrats `EFFECT` confrontés au réel
Toute la chaîne de preuve repose sur des `EFFECT` écrits à la main : T2
cherche une route en les appliquant, le planificateur enchaîne des `REQUIRES`
dessus, T5 valide un scénario à travers eux. Un `EFFECT` faux rend `verify`
vert et la production fausse.

- **T9 — « Le modèle d'effets est réfutable »** (SPEC §30). Un `EFFECT`
  qu'aucune `OBSERVE` ne recouvre ne peut jamais être démenti : la
  postcondition n'est pas fausse, elle est hors du domaine de la preuve.
  Mesuré sur le dépôt avant correction : **24 des 35 `EFFECT` d'`examples/`
  étaient irréfutables** — les onze programmes concernés ont été corrigés.
  Codes `V150` / `V151`.
- **`INTERNAL`** — marqueur d'un `EFFECT` qui porte sur la comptabilité de
  l'agent (`cycle.done`, `report.written`) et non sur le monde. Déclaratif et
  non deviné : une heuristique sur le nom aurait exempté n'importe quel effet
  mondain nommé `quarantine.sent`.
- **La crédibilité d'un `EFFECT` devient empirique.** Le registre de dérive
  comptait dans le vide : un effet démenti 3 fois sur 3 reposait sa croyance
  avec `CONFIDENCE 0.80`, le poids d'un effet toujours confirmé. Lissage de
  Jeffreys $(k+\frac12)/(n+1)$ sur le registre — le geste déjà fait par
  `PRIOR FROM`. Sans historique, on retombe sur la valeur déclarée : l'absence
  de jugement n'est pas un jugement neutre.
- **Le registre survit à l'exécution** via `MEMORY { LONG_TERM { effect_drift } }`,
  déclaré — un processus qui repart de zéro repart crédule. Compte courant et
  non journal : réécrit, pas empilé. `Runtime.seed_memory()` est la couture
  par laquelle l'hôte le rend.
- `agentl run` rapporte le registre en fin d'exécution : un compteur
  `effect_drift=3` disait qu'un modèle avait été démenti, pas lequel.
- `W103` ne signale plus une observation qui ne sert qu'à l'audit de dérive :
  elle est utilisée, par le runtime, et la retirer rendrait l'effet
  irréfutable.
- `tests/test_effect_contracts_aaa.py` : 20 tests.

### Corrigé — documentation et cohérence
- **`E005` était documenté depuis la v0.6 et émis nulle part** : la
  spécification promettait un contrôle qui n'existait pas. Un `EVENT` sans
  corps est désormais réellement signalé — l'événement était drainé, la trace
  affichait un `⚡`, et rien ne se déclenchait.
- Tous les codes `E`/`W`/`V`/`B` émis par le code sont maintenant présents
  dans `docs/SPEC.md` — `V113`, `V120`, `V123`, `V125` et `V126` manquaient
  aux tables du §21.
- **La grammaire EBNF était restée en v1.5** : ni `DURATION`, ni `DEADLINE`,
  ni `TIME_WEIGHT`, ni `INTERNAL`. `docs/agentl.ebnf` les décrit désormais, et
  le contrat du skill `agentl-author` passe en 1.3.0.
- Le chargeur d'hôte de la CLI n'enregistrait pas le module dans
  `sys.modules` avant de l'exécuter : tout hôte déclarant une `@dataclass`
  échouait sur un `AttributeError` venu de la bibliothèque standard
  (`examples/linux_defender.py` était concerné).
- Décomptes de théorèmes corrigés dans le README et le skill (« sept » /
  « quatre » → huit), et la limite « le temps n'est pas modélisé » du §28 est
  remplacée par ce qui reste vrai : le temps est modélisé, mais la `DURATION`
  est déclarée et rien ne la confronte au temps réellement passé.
- Version du paquet portée à `1.7.0`.

### Sécurité
Trois failles du moteur de politiques, trouvées par un audit externe le
2026-08-02 et **reproduites empiriquement** avant correction. Toutes trois
contredisaient en silence la même promesse — « aucune action ne touche un
outil sans passer par le moteur, et une garde indécidable échoue fermé ».

- **Une garde `NEVER` ne s'appliquait pas si sa donnée était absente**
  (SPEC §7.1). `on_error=True` ne couvrait que les *exceptions* ; une
  comparaison contre une valeur indéfinie ne lève pas, elle rend `False`. Le
  cas le plus fréquent — capteur en panne, chemin mal orthographié — passait
  donc à travers l'intention. Les gardes de politique suivent désormais la
  logique trivalente forte de Kleene (`agentl/trivalent.py`) : indéterminé
  applique un `NEVER`/`DENY`, ne satisfait pas un `ALLOW`, route une
  approbation vers l'humain.
- **`DELEGATE` contournait le moteur de politiques** (SPEC §7.2). Le
  sous-agent était appelé directement depuis `host.subagents` : ni contrôle,
  ni risque, ni approbation — sous `DEFAULT DENY`, il s'exécutait quand même.
  Il traverse désormais `PolicyEngine.check`, la cible de règle étant le nom
  du sous-agent. **Quatre exemples du dépôt exploitaient la faille sans le
  savoir** et déclarent maintenant leur contrat et leur autorisation.
- **Une charge utile pouvait désactiver un `NEVER`** (SPEC §7.3). Les payloads
  de message et d'événement étaient liés en noms nus parmi les `locals`,
  prioritaires sur le monde et les croyances : un événement portant
  `asset.criticality = LOW` masquait l'observation `CRITICAL`. Les noms nus
  vont désormais dans `state.untrusted`, consulté **en dernier** ; les formes
  préfixées (`payload.x`, `event.source`) restent dans les locales. Idem pour
  le retour d'un `DELEGATE`.

Trois autres constats du même audit, portant sur les **frontières** que le
moteur de politiques ne couvre pas — ce que l'agent montre au modèle, ce qu'un
humain répond, ce qu'un outil reçoit — également reproduits puis corrigés.

- **`USING` ne restreignait pas le contexte du modèle** (SPEC §7.4). Il
  ajoutait un champ `focus` ; croyances complètes, buts, plans et catalogue
  d'outils avec leur risque partaient de toute façon. Un `USING` non vide
  borne désormais réellement le contexte à `tick` + `focus`. Un `REASON` sans
  `USING` reçoit toujours tout — défaut historique conservé, mais signalé par
  `W129` : l'exposition maximale doit être une décision, pas une omission.
- **Une approbation refusée pouvait autoriser l'action** (SPEC §7.5).
  `Host.approve()` rendait `bool(self.approver(request))` : la chaîne `"no"`,
  `"refusé"`, un dict `{"decision": "denied"}` approuvaient tous. Seul un oui
  explicite approuve désormais — `True`, ou un mot d'accord reconnu
  (`host.approval_granted`). Le runtime applique la même règle, un hôte
  pouvant être une sous-classe ou un service distant.
- **Un booléen satisfaisait un contrat `Int`/`Number`** (SPEC §7.6).
  `isinstance(True, int)` est vrai en Python : `purge(retention_days=False)`
  passait le contrat et l'hôte recevait `0`. Refusé à l'entrée d'outil, et
  traité comme réponse hors schéma à la coercition LLM — où `float(True)`
  produisait une confiance de `1.0`, la valeur maximale, par accident de
  typage.
- Diagnostics `W127` (sous-agent délégué sans contrat, risque supposé
  `CRITICAL`), `W128` (garde `NEVER`/`DENY`/`APPROVAL` portant sur un
  identifiant nu que rien ne renseigne — lu comme une constante symbolique,
  la règle ne s'appliquerait pas) et `W129` (`REASON` sans `USING`).
- `tests/test_security_boundary_aaa.py` (37 tests) et
  `tests/test_security_surface_aaa.py` (24 tests), avec un **test de mutation
  par faille** — on rétablit l'ancien comportement et on vérifie que l'exploit
  revient. Un test de sécurité qui passerait aussi bien sans le correctif ne
  prouve rien.

### Corrigé
- **Le planificateur ne comparait pas les routes.** Un état atteint était
  marqué visité définitivement, fût-ce par la route la plus chère : à effets
  égaux, le plan retenu dépendait de **l'ordre de déclaration des outils** et
  non de leur coût — le critère annoncé n'était pas celui appliqué. La
  frontière conserve désormais un front de Pareto sur `(coût, durée)` par
  état ; une route n'est écartée que si elle est dominée sur les deux.
- **`MEMORY { SHARED }` était écrit seulement.** `State.get()` ne consultait
  que `SHORT_TERM`, `LONG_TERM` et `KNOWLEDGE` : aucune garde, règle `DECIDE`
  ni condition de but ne pouvait observer une clé partagée. Les écritures
  étaient tracées et versionnées, et n'influençaient aucune décision — un état
  « partagé » que personne ne peut observer ne partage rien. La lecture est
  **explicite** (`SHARED.k`), pour ne pas confondre une clé partagée avec un
  `LONG_TERM` homonyme. T8 couvre donc les deux moitiés de son graphe
  d'attente, et `V137` signale désormais une clé écrite que nulle garde ne
  relit — donnée morte, et non plus limite du langage.
- `Society` re-sème les clés `SHARED` déclarées après avoir substitué le
  dictionnaire partagé : sans cela une clé déclarée mais jamais écrite se
  lisait `UNDEFINED` au lieu de la suite vide, et `SHARED.k.count == 0` était
  faux au premier tick alors qu'il est précisément vrai.

### Modifié
- `Journal.load`/`loads` refusent par défaut un journal altéré (`strict=True`),
  plutôt que de le rejouer et d'annoncer un verdict à partir d'une pièce
  corrompue. `strict=False` permet de charger pour constater le dommage.

## [1.5.0] — 2026-07-31

### Ajouté
- `DEFAULT` sur les champs `REASON.PRODUCE` et `ATTESTS` sur les paramètres
  d’outil : replis fail-closed et provenance cible/preuve exprimés dans l’AST.
- Diagnostics `W119`–`W125`, contrôles de frontière `B008`–`B014` et
  théorèmes T6/T7 pour la provenance corrélée et la terminaison sans abandon.
- Contrat `agentl-author` 1.1.3 et tests de mutation sécurité.

### Corrigé
- `linux_defender` : cibles déterministes attestées, approbations fortes,
  anti-injection, dry-run explicite, déduplication, rollback et clôture gardée.

## [1.4.0] — 2026-07-28

### Ajouté
- **`SCENARIO { GIVEN … EXPECT … WITHIN n }`** (SPEC §27) : le programme porte
  ses critères d'acceptation. Exécutable — `agentl test`, contre le monde
  déclaré, sans hôte ni réseau — et attaquable par le vérificateur
  (**théorème T5**, `V120`-`V124`).
  - Une attente fausse au départ est une **éventualité** (elle doit advenir
    dans la borne) ; une attente déjà vraie est un **invariant** (elle doit
    tenir à chaque tick). Sans cette distinction, `EXPECT { isolated !=
    confirmed }` serait vert sans qu'aucun tick n'ait tourné.
  - L'opérateur et l'oracle se posent dans le `GIVEN` (`operator.approval`,
    `operator.answer`, noms produits par `REASON`). Par défaut l'opérateur
    refuse : le fail-closed du runtime.
  - Nouveaux diagnostics `E010`, `W117`, `W118` ; mots-clés `SCENARIO`,
    `GIVEN`, `WITHIN` (grammaire, coloration VS Code).
  - `soc_analyst.agent` porte deux scénarios, dont un test de mutation :
    retirer le `NEVER` fait tomber le scénario qui le teste.
- **Rejeu déterministe** (`agentl/replay.py`, SPEC §26). `agentl run
  --record J.json` journalise les huit points de franchissement de frontière
  (`Host.read/invoke/ask/approve/drain`, `DELEGATE`, `LLM.reason/select_plan`) ;
  `agentl replay J.json` re-dérive la décision **sans capteur, sans outil,
  sans réseau**. Le journal est scellé sur l'empreinte SHA-256 de la trace :
  le verdict est l'égalité caractère pour caractère, pas l'absence de plantage.
  Sociétés multi-agents comprises. API : `Journal`, `RecordingHost`,
  `RecordingLLM`, `ReplayHost`, `ReplayLLM`, `verify_trace`.
  - Les pannes se rejouent en pannes, avec le nom de classe d'origine.
  - Journal tronqué, réordonné ou aux arguments d'outil différents :
    `ReplayDivergence`. Une valeur non sérialisable déclare le journal
    lacunaire (`meta.lossy`) à l'enregistrement.
- Packaging : `pyproject.toml` (installable, `agentl` en commande),
  `LICENSE` (AGPL-3.0-or-later), `CONTRIBUTING.md`, `SECURITY.md`,
  ce journal, intégration continue GitHub Actions.

### Corrigé
- Test `test_pause_suspends_a_running_run` : course entre `pause()` et la fin
  d'un run court, qui pouvait faire échouer la suite au hasard.

## [1.3.0] — 2026-07-27

### Ajouté
- `FOREACH … IN … MAX n` : projection scalaire d'un élément de collection,
  liaisons défaites entre deux éléments, politiques réévaluées par élément.
- `agentl boundary X.agent` : analyse l'AST de `X.py` et signale les décisions
  métier prises côté hôte (`B000`–`B007`). Levées `# BOUNDARY-OK: motif`
  couvrant l'instruction entière ; sans motif, rien n'est levé.
- `E009` : appel d'outil dans une expression refusé à l'analyse — il
  contournerait le moteur de politiques par l'évaluateur.
- Coercitions d'entrée : `Symbol` satisfait `String`, `yes`/`no` satisfont
  `Boolean`.
- `bench/` : pont AutomationBench (Zapier) réutilisant le scorage officiel ;
  sept tâches à 1.00 et un test de neutralisation des gardes.

### Modifié
- README réorganisé autour des trois contrôles `check` → `verify` →
  `boundary`.
- SPEC : §24 `FOREACH`, §25 frontière hôte/agent, limites assumées en §26.

## [1.2.0] et antérieures

Langage, vérificateur (quatre théorèmes), moteur de politiques, inférence
bayésienne, planificateur, société multi-agents, visualiseur et studio.
Historique détaillé non reconstitué : le dépôt public commence à 1.3.
