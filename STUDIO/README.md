# AGENT-L Studio

AGENT-L Studio est une plateforme locale pour créer, gouverner, tester, optimiser et tracer des projets AGENT-L. Ce dossier est autonome et ne remplace pas le petit éditeur historique situé dans `agentl/studio`.

## Les quatre missions

| Mission | Ce que fait le Studio |
| --- | --- |
| **Créer** | Squelette `.agent` / `.py` dérivé des rôles saisis, puis génération par Codex ou Claude Code, à qui le contrat de grammaire est transmis en entier. |
| **Tester** | Vraies portes CLI `check`, `test`, `verify`, `boundary`, exécution tracée, journal de frontière chaîné et rejeu déterministe. |
| **Optimiser** | `agentl autoloop` avec un rédacteur de corrections, un lot retenu hors correction, et un programme corrigé proposé — jamais appliqué d'office. |
| **Skills** | Bibliothèque `SKILL.md` — **tous éditables, système compris** — génération assistée, sélection par projet copiée dans `.agentl/skills`. |

## Ce qui est inclus

- gestion de projets avec archivage, suppression contrôlée et workspaces isolés ;
- assistant auteur au choix — Codex CLI ou Claude Code — lancé en mode non interactif, à permissions minimales, avec le contrat de grammaire complet dans son prompt ;
- création, génération par l'agent auteur et édition de **tous** les skills au format `SKILL.md`, y compris ceux livrés avec le dépôt ;
- choix agent unique ou société multi-agents, avec rôles et missions qui **nomment réellement les agents produits** ;
- séparation explicite entre le modèle de l'agent auteur et le LLM runtime des agents AGENT-L ;
- runtimes Mock, Google Gemini cloud, Anthropic, OpenAI et API compatible OpenAI, avec test de connexion réel ;
- métrage runtime — appels, tokens, coût — et plafond appliqué dès qu'un tarif est déclaré ;
- éditeur des couples normatifs `X.agent` / `X.py`, avec historique consultable et restaurable ;
- revue de proposition en diff avant application, validée par les **quatre** portes et non par `check` seul ;
- exécutions en tâche de fond — génération par l'agent auteur comprise —, sortie en direct qui reprend après une coupure, bouton d'arrêt, un run à la fois par projet ;
- timeline filtrable **par agent**, graphe `agentl viz`, trace HTML autonome et journal JSON téléchargeable ;
- persistance SQLite en mode WAL des projets, skills et exécutions.

## Démarrage rapide

Depuis `AGENT-L/STUDIO` :

```bash
python3 -m pip install -r requirements.txt
npm run install:ui
npm run build
npm start
```

Ouvrir ensuite <http://127.0.0.1:8765>. Le serveur FastAPI sert directement le build React.

Pour développer avec rechargement automatique des deux côtés :

```bash
npm run dev
```

L'API écoute sur `127.0.0.1:8765` et Vite sur `127.0.0.1:5173`.

## Agent auteur : Codex ou Claude Code

Codex et Claude Code servent à **produire le code et les skills**. Ils ne sont jamais utilisés comme LLM d'exécution par les agents générés. À la création d'un projet, le Studio pose un squelette sûr, transmet la description, l'architecture, les rôles et les skills au CLI choisi, puis n'applique sa proposition que si les **quatre portes** passent dans une copie complète du projet.

Le défaut suit ce qui est réellement installé : si un seul des deux CLI est présent, c'est lui qui est proposé.

```bash
codex login
# ou l'authentification habituelle de Claude Code
claude
```

Codex est lancé avec `codex exec`, une sandbox en lecture seule, une sortie JSON contrainte par schéma, une session éphémère et `--output-last-message` — son message final arrive par fichier, jamais découpé au jugé dans un stdout bruyant. Claude Code est lancé avec `--print`, les outils désactivés, le mode de permission `plan`, une sortie JSON contrainte et aucune persistance de session.

Comme l'auteur tourne sans outil de lecture, tout ce dont il a besoin passe dans le prompt : le contrat de grammaire `references/generated/grammar-contract.md` — l'autorité extraite de l'AST du parseur — précède les skills sélectionnés, transmis entiers.

Le budget auteur est suivi par projet, **génération initiale comprise**. Claude Code rend son coût dans sa sortie JSON ; pour Codex, le Studio affiche « non mesuré » plutôt qu'un zéro qui se lirait « gratuit ».

## LLM d'exécution des agents

