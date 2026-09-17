"""`SCENARIO` — exécuter les critères d'acceptation du programme (v1.4).

Le vérificateur prouve des propriétés **génériques** : aucun appel interdit
n'aboutit, le but reste atteignable, aucune capacité n'est morte. Il ne prouve
rien de ce que **l'auteur** a exigé. `SCENARIO` comble exactement ce trou :

    SCENARIO attaque_actif_critique {
        GIVEN  { asset.criticality = CRITICAL, wazuh.alert_count = 37 }
        EXPECT { escalation.sent == yes } WITHIN 3
    }

Ce module en est l'exécution (`agentl test`) ; le théorème T5 du vérificateur
en est l'attaque statique.

### Contre quoi le scénario s'exécute

Contre le **monde déclaré**, pas contre l'hôte réel : `GIVEN` fixe l'état
initial, et chaque outil appelé applique ses propres `EFFECT`. Ce choix est le
cœur de la primitive, et il a une contrepartie qu'il faut énoncer sans
détour :

  * un test est **hermétique et déterministe** — ni réseau, ni disque, ni
    hôte à écrire, donc utilisable en intégration continue dès le premier
    jour ;
  * mais il ne prouve **rien sur l'hôte**. Il prouve que *les déclarations du
    programme entraînent l'attente*. Un `EFFECT` qui ment sur ce que fait
    l'outil rendra un test vert et une production fausse. C'est le contrôle
    `agentl boundary` et le registre de dérive d'effet qui traitent cette
    question — pas celui-ci.

Un outil sans `EFFECT` n'a donc aucun effet en scénario : il s'exécute, mais
le monde ne bouge pas. L'analyseur le signale (`W120`) plutôt que de laisser
croire à un test qui couvre.

### Ce que le résultat veut dire

Deux lectures, selon l'état de l'attente au départ — et la distinction est ce
qui empêche un scénario d'être vert sans rien avoir exécuté :

  * **éventualité** — fausse au tick 0 : elle doit *devenir* vraie dans la
    borne `WITHIN` ;
  * **invariant** — déjà vraie au tick 0 : elle doit *tenir* à chaque tick
    jusqu'au bout de la borne. C'est le cas de tout scénario qui exige qu'une
    action ne survienne pas (`EXPECT { isolated != confirmed }`) ; la juger
    au tick 0 la rendrait verte sans qu'aucun tick n'ait tourné.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .core import Symbol, UNDEFINED
from .llm import MockLLM
from .nodes import Agent, Node, Scenario
from .state import Evaluator


#: L'humain n'est pas absent du scénario : il en est une **hypothèse**, posée
#: dans le `GIVEN`. Sans cela, aucun outil sous `REQUIRE APPROVAL` ne serait
#: testable — or ce sont précisément les actions qui comptent. Le défaut reste
#: le refus : ne pas écrire la ligne, c'est un opérateur qui n'approuve pas.
APPROVAL_PATH = "operator.approval"
ANSWER_PATH = "operator.answer"


def _is_yes(value: Any) -> bool:
    if isinstance(value, Symbol):
        return value.name in ("yes", "true", "granted", "approved")
    if isinstance(value, str):
        return value.lower() in ("yes", "true", "granted", "approved")
    return bool(value)


def simulated_tool_result(tool: Any, world: Optional[Dict[str, Any]] = None
                          ) -> Dict[str, Any]:
    """Rend un accusé conforme pour un hôte de monde déclaré.

    Les hôtes de ``SCENARIO`` et d'Autoloop ne simulent pas le protocole
    Python d'un connecteur ; ils appliquent les ``EFFECT`` du programme. Ils
    rendaient historiquement ``{}``, ce qui contredit désormais à juste titre
    tout ``OUTPUT`` obligatoire. Le double doit respecter le contrat qu'il
    prétend simuler, sans pour autant inventer un choix métier : une valeur
    posée par ``GIVEN`` gagne, sinon on utilise le neutre du type.
    """
    world = world or {}
    defaults = {
        "symbol": Symbol("yes"),
        "string": "scenario",
        "number": 0.0,
        "float": 0.0,
        "int": 0,
        "bool": True,
        "boolean": True,
        "list": [],
        "json": {},
        "object": {},
    }
    return {
        key: world.get(f"{tool.name}.{key}", world.get(
            key, defaults.get(str(typ).lower(), UNDEFINED)))
        for key, typ in tool.outputs.items()
    }


class ScenarioHost:
    """Hôte simulé : le monde tel que le programme le déclare.

    Il n'a ni capteur ni outil Python. Les lectures viennent du `GIVEN` et des
    `EFFECT` déjà appliqués ; les appels d'outil appliquent les `EFFECT`
    déclarés.

    L'opérateur humain se déclare :

        GIVEN { operator.approval = yes, operator.answer = isolate }

    Sans ces lignes, il refuse et ne répond pas — le fail-closed du runtime.
    Un scénario ne suppose donc jamais un humain complaisant par accident : il
    doit l'écrire, et cela se lit dans le test.
    """

    def __init__(self, agent: Agent, world: Dict[str, Any]) -> None:
        self.agent = agent
        self.world = dict(world)
        self.calls: List[str] = []
        # Un `DELEGATE` se pose comme un `REASON` : en test, c'est l'auteur qui
        # écrit ce que le spécialiste a rendu, au lieu que cela se cache dans
        # un hôte. Tout sous-agent nommé rend les champs de son `EXPECT` tels
        # que le `GIVEN` les fournit — un champ non posé reste absent, et le
        # contrat EXPECT le signale comme il le ferait en production.
        self.subagents: Dict[str, Any] = _ScenarioSubagents(agent, self.world)

    def read(self, path: str) -> Any:
        return self.world.get(path)

    def invoke(self, name: str, args: Dict[str, Any]) -> Any:
        self.calls.append(name)
        tool = self.agent.tool(name)
        if tool is None:
            raise KeyError(f"outil `{name}` non déclaré")
        # Issue la plus probable : un scénario doit être reproductible, donc
        # on ne tire pas au sort. `EFFECT` est le cas dégénéré (p = 1).
        branches = tool.branches
        branch = max(branches, key=lambda b: b.probability) if branches else None
        if branch is not None:
            evaluator = Evaluator(_WorldState(self.world))
            for effect in branch.effects:
                try:
                    self.world[effect.path] = evaluator.eval(effect.value)
                except Exception:                         # noqa: BLE001
                    # Un EFFECT inévaluable ne fait pas tomber le scénario :
                    # il ne produit simplement pas de changement. Le test
                    # échouera, et c'est le bon signal.
                    pass
        # Les OUTPUT homonymes d'un EFFECT doivent refléter le monde que
        # l'appel vient de produire. Calculer l'accusé avant les effets
        # laissait le neutre ``yes`` masquer ensuite ``isolated=confirmed``
        # dans les locales et rendait un invariant faussement vert.
        return simulated_tool_result(tool, self.world)

    def ask(self, question: str, reason: str = "") -> Any:
        return self.world.get(ANSWER_PATH, Symbol("no_answer"))

    def approve(self, request: Any) -> bool:
        return _is_yes(self.world.get(APPROVAL_PATH))

    def drain(self) -> List[Dict[str, Any]]:
        return []

    def emit(self, source: str, **payload: Any) -> None:      # pragma: no cover
        pass


class ScenarioLLM(MockLLM):
    """Oracle du scénario : ce que le modèle est **supposé** avoir répondu.

    Un `REASON … PRODUCE { suspected_host: String }` écrit dans l'état ; sans
    réponse, il écrit la valeur par défaut et la chaîne cale sur la première
    précondition. Le scénario pose donc la réponse comme le reste :

        GIVEN { suspected_host = web-07, suspected_account = svc_backup }

    C'est cohérent avec le modèle du langage — le LLM propose, le runtime
    décide : en test, c'est l'auteur qui écrit la proposition, et elle se lit
    dans le scénario au lieu de se cacher dans un hôte.
    """

    def __init__(self, world: Dict[str, Any]) -> None:
        super().__init__()
        self.world = world

    def reason(self, task: str, context, produce):
        posed = {key: self.world[key] for key in produce if key in self.world}
        if not posed:
            return super().reason(task, context, produce)
        out = super().reason(task, context, produce)
        out.update(posed)
        # `MockLLM.reason` marque tout le schéma absent avant que le GIVEN
        # ne fournisse ici les réponses du scénario. Publier le verdict final
        # évite que le runtime ne réapplique ensuite un DEFAULT sur une valeur
        # pourtant explicitement posée par l'auteur du test.
        self.last_reason_missing = [key for key in produce if key not in posed]
        return out


class _ScenarioSubagents(dict):
    """Les sous-agents d'un scénario : le pendant de `ScenarioLLM` pour
    `DELEGATE`.

    Un superviseur ne voit d'un spécialiste que les champs de son `EXPECT` —
    c'est toute la frontière. En test, ces champs se **posent** dans le
    `GIVEN`, exactement comme la réponse d'un `REASON` :

        GIVEN { malicious = yes, threat_class = credential_phishing }

    Rien n'est simulé du spécialiste : le scénario du superviseur teste la
    conduite **du superviseur** face à un verdict donné. Le spécialiste, lui,
    porte ses propres `SCENARIO` et se vérifie seul — c'est la règle « chaque
    étage se vérifie seul » appliquée au test.
    """

    def __init__(self, agent: Agent, world: Dict[str, Any]) -> None:
        super().__init__()
        self._agent = agent
        self._world = world

    def get(self, name: str, default: Any = None) -> Any:
        return lambda payload: {
            key: self._world[key]
            for key in _expected_fields(self._agent, name)
            if key in self._world
        }

    def __contains__(self, name: object) -> bool:       # pragma: no cover
        return True


def _expected_fields(agent: Agent, subagent: str) -> List[str]:
    """Champs `EXPECT` déclarés pour ce sous-agent, tous sites confondus."""
    from .nodes import DelegateStmt

    fields: List[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, DelegateStmt) and node.agent == subagent:
            for key in node.expect:
                if key not in fields:
                    fields.append(key)
        for child in getattr(node, "__dict__", {}).values():
            if isinstance(child, list):
                for item in child:
                    if hasattr(item, "__dict__"):
                        walk(item)
            elif hasattr(child, "__dict__"):
                walk(child)

    walk(agent)
    return fields


class _WorldState:
    """Vue minimale d'état pour évaluer une valeur d'`EFFECT`."""

    def __init__(self, world: Dict[str, Any]) -> None:
        self.world = world
        self.local: Dict[str, Any] = {}
        self.beliefs: Dict[str, Any] = {}
        self.memory: Dict[str, Any] = {}
        self.tick = 0

    def get(self, path: str) -> Any:
        # `UNDEFINED`, pas `None` : c'est ce qui permet à l'évaluateur de lire
        # un identifiant nu comme une constante symbolique (`low`, `yes`).
        # Rendre `None` faisait de `GIVEN { x = CRITICAL }` un `x = None`.
        return self.world.get(path, UNDEFINED)


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    ticks_used: int
    within: int
    failed: List[str] = field(default_factory=list)     # attentes restées fausses
    invariants: List[str] = field(default_factory=list) # attentes vraies dès le départ
    calls: List[str] = field(default_factory=list)
    error: Optional[str] = None
    trace: str = ""

    def render(self) -> str:
        from .runtime import _render_expr                # noqa: F401  (symétrie)
        if self.error:
            return f"  ✘ {self.name} — {self.error}"
        if not self.passed:
            attentes = " ∧ ".join(self.failed) or "?"
            return (f"  ✘ {self.name} — attente non satisfaite en "
                    f"{self.ticks_used}/{self.within} tick(s) : {attentes}")
        if self.invariants:
            # Attente déjà vraie au départ : ce qui est prouvé, c'est qu'elle
            # a **tenu** pendant toute la borne, pas qu'elle est devenue vraie.
            return (f"  ✔ {self.name} — invariant maintenu sur "
                    f"{self.ticks_used}/{self.within} tick(s)")
        return (f"  ✔ {self.name} — satisfaite au tick "
                f"{self.ticks_used}/{self.within}")


@dataclass
class ScenarioReport:
    agent: str
    results: List[ScenarioResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    def render(self) -> str:
        if not self.results:
            return (f"scénarios — {self.agent}\n"
                    f"  (aucun SCENARIO déclaré : le programme ne porte pas "
                    f"ses critères d'acceptation)")
        ok = sum(1 for r in self.results if r.passed)
        body = "\n".join(r.render() for r in self.results)
        return (f"scénarios — {self.agent}\n{body}\n"
                f"  {ok}/{len(self.results)} satisfait(s)")


def _initial_world(scenario: Scenario) -> Dict[str, Any]:
    world: Dict[str, Any] = {}
    evaluator = Evaluator(_WorldState(world))
    for effect in scenario.given:
        try:
            world[effect.path] = evaluator.eval(effect.value)
        except Exception:                                 # noqa: BLE001
            world[effect.path] = None
    return world


def initial_expectations(agent: Agent, scenario: Scenario
                         ) -> Tuple[List[Node], List[Node]]:
    """Partage les attentes en (invariants, éventualités) au tick 0.

    Une attente déjà vraie dans l'état déclaré est un **invariant** : elle
    doit tenir, pas advenir. Le vérificateur (T5) s'en sert pour ne pas
    réfuter une attente qu'aucune action n'a à établir — chercher une route
    vers « l'isolement n'a pas eu lieu » n'a aucun sens.
    """
    from .runtime import Runtime

    host = ScenarioHost(agent, _initial_world(scenario))
    runtime = Runtime(agent, host, ScenarioLLM(host.world), echo=False)
    runtime._apply_initial_values(host.world, "scenario")
    evaluator = Evaluator(runtime.state)

    invariants, eventualities = [], []
    for expectation in scenario.expect:
        try:
            held = bool(evaluator.test(expectation))
        except Exception:                                 # noqa: BLE001
            held = False
        (invariants if held else eventualities).append(expectation)
    return invariants, eventualities


def run_scenario(agent: Agent, scenario: Scenario, echo: bool = False
                 ) -> ScenarioResult:
    """Exécute un scénario contre le monde déclaré et rend son verdict."""
    from .runtime import Runtime, _render_expr

    host = ScenarioHost(agent, _initial_world(scenario))
    runtime = Runtime(agent, host, ScenarioLLM(host.world), echo=echo)

    def holds(expectation: Node) -> bool:
        try:
            return bool(Evaluator(runtime.state).test(expectation))
        except Exception:                                 # noqa: BLE001
            return False               # inévaluable = non satisfaite

    try:
        # Amorçage : les croyances déclarées et le GIVEN doivent être dans
        # l'état avant qu'on juge de la satisfaction au tick 0.
        runtime._apply_initial_values(host.world, "scenario")

        # Une attente **déjà vraie** au départ n'est pas une attente : c'est
        # un invariant. C'est le cas de tout scénario qui exige qu'une action
        # ne survienne PAS (`isolated != confirmed`). La juger au tick 0 la
        # rendrait toujours verte sans rien exécuter ; on exige donc qu'elle
        # tienne à *chaque* tick, jusqu'au bout de la borne.
        invariants = [e for e in scenario.expect if holds(e)]
        eventualities = [e for e in scenario.expect if e not in invariants]
        broken: List[str] = []
        seen_broken: set = set()

        used = 0
        for _ in range(max(1, scenario.within)):
            if not eventualities and not invariants:
                break
            if not invariants and all(holds(e) for e in eventualities):
                break                  # rien à surveiller : arrêt anticipé
            runtime.tick()
            used = runtime.state.tick
            for inv in invariants:
                text = _render_expr(inv)
                # Seule la **première** rupture intéresse : la répéter à
                # chaque tick noierait le rapport.
                if text not in seen_broken and not holds(inv):
                    seen_broken.add(text)
                    broken.append(f"{text} (rompu au tick {used})")
        remaining = broken + [_render_expr(e) for e in eventualities
                              if not holds(e)]
    except Exception as exc:                              # noqa: BLE001
        return ScenarioResult(scenario.name, False, 0, scenario.within,
                              error=f"{type(exc).__name__}: {exc}",
                              trace=runtime.trace.render())

    return ScenarioResult(
        name=scenario.name,
        passed=not remaining,
        ticks_used=used,
        within=scenario.within,
        failed=remaining,
        invariants=[_render_expr(e) for e in invariants],
        calls=list(host.calls),
        trace=runtime.trace.render(),
    )


def run_scenarios(agent: Agent, echo: bool = False) -> ScenarioReport:
    report = ScenarioReport(agent.name)
    for scenario in agent.scenarios:
        report.results.append(run_scenario(agent, scenario, echo=echo))
    return report
