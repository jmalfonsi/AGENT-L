# La ligne de commande — toutes les options, et ce qu'elles garantissent

`SKILL.md` donne la chaîne des portes (`check → test → verify → boundary →
autoloop → run → replay`). Cette référence couvre le reste de `agentl` : les
options qui changent ce qu'une porte *prouve*, le scellement des journaux, le
pont MCP et le Studio.

`scripts/sync_grammar.py --check` **échoue** si une option de `agentl <cmd>
--help` n'est nommée nulle part dans le SKILL : ajouter une option sans la
documenter ici rend le contrôle rouge.

## Carte des sous-commandes

| Commande | Rôle | Options à connaître |
|---|---|---|
| `check F` | bonne formation, diagnostics E/W | — |
| `ast F` | arbre du programme | — |
| `plan F` | route synthétisée par le planificateur | `--ticks N` (0 = percevoir sans agir) |
| `infer F` | postérieurs des hypothèses | `--ticks N` (0 = percevoir sans agir) |
| `verify F` | théorèmes T1–T7 et T9 par agent, T8 pour une société | `--depth N`, `--require-proved` |
| `viz F` | graphe statique HTML | `-o/--out FICHIER`, `--open` |
| `boundary F` | linter de la frontière hôte/agent | `--project-root DIR`, `--external-policy {report,error}` |
| `test F` | exécute les `SCENARIO` | `--allow-empty`, `--trace` |
| `autoloop F` | cas dérivés, lot retenu, correction | voir ci-dessous |
| `run F` | exécute l'agent (hôte `F.py` implicite) | `--ticks`, `--quiet`, `--html`, `--events`, `--record`, `--sign-key`, `--durable`, `--run-id` |
| `replay J` | re-dérive un journal | `--source`, `--ticks`, `--quiet`, `--events`, `--force`, `--key`, `--require-seal` |
| `durable status\|export DIR` | état ou export d'une exécution durable | `-o/--out FICHIER` (export) |
| `seal [J]` | vérifie ou produit les clés de scellement | `--key`, `--keygen DIR`, `--name` |
| `mcp list\|import SRC` | inspecte ou importe les outils d'un serveur MCP | `--server`, `-o/--out`, `--force`, `--strip-descriptions`, `--unsafe-import-descriptions` |
| `studio [F]` | interface web locale | `--port`, `--host`, `--open`, `--no-save`, `--root` |

## `verify --require-proved` — la vraie porte de preuve

Par défaut, `verify` n'échoue (code 2) que sur un ✘ RÉFUTÉ. Un théorème
`◐ BORNÉ / NON PROUVÉ` passe. `--require-proved` échoue aussi sur ce cas : à
mettre en CI quand « pas réfuté » ne suffit pas. Un `◐` se traite en
augmentant `--depth` (V113/V122) ou en acceptant explicitement qu'il reste
indéterminé ; ne jamais le présenter comme démontré.

## `run --events` — la trace pour un outil

`agentl run X.agent --events F.jsonl` écrit un événement par ligne JSON
(`seq`, `agent`, `tick`, `kind`, `text`, `detail`), vidé au fil de
l'exécution. `replay --events` produit le même format. À préférer à tout
découpage de la trace texte (`┌─ tick`, `🔧`…), qui change de mise en forme
sans prévenir. Le fichier est ouvert **avant** le premier tick : un chemin
inutilisable est refusé (code 2) avant que l'agent n'agisse. `--quiet` supprime
l'écho de la trace dans le terminal.

## `autoloop` — les options qui bornent la mesure

| Option | Sens |
|---|---|
| `--holdout P` | part des cas retenus, jamais montrés à la boucle (défaut 0,3). **0 supprime la seule mesure du par-cœur.** |
| `--seed N` | graine du tirage des cas (défaut 7). Changer de graine = autre lot, donc autre verdict possible. |
| `--max-cases N` | plafond de cas dérivés (défaut 200) |
| `--max-attempts N` / `--patience N` / `--budget S` | les trois freins de la boucle de correction |
| `--model gemini-*\|claude-*` | rédacteur des corrections ; sans lui, diagnostic seul |
| `--host-pass` | second temps contre l'hôte réel, **en lecture seule** (aucun outil qui écrit, aucune approbation accordée) |
| `--host-ticks N` | nombre de ticks de ce second temps |
| `-o/--out FICHIER` | écrit le programme corrigé ; sans lui rien n'est écrit |

Code de sortie 3 : appris par cœur (voir `autoloop.md`).

## Sceller et signer un journal

`run --record J.json` écrit un journal **chaîné** (SHA-256, sans clé) :
altération localisée, entrée retirée détectée, troncature en fin trahie par la
tête de chaîne scellée. La chaîne atteste l'**intégrité**, pas l'**origine** :
qui détient le fichier peut le réécrire en entier. La signature ferme cette
brèche.

| Algorithme | Dépendance | Qui peut forger | Usage |
|---|---|---|---|
| `HMAC-SHA256` | stdlib | tout détenteur du secret, **y compris le vérificateur** | entre composants d'un même système |
| `Ed25519` | extra `sign` (`cryptography`) | seul le détenteur de la clé privée | audit externe |