Le LLM runtime est configuré séparément pour chaque projet, et peut différer du modèle qui pilote Codex ou Claude Code. **Google Gemini est le défaut dès qu'une clé est disponible** ; sinon le Studio retombe sur `MockLLM`, qui garde les portes, les tests et le rejeu déterministes hors ligne.

```bash
export GEMINI_API_KEY="..."      # runtime Google Gemini cloud
export ANTHROPIC_API_KEY="..."   # runtime Anthropic
export OPENAI_API_KEY="..."      # runtime OpenAI
npm start
```

Un fichier `STUDIO/.env` — ou `AGENT-L/.env` — est lu au démarrage. Une variable déjà exportée l'emporte toujours : le fichier complète, il ne redéfinit pas.

Le projet ne stocke jamais la clé, seulement le nom de sa variable d'environnement, et ce nom est contraint : liste explicite (`GEMINI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) ou suffixe `_API_KEY`, élargissable par `AGENTL_STUDIO_EXTRA_KEY_ENVS`. Sans cette contrainte, un projet pouvait nommer n'importe quel secret du serveur et l'envoyer à l'URL de base de son choix.

Le fichier généré `runtime_llm.py` lit `project.agentl.json` et construit l'oracle demandé au moment de l'exécution. **Il porte sa version en première ligne** : quand le gabarit du Studio change, l'adaptateur des projets existants est archivé dans `.history/` puis remplacé — un correctif de sécurité qui reste dans le dépôt ne protège personne.

Exemple de configuration persistée :

```json
{
  "authoringAgent": "claude-code",
  "authoringModel": "",
  "runtimeProvider": "google-gemini",
  "runtimeModel": "gemini-3.1-flash-lite",
  "runtimeApiKeyEnv": "GEMINI_API_KEY",
  "runtimeMaxCostUsd": 1.0,
  "runtimePricePerMTokIn": 0.0,
  "runtimePricePerMTokOut": 0.0,
  "autoloopModel": ""
}
```

`runtimeMaxCostUsd` n'est appliqué que si un tarif est déclaré. Sans tarif, appels et tokens sont mesurés et le coût est rendu `null` — un zéro se lirait « gratuit ».

## Skills : éditer, comparer, revenir

Tout skill s'édite depuis la bibliothèque, y compris les quatre skills système livrés dans `SKILLS/<slug>/SKILL.md`. Une édition d'un skill système :

- **survit aux redémarrages**. Le semis tourne à chaque démarrage et écrasait tout ; il respecte désormais une version marquée `customized` ;
- **garde son slug et son nom**, sous lesquels les projets le sélectionnent : seul le contenu compte pour l'agent auteur ;
- **atteint ce qui le consomme** : la copie dans `.agentl/skills/<slug>/SKILL.md` des projets et le contexte transmis à Codex ou Claude Code.

La version livrée reste dans le dépôt et sert de référence. L'éditeur affiche un diff « livrée → la vôtre » et un bouton **Version livrée** qui y ramène. Un skill système ne se supprime pas — le semis le réinstallerait au démarrage suivant, et une suppression qui revient toute seule est pire qu'un refus ; réinitialiser est le geste qui existe vraiment.

Pour modifier la version de référence elle-même, éditer le fichier du dépôt : les projets non personnalisés la reprendront au démarrage suivant.

## Optimiser : `agentl autoloop`

L'onglet **Optimiser** lance la boucle avec un rédacteur de corrections (`gemini-*` ou `claude-*`), hérité du runtime du projet ou déclaré dans `autoloopModel`. Sans modèle, la boucle diagnostique et ne réécrit rien : c'est dit explicitement dans l'interface. Le budget de la boucle vaut 60 % du timeout du sous-processus, pour que son rapport survive à l'arrêt. Le programme corrigé est écrit à part, montré en diff, et adopté seulement sur décision — après les quatre portes, comme toute proposition.

## Exécutions : ce qui est garanti

