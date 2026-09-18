Oui. Le bon objectif n’est pas de “faire plus de features”, mais de **réduire les hypothèses de confiance autour du noyau sans modifier ses invariants**.

Je garderais absolument intacts : **LLM = oracle et non pilote**, médiation obligatoire des actions par la policy, `NEVER` irrévocable, fail-closed sur l’inconnu, séparation `untrusted`, capacités/plans déclarés, vérification statique et replay déterministe. Ce sont eux qui donnent à AGENT-L son identité.

Le dépôt reconnaît déjà correctement plusieurs limites : les hosts Python ne sont pas sandboxés, le Studio exécute du Python arbitraire, le comportement des modèles tiers est hors périmètre, et le contrôle `boundary` reste syntaxique.  De même, l’audit reconnaît que la provenance reste en partie heuristique et qu’une garde de confiance ne prouve pas qu’un détecteur d’injection soit bon.  C’est précisément là qu’il faut travailler.

## Architecture cible

Je ferais évoluer AGENT-L vers quatre couches très nettes :

```text
┌──────────────────────────────────────────────────────────┐
│                     Applications                         │
│ Studio / CLI / VSCode / agents / integrations           │
├──────────────────────────────────────────────────────────┤
│                  Adapters non fiables                     │
│ LLM / MCP / HTTP / DB / filesystem / queues             │
├──────────────────────────────────────────────────────────┤
│            Execution + Durable Runtime                    │
│ checkpoint / transactions / retries / replay / telemetry │
├──────────────────────────────────────────────────────────┤
│                 TRUSTED KERNEL AGENT-L                    │
│ state │ policy │ provenance │ capabilities │ verifier    │
└──────────────────────────────────────────────────────────┘
```

Le principe est important : **on enrichit les couches supérieures ; on rétrécit la couche inférieure**.

---

## Plan proposé

| Phase  | Travail                                | Gain                            | Risque pour les qualités actuelles |
| ------ | -------------------------------------- | ------------------------------- | ---------------------------------- |
| **P0** | Geler les invariants de sécurité       | Empêche les régressions futures | Aucun                              |
| **P1** | Réduire le Trusted Computing Base      | Plus auditable                  | Faible                             |
| **P2** | Durabilité transactionnelle            | Rattrape LangGraph              | Moyen                              |
| **P3** | Provenance/taint interprocédural       | Renforce prompt-injection       | Faible                             |
| **P4** | Evidence/Grounding pour hallucinations | Réduit erreurs factuelles       | Faible                             |
| **P5** | Isolation des hosts/outils             | Réduit blast radius             | Moyen                              |
| **P6** | Observabilité standard                 | Production-ready                | Aucun                              |
| **P7** | Providers et extensions                | Adoption                        | Faible                             |
| **P8** | Vérification formelle du kernel        | Crédibilise les garanties       | Aucun                              |
| **P9** | CI/releases/benchmarks externes        | Crédibilité publique            | Aucun                              |

---

# P0 — Écrire la “constitution” du runtime

Avant tout refactor, créer une petite spécification normative du noyau, par exemple :

```text
SECURITY INVARIANTS

I1. Aucune action externe ne peut atteindre Host.invoke
    sans ActionRequest + PolicyDecision.

I2. NEVER ne peut jamais être overridden.

I3. UNKNOWN ne peut jamais créer une autorisation.

I4. Une donnée untrusted ne peut pas masquer une donnée trusted.

I5. Un LLM ne peut sélectionner que des capacités/plans déclarés.

I6. Une action REQUIRE_APPROVAL ne peut être exécutée
    sans approval lié cryptographiquement à cette action.

I7. Le replay ne peut pas créer une action absente du journal original.

I8. Toute donnée LLM utilisée pour une décision risquée
    conserve sa provenance.

I9. Un crash/retry ne peut pas doubler un effet externe.

I10. Une sortie textuelle du LLM ne constitue jamais une autorité.
```

Puis transformer chaque invariant en tests adversariaux impossibles à désactiver.

Le projet a déjà une excellente base : la documentation annonce 1 070 tests, 90,44 % de couverture, 68 nouvelles régressions CHECK/TEST et 98 cas Boundary.

Je ne remplacerais surtout pas cela.

Je créerais plutôt une suite extrêmement petite :

