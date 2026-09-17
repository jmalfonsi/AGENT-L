# AITESTPLATFORM

Plateforme locale de comparaison de systèmes agentiques sur les tâches
publiques d'AutomationBench. La V3 (`automationbench-native-prompt-facade-v3.1`) exécute réellement les runtimes, les outils
et le rubric du benchmark. Elle ne possède ni moteur de simulation, ni état
final de secours, ni métriques de tokens estimées.

## Runtimes intégrés

- AGENT-L : programme spécifique `.agent`, compilé, avec hôte Python contrôlé.
- LangGraph : agent ReAct natif et façade métier structurée commune.
- CrewAI : agent et tâche CrewAI natifs, outils `BaseTool` réels via LiteLLM.
- OpenAI Agents SDK : `Agent`, `Runner` et `FunctionTool` natifs via LiteLLM.

Tous utilisent `gemini-3.1-flash-lite`, une température de `0`, le niveau de
raisonnement `low` et un plafond de `8192` tokens de sortie. Chaque exécution
reconstruit un `WorldState` isolé et appelle directement le rubric officiel.
L’état attendu et les assertions ne sont jamais inclus dans le prompt.

La façade métier, figée dans `task_tool_manifest.py`, est la même pour les
quatre runtimes, et chacun de ses outils agit réellement sur les outils
AutomationBench. Ce qui distingue les runtimes tient à un seul élément — le
programme `.agent` — et c'est le **régime de comparaison** qui décide de son
sort.

### Typage des outils

Les hôtes de tâche n'annotent pas leurs paramètres : le contrat de typage vit
dans le bloc `INPUT` du `.agent`. `tool_signatures.py` l'y lit et l'injecte
dans le schéma JSON transmis aux baselines, **dans les deux régimes**. Sans
lui, chaque paramètre restait sans `type` et `langchain-google-genai` déclarait
tout en `STRING` : Gemini renvoyait `row_id="2"` là où l'hôte indexe par
entier, et l'appel échouait sur un message parlant d'un jeton d'attestation
invalide alors que le jeton était exact. Une information que la plateforme
possédait n'était donnée qu'à AGENT-L ; c'était un défaut du pont, pas une
faiblesse du framework mesuré.

Ce chemin transmet une **signature**, jamais une procédure : ni `BIND`, ni
`REQUIRES`, ni `EFFECT`, ni ordre d'appel, ni garde, ni interdit. Savoir que
`limit` est un nombre n'apprend pas quelle limite appliquer. La séparation des
deux régimes reste donc entière.

## Régimes de comparaison

Deux régimes coexistent. Ils ne mesurent pas la même chose et leurs runs ne
sont **jamais** moyennés ensemble : `stats.py` tranche le régime avant toute
autre partition, et l'interface le sépare partout.

| | Ce qui est mesuré | Ce que reçoivent LangGraph / CrewAI / OpenAI Agents | Version de protocole |
|---|---|---|---|
| `prompt_only` (défaut) | Trouver **et** exécuter la marche à suivre. Protocole officiel AutomationBench. | Énoncé + consignes système + outils | `automationbench-native-prompt-facade-v3.1` |
| `plan_parity` | Exécuter fidèlement un plan connu. | Idem **+ la transcription du `.agent` de la tâche** | `automationbench-plan-parity-facade-v4` |

Le briefing de `plan_parity` est produit par `agent_briefing.py` **depuis l'arbre
syntaxique** du programme, jamais rédigé à la main : phases et leurs gardes,
étapes, boucles avec leur plafond, appels d'outil avec leurs arguments nommés,
sorties de LLM avec leur domaine clos, et interdits `NEVER`. `coverage()`
énumère ces éléments et `tests/test_agent_briefing.py` vérifie, sur les dix
programmes réellement joués, qu'aucun ne manque au texte produit. Sans cette
garantie, un plan affaibli avantagerait AGENT-L sans que rien ne le signale.

