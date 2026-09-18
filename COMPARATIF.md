

## Verdict

**Non, AGENT-L n’est pas aujourd’hui supérieur globalement à LangGraph, PydanticAI ou CrewAI.**

En revanche, il possède une propriété que ces frameworks n’ont pas au même niveau : **AGENT-L traite l’agent comme un programme déclaratif qu’on peut analyser et partiellement vérifier avant de l’exécuter, au lieu de traiter principalement l’agent comme du Python orchestrant un LLM.**

Et sur cet axe précis — **gouvernance des actions, politiques statiques, réduction de l’autorité du LLM, vérification avant exécution** — je considère AGENT-L comme **architecturalement plus fort** que les trois autres.

| Axe                                       | AGENT-L                 | LangGraph                | PydanticAI                     | CrewAI                  |
| ----------------------------------------- | ----------------------- | ------------------------ | ------------------------------ | ----------------------- |
| Vérification statique avant exécution     | **Très forte**          | Faible/native limitée    | Faible/native limitée          | Faible                  |
| Policies de sécurité intégrées au langage | **Très forte**          | À construire             | À construire/composer          | À construire            |
| LLM sans autorité directe                 | **Très fort**           | Dépend de l'architecture | Dépend de l'architecture       | Autonomie plus centrale |
| Planification sous contraintes de policy  | **Native**              | Faisable manuellement    | Faisable manuellement          | Moins centrale          |
| Typage Python / structured outputs        | Moyen                   | Bon                      | **Excellent**                  | Bon                     |
| Durable execution / crash recovery        | **Faible actuellement** | **Excellent**            | **Excellent**                  | Bon                     |
| Async / parallélisme                      | Faible                  | **Fort**                 | **Fort**                       | Fort                    |
| Écosystème/providers                      | Faible                  | **Très fort**            | **Très fort**                  | **Très fort**           |
| Multi-agent ergonomique                   | Moyen                   | Fort/flexible            | Composable                     | **Très fort**           |
| Audit/replay déterministe                 | **Très intéressant**    | Très fort                | Très fort avec durable engines | Bon                     |
| Maturité publique                         | **Encore faible**       | Très forte               | Forte                          | Forte                   |

Le point important est que **ce ne sont pas exactement les mêmes catégories de produit**.

---

## Ce que je trouve réellement meilleur dans AGENT-L

La meilleure décision architecturale est celle-ci :

> **Le LLM propose ; le runtime décide.**

Ce n'est pas seulement écrit dans le README. C'est visible dans le code.

Dans `runtime.py`, une proposition du LLM ne peut sélectionner qu'un plan déclaré. Les actions passent par `_authorize_action()`, qui appelle `PolicyEngine.check()`. Une erreur dans l'évaluation de la policy se replie vers **DENY**, l'approbation humaine est également fail-closed, et un plan inconnu proposé par le modèle est bloqué.

C'est une différence beaucoup plus profonde qu'un simple guardrail ajouté autour d'un agent.