- **Tout run finit.** Portes, suite, exécution, rejeu et agent auteur passent par le même démarrage : une exception termine le run en `failed`, jamais « en cours » à vie.
- **Un arrêt du serveur se dit.** Au démarrage, les runs restés `running` passent en `interrupted`.
- **Un flux coupé reprend.** Chaque lot SSE porte sa position (`id`) ; le navigateur relit l'état du run et reprend avec `?from=`, sans doublon.
- **Un run à la fois par projet.** Les suivants attendent leur tour, et Arrêter fonctionne aussi en file d'attente. Appliquer une proposition pendant un run répond `409`.
- **L'agent auteur est un run.** La création et l'assistant rendent aussitôt le run à suivre (`wait: false`), Arrêter atteint le CLI auteur, et la proposition se relit sur `/api/runs/{id}/draft`. Sans `wait: false`, l'API attend la fin comme avant.
- **Les avertissements se voient.** Chaque porte compte ses erreurs et ses avertissements, rattachés à leur agent et à leur ligne ; un clic ouvre la ligne dans l'éditeur.
- **La chronologie ne dépend plus de l'affichage du terminal** : elle est lue dans `agentl run --events`, en JSON Lines.
- **Les artefacts sont purgés.** Chaque projet garde ceux de ses 50 derniers runs (`AGENTL_STUDIO_KEEP_ARTIFACTS`) ; supprimer un projet arrête d'abord ses runs.

## Sécurité

- écoute limitée à `127.0.0.1`, **en-tête `Host` validé** — la défense contre le rebinding DNS d'un service local ;
- jeton de session facultatif : poser `AGENTL_STUDIO_TOKEN` exige `X-Studio-Token` sur toute écriture ; le serveur imprime l'URL à ouvrir ;
- commandes CLI sur liste blanche, sans shell ;
- chemins résolus et refusés hors de leur workspace ;
- noms de variables de clé contraints, valeurs jamais transmises au navigateur ;
- historique avant chaque écriture, consultable et restaurable.

## Stockage

Par défaut, les données sont écrites dans `STUDIO/.data/` :

```text
.data/
├── studio.sqlite3
├── projects/<slug>/
│   ├── .history/<horodatage>/   # versions archivées, restaurables
│   └── runtime_usage.json       # métrage du dernier run
└── runs/<run-id>/
    ├── trace.html
    ├── graph.html
    ├── record.json
    └── corrected.agent
```

Pour déplacer ce stockage, définir `AGENTL_STUDIO_DATA_DIR`.

## Validation

```bash
npm test
npm run build
```

Les tests couvrent l'isolation des chemins, la propagation versionnée de l'adaptateur runtime, la contrainte sur les variables de clé, la validation de l'en-tête `Host`, la création mono-agent et multi-agents avec rôles, les quatre portes, une exécution enregistrée et son rejeu déterministe, l'attribution de trace par agent, le flux SSE et l'annulation, l'historique restaurable, la comptabilité du budget auteur, l'édition d'un skill système et sa survie à un redémarrage, et les invocations réelles de Codex et Claude Code.

## API principale

| Route | Usage |
| --- | --- |
| `GET/POST /api/projects` | lister et créer les projets (`wait: false` rend le projet et son run de génération sans attendre) |
| `GET/PATCH/DELETE /api/projects/{id}` | gérer un projet |
| `GET/PUT /api/projects/{id}/file` | lire et éditer un fichier isolé |
| `GET /api/projects/{id}/history` | versions archivées |
| `POST /api/projects/{id}/history/restore` | restaurer une version |
| `GET/POST/PUT/DELETE /api/skills` | bibliothèque de skills (PUT accepte les skills système) |
| `GET /api/skills/{id}/origin` | version livrée d'un skill, pour la comparer |
| `POST /api/skills/{id}/reset` | ramener un skill à sa version livrée |
| `POST /api/skills/draft` | générer un `SKILL.md` avec Codex ou Claude Code |
| `POST /api/projects/{id}/runs` | lancer une porte, une suite, un run, un graphe, une boucle ou un rejeu (`wait: false` pour ne pas bloquer) |
| `GET /api/runs/{id}/stream` | sortie du run en direct (SSE) ; `?from=N` reprend après la position `N` |
| `GET /api/runs/{id}/draft` | proposition portée par un run d'auteur, avec les sources qu'elle remplacerait |
| `DELETE /api/runs/{id}` | arrêter un run en cours |
| `GET /api/runs/{id}/trace-html` | journal visuel autonome |
| `GET /api/runs/{id}/graph-html` | graphe `agentl viz` |
| `GET /api/runs/{id}/record` | journal de rejeu |
| `GET /api/runs/{id}/corrected` | programme réécrit par la boucle |
| `POST /api/projects/{id}/assistant` | proposer ou appliquer une modification via l'agent auteur (`wait: false` rend le run à suivre) |
| `POST /api/projects/{id}/draft/validate` | éprouver une proposition sur les quatre portes |
| `POST /api/projects/{id}/draft/apply` | appliquer une proposition validée |
| `POST /api/projects/{id}/runtime-test` | aller-retour réel vers l'oracle du projet |
