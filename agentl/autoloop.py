"""`autoloop` — mener un agent jusqu'à ce qu'il tienne, puis chercher où il casse.

`check`, `verify`, `boundary` et `test` disent si le programme est recevable.
Aucun ne dit s'il **tient sur d'autres données que celles que l'auteur a
écrites**. C'est le trou que cette boucle comble, en deux temps :

  1. **la barrière** — analyse, preuve, frontière, scénarios déclarés. Tant
     qu'elle n'est pas franchie, rien d'autre n'a de sens ;
  2. **les données** — on rejoue le programme sur des mondes dérivés du sien,
     et on vérifie qu'il tient encore.

À chaque tour raté, le programme est renvoyé à un rédacteur (un modèle) avec
**ce qui s'est passé**, et la boucle recommence.

### Qui dit ce qui est attendu, sur une donnée que personne n'a écrite ?

C'est la question qui décide de la valeur de tout le reste. Faire juger le
modèle qu'on corrige serait un oracle qui bouge : il suffirait qu'il écrive
une attente complaisante pour obtenir 100 %. La réponse retenue ici est
l'**invariant** — une propriété vraie quelles que soient les données, donc
transportable sur un monde que l'auteur n'a pas prévu.

Deux familles, et la distinction porte toute la solidité du dispositif.

**Les invariants universels** valent pour tout programme, sur toute donnée :

  * `U1` aucune erreur d'exécution ;
  * `U2` aucune vérification déclarée (`VERIFY`) en échec ;
  * `U3` aucun outil interdit sans condition par la `POLICY` n'a été exécuté ;
  * `U4` aucun outil exigeant une approbation n'a été exécuté sans elle.

`U3` et `U4` ne retiennent que les règles **sans garde** : une règle gardée
dépend des données, et la juger reviendrait à redemander au runtime ce qu'il
vient de décider. Ces quatre-là sont vrais par construction ; les voir tomber,
c'est tenir un vrai défaut.

**Les invariants déclarés** sont les `EXPECT` de l'auteur qui sont **déjà
vraies au tick 0** — la distinction qu'`agentl test` fait déjà (§ scenario).
`EXPECT { sandbox.stale_count != 0 }` n'exige pas qu'un événement advienne :
elle exige que la purge n'ait **pas** lieu. Une telle attente se transporte ;
une éventualité (`escalation.sent == yes`) ne se transporte pas, puisque son
avènement dépend précisément des données qu'on vient de changer. Les
éventualités sont donc écartées sur les cas dérivés — jamais jugées, jamais
comptées.

### Ce qu'on a le droit de changer

Transporter une attente ne suffit pas : encore faut-il ne pas détruire la
**raison** qui la rend vraie. Sur `disk_sentinel`, « un répertoire protégé
n'est jamais purgé » tient parce que `sandbox.protected = yes` ; faire varier
`protected` ne réfute pas le programme, cela change de scénario.

D'où deux étages de cas dérivés :

  * **étage « données »** — on ne fait varier que les chemins qui n'entrent
    dans **aucune** décision : ni garde de `POLICY`, ni `REQUIRES` d'outil, ni
    `WHEN`, ni `IF`, ni `VERIFY`, ni attente — et, de proche en proche, aucune
    évidence d'une hypothèse citée dans une de ces conditions. Le contexte de
    décision est intact : les invariants déclarés s'y transportent, et sont
    jugés ;
  * **étage « contexte »** — on fait varier n'importe quoi, y compris
    l'approbation de l'opérateur. Le contexte de décision change, donc **seuls
    les invariants universels sont jugés**. C'est l'étage qui couvre : la
    donnée absente, la valeur au bord d'un seuil, l'opérateur qui refuse.

Les valeurs ne sont pas tirées au hasard dans le vide : elles viennent des
**littéraux auxquels le programme compare lui-même ce chemin**, encadrés (t-1,
t, t+1), plus l'absence de donnée — la panne la plus fréquente en production
et la moins souvent écrite dans un test.

### Le lot retenu

Une boucle qui voit tous les cas pendant qu'elle se corrige finit par coder
les cas, pas la tâche. Une part des cas dérivés est donc **retenue** : la
boucle ne les exécute jamais, le rédacteur ne les voit jamais, et ils ne sont
ouverts qu'à la fin. 100 % sur ce qu'elle a vu et moins sur le lot retenu n'est
pas une réussite : c'est du par-cœur, et le rapport le dit.

Les cas sont engendrés **une seule fois**, depuis la première version qui
franchit la barrière. Les réengendrer à chaque tour donnerait à la boucle le
pouvoir de choisir ses propres épreuves.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from .core import Symbol, UNDEFINED
from .nodes import (Agent, BinOp, CallExpr, Literal, Node, PathExpr, Program,
                    Scenario)
from .scenario import (APPROVAL_PATH, ScenarioHost, ScenarioLLM, _initial_world,
                       _is_yes, initial_expectations, simulated_tool_result)
from .state import Evaluator

#: Absence de donnée. Distinct de `None` : le chemin n'est pas dans le monde,
#: donc toute garde qui le lit échoue fermé — c'est la panne de capteur, et
#: c'est le cas dérivé le plus payant.
MISSING = object()

#: Champs qui portent une **condition**. Les nommer plutôt que d'énumérer les
#: types de nœuds fait que toute construction future qui décide (une garde
#: neuve, un `UNTIL`) entre d'elle-même dans le contexte gelé.
CONDITION_FIELDS = ("guard", "requires", "when", "cond", "until", "filter",
                    "prior_filter", "explains_value")

COMPARISONS = ("==", "!=", ">", ">=", "<", "<=")

#: Symboles qui se comportent en booléens. Ailleurs, basculer un symbole vers
#: `yes` ne produit pas un cas, mais une donnée impossible.
_BOOLEAN_SYMBOLS = {"yes", "no", "true", "false", "granted", "denied",
                    "approved", "refused"}


# --------------------------------------------------------------- parcours AST

def _walk(node: Any) -> Iterator[Node]:
    if isinstance(node, Node):
        yield node
        for f in dataclasses.fields(node):
            yield from _walk(getattr(node, f.name))
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item)


def _paths_in(node: Any) -> Set[str]:
    return {n.dotted for n in _walk(node) if isinstance(n, PathExpr)}


def _hypotheses_in(node: Any) -> Set[str]:
    """Hypothèses citées par `P(...)` dans une condition."""
    named: Set[str] = set()
    for n in _walk(node):
        if isinstance(n, CallExpr) and n.name.upper() == "P":
            for arg in getattr(n, "args", []) or []:
                if isinstance(arg, PathExpr):
                    named.add(arg.dotted)
    return named


def decision_paths(agent: Agent, extra: Sequence[Node] = ()) -> Set[str]:
    """Chemins dont dépend une décision du programme — donc à ne pas toucher.

    La fermeture par les hypothèses n'est pas un raffinement : sans elle, faire
    varier un compteur qui n'apparaît dans aucune garde déplacerait quand même
    `P(junk_accumulation)`, franchirait le seuil d'une règle `ALLOW … IF P(…)`,
    et l'agent agirait — à bon droit. On aurait imputé au programme une faute
    qui est celle du générateur de cas.
    """
    conditions: List[Any] = list(extra)
    for node in _walk(agent):
        for name in CONDITION_FIELDS:
            value = getattr(node, name, None)
            if isinstance(value, Node):
                conditions.append(value)

    paths = _paths_in(conditions)
    cited = _hypotheses_in(conditions)
    for hypothesis in agent.hypotheses:
        # Le seuil d'une hypothèse la rend décisive dès qu'elle est citée, et
        # `explains_path` la relie au monde même sans citation explicite.
        if hypothesis.name in cited or hypothesis.explains_path in paths:
            paths |= _paths_in(hypothesis.evidence)
    return paths


def compared_literals(agent: Agent, path: str) -> List[Any]:
    """Valeurs auxquelles le programme compare ce chemin, dans l'ordre du texte."""
    found: List[Any] = []
    for node in _walk(agent):
        if not isinstance(node, BinOp) or node.op not in COMPARISONS:
            continue
        for side, other in ((node.left, node.right), (node.right, node.left)):
            if isinstance(side, PathExpr) and side.dotted == path \
                    and isinstance(other, Literal) and other.value not in found:
                found.append(other.value)
    return found