```text
tests/kernel_invariants/
```

qui doit rester verte indépendamment du reste.

---

# P1 — Réduire drastiquement le Trusted Computing Base

C'est probablement la modification architecturale la plus importante.

Aujourd'hui, `runtime.py`, `verifier.py`, `analyzer.py` concentrent énormément de logique. Plus le code qui participe à une garantie de sécurité est volumineux, plus cette garantie devient difficile à auditer.

Je viserais un kernel de ce genre :

```text
agentl/kernel/
    action.py
    capability.py
    policy.py
    state.py
    provenance.py
    authorization.py
    effects.py
```

Et surtout une seule primitive autorisée :

```python
result = kernel.execute(action)
```

Le runtime ne devrait **jamais** appeler directement :

```python
host.invoke(...)
```

> **Vérification.** Il ne l'appelle déjà qu'à **un seul endroit**
> (`agentl/runtime.py`, dans `call_tool`), derrière `_authorize_action`, qui
> couvre aussi `DELEGATE` depuis la v1.6. Le permis ne ferme donc pas un trou
> ouvert : il transforme une convention en garantie de type, ce qui vaut pour
> la non-régression mais ne réduit pas d'une ligne la TCB. Le vrai travail de
> P1 est l'extraction en `agentl/kernel/`.

La seule chaîne légale deviendrait :

```text
runtime
   ↓
ActionRequest
   ↓
Kernel.authorize()
   ↓
ExecutionPermit
   ↓
Executor.commit()
   ↓
Host
```

Et `ExecutionPermit` devrait être impossible à construire depuis l'extérieur.

Par exemple :

```python
@dataclass(frozen=True)
class ExecutionPermit:
    action_hash: str
    policy_hash: str
    state_version: int
    decision: Literal["ALLOW"]
    nonce: str
```

Cela transforme :

> « normalement tous les chemins passent par PolicyEngine »

en quelque chose de beaucoup plus proche de :

> « architecturalement, il n'existe qu'un seul chemin ».

**Ne change pas PolicyEngine. Enferme-le.**

---

# P2 — Ajouter la vraie durable execution

C'est le plus gros manque face à LangGraph/PydanticAI dans une utilisation production.

Le replay actuel est une très bonne propriété, mais :

```text
REPLAY ≠ RESUME
```

Il faut ajouter :

```text
checkpoint
+
resume
+
idempotency
+
transactions
```

sans toucher à la déterminisme du replay.

Le modèle devrait devenir :

```text
PLAN ACTION
    ↓
persist intent
    ↓
authorize
    ↓
persist permit
    ↓
execute with idempotency_key
    ↓
persist result
    ↓
apply EFFECT
    ↓
OBSERVE
    ↓
checkpoint
```

Et non :

```text
authorize
↓
tool()
↓
hope the process doesn't crash here
```

Le cas critique est :

```text
transfer_money()
       ↓
process crashes
       ↓
resume
       ↓
transfer_money() AGAIN
```

AGENT-L devrait générer pour chaque action un identifiant déterministe :

```text
execution_id
agent_id
run_id
action_id
attempt_id
idempotency_key
```

Puis distinguer :

```text
PLANNED
AUTHORIZED
COMMITTED
OBSERVED
VERIFIED
```

Cela colle parfaitement à la philosophie AGENT-L : **rendre les effets explicites**.

---

# P3 — Transformer la provenance heuristique en provenance native

C'est à mon avis la prochaine grosse différenciation possible.

Aujourd'hui, certaines analyses de provenance sont volontairement heuristiques. Le dépôt le reconnaît explicitement pour W119/W125 et indique qu'elles ne constituent pas une preuve interprocédurale générale.

Au lieu d'inférer la provenance depuis des noms :

```text
raw_message
trusted_target
validated_account
```

je la rendrais partie du **type de valeur runtime**.

Conceptuellement :

```python
Value(
    data="server-42",
    provenance={
        Source.MESSAGE,
        Source.LLM
    },
    trust=UNTRUSTED,
    attestations=[],
)
```

Puis :

```text
web page
   ↓
UNTRUSTED

LLM(web page)
   ↓
UNTRUSTED + DERIVED_BY_LLM

validator(value)
   ↓
toujours UNTRUSTED
+ ATTESTED_BY validator_xyz
```

