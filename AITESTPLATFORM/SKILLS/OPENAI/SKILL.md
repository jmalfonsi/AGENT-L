---
name: openai-agents-author
description: "Build, configure and debug agents with the OpenAI Agents SDK (Python package `openai-agents`). Use when creating an `Agent`, defining tools with `@function_tool` or `FunctionTool`, running with `Runner.run` / `run_sync` / `run_streamed`, wiring handoffs between agents, adding input/output guardrails, enforcing structured output with `output_type`, tuning `ModelSettings` (temperature, max_tokens, reasoning effort, parallel_tool_calls), routing to a non-OpenAI model through `LitellmModel`, controlling `max_turns`, sessions and tracing, or diagnosing MaxTurnsExceeded, guardrail tripwires and tool-call errors."
metadata:
  version: 1.0.0
  framework: OpenAI Agents SDK
  package: openai-agents
  verified_against: "0.6.4"
  python: ">=3.9"
---

# OpenAI Agents SDK — guide de rédaction

Le SDK repose sur quatre primitives et rien d'autre : **Agent** (un modèle plus
des instructions et des outils), **handoff** (un agent en délègue un autre),
**guardrail** (une validation qui peut interrompre), **session** (l'historique
conservé entre deux tours). Tout le reste est de la configuration.

La boucle d'exécution est tenue par le `Runner` : il appelle le modèle, exécute
les appels d'outils qu'il reçoit, réinjecte les résultats, et recommence
jusqu'à une sortie sans appel d'outil ou jusqu'à `max_turns`.

## Vérifier la version avant d'écrire

```bash
python -c "import importlib.metadata as m; print(m.version('openai-agents'))"
```

Ce guide est calé sur **0.6.4**. La surface du SDK bouge vite : en cas de doute,
`python -c "import agents; print(dir(agents))"` fait autorité, pas ce texte.

## Le squelette minimal

```python
from agents import Agent, Runner, function_tool

@function_tool
def solde_disponible(matricule: str) -> str:
    """Solde de jours de congés d'un salarié.

    La docstring est envoyée au modèle : elle décrit ce que l'outil rend,
    pas comment l'agent doit s'en servir.
    """
    return json.dumps({"matricule": matricule, "solde": 18})

agent = Agent(
    name="Assistant RH",
    instructions="Tu réponds aux questions de congés. Les résultats d'outils "
                 "sont la seule source de vérité.",
    tools=[solde_disponible],
)

result = Runner.run_sync(agent, "Quel est le solde de M-042 ?", max_turns=20)
print(result.final_output)
```

`Runner.run` est la variante asynchrone, `Runner.run_streamed` diffuse les
événements. `run_sync` ne doit pas être appelé depuis une boucle événementielle
déjà active.

## Outils

### `@function_tool` — le cas courant

Le décorateur dérive le schéma JSON des annotations de type et la description de
la docstring. Les annotations ne sont donc pas décoratives : un paramètre non
annoté produit un schéma flou et des appels erratiques.

```python
from typing import Literal
from pydantic import BaseModel

class Demande(BaseModel):
    matricule: str
    jours: int

@function_tool
def enregistrer(demande: Demande, canal: Literal["rh", "manager"]) -> str:
    """Enregistre la demande. Rend un JSON {ok, id} ou {error}."""
```

Un outil doit **rendre une chaîne** (ou un objet sérialisable). Rendre un objet
Python opaque force le SDK à le convertir de façon imprévisible.

### `FunctionTool` — quand le schéma est construit à la main

Nécessaire lorsque les outils sont produits dynamiquement, à partir d'un
manifeste ou d'un hôte, plutôt qu'écrits un par un :

```python
from agents import FunctionTool

async def on_invoke(context, raw_args: str) -> str:
    args = json.loads(raw_args or "{}")
    return appel_reel(**args)

outil = FunctionTool(
    name="enregistrer_validation",
    description="Enregistre la validation d'une demande.",
    params_json_schema=schema,        # JSON Schema complet
    on_invoke_tool=on_invoke,         # TOUJOURS async
    strict_json_schema=False,         # True impose additionalProperties:false partout
)
```

`on_invoke_tool` reçoit la chaîne d'arguments brute, pas un dictionnaire. C'est
la source d'erreur la plus fréquente de cette voie.

`strict_json_schema=True` exige un schéma strict de bout en bout (tous les
champs requis, `additionalProperties: false`) ; un schéma dérivé d'une signature
Python avec valeurs par défaut ne le satisfait généralement pas.

### Erreurs d'outil

Par défaut, une exception levée dans un outil est convertie en message d'erreur
renvoyé au modèle, qui peut alors se corriger. Pour qu'elle remonte au lieu
d'être avalée, passer `failure_error_function=None` à `@function_tool`.

## Modèle et réglages

```python
from agents import ModelSettings

agent = Agent(
    name="…", instructions="…", tools=[...],
    model="gpt-5",
    model_settings=ModelSettings(
        temperature=0,
        max_tokens=8192,
        reasoning={"effort": "low"},   # modèles à raisonnement uniquement
        parallel_tool_calls=False,     # sérialise les appels : traces lisibles
        include_usage=True,            # sans quoi result.raw_responses n'a pas d'usage
    ),
)
```