# ------------------------------------------------------------------- les cas

def _render(value: Any) -> str:
    if value is MISSING:
        return "∅"
    if isinstance(value, Symbol):
        return value.name
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def variants(value: Any, literals: Sequence[Any]) -> List[Any]:
    """Valeurs dérivées d'une valeur observée, aux bornes que le programme nomme.

    L'absence (`MISSING`) est proposée pour tout chemin : c'est la panne que
    l'on écrit le moins et qui survient le plus.
    """
    out: List[Any] = [MISSING]

    def add(candidate: Any) -> None:
        if not any(_same(candidate, seen) for seen in out):
            out.append(candidate)

    if isinstance(value, bool) or isinstance(value, Symbol):
        for literal in literals:
            add(literal)
        # Un `yes`/`no` ne se propose que si le chemin en porte déjà un :
        # offrir `yes` à un niveau de menace ne teste rien, cela fabrique une
        # donnée qu'aucun capteur ne produirait.
        if isinstance(value, bool) or (isinstance(value, Symbol)
                                       and value.name in _BOOLEAN_SYMBOLS):
            add(Symbol("no") if _is_yes(value) else Symbol("yes"))
        add(Symbol("indetermine"))          # symbole qu'aucune garde ne nomme
    elif isinstance(value, (int, float)):
        add(0)
        for literal in literals:
            if isinstance(literal, (int, float)) and not isinstance(literal, bool):
                # Le seuil lui-même et ses deux bords : c'est là que se logent
                # les fautes de comparaison (`>` écrit pour `>=`).
                for edge in (literal - 1, literal, literal + 1):
                    add(type(value)(edge) if isinstance(value, int)
                        and float(edge).is_integer() else edge)
        add(-abs(value) - 1)                # négatif : une quantité ne l'est pas
        add(value * 2 if value else 1)
    elif isinstance(value, str):
        add("")
        for literal in literals:
            if isinstance(literal, str):
                add(literal)
    else:
        for literal in literals:
            add(literal)
    return out