```bash
agentl seal --keygen keys/ --name prod     # prod.key (0600) + prod.pub ; refuse d'écraser
agentl run  X.agent --record j.json --sign-key keys/prod.key
agentl seal j.json --key keys/prod.pub     # vérifie sans le programme ni exécution
agentl replay j.json --key keys/prod.pub --require-seal
```

- `--sign-key` accepte le chemin d'une clé privée Ed25519 (PEM), le chemin
  d'un fichier de secret, ou un secret littéral ; à défaut, la variable
  `AGENTL_JOURNAL_KEY`. La clé est résolue **avant le premier tick** : une clé
  inutilisable arrête le run avant que l'agent n'agisse.
- Ce qui est signé est la **méta scellée** (tête de chaîne, `trace_sha256`,
  `source_sha256`, format), pas chaque entrée.
- **Trois verdicts, jamais confondus** :
  1. chaîne intacte + sceau valide → pièce authentique ;
  2. chaîne intacte, non signé → non contrefait mais non attribué (code 0
     pour `seal`, rejeu autorisé sauf `--require-seal`) ;
  3. chaîne rompue, ou sceau présent et faux → corrompu ou falsifié (code 1,
     **le rejeu s'arrête toujours**). Un sceau valide avec chaîne rompue veut
     dire : entrées retouchées après signature.
- `replay --require-seal` échoue aussi si le journal n'est pas signé.
- **Ne prouve pas *quand***. Rien n'horodate : un journal peut être re-signé
  plus tard avec la même clé. Ne jamais écrire « signé donc daté ».
- Pas de HMAC pour un audit externe : le vérificateur détiendrait de quoi
  forger. Utiliser Ed25519 et ne remettre que la clé publique.
- `seal --keygen` crée le répertoire, écrit la clé privée en 0600 dès sa
  création et refuse d'écraser une clé existante.

## Importer des outils MCP

```bash
agentl mcp list   mcp.json                       # inspecte, n'écrit rien
agentl mcp import mcp.json --server github -o github.agent
agentl mcp import tools-list.json --server github   # catalogue brut capturé : --server obligatoire
```

Source : une configuration (`mcpServers`) pour parler à un vrai serveur
(extra `mcp`), ou un `tools/list` capturé en JSON, qui marche **hors ligne**
et rend l'import reproductible.

Ce que le pont refuse de combler, et pourquoi :

- **`RISK` sort à `UNSET`** → `E011` fait échouer `agentl check` (donc `run`).
  Aucun `ALLOW *` ne le fait taire : trancher chaque risque à la main, puis
  `agentl check`. Les annotations MCP (`readOnlyHint`…) sont recopiées **en
  commentaire**, jamais en valeur : des auto-déclarations du serveur.
- **Pas de `EFFECT` / `OUTCOME` / `COST`** → l'outil n'est pas planifiable ; il
  s'appelle depuis un `PLAN` écrit à la main. Ne pas en inventer pour le rendre
  planifiable : un `EFFECT` qui ment corrompt planificateur et vérificateur.
- **Les descriptions ne sont pas importées par défaut** : texte d'un tiers, lu
  par le modèle. `--unsafe-import-descriptions` les importe (à lire d'abord ;
  `B015` signale celles au ton impératif) ; `--strip-descriptions` est un
  synonyme obsolète du défaut, incompatible avec le précédent.
- **Le catalogue est figé** : l'empreinte SHA-256 du `tools/list` est scellée
  dans l'en-tête ; `MCPHost` (et `AsyncMCPHost`) **refuse de démarrer** si le
  serveur rend un autre catalogue, en nommant l'écart.
- Contraintes de schéma que `INPUT` ne porte pas (`minLength`, `pattern`,
  `oneOf`…) : recopiées en commentaire, **non vérifiées**.
- `import` refuse d'écraser un fichier existant (les `RISK` déjà tranchés y
  seraient perdus) ; `--force` pour passer outre.

## Le Studio — dépendances, réseau, écriture

Installer l'extra `studio` (FastAPI, uvicorn, websockets) : *le Studio n'est
pas sans dépendance*. Les extras `sign` et `mcp` sont séparés ; le cœur reste
sans dépendance.

| Option | Sens |
|---|---|
| `--port N` | port d'écoute (défaut 8765) |
| `--host H` | interface (défaut `127.0.0.1`). `0.0.0.0` expose l'exécution d'agents et l'écriture de fichiers à tout le réseau. |
| `--root DIR` | racine des fichiers accessibles |
| `--no-save` | lecture seule : aucune écriture sur disque |
| `--open` | ouvre le navigateur |

Depuis la 1.11, le serveur refuse : toute requête dont l'`Origin` n'est pas le
serveur lui-même (défense CSRF), tout `Host` hors liste (rebinding DNS), tout
corps qui n'est pas `application/json`, et l'écriture d'un fichier qui n'est
pas `.agent`. Un proxy placé devant doit présenter le même `Host` que la
liaison. L'hôte `xxx.py` n'est jamais sélectionnable : il est imposé par la
norme.