Très important :

```text
validated != trusted
```

Un validator peut attester :

```text
exists(account_id)
```

sans transformer ce compte en donnée “de confiance absolue”.

Tu obtiens alors un vrai lattice :

```text
SOURCE
  USER
  WEB
  MESSAGE
  MCP
  TOOL
  SENSOR
  LLM

TRUST
  UNTRUSTED
  OBSERVED
  ATTESTED

DERIVATION
  COPY
  TRANSFORM
  LLM
  TOOL

SENSITIVITY
  PUBLIC
  INTERNAL
  SECRET
  CREDENTIAL
```

Et les politiques peuvent raisonner dessus.

Exemple :

```text
NEVER transfer
WHEN target PROVENANCE CONTAINS LLM
AND target NOT ATTESTED BY resolve_account
```

Là, AGENT-L commencerait à avoir quelque chose de réellement rare.

---

# P4 — Ajouter un modèle d’“evidence” pour les hallucinations

Ne cherche pas à empêcher les hallucinations.

Empêche les **affirmations importantes sans preuves**.

Par exemple, ajouter une structure native :

```python
Claim(
    value=0.93,
    evidence=[
        Evidence(
            source="fraud_database",
            reference="tx-3388",
            observed_at=...
        )
    ]
)
```

Et permettre :

```text
REQUIRE EVIDENCE fraud_score
FROM fraud_service
FRESH < 5m
```

ou :

```text
VERIFY customer.exists
USING customer_service
```

Puis réserver certains effets :

```text
NEVER freeze_account
WHEN decision.evidence_count == 0
```

La différence philosophique serait très forte :

```text
Pydantic:
"la réponse a la bonne forme"

AGENT-L:
"la réponse qui déclenche cet effet
 doit avoir une chaîne de preuves acceptable"
```

Cela attaque beaucoup mieux les hallucinations sémantiques.

---

# P5 — Sandboxing : traiter sérieusement la frontière Host

C'est le trou de sécurité principal actuellement.

Le propre `SECURITY.md` dit que les modules hosts sont du Python non sandboxé et qu'exécuter un `.agent` tiers avec son host revient à exécuter du code tiers. Il indique également que le Studio exécute du Python arbitraire.

Je ne chercherais surtout pas à fabriquer un “sandbox Python” maison.

Je créerais plutôt :

```text
HostBackend
 ├── InProcessHost       # actuel, explicitement UNSAFE
 ├── ProcessHost
 ├── ContainerHost
 └── RemoteHost
```

Et pour la production :

```text
AGENT-L runtime
       │
       │ signed/capability request
       ↓
isolated executor
       │
       ↓
tool
```

Le host isolé reçoit **une capability précise**, pas tout l'état de l'agent.

Exemple :

```json
{
  "capability": "customer.read",
  "resource": "customer:9382",
  "expires": "2026-09-17T18:03:00Z"
}
```

Pas :

```text
voici toutes les credentials,
débrouille-toi.
```

Idéalement, le sandbox applique aussi :

```text
filesystem namespace
network allowlist
CPU/memory/time limits
secret scoping
seccomp/container profile
```

Ainsi, même un bug du runtime ou une injection qui atteint le tool voit son blast radius limité.

---

# P6 — Ajouter OpenTelemetry sans rendre la télémétrie autoritaire

AGENT-L a déjà une notion forte de journal.

Il ne faut pas la remplacer par OpenTelemetry.

Faire :

```text
Journal AGENT-L = source normative
OpenTelemetry = projection observable
```

Chaque événement :

```text
reason
plan_selected
policy_checked
approval_requested
tool_started
tool_finished
effect_applied
observe
verify
blocked
```

devient un span/event OTel.

Avec :

```text
trace_id
run_id
action_id
policy_hash
agent_hash
model
tool
latency
tokens
cost
decision
```

mais les secrets doivent être **redacted avant export**, et `NEVER SEND` doit s'appliquer aux exporters également.

Ainsi tu obtiens l'écosystème Datadog/Grafana/Honeycomb/etc. sans abandonner ton système de trace déterministe.

---

# P7 — Providers : standardiser les adaptateurs, pas le noyau

N'ajoute surtout pas OpenAI/Gemini/etc. directement partout dans le runtime.