def _same(a: Any, b: Any) -> bool:
    if a is MISSING or b is MISSING:
        return a is b
    if isinstance(a, Symbol) and isinstance(b, Symbol):
        return a.name == b.name
    try:
        return type(a) is type(b) and a == b
    except Exception:                                     # noqa: BLE001
        return False


@dataclass(frozen=True)
class Case:
    """Un monde dérivé d'un scénario de l'auteur."""

    scenario: str
    label: str
    tier: str                       # "données" | "contexte"
    changes: Tuple[Tuple[str, Any], ...]
    holdout: bool = False

    def world(self, base: Dict[str, Any]) -> Dict[str, Any]:
        world = dict(base)
        for path, value in self.changes:
            if value is MISSING:
                world.pop(path, None)
            else:
                world[path] = value
        return world


def _holdout(label: str, ratio: float) -> bool:
    digest = hashlib.sha1(label.encode("utf-8")).digest()
    return (digest[0] / 256.0) < ratio


def generate_cases(agent: Agent, *, seed: int = 7, max_cases: int = 200,
                   holdout_ratio: float = 0.3) -> List[Case]:
    """Engendre les cas dérivés, une fois pour toutes, de façon reproductible."""
    rng = random.Random(seed)
    cases: List[Case] = []

    for scenario in agent.scenarios:
        base = _initial_world(scenario)
        frozen = decision_paths(agent, extra=list(scenario.expect))
        free = [p for p in base if p not in frozen
                and not p.startswith("operator.")]
        context = [p for p in base if p not in free]

        for tier, paths in (("données", free), ("contexte", context)):
            for path in paths:
                for value in variants(base[path], compared_literals(agent, path)):
                    if _same(value, base.get(path, MISSING)):
                        continue
                    label = f"{scenario.name}·{tier}·{path}={_render(value)}"
                    cases.append(Case(scenario.name, label, tier,
                                      ((path, value),),
                                      _holdout(label, holdout_ratio)))

        # Quelques combinaisons : une faute peut ne se révéler qu'au croisement
        # de deux valeurs inhabituelles, qu'aucune mutation isolée n'atteint.
        for _ in range(min(8, len(base))):
            picked = rng.sample(sorted(base), min(2, len(base)))
            changes = tuple(
                (path, rng.choice(variants(base[path],
                                           compared_literals(agent, path))))
                for path in picked)
            label = (f"{scenario.name}·contexte·"
                     + "+".join(f"{p}={_render(v)}" for p, v in changes))
            cases.append(Case(scenario.name, label, "contexte", changes,
                              _holdout(label, holdout_ratio)))

    # Dédoublonnage puis échantillonnage déterministe : deux mutations
    # distinctes peuvent produire le même monde.
    unique: Dict[str, Case] = {}
    for case in cases:
        unique.setdefault(case.label, case)
    ordered = sorted(unique.values(), key=lambda c: c.label)
    if len(ordered) > max_cases:
        ordered = sorted(rng.sample(ordered, max_cases), key=lambda c: c.label)
    return ordered


# --------------------------------------------------------------- l'exécution

@dataclass
class CaseResult:
    case: Case
    passed: bool
    broken: List[str] = field(default_factory=list)
    calls: List[str] = field(default_factory=list)
    error: Optional[str] = None
    trace: str = ""

    def render(self) -> str:
        if self.passed:
            return f"  ✔ {self.case.label}"
        why = self.error or " ∧ ".join(self.broken)
        return f"  ✘ {self.case.label} — {why}"