AGENT-L ne reçoit jamais le briefing : il exécute déjà le programme dont le
texte est tiré. Le run reste néanmoins étiqueté avec le régime de sa campagne,
puisque c'est à ces baselines-là qu'il doit se comparer.

**Ce que la parité de plan n'égalise pas, et qu'il faut lire comme un résultat :**
un `NEVER` d'AGENT-L est refusé par le runtime à chaque appel, alors que sa
transcription n'est qu'une consigne, que le modèle peut enfreindre. L'écart qui
subsiste après égalisation des plans mesure donc l'apport de l'exécution
contrainte — c'est l'information que ce régime existe pour produire, pas un
défaut de transcription.

Consulter le plan sans dépenser un jeton : `python3 benchmark_runner.py briefing
--task hr.employee_request_routing`, ou le bouton « Voir le plan transmis » de
l'écran de lancement.

## Prérequis

- `/home/ubuntu/AutomationBench` et son environnement `.venv` ;
- `/home/ubuntu/AGENT-L` et les paires `bench/tasks/*.agent` + `*.py` ;
- Bun ;
- `GEMINI_API_KEY` dans l'environnement, dans `AITESTPLATFORM/.env`, ou dans
  `/home/ubuntu/HAL/.env`.

Installation :

```bash
cd /home/ubuntu/AGENT-L/AITESTPLATFORM
bun install
uv pip install --python /home/ubuntu/AutomationBench/.venv/bin/python -r requirements.txt
```

Lancement local :

```bash
bun run dev
```

L'interface écoute uniquement sur `http://127.0.0.1:3000`. Les endpoints de
benchmark déclenchent de vrais appels Gemini et ne doivent pas être exposés
publiquement sans authentification, quotas par utilisateur et stockage durable.

## Commandes de validation

```bash
bun run lint          # tsc --noEmit ; nécessite @types/react, sans quoi src/ n'est pas vérifié
bun run build
/home/ubuntu/AutomationBench/.venv/bin/python -m pytest tests -q

/home/ubuntu/AutomationBench/.venv/bin/python benchmark_runner.py health
/home/ubuntu/AutomationBench/.venv/bin/python benchmark_runner.py tasks
/home/ubuntu/AutomationBench/.venv/bin/python benchmark_runner.py run \
  --task hr.employee_transfer_approval_workflow \
  --framework agent_l

# Plan transmis aux baselines en parité de plan — aucun appel au modèle
/home/ubuntu/AutomationBench/.venv/bin/python benchmark_runner.py briefing \
  --task hr.employee_request_routing

# Red-team hors ligne d'un agent produit par la fabrique. Le code 1 signifie
# que la certification est refusée ; le rapport nomme chaque propriété tombée.
python adversarial_protocol.py --json /tmp/cave-adversarial.json
```

Le modèle de menace, les portes à tolérance zéro et les limites de cette
épreuve sont détaillés dans [`ADVERSARIAL_PROTOCOL.md`](ADVERSARIAL_PROTOCOL.md).

Variables utiles :

- `AGENT_BENCH_MODEL` : modèle commun, défaut `gemini-3.1-flash-lite` ;
- `AGENT_BENCH_MAX_TURNS` : garde commune, défaut `40` ;
- `AGENT_BENCH_MAX_OUTPUT_TOKENS` : plafond commun, défaut `8192` ;
- `AGENT_BENCH_THINKING_LEVEL` : raisonnement commun, défaut `low` ;
- `AGENT_BENCH_RPM` : limite inter-processus Gemini, défaut `6` ;
- `AGENT_BENCH_AGENTL_TICKS` : ticks maximum AGENT-L, défaut `12` ;
- `AGENT_BENCH_PYTHON` : interpréteur du runner si différent du venv officiel ;
- `PORT` : port HTTP local, défaut `3000`.

## Exploration et historique

Chaque tâche expose son `example_id` officiel, le prompt, les contrats d’outils,
les assertions, l’état initial et l’objet AutomationBench brut via
`GET /api/tasks/:taskId`. Ces données sont affichées après coup. L’objet brut, l’état initial et les
assertions ne sont jamais ajoutés au contexte. Le programme `.agent` est chargé
uniquement par AGENT-L.

