# AGENT-L Studio — contrat d'intégration (source de vérité)

Interface web de **visualisation + édition en direct** d'un agent AGENT-L,
style N8N : canevas de nœuds à gauche/centre, éditeur de code, inspecteur,
et exécution en direct (le graphe s'illumine au fil des ticks).

Ce fichier est **normatif**. Chaque module l'implémente sans le modifier.
Toute divergence constatée est signalée, pas corrigée unilatéralement.

## 0. Propriété des fichiers (ne jamais écrire hors de sa liste)

| Module | Fichiers possédés |
|---|---|
| M1 — moteur de session | `agentl/studio/session.py`, `agentl/studio/events.py` |
| M2 — serveur | `agentl/studio/server.py`, `agentl/studio/__init__.py`, patch `agentl/cli.py` (ajout sous-commande `studio` uniquement) |
| M3 — coque + design | `agentl/studio/static/index.html`, `static/css/studio.css`, `static/js/app.js`, `static/js/bus.js` |
| M4 — canevas graphe | `agentl/studio/static/js/canvas.js`, `static/js/graph.js` |
| M5 — éditeur + panneaux | `static/js/editor.js`, `static/js/inspector.js`, `static/js/timeline.js` |
| M6 — tests | `tests/test_studio_aaa.py` |

## 0 bis. Norme de nommage — impérative

Un programme `X.agent` est servi par l'hôte `X.py`, **même répertoire, même
nom**. Il n'existe ni option `--host`, ni sélecteur d'hôte : le nom décide, et
sans `X.py` il n'y a pas d'exécution. La lecture syntaxique des capteurs et
outils déclarés par l'hôte ne fait que **confirmer** l'appariement — jamais
d'import, vérifier ne doit pas exécuter de code arbitraire.

Publié dans `hostStatus` : `{path, exists, confirmed, sensors,
expectedSensors, tools, expectedTools, missing[], message}`.

## 1. Contraintes globales

- **Zéro dépendance frontend externe** (pas de CDN, pas de npm) : ESM natif,
  CSS natif, SVG inline. Le studio doit fonctionner hors ligne / air-gap.
- Backend : `fastapi` + `uvicorn` (déjà installés). Aucune autre dépendance.
- Python ≥ 3.10, style du dépôt : `from __future__ import annotations`,
  docstrings en français, pas de traceback nu remontant à l'utilisateur
  (toute erreur devient `AgentLError` ou une réponse JSON `{"error": ...}`).
- Qualité AAA : chaque module fournit ses propres garde-fous d'erreur ; aucun
  `except Exception: pass`.
- Le thème suit `prefers-color-scheme` **et** un attribut `data-theme` sur
  `<html>` (le toggle doit gagner dans les deux sens).

## 2. Schéma d'événement (M1 émet, M2 transporte, M3/M4/M5 consomment)

Tout message serveur→client est un objet JSON `{"type": ..., ...}`.

```jsonc
// flux d'exécution
{"type":"run.started",  "runId":"r3", "agent":"soc_analyst", "maxTicks":12}
{"type":"tick",         "runId":"r3", "tick":4}
{"type":"phase",        "runId":"r3", "tick":4, "phase":"OBSERVE", "status":"enter|exit"}
{"type":"trace",        "runId":"r3", "seq":118, "tick":4,
 "kind":"OBSERVE|BELIEF|GOAL|PLAN|STEP|TOOL|BLOCKED|APPROVAL|VERIFY_OK|VERIFY_FAIL|LLM|MEMORY|EVENT|ASK|DELEGATE|ERROR|INFO|RETRY|BAYES|PLANNER|MESSAGE|SHARED|TICK",
 "text":"...", "detail":"...", "nodeId":"soc_analyst::tool::isolate_host", "ts":1753600000.12}
{"type":"state",        "runId":"r3", "tick":4,
 "beliefs":{"wazuh.alert_count":37}, "hypotheses":{"compromise":0.93},
 "goals":{"contain":"active"}, "metrics":{"ticks":4,"tool_calls":2,...}}
{"type":"run.finished", "runId":"r3", "status":"done|stopped|error",
 "metrics":{...}, "error":null}
{"type":"run.paused",   "runId":"r3", "tick":4}
{"type":"run.resumed",  "runId":"r3", "tick":4}

// interaction humaine (le studio EST l'approbateur / le répondeur)
{"type":"prompt", "runId":"r3", "promptId":"p7", "mode":"approve|ask",
 "question":"isolate_host(host=srv-12) — approuver ?", "reason":"RISK high",
 "nodeId":"soc_analyst::tool::isolate_host", "payload":{...}}

// édition / analyse
{"type":"diagnostics", "diags":[{"severity":"error|warning","code":"V101",
 "message":"...","line":42,"col":null,"nodeId":null}]}
// `col` vaut TOUJOURS null : agentl.analyzer.Diagnostic ne porte pas de
// colonne. Le champ existe pour l'avenir ; l'éditeur souligne la ligne.
{"type":"graph", "graph":{...}}   // même forme que agentl.viz.build_program
{"type":"source", "text":"...", "rev":7}   // diffusion d'une édition
{"type":"hello",  "session":{"file":"examples/soc_analyst.agent",
 "host":"examples/soc_analyst.py","hostStatus":{"exists":true,"confirmed":true,
 "sensors":6,"expectedSensors":6,"message":"…"},"rev":7,"running":false}}
{"type":"error",  "message":"..."}
```

Message client→serveur (WebSocket, même canal) :

```jsonc
{"type":"run",    "ticks":12, "step":false, "pace":0.35}
// `host` n'existe plus : la norme de nommage impose `X.agent` → `X.py`.
// Un client qui en propose un reçoit une erreur explicite.
{"type":"pace",   "value":1.6}               // allure de démonstration, à chaud
{"type":"pause"} {"type":"resume"} {"type":"step"} {"type":"stop"}
{"type":"reply",  "promptId":"p7", "value":true}
{"type":"edit",   "text":"...", "rev":7}     // remplacement complet du source
{"type":"ping"}
```

**Règles de flux** : `seq` est un entier monotone par run, jamais réutilisé —
le client s'en sert pour ordonner et dédupliquer. Un `nodeId` absent vaut
`null`. Les `float` non finis sont sérialisés en `null` (jamais `NaN`).

## 3. Identité de nœud (partagée M1 ↔ M4)

L'identifiant de nœud est **celui que produit `agentl/viz.py`**, et rien
d'autre — M4 réutilise `build_program()`, donc toute autre convention
casserait l'illumination :

```
nodeId = f"{lane}:{key}"                 # programme mono-agent
nodeId = f"{agent}|{lane}:{key}"         # programme multi-agents
```

où `lane ∈ {observe, hypothesis, goal, trigger, plan, tool, policy, society}`
et `key` est le nom déclaré (chemin pointé pour `observe`). Attention : la
lane n'est pas le genre — `decide`, `event` et `inbox` tombent tous dans
`trigger`, `message` dans `society`. M1 dérive ses `nodeId` de cette règle
(`events.node_id`), M4 et M5 acceptent en outre la forme historique
`{agent}::{kind}::{key}` par tolérance.

> Révision : ce paragraphe décrivait initialement `{agent}::{kind}::{key}`.
> Le code de `viz.py` fait foi ; le contrat a été aligné après vérification
> (test `TestNodeIdDivergence`).

## 4. API HTTP (M2)

| Méthode | Route | Rôle |
|---|---|---|
| GET | `/` | `index.html` |
| GET | `/static/*` | fichiers statiques |
| GET | `/api/session` | `{file, source, rev, graph, diags, host, hostStatus, running}` |
| GET | `/api/files` | fichiers `.agent` découvrables (cwd, `examples/`) |
| POST | `/api/open` | `{path}` → charge un fichier, renvoie comme `/api/session` |
| POST | `/api/source` | `{text, rev}` → `{rev, graph, diags}` (409 si `rev` périmé) |
| POST | `/api/save` | `{path?}` → écrit sur disque |
| POST | `/api/check` | `{text?}` → `{diags}` |
| POST | `/api/verify` | `{text?, depth}` → `{report, theorems, refuted}` |
| WS | `/ws` | canal temps réel §2 |

Le serveur n'écrit sur disque que sur `/api/save` explicite.
`--no-save` désactive la route (403).

## 5. Contrat JS (ESM, chemins relatifs)

`bus.js` (M3) exporte :
```js
export const bus = { on(type, fn), off(type, fn), emit(type, payload), send(msg) };
export function connect(url);       // WS + reconnexion exponentielle
export const state = { session, running, tick, lastState, diags, graph };
```
Chaque panneau s'abonne via `bus.on('trace', ...)` etc. et n'appelle jamais
`fetch` sur une route d'un autre module — il passe par `bus.send`.

`canvas.js` (M4) exporte `export function mountCanvas(el)` et réagit à
`graph`, `trace` (illumination du `nodeId`), `phase`, `run.*`.
`editor.js` / `inspector.js` / `timeline.js` (M5) exportent respectivement
`mountEditor(el)`, `mountInspector(el)`, `mountTimeline(el)`.
`app.js` (M3) monte les quatre dans la grille et ne contient aucune logique
métier de panneau.

## 6. Ergonomie exigée (M3 arbitre)

Grille : barre supérieure (fichier, run/pause/step/stop, ticks, hôte, thème) ·
gauche = éditeur de code · centre = canevas · droite = inspecteur ·
bas = chronologie/journal filtrable. Panneaux redimensionnables, état
persisté en `localStorage`. Raccourcis : `⌘/Ctrl+Enter` lancer, `Espace`
pause/reprise, `→` pas à pas, `⌘/Ctrl+S` sauvegarder, `/` filtrer le journal.
Aucun mouvement clignotant ; transitions ≤ 180 ms ; contraste AA minimum.