def unconditional_bans(agent: Agent) -> Tuple[Set[str], Set[str]]:
    """(outils interdits sans condition, outils exigeant une approbation).

    On ne retient que les règles **sans garde** : elles valent quelles que
    soient les données, et c'est là toute leur valeur ici. Une règle gardée
    demanderait de réévaluer la garde au moment de l'appel — c'est-à-dire de
    redemander au runtime ce qu'il vient de décider.
    """
    forbidden: Set[str] = set()
    approval: Set[str] = set()
    allowed: Set[str] = set()
    for rule in agent.policies:
        if rule.effect in ("NEVER", "DENY") and rule.guard is None:
            forbidden.add(rule.target)
        if rule.effect == "REQUIRE_APPROVAL" and rule.guard is None:
            approval.add(rule.target)
        if rule.effect == "ALLOW":
            allowed.add(rule.target)
    if agent.policy_default.upper() == "DENY" and "*" not in allowed:
        # Sous DEFAULT DENY, un outil qu'aucune règle n'autorise — même sous
        # condition — ne peut jamais s'exécuter.
        forbidden |= {t.name for t in agent.tools if t.name not in allowed}
    forbidden.discard("*")
    approval.discard("*")
    return forbidden, approval


def _prepare(agent: Agent, world: Dict[str, Any]):
    """Runtime amorcé sur un monde donné, prêt à tourner."""
    from .runtime import Runtime

    host = ScenarioHost(agent, world)
    runtime = Runtime(agent, host, ScenarioLLM(host.world), echo=False)
    runtime._apply_initial_values(host.world, "autoloop")
    return runtime, host


def universal_breaches(agent: Agent, runtime, host, world: Dict[str, Any]
                       ) -> List[str]:
    """U1–U4 : ce qui doit tenir sur n'importe quelle donnée."""
    broken: List[str] = []
    errors = [e for e in runtime.trace.events if e.kind == "ERROR"]
    if errors:
        first = errors[0]
        detail = f" — {first.detail}" if first.detail else ""
        broken.append(f"U1 erreur d'exécution : {first.text}{detail}")

    failures = [e for e in runtime.trace.events if e.kind == "VERIFY_FAIL"]
    if failures:
        broken.append(f"U2 vérification en échec : {failures[0].text}")

    forbidden, approval = unconditional_bans(agent)
    executed = set(host.calls)
    for name in sorted(executed & forbidden):
        broken.append(f"U3 outil interdit exécuté : {name}")
    if not _is_yes(world.get(APPROVAL_PATH)):
        for name in sorted(executed & approval):
            broken.append(f"U4 outil exécuté sans approbation : {name}")
    return broken


def baseline_breaches(agent: Agent, scenario: Scenario) -> List[str]:
    """Invariants universels déjà rompus dans le monde **que l'auteur a écrit**.

    Sans cette soustraction, tous les cas dérivés d'un scénario dont
    l'exécution lève déjà une erreur seraient rouges pour une raison que la
    mutation n'a pas causée : le signal se noierait dans le bruit. Ces ruptures
    ne sont pas escamotées pour autant — elles ont leur propre barrière, et
    c'est là qu'elles doivent être corrigées.
    """
    world = _initial_world(scenario)
    try:
        runtime, host = _prepare(agent, world)
        for _ in range(max(1, scenario.within)):
            runtime.tick()
    except Exception as exc:                              # noqa: BLE001
        return [f"U1 exception : {type(exc).__name__}: {exc}"]
    return universal_breaches(agent, runtime, host, world)


def run_case(agent: Agent, scenario: Scenario, case: Case,
             baseline: Sequence[str] = ()) -> CaseResult:
    """Exécute un cas dérivé et rend ce qui a cédé.

    Les éventualités ne sont **jamais** jugées : leur avènement dépend des
    données qu'on vient précisément de changer.
    """
    from .runtime import _render_expr

    world = case.world(_initial_world(scenario))
    try:
        runtime, host = _prepare(agent, world)
    except Exception as exc:                              # noqa: BLE001
        return CaseResult(case, False, error=f"{type(exc).__name__}: {exc}")

    def holds(expectation: Node) -> bool:
        try:
            return bool(Evaluator(runtime.state).test(expectation))
        except Exception:                                 # noqa: BLE001
            return False

    judged = ([e for e in scenario.expect if holds(e)]
              if case.tier == "données" else [])
    broken: List[str] = []
    seen: Set[str] = set()
    try:
        for _ in range(max(1, scenario.within)):
            runtime.tick()
            for expectation in judged:
                text = _render_expr(expectation)
                if text not in seen and not holds(expectation):
                    seen.add(text)
                    broken.append(f"invariant rompu au tick "
                                  f"{runtime.state.tick} : {text}")
    except Exception as exc:                              # noqa: BLE001
        return CaseResult(case, False, broken=broken, calls=list(host.calls),
                          error=f"{type(exc).__name__}: {exc}",
                          trace=runtime.trace.render())

    broken += [b for b in universal_breaches(agent, runtime, host, world)
               if b not in baseline]
    return CaseResult(case, not broken, broken, list(host.calls),
                      trace=runtime.trace.render())