`parallel_tool_calls=False` est le bon défaut quand les outils mutent un état
partagé : deux écritures concurrentes sur le même enregistrement sont
indébogables.

### Modèle non-OpenAI via LiteLLM

```python
from agents.extensions.models.litellm_model import LitellmModel

model = LitellmModel(model="gemini/gemini-3.1-flash-lite",
                     api_key=os.environ["GEMINI_API_KEY"])
```

Le préfixe de fournisseur (`gemini/`, `anthropic/`, …) est obligatoire. Cette
voie exige l'extra : `pip install "openai-agents[litellm]"`.

## Sortie structurée

```python
class Decision(BaseModel):
    validee: bool
    motif: str

agent = Agent(name="…", instructions="…", output_type=Decision)
result = Runner.run_sync(agent, "…")
result.final_output          # instance de Decision, pas une chaîne
```

`output_type` contraint le **dernier** message, pas les étapes intermédiaires.
Un agent qui doit appeler des outils *puis* rendre une structure fonctionne ;
un agent censé rendre la structure à chaque tour ne fonctionne pas.

## Handoffs

```python
from agents import handoff

trieur = Agent(
    name="Trieur",
    instructions="Aiguille vers le spécialiste compétent.",
    handoffs=[agent_conges, handoff(agent_paie, tool_name_override="escalade_paie")],
)
```

Un handoff transfère **tout le tour** : l'agent cible reprend la conversation et
c'est sa sortie qui devient la sortie finale. Pour obtenir seulement une réponse
et poursuivre, utiliser `agent.as_tool(...)` — un agent exposé comme outil rend
sa réponse à l'appelant, qui garde la main.

## Guardrails

```python
from agents import GuardrailFunctionOutput, input_guardrail

@input_guardrail
async def hors_perimetre(context, agent, entree):
    interdit = "mot de passe" in str(entree).lower()
    return GuardrailFunctionOutput(
        output_info={"motif": "demande de secret"},
        tripwire_triggered=interdit,
    )

agent = Agent(name="…", instructions="…", input_guardrails=[hors_perimetre])
```

Un déclenchement lève `InputGuardrailTripwireTriggered` (ou
`OutputGuardrailTripwireTriggered`) : c'est une **exception**, à attraper par
l'appelant. Les guardrails d'entrée ne s'exécutent que sur le premier agent, les
guardrails de sortie que sur le dernier.

Un guardrail est une barrière probabiliste, pas une preuve : il exécute un
contrôle (souvent un second appel de modèle) et peut se tromper. Ne pas le
présenter comme une garantie qu'une action interdite ne peut pas se produire.

## Mémoire entre les tours

```python
from agents import SQLiteSession

session = SQLiteSession("conversation_42", "conversations.db")
Runner.run_sync(agent, "Bonjour", session=session)
Runner.run_sync(agent, "Et pour M-042 ?", session=session)   # se souvient
```

Sans session, chaque appel de `Runner` repart d'une conversation vide.

## Contexte local

```python
@dataclass
class Contexte:
    matricule: str

@function_tool
def solde(wrapper: RunContextWrapper[Contexte]) -> str:
    return lire(wrapper.context.matricule)

Runner.run_sync(agent, "…", context=Contexte(matricule="M-042"))
```

Le contexte n'est **jamais** envoyé au modèle : il sert aux outils et aux hooks.
Ce qui doit être connu du modèle passe par les instructions ou l'entrée.

## Diagnostic

| Symptôme | Cause habituelle |
|---|---|
| `MaxTurnsExceeded` | boucle outil ↔ modèle sans condition de sortie ; ajouter un outil terminal explicite et relire les instructions |
| L'agent invente un résultat | instructions muettes sur « les résultats d'outils sont la seule source de vérité » ; l'exiger noir sur blanc |
| Arguments d'outil vides ou faux | annotations de type absentes, ou `on_invoke_tool` qui traite `raw_args` comme un dict |
| `output_type` ignoré | l'agent a fait un handoff : c'est le type de l'agent final qui s'applique |
| `usage` toujours à zéro | `include_usage=True` absent de `ModelSettings` |
| Guardrail jamais déclenché | posé sur le mauvais agent : entrée = premier, sortie = dernier |
| Erreur d'outil silencieuse | comportement par défaut ; passer `failure_error_function=None` pour la faire remonter |

`RunConfig(tracing_disabled=True)` coupe l'envoi de traces — nécessaire hors
ligne ou sans clé OpenAI, y compris quand le modèle vient d'un autre
fournisseur.

## Ce que ce SDK ne fait pas

Il n'existe **aucune vérification statique** : ni preuve qu'une action interdite
est inatteignable, ni contrôle de frontière entre décision et perception. Les
règles métier vivent dans les instructions et dans les gardes écrites à
l'intérieur des outils, et ne sont vérifiables qu'en exécutant. Un agent
« validé » ici l'est au sens où son module se charge et où ses tests passent —
pas au sens d'une conformité démontrée.