Chaque carte de framework donne accès à son code réellement exécuté via
`GET /api/frameworks/:frameworkId/code`. Pour AGENT-L, la réponse contient le
programme `.agent`, l’hôte Python de la tâche actuellement sélectionnée et
l’adaptateur du runner. Pour LangGraph, CrewAI et OpenAI Agents SDK, elle
contient l’adaptateur natif partagé, la façade métier et le prompt système
commun, sans le fichier `.agent`. Il ne s’agit pas de pseudo-code et aucune variable
d’environnement ni clé API n’est exposée.

Chaque nouveau run est écrit immédiatement dans `data/run-history.jsonl` sous
verrou fichier, avec son entrée dans `data/run-history.index.jsonl`. Le journal
reste la seule source de vérité : l’index n’est qu’un raccourci, reconstruit
automatiquement s’il est absent, corrompu ou désynchronisé (journal réécrit,
tronqué, ou complété par un autre processus). Il est local, persiste aux
redémarrages et reste ignoré par Git.

`GET /api/history` renvoie des **lignes légères** — jamais l’état du monde, les
assertions ni les appels d’outils, qui pèsent ~29 Ko par run et n’étaient pas
affichés dans la liste. Le détail complet se demande à l’ouverture d’une ligne,
par `GET /api/history/:runId`. Paramètres : `limit`, `offset`, `beforeSeq`
(curseur stable, à préférer à `offset` car un run écrit entre deux pages décale
la fenêtre), `framework`, `task`, `protocol`, `regime`, `status`.

Les résultats incluent le nombre d’appels, le fournisseur, les tokens, le motif
de fin, le niveau de raisonnement et le plafond de sortie. Les retries 429 sont
comptés. Une sortie vide ou arrêtée à la limite sans outil produit un statut
`invalid` et est exclue des taux et moyennes. Une métrique non fournie par le
runtime reste `null` : « N/D » n’est pas « 0 ».

`GET /api/stats` agrège **tout** l’historique — classement par framework et
matrice tâche × framework. Par défaut, seule la version de protocole la plus
récente est retenue : mélanger `legacy-v1` et une campagne V3 produirait une
comparaison mensongère, puisque les règles d’exécution ont changé. Les versions
écartées sont listées dans `excludedProtocolVersions`, et `protocol=all` force
l’inclusion. Le **régime** est tranché avant la version : par défaut
`prompt_only`, et `regimeCounts` donne le nombre de runs de chaque régime sur
tout l’historique, pour qu’un régime vide ne se lise pas comme un historique
vide. Filtres : `regime`, `protocol`, `task`, `framework`, `since`.

`GET /api/export?format=csv|json&scope=runs|stats` télécharge les mêmes données
filtrées, avec `Content-Disposition`. Une valeur absente donne une cellule vide,
jamais « 0 » ni « None ».

La version de protocole décrit les **règles d’exécution vues par les agents** —
modèle, prompt, façade d’outils, plafonds. Elle ne doit pas être incrémentée
pour une évolution du stockage ou de l’affichage : cela fragmenterait
l’historique et rendrait les campagnes incomparables sans raison.

## Exécution en direct, reprise et annulation

`POST /api/benchmark/run-suite` **rend la main immédiatement** (`202`) : la
campagne vit côté serveur. Auparavant la requête restait ouverte pendant toute
la campagne — jusqu’à dix heures — et un rechargement de page perdait des
résultats déjà calculés et facturés.

- `GET /api/benchmark/live/:campaignId` diffuse la progression en SSE. Le flux
  ne contient ni réponse du modèle, ni observation métier, ni chaîne de pensée.
  `Last-Event-ID` est honoré : une reconnexion ne perd pas d’événement.
- `GET /api/benchmark/active` liste les campagnes vivantes : c’est ce qui permet
  à une page rechargée de se rebrancher automatiquement.