def run_cases(agent: Agent, cases: Sequence[Case]) -> List[CaseResult]:
    by_name = {s.name: s for s in agent.scenarios}
    baselines: Dict[str, List[str]] = {}
    results: List[CaseResult] = []
    for case in cases:
        scenario = by_name.get(case.scenario)
        if scenario is None:
            # Le rédacteur a supprimé ou renommé le scénario d'origine : le cas
            # ne veut plus rien dire, mais l'escamoter ferait monter le score
            # en effaçant les épreuves. On le compte perdu.
            results.append(CaseResult(case, False,
                                      error=f"scénario `{case.scenario}` disparu"))
            continue
        if case.scenario not in baselines:
            baselines[case.scenario] = baseline_breaches(agent, scenario)
        results.append(run_case(agent, scenario, case, baselines[case.scenario]))
    return results


# ------------------------------------------------------------- les barrières

@dataclass
class Gate:
    name: str
    passed: bool
    detail: str = ""
    #: Barrière non posée faute de matière — pas une barrière franchie. La
    #: distinction compte : afficher un ✔ pour un contrôle qui n'a pas eu lieu
    #: est la façon la plus simple de mentir dans un rapport.
    skipped: bool = False

    def render(self) -> str:
        mark = "·" if self.skipped else "✔" if self.passed else "✘"
        return f"  {mark} {self.name}" + (f" — {self.detail}" if self.detail else "")


def run_gates(program: Program, *, filename: Optional[str] = None,
              depth: Optional[int] = None) -> List[Gate]:
    """Analyse, preuve, frontière, scénarios déclarés — dans cet ordre."""
    from .analyzer import Analyzer, check_program
    from .scenario import run_scenarios
    from .verifier import verify

    gates: List[Gate] = []

    diagnostics = [d for agent in program.agents for d in Analyzer(agent).run()]
    diagnostics += check_program(program) if len(program.agents) > 1 else []
    errors = [d for d in diagnostics if d.severity == "error"]
    gates.append(Gate("analyse", not errors,
                      "; ".join(d.render() for d in errors[:5])))

    refuted: List[str] = []
    for agent in program.agents:
        report = verify(agent, **({"depth": depth} if depth else {}))
        refuted += [f"{t.key} {t.title} — {t.summary}" for t in report.refuted]
    gates.append(Gate("preuve", not refuted, "; ".join(refuted[:5])))

    if filename:
        from .boundary import check_pair, render as render_boundary

        # Norme de nommage : `X.agent` → `X.py`. L'hôte n'est pas un paramètre,
        # c'est le pendant du programme.
        host = Path(filename).with_suffix(".py")
        if not host.is_file():
            # La frontière est un contrôle **de la paire** : sans hôte, il n'y
            # a rien à contrôler. On le dit au lieu de refuser un programme qui
            # n'a pas encore d'hôte — c'est l'état normal en début d'écriture.
            gates.append(Gate("frontière", True,
                              f"non contrôlée : {host.name} absent",
                              skipped=True))
        else:
            report = check_pair(filename)
            gates.append(Gate("frontière", report.ok(),
                              "" if report.ok() else render_boundary(report)))

    failed: List[str] = []
    declared = 0
    for agent in program.agents:
        report = run_scenarios(agent, echo=False)
        declared += len(report.results)
        failed += [r.render().strip() for r in report.results if not r.passed]
    gates.append(Gate(f"scénarios ({declared})", not failed, "\n".join(failed[:5])))

    # Cinquième barrière, qu'aucune commande existante ne pose : un scénario
    # peut être vert — son EXPECT est satisfaite — alors que l'exécution a levé
    # une erreur ou exécuté un outil interdit en chemin. `agentl test` ne juge
    # que l'attente ; ici on juge aussi la façon d'y arriver.
    breaches: List[str] = []
    for agent in program.agents:
        for scenario in agent.scenarios:
            for breach in baseline_breaches(agent, scenario):
                breaches.append(f"{scenario.name} : {breach}")
    gates.append(Gate("invariants (monde déclaré)", not breaches,
                      "\n".join(breaches[:5])))
    return gates


# ---------------------------------------------------------------- le second temps