[runtime.py](https://github.com/jmalfonsi/AGENT-L/blob/main/agentl/runtime.py?utm_source=chatgpt.com)
[policy.py](https://github.com/jmalfonsi/AGENT-L/blob/main/agentl/policy.py?utm_source=chatgpt.com)

Prenons :

```text
NEVER delete_customer WHEN customer.vip == true
```

Dans AGENT-L, ce n'est pas une phrase mise dans un system prompt en espérant que le LLM la respecte.

Elle entre dans :

```text
parser
  ↓
AST
  ↓
Analyzer / Verifier
  ↓
Planner
  ↓
PolicyEngine
  ↓
Runtime
  ↓
Host.invoke()
```

C'est précisément la bonne séparation pour des agents qui ont des **effets de bord sérieux**.

Encore plus intéressant : le planificateur consulte lui aussi les policies. Une action interdite peut donc être **éliminée de l'espace de recherche avant même que le runtime envisage de l'exécuter**.

C'est supérieur à :

```python
response = llm(...)
if looks_safe(response):
    tool(...)
```

et également supérieur à une architecture où l'on ajoute seulement une callback d'approbation autour d'un tool call.

---

# Le `Verifier` est le vrai différenciateur

C'est probablement la partie la plus originale du projet.

AGENT-L ne se contente pas de faire :

```text
parse → execute
```

Il possède :

```text
parse
  ↓
analyze
  ↓
verify
  ↓
execute
```

Le `Verifier` travaille sur les chemins d'exécution, les conditions d'entrée, les `NEVER`, l'atteignabilité, les capacités mortes, la surface offerte au LLM, la provenance, la vivacité, etc.

Il distingue même :

```python
holds = True
holds = False
holds = None
```

c'est-à-dire :

```text
démontré
réfuté
non démontré / borné
```

C'est une excellente décision. Il ne transforme pas automatiquement un timeout du solveur en « sûr ».

[verifier.py](https://github.com/jmalfonsi/AGENT-L/blob/main/agentl/verifier.py?utm_source=chatgpt.com)

LangGraph sait extrêmement bien orchestrer des graphes et reprendre leur exécution, mais ce n'est pas un vérificateur statique de propriétés métier. Sa documentation le présente comme un framework d'orchestration bas niveau pour agents stateful et long-running, avec persistence, streaming et human-in-the-loop. ([LangChain][1])

Même chose pour PydanticAI : son point fort est plutôt le **typage et la validation de l'interface avec le modèle**. Les outputs structurés conservent leur type générique Python et sont validés avec les schémas Pydantic. ([GitHub][2])

AGENT-L cherche à vérifier autre chose :

> « Même avec des données valides et un LLM qui propose quelque chose de plausible, cette action peut-elle apparaître dans un état où elle est interdite ? »

C'est plus ambitieux.

---

## MCP est étonnamment bien pensé

J'ai particulièrement apprécié `mcp.py`.

Lorsqu'AGENT-L importe un outil MCP, il **refuse d'inventer** :

* son niveau de risque ;
* ses effets de bord ;
* ses postconditions ;
* son coût.

Le risque devient `UNSET`, ce qui empêche le programme de passer `check` tant qu'il n'a pas été qualifié.

Les annotations MCP comme `destructiveHint` ne sont pas considérées comme une autorité de sécurité, puisque c'est le serveur lui-même qui les fournit.

Et le catalogue MCP est scellé par hash : une modification ultérieure du catalogue est détectée.

[mcp.py](https://github.com/jmalfonsi/AGENT-L/blob/main/agentl/mcp.py?utm_source=chatgpt.com)

C'est exactement le genre de décision que je voudrais pour un système sensible. Beaucoup d'intégrations d'outils font implicitement :

```text
MCP says tool exists
       ↓
give tool to LLM
       ↓
hope for the best
```

AGENT-L introduit plutôt une phase :

```text
capability discovery
       ↓
human qualification
       ↓
static contract
       ↓
runtime authority
```

C'est très bon.

---

# Mais il y a un gros problème : ce n'est pas encore un runtime durable

C'est actuellement **la faiblesse numéro 1**, et le dépôt lui-même l'admet explicitement dans `PLAN_SUITE_AUDIT_GENERAL_COMPARATIF.md` :

> `REPLAY ≠ RESUME`

C'est juste.

Aujourd'hui, AGENT-L sait très bien **rejouer** une exécution enregistrée.

Mais il ne possède pas encore nativement le cycle transactionnel :

```text
persist intent
     ↓
authorize
     ↓
persist authorization
     ↓
execute idempotently
     ↓
persist result
     ↓
checkpoint
```

Le cas problématique est simple :

```text
authorize transfer()
        ↓
transfer()
        ↓
PROCESS CRASH
        ↓
restart
        ↓
????
```

Si on ne sait pas atomiquement que `transfer()` a déjà eu lieu, le retry peut produire :

```text
transfer()
transfer()
```

Pour un agent SOC ce n'est déjà pas agréable.

Pour :

```text
delete_account()
charge_credit_card()
send_payment()
deploy_production()
rotate_credentials()
```

c'est rédhibitoire.

Et c'est précisément là que **LangGraph est aujourd'hui plus avancé** : ses checkpointers conservent les états et les écritures de nœuds réussis pour permettre la reprise. Il dispose notamment de backends SQLite/PostgreSQL synchrones et asynchrones. ([LangChain][3])

LangGraph prévoit également explicitement que les opérations non déterministes et les effets de bord soient encapsulés de manière à ne pas être rejoués naïvement pendant une reprise. ([GitHub][4])

PydanticAI est désormais encore plus difficile à battre sur ce terrain : il offre des intégrations officielles de durable execution avec **Temporal, DBOS, Prefect, Restate et AWS Lambda Durable Functions**. ([Pydantic][5])

DBOS, par exemple, checkpoint les étapes en base et reprend le workflow à partir de la dernière étape terminée. ([Pydantic][6])

Donc sur ce sujet :

**LangGraph / PydanticAI > AGENT-L. Nettement.**

---

# Deuxième faiblesse : le runtime est essentiellement synchrone

`Host.invoke()` est :

```python
return fn(**args)
```

L'interface `LLM.reason()` est synchrone.

`Runtime.run()` est synchrone.

Même MCP, qui est intrinsèquement async, crée une event loop privée pour présenter au runtime une interface **bloquante**.

Et `Society.tick()` fait :

```python
for name in self.order:
    self.runtimes[name].tick()
```

Donc la « société d'agents » est une simulation coopérative **round-robin**, pas un runtime réellement concurrent.

[host.py](https://github.com/jmalfonsi/AGENT-L/blob/main/agentl/host.py?utm_source=chatgpt.com)
[society.py](https://github.com/jmalfonsi/AGENT-L/blob/main/agentl/society.py?utm_source=chatgpt.com)

Pour 3 agents et quelques appels API, ce n'est pas grave.

Pour :

```text
100 agents
500 MCP tools
streaming
websockets
long-running jobs
parallel tool calls
backpressure
cancellation
timeouts distribués
```

ça devient une limitation architecturale.

LangGraph expose des primitives async et des checkpointers async. ([LangChain][1])

CrewAI Flows est également conçu autour de workflows event-driven ; ses points d'entrée satisfaits peuvent notamment s'exécuter en parallèle. ([CrewAI Documentation][7])

---

# PydanticAI bat clairement AGENT-L sur le système de types

AGENT-L possède un typage raisonnable :

```text
Int
Number
String
Bool
Symbol
List
```

et le runtime effectue de nombreux contrôles intelligents.

J'ai notamment vu les corrections qui empêchent :

```python
True == 1
```

de devenir accidentellement un entier d'outil, ainsi que les coercitions booléennes strictes pour les sorties LLM.

C'est très bien du point de vue sécurité.

Mais ce n'est pas au niveau de :

```python
class Payment(BaseModel):
    iban: IBAN
    amount: Decimal
    currency: Literal["EUR", "USD"]
```

avec génériques, type-checking IDE, validation Pydantic, JSON Schema et intégration native Python.

PydanticAI transporte le type de sortie jusque dans `AgentRunResult[T]` et utilise les modèles Pydantic pour produire et valider le JSON Schema. ([GitHub][2])

Donc pour une application Python traditionnelle :

**ergonomie + types : PydanticAI gagne.**

AGENT-L gagne autre chose : la **possibilité d'analyser globalement le programme**, justement parce qu'il a choisi un DSL beaucoup plus restreint.

C'est le compromis fondamental :

```text
PydanticAI
Python expressif
→ très ergonomique
→ plus difficile à raisonner globalement

AGENT-L
langage limité
→ moins expressif
→ beaucoup plus analysable
```

Et je pense que ce choix d'AGENT-L est cohérent.

---

# Le multi-agent de CrewAI est plus riche, mais moins intéressant pour la sûreté

CrewAI est beaucoup plus naturel pour écrire :

```text
researcher
   ↓
analyst
   ↓
reviewer
   ↓
writer
```

avec rôles, délégation et collaboration.

CrewAI distingue maintenant les **Crews**, orientés autonomie collaborative, des **Flows**, orientés orchestration structurée et état. Ses Flows ont également de la persistance et peuvent restaurer l'état après redémarrage. ([GitHub][8])

Mais AGENT-L pose une question différente :

```text
Quels pouvoirs possède chaque agent ?
Quand peut-il les exercer ?
Quelles actions lui sont absolument interdites ?
Le système peut-il le prouver avant l'exécution ?
```

Pour :

```text
écrire un article
faire une recherche
brainstormer
```

je prendrais plus volontiers CrewAI.

Pour :

```text
isoler un endpoint
désactiver un compte
modifier une ACL
supprimer une ressource cloud
déployer en prod
```

la philosophie AGENT-L devient beaucoup plus intéressante.

---

# Il y a aussi une faiblesse structurelle dans les garanties

Il faut être précis ici.

**AGENT-L ne prouve pas que “le système complet est sûr”.**

Il démontre des propriétés **dans son modèle**.

Le Python hôte reste une frontière de confiance.

Le dépôt le reconnaît explicitement dans `docs/QUALITY.md` :

* le Python du host n'est pas sandboxé ;
* `boundary` est une analyse syntaxique ;
* les bibliothèques appelées par le host ne sont pas comprises sémantiquement ;
* le verifier ne constitue pas une preuve formelle de l'implémentation Python du runtime ;
* les `EFFECT` peuvent être faux par rapport au monde.

[QUALITY.md](https://github.com/jmalfonsi/AGENT-L/blob/main/docs/QUALITY.md?utm_source=chatgpt.com)

C'est essentiel.

Un host pourrait théoriquement contenir :

```python
@host.sensor("foo")
def foo():
    dangerous_side_effect()
    return 42
```

et toute la belle policy AGENT-L entourant les **tools** ne transforme pas magiquement ce Python en code sûr.

`boundary` réduit ce risque, mais ce n'est pas un sandbox.

---

# Le Trusted Computing Base est encore trop gros

C'est probablement mon principal reproche au design interne.

Les fichiers centraux sont énormes :

```text
runtime.py     ~96 KB
analyzer.py    ~68 KB
verifier.py    ~63 KB
parser.py      ~55 KB
cli.py         ~52 KB
```

Cela représente beaucoup de code maison entre :

```text
source program
```

et :

```text
« cette action est sûre »
```

Or plus le TCB est gros, plus une garantie de sécurité devient difficile à croire et à auditer.

Le dépôt l'a déjà identifié lui-même et propose d'extraire un kernel minimal autour de :

```text
ActionRequest
Policy
Authorization
ExecutionPermit
State
Provenance
```

Je suis totalement d'accord avec cette direction.

Pour devenir réellement remarquable, il faudrait arriver à :

```text
                  ┌───────────────────────┐
untrusted world → │ Runtime / Planner     │
                  └──────────┬────────────┘
                             │ ActionRequest
                             ▼
                  ┌───────────────────────┐
                  │ TINY TRUSTED KERNEL   │
                  │ policy                │
                  │ provenance            │
                  │ authorization         │
                  │ capability            │
                  └──────────┬────────────┘
                             │ sealed permit
                             ▼
                         Executor
```

avec idéalement quelques milliers de lignes auditées, pas des dizaines de milliers.

---

# Je trouve par contre la qualité défensive du code assez élevée

Beaucoup de petits détails montrent que les problèmes réels ont été réfléchis.

J'ai trouvé des protections contre :

```text
bool Python accepté comme int
"No" accepté comme True par bool("No")
payload externe masquant une donnée trusted
argument d'outil masqué par une locale de policy
NaN / inf dans les valeurs numériques
approbateur indisponible
capteur indisponible
tool en panne répétée
catalogue MCP modifié
description MCP utilisée pour prompt-injecter le modèle
sortie LLM partielle
postcondition EFFECT démentie par une observation réelle
```

Ce n'est pas du code de démo.

En particulier, la séparation :

```text
trusted state
untrusted payload
```

avec priorité au trusted state est une très bonne idée.

Le disjoncteur de tools avec half-open après cooldown est aussi pertinent.

Et les commentaires documentent les vulnérabilités passées avec leurs conséquences. C'est généralement un bon signe d'ingénierie.

---

# Mais attention à la maturité publique

Le dépôt public que j'ai sous les yeux n'a que **trois commits visibles**, les 17 et 18 septembre 2026.

Ça peut parfaitement être un import ou un historique squashé ; donc je n'en déduis pas que le code n'existait pas avant.

En revanche, cela signifie qu'on ne dispose actuellement que de très peu d'**historique public observable** permettant de mesurer :

```text
stabilité des APIs
volume de contributions
régressions dans le temps
maintenance de versions anciennes
adoption en production
qualité du processus de release
```

Et la CI elle-même contient actuellement trois tests du Studio explicitement `--deselect`, en raison d'une dette connue avec `boundary`.

Le projet est d'ailleurs assez honnête pour documenter ce problème plutôt que de le masquer.

C'est positif pour la crédibilité, mais cela interdit de dire aujourd'hui :

> « AGENT-L est plus mature que LangGraph/PydanticAI. »

Il ne l'est pas, sur la preuve publique disponible.

---

# Là où je positionnerais vraiment AGENT-L

Je ne le vendrais surtout pas comme :

> « un autre framework d'agents meilleur que LangGraph ».

Ce serait réduire son avantage.

Je le positionnerais comme :

> **un langage et runtime de gouvernance pour agents à effets de bord, avec analyse statique et vérification avant exécution.**

Là, la comparaison devient beaucoup plus favorable.

On obtient approximativement :

```text
                  flexibilité / écosystème
                         ▲
                         │
          LangGraph     │      PydanticAI
                         │
                         │
    CrewAI               │
                         │
─────────────────────────┼──────────────►
                         │       assurance /
                         │       gouvernance
                         │
                         │             AGENT-L
                         │
```

AGENT-L essaie de déplacer la confiance de :

```text
"le modèle suivra mes instructions"
```

vers :

```text
"le modèle ne possède structurellement pas le pouvoir
de sortir des capacités et politiques déclarées"
```

**C'est une meilleure fondation pour des agents qui agissent réellement sur des systèmes.**

---

## Ce qu'il lui manque pour que je puisse dire « oui, il est supérieur »

Il y a cinq chantiers qui changeraient vraiment mon verdict :

1. **Durable execution transactionnelle** : checkpoint/resume, action IDs, idempotency keys, intent/commit/result journal, exactement-once logique là où c'est possible. C'est le gap le plus important avec LangGraph/PydanticAI.

2. **Réduire fortement le trusted kernel** : extraire l'autorisation et l'exécution de `runtime.py` et rendre impossible un `Host.invoke()` sans un permit créé par le kernel.

3. **Async natif** : `async Host`, `async LLM`, tool calls concurrents bornés, cancellation, timeouts, backpressure et vraie exécution multi-agent.

4. **Provenance native dans le type des valeurs** plutôt que seulement des heuristiques statiques : `UNTRUSTED`, `LLM_DERIVED`, `OBSERVED`, attestations, transformations de provenance.

5. **Validation externe** : benchmarks reproductibles contre LangGraph/PydanticAI/CrewAI, CI publique stable sur plusieurs releases, fuzzing/property testing du parser/solver/policy et idéalement vérification formelle du petit kernel.

Si ces cinq éléments sont réalisés **sans perdre le caractère déclaratif et fail-closed actuel**, le projet devient beaucoup plus difficile à comparer directement aux frameworks existants, parce qu'il aurait simultanément :

```text
LangGraph       → durabilité / orchestration
PydanticAI      → robustesse des interfaces
CrewAI          → multi-agent
AGENT-L         → vérification / capability security
```

avec ce dernier élément réellement intégré au langage.

### Conclusion

**Aujourd'hui : je choisirais LangGraph ou PydanticAI pour construire une plateforme agentique généraliste de production. Je choisirais CrewAI pour monter rapidement une équipe d'agents autonomes.**

**Mais pour un agent SOC/SRE/ops ayant le droit d'effectuer des actions dangereuses, AGENT-L est le design le plus intéressant des quatre sur la question “comment empêcher structurellement l'IA de faire ce qu'elle ne doit pas faire ?”.**

C'est là son vrai avantage compétitif. Pas dans le nombre de providers, ni dans l'orchestration, ni dans la maturité : **dans le fait d'avoir transformé une partie du problème de l'alignement opérationnel d'un agent en problème de langage, de compilation et de vérification.**

[1]: https://langchain-ai.github.io/langgraph/reference/?utm_source=chatgpt.com "langgraph | LangChain Reference"
[2]: https://github.com/pydantic/pydantic-ai/blob/main/docs/output.md?utm_source=chatgpt.com "pydantic-ai/docs/output.md at main · pydantic/pydantic-ai · GitHub"
[3]: https://langchain-ai.github.io/langgraph/reference/checkpoints/?h=langgraph+checkpoint+sqlite+import+saver&utm_source=chatgpt.com "checkpoints | langgraph | LangChain Reference"
[4]: https://github.com/miraland-labs/langchain-ai-docs/blob/main/src/oss/langgraph/durable-execution.mdx?utm_source=chatgpt.com "langchain-ai-docs/src/oss/langgraph/durable-execution.mdx at main · miraland-labs/langchain-ai-docs · GitHub"
[5]: https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/?utm_source=chatgpt.com "Durable Execution | Pydantic Docs"
[6]: https://pydantic.dev/docs/ai/capabilities/durable_execution/dbos/?utm_source=chatgpt.com "Durable Execution with DBOS | Pydantic Docs"
[7]: https://docs.crewai.com/en/concepts/flows "Flows - CrewAI"
[8]: https://github.com/crewAIInc/crewAI/blob/main/docs/v1.15.2/en/guides/flows/mastering-flow-state.mdx?utm_source=chatgpt.com "crewAI/docs/v1.15.2/en/guides/flows/mastering-flow-state.mdx at main · crewAIInc/crewAI · GitHub"