Créer une interface minuscule :

```python
class Reasoner(Protocol):
    async def reason(...)
    async def select_plan(...)
```

Puis :

```text
agentl-provider-openai
agentl-provider-anthropic
agentl-provider-gemini
agentl-provider-ollama
agentl-provider-bedrock
```

Même chose pour :

```text
storage
checkpoints
tracing
approval
MCP transports
```

Le kernel ne doit connaître **aucun fournisseur**.

Ça réduit aussi la supply-chain attack surface du package `agentl`.

---

# P8 — Formaliser seulement le petit kernel

Je n'essaierais surtout pas de prouver formellement les dizaines de milliers de lignes d'AGENT-L.

Ce serait probablement un gouffre.

En revanche, après P1, tu peux spécifier mathématiquement cinq opérations :

```text
resolve state
construct action
authorize action
commit action
apply observation
```

Et quelques propriétés :

```text
NEVER → ¬EXECUTE

UNKNOWN(ALLOW) → ¬EXECUTE

APPROVAL_REQUIRED ∧ ¬approval → ¬EXECUTE

untrusted shadowing → impossible

undeclared capability → impossible
```

TLA+, Alloy ou même un petit modèle Lean/Coq peuvent convenir selon l'ambition.

Le gain marketing et scientifique est énorme si tu peux dire précisément :

> « Ces invariants du security kernel ont été vérifiés sur un modèle formel. »

Plutôt que :

> « AGENT-L est formellement vérifié. »

La deuxième formulation serait beaucoup trop forte.

Le projet fait déjà bien cette distinction : son document Grade AAA précise que ce label interne n'est ni une certification externe ni une preuve formelle de l'implémentation Python.

---

# P9 — Rendre les preuves de qualité publiques et reproductibles

Il y a ici un problème simple à régler rapidement.

`QUALITY.md` définit une porte AAA sérieuse : tests, ≥90 % de couverture, sécurité, exemples, build, reproductibilité et validation de la documentation.

Mais le même document dit que le workflow `.github` local est ignoré par Git.

Donc l'utilisateur GitHub ne voit pas cette garantie s'exécuter.

> **Fait en v1.8.2.** Le travail n'était pas d'écrire une CI mais de la
> **dé-ignorer** : `.github/workflows/ci.yml` existait, plus complet que la
> version minimale proposée ici (cœur, AutomationBench, TypeScript, build,
> Studio). `.gitignore` contenait `.github/` ; la ligne est retirée. Il fallait
> d'abord resynchroniser `docs/boundary-example-debt.json`, sans quoi la CI
> était rouge dès sa première exécution.
>
> **La matrice ci-dessous est à corriger** : `pyproject.toml` déclare
> `requires-python = ">=3.10"`. Publier une CI qui ne teste pas 3.10, c'est
> annoncer un support qu'on ne contrôle plus ; y ajouter 3.13 sans l'avoir
> jamais exécuté, c'est en annoncer un qu'on n'a pas. La CI publiée teste donc
> 3.10 / 3.11 / 3.12 — la matrice réellement supportée.

Je publierais immédiatement une CI minimale :

```text
Linux Python 3.10
Linux Python 3.11
Linux Python 3.12

pytest
coverage >= 90
security regression
boundary
examples
verify
build wheel
twine check
```

Puis sur release :

```text
SBOM
wheel/sdist hashes
Sigstore signing
test report
coverage artifact
benchmark metadata
commit SHA
```

L'objectif :

```text
README claim
     ↓
click
     ↓
CI run
     ↓
exact commit
     ↓
exact artifacts
```

La crédibilité d'un framework de sécurité dépend énormément de ça.

---

# Ce que je ne changerais surtout pas

| Élément actuel           | Décision                                    |
| ------------------------ | ------------------------------------------- |
| DSL déclaratif restreint | **Conserver**                               |
| `NEVER`                  | **Conserver exactement**                    |
| DEFAULT DENY             | **Conserver**                               |
| Trivalent UNKNOWN        | **Conserver**                               |
| Plans déclarés           | **Conserver**                               |
| LLM oracle               | **Conserver**                               |
| `untrusted` séparé       | **Étendre, jamais supprimer**               |
| Static verifier          | **Étendre**                                 |
| Replay                   | **Étendre vers durable execution**          |
| Explicit EFFECT          | **Conserver + attester**                    |
| Scénarios adversariaux   | **Multiplier**                              |
| MCP                      | **Conserver derrière les mêmes frontières** |