class DryRunHost:
    """Hôte réel en lecture seule : les capteurs parlent, rien n'est écrit.

    Le second temps n'est pas un test de plus : c'est le seul moment où la
    donnée vient d'ailleurs que du programme. Un `EFFECT` qui ment passe tous
    les scénarios du monde déclaré et se démasque ici — mais il serait
    inacceptable qu'un agent encore en correction agisse pour de bon, donc
    tout outil qui écrit est refusé, et l'approbation n'est jamais accordée.
    """

    def __init__(self, inner: Any, agent: Agent) -> None:
        self.inner = inner
        self.agent = agent
        self.refused: List[str] = []
        # Même nom que sur `ScenarioHost` : `universal_breaches` juge les deux
        # temps avec le même code, donc avec la même définition d'« exécuté ».
        # Un outil refusé en lecture seule y figure quand même : il a franchi
        # la POLICY, et c'est cela que U3 et U4 jugent.
        self.calls: List[str] = []

    def read(self, path: str) -> Any:
        return self.inner.read(path)

    def drain(self) -> List[Dict[str, Any]]:
        return self.inner.drain()

    def invoke(self, name: str, args: Dict[str, Any]) -> Any:
        self.calls.append(name)
        tool = self.agent.tool(name)
        if tool is None or tool.side_effects or tool.risk.upper() in ("HIGH", "CRITICAL"):
            self.refused.append(name)
            return simulated_tool_result(tool, {}) if tool is not None else {}
        return self.inner.invoke(name, args)

    def ask(self, question: str, reason: str = "") -> Any:
        return Symbol("no_answer")

    def approve(self, request: Any) -> bool:
        return False

    def emit(self, source: str, **payload: Any) -> None:
        pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


@dataclass
class HostPass:
    ran: bool
    passed: bool
    breaches: List[str] = field(default_factory=list)
    refused: List[str] = field(default_factory=list)
    ticks: int = 0
    detail: str = ""

    def render(self) -> str:
        if not self.ran:
            return f"  · hôte réel — non exécuté ({self.detail})"
        mark = "✔" if self.passed else "✘"
        body = "" if self.passed else " — " + " ∧ ".join(self.breaches)
        lines = [f"  {mark} hôte réel sur {self.ticks} tick(s){body}"]
        if self.refused:
            lines.append("    neutralisés en lecture seule, donc non jugés : "
                         + ", ".join(sorted(set(self.refused))))
        return "\n".join(lines)


def run_host_pass(agent: Agent, host: Any, llm: Any = None, ticks: int = 3) -> HostPass:
    from .llm import MockLLM
    from .runtime import Runtime

    guarded = DryRunHost(host, agent)
    runtime = Runtime(agent, guarded, llm or MockLLM(), echo=False)
    try:
        runtime.run(max_ticks=ticks)
    except Exception as exc:                              # noqa: BLE001
        return HostPass(True, False, [f"exception : {type(exc).__name__}: {exc}"],
                        guarded.refused, runtime.state.tick)
    world = {APPROVAL_PATH: Symbol("no")}
    breaches = universal_breaches(agent, runtime, guarded, world)

    # Un outil neutralisé n'a pas produit son `EFFECT` : la dérive d'effet et
    # les `VERIFY` qui en découlent seraient imputées au programme alors
    # qu'elles sont causées par la neutralisation elle-même. On les écarte —
    # et le rapport dit lesquelles, pour que personne ne lise un second temps
    # vert comme « les EFFECT déclarés sont vrais ». Ils ne sont pas jugés
    # ici : il faudrait laisser l'agent agir pour de bon, ce que cette passe
    # refuse par construction.
    if guarded.refused:
        neutralised = set(guarded.refused)
        breaches = [b for b in breaches
                    if not (b.startswith("U2 ")
                            or any(name in b for name in neutralised))]
    return HostPass(True, not breaches, breaches, guarded.refused,
                    runtime.state.tick)


# ------------------------------------------------------------------ la boucle

#: Un rédacteur reçoit une consigne et rend une source `.agent` complète.
Rewriter = Callable[[str], str]

WRITER_SYSTEM = (
    "Tu écris des programmes AGENT-L. Tu rends UNIQUEMENT le programme complet, "
    "sans commentaire d'accompagnement et sans balise de code. Tu conserves "
    "l'intention, les outils et les SCENARIO existants : tu corriges ce qui est "
    "signalé, tu n'écris pas un autre programme."
)