- `GET /api/benchmark/campaigns` et `/api/benchmark/campaigns/:campaignId`
  rendent les campagnes persistées dans `data/campaigns/`. L’enregistrement est
  écrit à la création, après **chaque** run terminé, puis à la fin : un arrêt
  brutal du serveur ne perd pas les runs déjà effectués.
- `POST /api/benchmark/cancel` `{campaignId}` interrompt la campagne. Le
  sous-processus reçoit SIGTERM puis SIGKILL s’il s’attarde — sans cette
  escalade, un Python bloqué en `sleep` gardait le verrou de quota et gelait
  tous les runs suivants de la machine. Les runs déjà terminés sont conservés :
  ils sont réels. Annuler une campagne inconnue ou terminée répond `409`.

La fabrique suit le même schéma : `POST /api/factory/generate` répond `202`,
`GET /api/factory/builds/:campaignId` rend le résultat, `POST /api/factory/cancel`
interrompt.

`tasks`, `frameworks`, `framework-code` et `health` sont servis depuis un cache
mémoire avec déduplication des requêtes en vol : chaque appel lançait sinon un
processus Python important crewai et langgraph, soit ~4 s par requête. Le
chargement complet de l’interface passe de plusieurs secondes à ~0,3 s.
`?refresh=1` contourne le cache.

## Fabrique d'agents (`spec-to-agent-v2`)

Second usage de la plateforme : partir d'un cahier des charges et en tirer un
agent validé. Onglet « Créer un agent ».

Le cycle est **ingestion → grille de complétude → questions → rédaction →
validation → réparation**.

1. **Ingestion** (`spec_intake.py`). Texte, TXT, MD ou PDF. L'extraction PDF est
   faite par `pdfminer.six`, jamais par un modèle : un cahier des charges résumé
   avant analyse n'est plus opposable à l'agent produit. Un PDF scanné est
   refusé explicitement au lieu d'être analysé comme « incomplet ».
2. **Grille de complétude** (`spec_analysis.py`). Dix dimensions déclarées dans
   le code, pas laissées au jugement libre du modèle. Le modèle constate et cite
   le texte ; le verdict — générer ou réclamer des précisions — est **calculé**.
   Une dimension exigée et absente bloque la génération. `interdits` et
   `exceptions` ne sont exigés que pour AGENT-L, seul framework qui les prouve.
3. **Questions**. Les réponses sont ajoutées au cahier des charges et l'ensemble
   est **réanalysé depuis zéro** : c'est le document complété qui fait foi, pas
   un correctif posé sur un verdict précédent. Chaque contenu distinct crée une
   révision `specRevision` ; les builds précédents restent rattachés à la révision
   qui les a produits et passent à l’état « à mettre à jour ».
4. **Rédaction** (`codegen_backend.py`). L'agent de codage est Claude Code en
   mode non interactif. Pour AGENT-L il invoque le skill `agentl-author`, dont le
   contrat de grammaire versionné est lu dans le dépôt — le skill n'est pas
   recopié dans le prompt. L'analyse tourne **sans aucun outil** ; seule la
   rédaction écrit, et uniquement dans le répertoire du projet.
5. **Validation** (`agent_validation.py`). La plateforme rejoue elle-même la
   chaîne `check → test → verify → boundary → run`, chaque étape étant un
   processus `agentl` distinct dont le code de sortie et la sortie brute sont
   conservés. Une étape n'est jamais réputée passée : après un échec bloquant,
   les suivantes sont marquées `skipped`, jamais `passed`. L'absence de
   `SCENARIO` est bloquante — un agent sans critère d'acceptation exécutable
   n'est pas validable.
6. **Réparation.** Le constat de validation est renvoyé à l'agent de codage,
   avec consigne de ne pas affaiblir les `SCENARIO` pour les faire passer. On
   répare sur ce qui a été refusé, jamais sur un objectif de score.

Cette fabrique **remplace** la compilation automatique par un modèle piloté au
prompt (`bench/compile_agent.py` + `bench/loop.py`), retirée du dépôt : elle
plafonnait à 0,227 de crédit partiel là où l'écriture à la main atteignait 1,00.

