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

Un outil sans `EFFECT` ne modifie pas le monde simulé ; ses OUTPUT doivent
être posés explicitement dans GIVEN. Une réponse manquante invalide le test.

### Ce que le résultat veut dire

Deux lectures, selon l'état de l'attente au départ — et la distinction est ce
qui empêche un scénario d'être vert sans rien avoir exécuté :

  * **éventualité** — fausse au tick 0 : elle doit *devenir* vraie dans la
    borne `WITHIN` ;
  * **invariant** — déjà vraie au tick 0 : elle doit *tenir* après chaque instruction, effet simulé et phase
    jusqu’à l’arrêt du programme ou la borne. C'est le cas de tout scénario qui exige qu'une
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
    from .host import approval_granted
    return approval_granted(value)


def simulated_tool_result(tool: Any, world: Optional[Dict[str, Any]] = None
                          ) -> Dict[str, Any]:
    """Sorties explicitement posées ou produites par EFFECT, jamais inventées."""
    world = world or {}
    return {key: world.get(f"{tool.name}.{key}", world.get(key, UNDEFINED))
            for key in tool.outputs}


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
        self.errors: List[str] = []
        self.events: List[Dict[str, Any]] = []
        self.on_effect = None
        self.state = None
        self.latest_effects: Dict[str, Any] = {}
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
        self.latest_effects = {}
        try:
            branches = [b for b in tool.branches if b.probability > 0]
            selected = self.world.get(f"scenario.outcome.{name}", UNDEFINED)
            if selected is not UNDEFINED:
                branch = next((b for b in branches if b.name == str(selected)), None)
                if branch is None:
                    raise ValueError(f"OUTCOME inconnu ou impossible pour {name} : {selected}")
            elif len(branches) > 1:
                raise ValueError(f"OUTCOME ambigu pour {name} : poser scenario.outcome.{name} dans GIVEN")
            else:
                branch = branches[0] if branches else None
            if branch is not None:
                # INPUT appartient à la portée de l'effet de cet appel.
                scope = dict(args)
                host = self
                class EffectState(_WorldState):
                    def get(self, path):
                        if path in scope:
                            return scope[path]
                        if host.state is not None:
                            return host.state.get(path)
                        return host.world.get(path, UNDEFINED)
                evaluator = Evaluator(EffectState(scope))
                for effect in branch.effects:
                    value = evaluator.eval(effect.value)
                    if value is UNDEFINED:
                        raise ValueError(f"EFFECT {effect.path} indéfini")
                    scope[effect.path] = value
                    self.world[effect.path] = value
                    self.latest_effects[effect.path] = value
                    if self.on_effect:
                        self.on_effect(self.latest_effects)
            result = simulated_tool_result(tool, self.world)
            missing = [key for key, value in result.items() if value is UNDEFINED]
            if missing:
                raise ValueError(f"OUTPUT de {name} non posé dans GIVEN/EFFECT : {', '.join(missing)}")
            return result
        except Exception as exc:
            self.errors.append(str(exc))
            raise

    def ask(self, question: str, reason: str = "") -> Any:
        return self.world.get(ANSWER_PATH, Symbol("no_answer"))

    def approve(self, request: Any) -> bool:
        return _is_yes(self.world.get(APPROVAL_PATH))

    def drain(self) -> List[Dict[str, Any]]:
        events, self.events = self.events, []
        return events

    def emit(self, source: str, **payload: Any) -> None:
        self.events.append({"source": source, "payload": payload})


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

    def select_plan(self, context, candidates):
        choice = self.world.get("llm.plan", UNDEFINED)
        if choice is UNDEFINED:
            raise ValueError("sélection LLM non déclarée : poser llm.plan dans GIVEN")
        if str(choice) not in candidates:
            raise ValueError(f"llm.plan inconnu : {choice}")
        return str(choice)

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
        return bool(self.results) and all(r.passed for r in self.results)

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
        value = evaluator.eval(effect.value)
        if value is UNDEFINED:
            raise ValueError(f"GIVEN {effect.path} indéfini")
        world[effect.path] = value
    return world


def _expectation_holds(agent, state, expression):
    from .analyzer import Analyzer
    from .trivalent import evaluate
    known = Analyzer(agent)._known_state_paths()
    class ExpectEvaluator(Evaluator):
        def _path(self, node):
            if node.dotted in known:
                return self.state.get(node.dotted)
            return super()._path(node)
    # Kleene garde UNKNOWN sous NOT ; strict_undefined seul ne le fait pas.
    return evaluate(ExpectEvaluator(state, strict_undefined=True), expression) is True


def _scenario_errors(agent, scenario):
    from dataclasses import replace
    from .analyzer import Analyzer
    analyzer = Analyzer(replace(agent, scenarios=[scenario]))
    analyzer._check_scenarios()
    return [d.render() for d in analyzer.diags
            if d.severity == "error" or d.code in ("W117", "W118")]


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

    invariants, eventualities = [], []
    for expectation in scenario.expect:
        try:
            held = _expectation_holds(agent, runtime.state, expectation)
        except Exception:                                 # noqa: BLE001
            held = False
        (invariants if held else eventualities).append(expectation)
    return invariants, eventualities