def build_prompt(source: str, gates: Sequence[Gate],
                 failures: Sequence[CaseResult], attempt: int) -> str:
    """La consigne de correction : ce qui s'est passé, jamais ce qu'on espérait.

    Elle ne nomme **aucun** cas retenu : le rédacteur ne doit pas pouvoir viser
    l'épreuve finale.
    """
    parts = [WRITER_SYSTEM, "", f"Tentative {attempt}.", "",
             "PROGRAMME ACTUEL :", source, ""]

    broken_gates = [g for g in gates if not g.passed]
    if broken_gates:
        parts.append("CONTRÔLES EN ÉCHEC :")
        parts += [f"- {g.name} : {g.detail}" for g in broken_gates]
        parts.append("")

    if failures:
        parts.append("CAS DÉRIVÉS EN ÉCHEC (mondes obtenus en modifiant le "
                     "GIVEN d'un SCENARIO existant) :")
        for result in failures[:12]:
            changes = ", ".join(f"{p} = {_render(v)}"
                                for p, v in result.case.changes)
            why = result.error or " ∧ ".join(result.broken)
            parts.append(f"- depuis {result.case.scenario}, avec {changes} : {why}")
        if len(failures) > 12:
            parts.append(f"- (… {len(failures) - 12} autres du même ordre)")
        parts.append("")

    parts.append(
        "Corrige le programme. Un invariant rompu se corrige dans la POLICY ou "
        "dans les gardes, pas en retirant le SCENARIO qui l'expose : supprimer "
        "une épreuve est compté comme un échec.")
    return "\n".join(parts)


@dataclass
class Attempt:
    index: int
    source: str
    gates: List[Gate]
    results: List[CaseResult] = field(default_factory=list)
    parse_error: Optional[str] = None

    @property
    def gates_ok(self) -> bool:
        return all(g.passed for g in self.gates) and self.parse_error is None

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def score(self) -> Tuple[int, int]:
        return self.passed, len(self.results)

    def failures(self) -> List[CaseResult]:
        return [r for r in self.results if not r.passed]


@dataclass
class AutoloopReport:
    agent: str = ""
    attempts: List[Attempt] = field(default_factory=list)
    holdout: List[CaseResult] = field(default_factory=list)
    host: HostPass = field(default_factory=lambda: HostPass(False, True,
                                                            detail="non demandé"))
    stopped_by: str = ""
    source: str = ""
    seen_total: int = 0
    holdout_total: int = 0

    @property
    def last(self) -> Optional[Attempt]:
        return self.attempts[-1] if self.attempts else None

    @property
    def gates_ok(self) -> bool:
        return bool(self.last) and self.last.gates_ok

    @property
    def seen_ok(self) -> bool:
        return bool(self.last) and not self.last.failures()

    @property
    def holdout_ok(self) -> bool:
        return all(r.passed for r in self.holdout)

    @property
    def overfit(self) -> bool:
        """100 % sur ce que la boucle a vu, moins sur ce qu'elle n'a pas vu."""
        return bool(self.holdout) and self.gates_ok and self.seen_ok \
            and not self.holdout_ok

    @property
    def ok(self) -> bool:
        return (self.gates_ok and self.seen_ok and self.holdout_ok
                and (self.host.passed if self.host.ran else True))

    def render(self) -> str:
        lines = [f"autoloop — {self.agent}"]
        for attempt in self.attempts:
            passed, total = attempt.score
            head = f"\n── tentative {attempt.index}"
            if attempt.parse_error:
                lines.append(f"{head}\n  ✘ grammaire — {attempt.parse_error}")
                continue
            lines.append(head)
            lines += [g.render() for g in attempt.gates]
            if total:
                lines.append(f"  cas dérivés vus : {passed}/{total}")
                lines += [r.render() for r in attempt.failures()[:8]]

        if self.holdout:
            ok = sum(1 for r in self.holdout if r.passed)
            lines.append(f"\n── lot retenu (jamais montré à la boucle) : "
                         f"{ok}/{len(self.holdout)}")
            lines += [r.render() for r in self.holdout if not r.passed][:8]
        if self.host.ran or self.host.detail != "non demandé":
            lines.append("\n── second temps")
            lines.append(self.host.render())

        lines.append("")
        if self.overfit:
            lines.append("✘ appris par cœur : 100 % sur les cas vus, "
                         "des ruptures sur le lot retenu. L'agent n'est pas prêt.")
        elif self.ok:
            lines.append("✓ l'agent tient sur ses invariants, y compris sur le "
                         "lot retenu.")
        else:
            lines.append("✘ l'agent ne tient pas.")
        lines.append(f"  arrêt : {self.stopped_by}")
        return "\n".join(lines)