Les agents produits sont testés contre un **hôte de simulation** écrit par
l'agent de codage, portant un jeu de données tiré du cahier des charges : aucun
système externe n'est joint. Les points de branchement à remplacer en production
sont marqués en tête de fichier. Les frameworks autres qu'AGENT-L n'offrent ni
preuve sur l'AST ni contrôle de frontière : leur validation se limite au
chargement du module, et le rapport le dit.

Commandes :

```bash
V=/home/ubuntu/AutomationBench/.venv/bin/python
$V agent_factory.py capabilities
$V agent_factory.py analyze --file cahier.pdf --frameworks agent_l
$V agent_factory.py answer --project prj_xxx --answers '[{"question":"…","answer":"…"}]'
$V agent_factory.py generate --project prj_xxx --framework agent_l
$V agent_factory.py projects
```

Variables : `AGENT_FACTORY_MODEL` (défaut `opus`), `AGENT_FACTORY_ATTEMPTS`
(défaut `3`), `AGENT_FACTORY_AUTHOR_TIMEOUT` (défaut `1800` s),
`AGENT_FACTORY_STEP_TIMEOUT` (défaut `300` s), `AGENT_FACTORY_CLAUDE_BIN`.

Les projets vivent dans `data/projects/<id>/` : cahier des charges courant, snapshots
`spec_versions/vN.txt`, `project.json`, `journal.jsonl` et artefacts immuables sous
`versions/<framework>/vN_<buildId>/`. Le répertoire `<framework>/` est uniquement
la dernière version validée publiée : une fabrication refusée ne le remplace jamais. Ce stockage est local et ignoré
par Git. La génération a sa propre file d'attente, distincte de celle du banc :
une fabrication de plusieurs minutes ne retient pas une campagne de mesure.

## Lisibilité pour un lecteur non spécialiste

La plateforme est lue par des gens qui ne connaissent ni AutomationBench ni le
vocabulaire des agents. Trois règles s’appliquent à toute évolution de
l’interface :

1. **Chaque chiffre porte son explication.** Les définitions vivent dans
   `src/glossary.ts`, source unique, affichées par `src/Tooltip.tsx`. Ne
   réécrivez pas une définition localement dans un composant.
2. **Un écran de résultats dit qui gagne, en une phrase.** Quatre pourcentages
   côte à côte ne sont pas une conclusion. La phrase signale aussi le nombre
   d’exécutions : une avance établie sur deux runs n’est pas une avance.
3. **Une action longue ou facturée s’annonce avant d’être lancée** — durée
   estimée sur les mesures réelles de l’historique, coût, possibilité d’arrêt.
4. **Un score se lit avec ce qui le compose.** `src/assertions.ts` traduit les
   vérifications d’AutomationBench (`gmail_message_sent_to_with_body_contains`,
   …) en français, `src/OfficialScorePanel.tsx` les affiche par application
   Zapier et sépare ce qui manque, ce qui est validé et ce qui est hors barème.
   Un type d’assertion inconnu est décomposé par famille plutôt qu’affiché brut :
   ne recollez jamais un identifiant technique dans l’interface.
5. **Le journal des actions dit aussi ce qui a raté.** `src/ActionJournal.tsx`
   distingue l’exception technique du refus métier, compte les échecs, liste les
   outils jamais appelés et signale le cas piégeux : zéro erreur technique et
   pourtant des vérifications non satisfaites.

## Contrat de preuve

Un résultat contient seulement des faits observés : appels d'outils, arguments,
observations, état final, résultats d'assertions et métriques renvoyées par le
runtime. Une métrique indisponible reste `null`/`N/D`. La plateforme ne collecte
pas et n'affiche pas de chaîne de pensée.

Deux partitions protègent la comparabilité et ne doivent jamais être
contournées : le **régime** (`prompt_only` / `plan_parity`) puis, à l'intérieur
du régime retenu, la **version de protocole**. Un chiffre qui agrège les deux
régimes ne répond à aucune question et ne doit pas être publié.