def run_scenario(agent: Agent, scenario: Scenario, echo: bool = False
                 ) -> ScenarioResult:
    """Exécute le modèle déclaré avec oracles explicites et moniteurs d'état."""
    from .runtime import Runtime, _render_expr

    runtime = None
    host = None
    try:
        errors = _scenario_errors(agent, scenario)
        if errors:
            raise ValueError("scénario invalide : " + "; ".join(errors))
        host = ScenarioHost(agent, _initial_world(scenario))
        broken = []
        reached = set()
        invariants = []
        eventualities = []

        def holds(expectation, overrides=None):
            class View:
                def __getattr__(self, key):
                    return getattr(runtime.state, key)
                def get(self, path):
                    return overrides[path] if overrides and path in overrides else runtime.state.get(path)
            return _expectation_holds(agent, View(), expectation)

        def monitor(overrides=None):
            for i, inv in enumerate(invariants):
                if i not in seen_broken and not holds(inv, overrides):
                    seen_broken.add(i)
                    broken.append(f"{_render_expr(inv)} (rompu au tick {runtime.state.tick})")
            for i, expectation in enumerate(eventualities):
                if holds(expectation, overrides):
                    reached.add(i)

        class MonitoredRuntime(Runtime):
            def exec_stmt(self, stmt):
                result = super().exec_stmt(stmt)
                monitor()
                return result
            def _run_phase(self, phase):
                super()._run_phase(phase)
                monitor()
            def call_tool(self, name, args, origin="plan", **options):
                host.latest_effects = {}
                # Les options — dont la provenance des arguments (v1.9) —
                # suivent : un scénario doit juger l'action comme la
                # production la jugerait.
                result = super().call_tool(name, args, origin, **options)
                # Les OUTCOME du double sont les faits du monde simulé.
                # Le runtime ne doit pas rester sur une branche nominale.
                self._apply_initial_values(host.latest_effects, "scenario-effect")
                monitor()
                return result

        runtime = MonitoredRuntime(agent, host, ScenarioLLM(host.world), echo=echo)
        host.state = runtime.state
        runtime._apply_initial_values(host.world, "scenario")
        for stimulus in scenario.stimuli:
            payload = {}
            evaluator = Evaluator(_WorldState(dict(host.world)))
            for effect in stimulus.payload:
                value = evaluator.eval(effect.value)
                if value is UNDEFINED:
                    raise ValueError(f"stimulus {stimulus.name}.{effect.path} indéfini")
                payload[effect.path] = value
            if stimulus.kind == "EVENT":
                if not any(h.source == stimulus.name for h in agent.events):
                    raise ValueError(f"EVENT sans gestionnaire : {stimulus.name}")
                host.emit(stimulus.name, **payload)
            else:
                if not any(h.name == stimulus.name for h in agent.messages):
                    raise ValueError(f"MESSAGE sans gestionnaire : {stimulus.name}")
                runtime.inbox.append({"name": stimulus.name, "from": stimulus.sender, "payload": payload})

        for expectation in scenario.expect:
            (invariants if holds(expectation) else eventualities).append(expectation)
        seen_broken = set()
        host.on_effect = monitor
        used = 0
        for _ in runtime.iter_ticks(horizon=scenario.within):
            used = runtime.state.tick
            monitor()
            # Les assertions de trace et les invariants exigent toute la borne.
            if not invariants and not scenario.assertions and len(reached) == len(eventualities):
                break
        if host.events or runtime.inbox:
            raise ValueError("stimulus non consommé : vérifier les phases RECEIVE/SELECT_PLAN du LOOP")
        errors = [f"{e.text}: {e.detail}" for e in runtime.trace.events if e.kind == "ERROR"]
        if host.errors or errors:
            raise ValueError("; ".join(host.errors + errors))
        remaining = broken + [_render_expr(e) for i, e in enumerate(eventualities) if i not in reached]
        for assertion in scenario.assertions:
            if assertion.kind == "CALL":
                satisfied = assertion.target in host.calls
            elif assertion.kind == "NEVER CALL":
                satisfied = assertion.target not in host.calls
            elif assertion.kind == "BLOCKED":
                satisfied = any(e.kind == "BLOCKED" and e.text.startswith(assertion.target + "(")
                                for e in runtime.trace.events)
            elif assertion.kind == "EVENT":
                satisfied = any(e.kind == "EVENT" and e.text == assertion.target + " déclenché"
                                for e in runtime.trace.events)
            else:  # NO ERROR ; déjà contrôlé ci-dessus
                satisfied = not errors
            if not satisfied:
                remaining.append(f"EXPECT {assertion.kind} {assertion.target}".strip())
    except Exception as exc:
        return ScenarioResult(scenario.name, False,
                              runtime.state.tick if runtime else 0, scenario.within,
                              calls=list(host.calls) if host else [],
                              error=f"{type(exc).__name__}: {exc}",
                              trace=runtime.trace.render() if runtime else "")
    return ScenarioResult(scenario.name, not remaining, used, scenario.within,
                          failed=remaining, invariants=[_render_expr(e) for e in invariants],
                          calls=list(host.calls), trace=runtime.trace.render())


def run_scenarios(agent: Agent, echo: bool = False) -> ScenarioReport:
    report = ScenarioReport(agent.name)
    for scenario in agent.scenarios:
        report.results.append(run_scenario(agent, scenario, echo=echo))
    return report