Le piège serait de vouloir améliorer l'ergonomie avec :

```python
agent.run("do whatever is needed")
```

où le modèle génère librement outils, plans et contrôles.

**Ne fais jamais ça dans le cœur d'AGENT-L.**

Tu perdrais immédiatement son avantage.

---

# L'ordre dans lequel je le ferais

> **Correction d'ordre.** Les deux premières étapes sont à inverser. La CI est
> gratuite, sans risque, et c'est le filet de tout ce qui suit — à commencer
> par P1, qui touche la TCB. Elle est donc faite en premier (v1.8.2).
>
> **Et deux TCB à chiffrer séparément**, sans quoi la cible paraît hors
> d'atteinte : la TCB d'**exécution** (`runtime` + `policy` + `state` +
> `trivalent`, ≈ 2 700 lignes, active à chaque action) et la TCB d'**analyse**
> (`verifier` + `analyzer` + `solver`, ≈ 3 100 lignes, hors ligne). Un faux
> négatif du vérificateur est une faille — `SECURITY.md` le dit — mais il ne
> s'exécute jamais en production. C'est `runtime.py` qu'il faut dégraisser, et
> une partie de son contenu n'est pas du noyau : traces, métriques,
> disjoncteur, planificateur.
>
> **P2 promet plus que ce que le runtime peut tenir.** Générer une clé
> d'idempotence déterministe est à sa portée ; la **faire respecter** ne l'est
> pas — l'hôte est du Python tiers non mis en bac à sable (`SECURITY.md`,
> frontière SPEC §28). À écrire noir sur blanc : *au moins une fois* par
> défaut, *exactement une fois* seulement si l'hôte honore la clé. À traiter
> avec P5, qui seul rend la clé vérifiable. Et checkpoint et journal de rejeu
> doivent partager une seule source d'ordre, sinon deux histoires divergentes
> de la même exécution.
>
> **P3 a déjà commencé, par son cas le plus concret** : le retour d'un outil
> est traité comme frontière externe depuis la v1.8.2. La suite — étiquettes
> portées par les valeurs — devra sérialiser ces étiquettes dans le journal,
> sinon le rejeu diverge.
>
> **P4** gagnerait à étendre `ATTESTS` et la confiance des croyances plutôt
> qu'à ajouter un type `Claim` : T9 couvre déjà une part du besoin. **P7**
> (cinq paquets `agentl-provider-*`) est cher en maintenance pour un projet à
> un mainteneur : un protocole plus des points d'entrée suffisent. **P6**
> introduirait la première dépendance du cœur — donc en extra, jamais en
> `dependencies`.

1. **Security Kernel + invariant tests** : isoler la chaîne Action → Policy → Permit → Execution sans changer le comportement actuel.
2. **CI publique reproductible** : rendre immédiatement les garanties actuelles visibles.
3. **Durable execution** : checkpoints, action IDs, idempotency et reprise après crash.
4. **Provenance native** : remplacer progressivement les heuristiques de taint par des métadonnées portées par les valeurs.
5. **Evidence/attestation** : combattre les hallucinations qui déclenchent des décisions importantes.
6. **Host isolation** : Process/Container/RemoteHost et capabilities temporaires à portée minimale.
7. **OTel + adapters providers** : améliorer production et adoption sans contaminer le kernel.
8. **Modèle formel du kernel** : une fois l'API stabilisée.
9. **Benchmarks externes/red-team** : prompt injection, confused deputy, replay attacks, stale observations, TOCTOU, duplicate effects, policy shadowing et malicious MCP server.

Si tu fais ces neuf étapes **sans augmenter les pouvoirs accordés au LLM**, AGENT-L ne deviendra pas seulement “plus complet”. Il deviendra beaucoup plus difficile à comparer directement à LangGraph/CrewAI : son positionnement naturel serait alors celui d'un **secure agent runtime / policy-enforced execution language**, avec LangGraph/PydanticAI/CrewAI éventuellement utilisés au-dessus ou autour de lui plutôt que nécessairement comme concurrents directs.