def autoloop(source: str, *, filename: Optional[str] = None,
             rewrite: Optional[Rewriter] = None,
             max_attempts: int = 6,
             max_cases: int = 200,
             holdout_ratio: float = 0.3,
             seed: int = 7,
             patience: int = 2,
             budget_seconds: Optional[float] = None,
             host: Any = None, llm: Any = None, host_ticks: int = 3,
             on_attempt: Optional[Callable[[Attempt], None]] = None,
             ) -> AutoloopReport:
    """Boucle jusqu'à ce que l'agent tienne — ou jusqu'à ce qu'il faille l'admettre.

    Trois freins, parce que « boucler jusqu'à 100 % » n'est pas une condition
    d'arrêt : le plafond de tentatives, le budget de temps, et l'absence de
    progrès (`patience` tours sans qu'un cas de plus ne passe). Le dernier est
    le plus utile : un modèle qui tourne en rond le fait très vite.
    """
    from .parser import parse_source

    report = AutoloopReport()
    started = time.monotonic()
    cases: Optional[List[Case]] = None
    seen: List[Case] = []
    best = -1
    stale = 0
    current = source

    for index in range(1, max_attempts + 1):
        attempt = Attempt(index, current, [])
        try:
            program = parse_source(current, filename or "<autoloop>")
        except Exception as exc:                          # noqa: BLE001
            attempt.parse_error = f"{type(exc).__name__}: {exc}"
            report.attempts.append(attempt)
            if on_attempt:
                on_attempt(attempt)
            if rewrite is None:
                report.stopped_by = "programme illisible et aucun rédacteur"
                break
            current = _rewrite(rewrite, build_prompt(current, [], [], index))
            continue

        agent = program.agents[0]
        report.agent = agent.name
        attempt.gates = run_gates(program, filename=filename)

        if attempt.gates_ok:
            if cases is None:
                # Une seule fois, depuis la première version recevable : la
                # boucle ne choisit pas ses propres épreuves.
                cases = generate_cases(agent, seed=seed, max_cases=max_cases,
                                       holdout_ratio=holdout_ratio)
                seen = [c for c in cases if not c.holdout]
                report.seen_total = len(seen)
                report.holdout_total = len(cases) - len(seen)
            attempt.results = run_cases(agent, seen)

        report.attempts.append(attempt)
        report.source = current
        if on_attempt:
            on_attempt(attempt)

        if attempt.gates_ok and not attempt.failures():
            report.stopped_by = "réussite"
            break

        score = attempt.passed if attempt.gates_ok else -1
        stale = stale + 1 if score <= best else 0
        best = max(best, score)

        if rewrite is None:
            report.stopped_by = "aucun rédacteur : diagnostic seul"
            break
        if stale >= patience:
            report.stopped_by = f"aucun progrès sur {patience} tentatives"
            break
        if budget_seconds and time.monotonic() - started >= budget_seconds:
            report.stopped_by = f"budget de {budget_seconds:g} s épuisé"
            break
        if index == max_attempts:
            report.stopped_by = f"plafond de {max_attempts} tentatives"
            break
        current = _rewrite(rewrite, build_prompt(current, attempt.gates,
                                                 attempt.failures(), index + 1))

    report.source = current
    # Le lot retenu ne s'ouvre qu'ici, sur la version finale, et son verdict ne
    # sert jamais à corriger : le montrer à la boucle le rendrait inutile.
    if cases and report.gates_ok:
        from .parser import parse_source as _parse

        agent = _parse(report.source, filename or "<autoloop>").agents[0]
        report.holdout = run_cases(agent, [c for c in cases if c.holdout])

    if host is not None and report.gates_ok:
        agent = parse_source(report.source, filename or "<autoloop>").agents[0]
        report.host = run_host_pass(agent, host, llm, ticks=host_ticks)
    elif host is not None:
        report.host = HostPass(False, True,
                               detail="la barrière n'est pas franchie")
    return report


def _rewrite(rewrite: Rewriter, prompt: str) -> str:
    text = rewrite(prompt).strip()
    # Un modèle encadre volontiers sa réponse ; le parseur, lui, ne pardonne pas.
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


# ----------------------------------------------------------------- rédacteurs

def writer_for(model: str, api_key: Optional[str] = None) -> Rewriter:
    """Rédacteur adossé à un fournisseur, choisi sur le nom du modèle."""
    if model.startswith("gemini"):
        return _gemini_writer(model, api_key)
    if model.startswith("claude"):
        return _anthropic_writer(model, api_key)
    raise ValueError(f"modèle inconnu : {model} (attendu gemini-* ou claude-*)")


def _anthropic_writer(model: str, api_key: Optional[str]) -> Rewriter:  # pragma: no cover - réseau
    import urllib.request

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    def rewrite(prompt: str) -> str:
        body = json.dumps({
            "model": model, "max_tokens": 8192,
            "system": WRITER_SYSTEM,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode())
        return "".join(b.get("text", "") for b in data.get("content", []))

    return rewrite


def _gemini_writer(model: str, api_key: Optional[str]) -> Rewriter:  # pragma: no cover - réseau
    import urllib.request

    key = api_key or os.environ.get("GEMINI_API_KEY", "")

    def rewrite(prompt: str) -> str:
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        body = json.dumps({
            "system_instruction": {"parts": [{"text": WRITER_SYSTEM}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0, "maxOutputTokens": 16384},
        }).encode()
        request = urllib.request.Request(
            url, data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.loads(response.read().decode())
        parts = data["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)

    return rewrite
