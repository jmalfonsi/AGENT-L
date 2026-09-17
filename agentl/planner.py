"""Planificateur (AGENT-L v0.5 → v0.7).

**v0.5** — recherche en avant sur un état certain, minimisant le coût, bornée
par les politiques.

**v0.7** — le monde ne se laisse pas modéliser par des postconditions sûres.
Un outil déclare désormais une *distribution* d'issues :

    OUTCOME contenu WITH 0.85 { threat.status = contained }
    OUTCOME evade   WITH 0.15 { alarm.raised   = yes }

La recherche ne porte donc plus sur un état mais sur une **distribution
d'états** — un état de croyance au sens usuel. Un nœud est un ensemble de
branches pondérées ; appliquer une action éclate chaque branche selon les
issues de l'action.

On produit un **plan conforme** (*conformant plan*) : une séquence unique
d'actions, choisie pour être bonne en espérance sur toutes les branches, et
non un arbre de contingence. C'est un choix assumé — un plan conditionnel
exigerait d'observer la branche réalisée, ce que `VERIFY` fait déjà mais à
l'exécution, pas à la planification. Le runtime replanifie au tick suivant :
la contingence est obtenue par rebouclage, pas par branchement.

Critère optimisé :

    score(π) = E[U(s)] − coût(π)      (coût pondéré par P(déclenchement))
    arrêt    si  P(but) ≥ TARGET_CONFIDENCE

`UTILITY` déclare U par termes additifs. Sans bloc `UTILITY`, U ≡ 0 et le
critère se réduit exactement au coût minimal de la v0.5 : la v0.7 est une
généralisation stricte, et un programme v0.5 s'exécute à l'identique.

L'invariant de sûreté est renforcé, pas relâché :

    une action dont **une seule branche** de l'état de croyance déclenche
    une interdiction `NEVER` est exclue de la recherche entière.

Autrement dit, on ne parie pas sur l'incertitude pour contourner un interdit.
"""
from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .core import UNDEFINED, fmt
from .nodes import (Agent, CallExpr, CallStmt, Literal, Node, Plan,
                    PlannerSpec, Step, ToolDecl, VerifyStmt)
from .policy import (ALLOWED, APPROVAL_REQUIRED, PolicyEngine,
                     action_from_tool)
from .state import Evaluator, State

# Coût par défaut : à égalité d'effet, préférer le moins risqué.
RISK_COST = {"LOW": 1.0, "MEDIUM": 3.0, "HIGH": 8.0,
             "SEVERE": 15.0, "CRITICAL": 25.0, "UNKNOWN": 12.0}

EPS = 1e-9


# --------------------------------------------------------------------------
# Résultats
# --------------------------------------------------------------------------
@dataclass
class PlannedAction:
    tool: str
    args: Dict[str, Any]
    cost: float
    needs_approval: bool = False
    fire_probability: float = 1.0
    #: Durée déclarée de l'outil, en secondes. `0.0` quand l'outil n'en
    #: déclare pas — voir `W126`, qui refuse de laisser ce zéro passer pour
    #: une mesure dès que le temps entre dans l'arbitrage.
    duration: float = 0.0

    def render(self) -> str:
        rendered = ", ".join(f"{k}={fmt(v)}" for k, v in self.args.items())
        mark = " 🙋" if self.needs_approval else ""
        odds = ("" if self.fire_probability > 1 - 1e-6
                else f" [p={self.fire_probability:.2f}]")
        return f"{self.tool}({rendered}){mark}{odds}"


def fmt_duration(seconds: float) -> str:
    """Durée lisible. `165` se lit mal ; `2min45s` se lit."""
    if seconds <= 0:
        return "0s"
    if seconds < 1:
        return f"{seconds * 1000:g}ms"
    if seconds < 60:
        return f"{seconds:g}s"
    minutes, rest = divmod(int(round(seconds)), 60)
    if minutes < 60:
        return f"{minutes}min" + (f"{rest}s" if rest else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h" + (f"{minutes:02d}" if minutes else "")


@dataclass
class PlanningResult:
    actions: List[PlannedAction] = field(default_factory=list)
    cost: float = 0.0
    expanded: int = 0
    reason: str = ""
    pruned_by_policy: List[str] = field(default_factory=list)
    goal_probability: float = 0.0
    expected_utility: float = 0.0
    uncertain: bool = False
    #: Durée cumulée du plan, en secondes — somme des durées **pleines**, non
    #: pondérées par la probabilité de déclenchement : une action qui ne se
    #: déclenche qu'une fois sur trois prend malgré tout tout son temps quand
    #: elle se déclenche. Pour une borne, c'est le pire cas qui compte.
    duration: float = 0.0

    @property
    def found(self) -> bool:
        return bool(self.actions)

    @property
    def score(self) -> float:
        return self.expected_utility - self.cost

    def render(self) -> str:
        if not self.found:
            return f"aucun plan ({self.reason}, {self.expanded} nœuds)"
        chain = " → ".join(a.render() for a in self.actions)
        tail = f"coût {self.cost:g}"
        if self.duration > 0:
            tail += f", durée {fmt_duration(self.duration)}"
        if self.uncertain:
            tail += (f", P(but)={self.goal_probability:.2f}"
                     f", E[U]={self.expected_utility:.1f}"
                     f", score={self.score:.1f}")
        return f"{chain}   [{tail}, {self.expanded} nœuds]"


# --------------------------------------------------------------------------
# État abstrait et distributions
# --------------------------------------------------------------------------
class _Overlay(State):
    """Calque de planification : le monde réel en lecture, les effets par-dessus.

    La recherche ne mute jamais l'état du runtime.
    """

    def __init__(self, base: State, overlay: Optional[Dict[str, Any]] = None):
        self.world = base.world
        self.beliefs = base.beliefs
        self.memory = base.memory
        self.tick = base.tick
        self.untrusted = getattr(base, "untrusted", {})
        self._base_locals = getattr(base, "_base_locals", base.locals)
        self.overlay: Dict[str, Any] = dict(overlay or {})
        self.locals = dict(self._base_locals)
        self.locals.update(self.overlay)


def _derive(node: _Overlay, extra: Dict[str, Any]) -> _Overlay:
    fresh = _Overlay.__new__(_Overlay)
    fresh.world = node.world
    fresh.beliefs = node.beliefs
    fresh.memory = node.memory
    fresh.tick = node.tick
    fresh._base_locals = node._base_locals
    fresh.overlay = {**node.overlay, **extra}
    fresh.locals = dict(node._base_locals)
    fresh.locals.update(fresh.overlay)
    return fresh


Belief = List[Tuple[_Overlay, float]]        # distribution sur états abstraits


def _normalise(belief: Belief, tracked: Sequence[str]) -> Belief:
    """Fusionne les branches indiscernables sur les chemins suivis."""
    merged: Dict[Tuple, Tuple[_Overlay, float]] = {}
    for state, probability in belief:
        if probability < EPS:
            continue
        key = tuple((p, repr(state.get(p))) for p in tracked)
        if key in merged:
            existing, weight = merged[key]
            merged[key] = (existing, weight + probability)
        else:
            merged[key] = (state, probability)
    return list(merged.values())


# --------------------------------------------------------------------------
# Planificateur
# --------------------------------------------------------------------------
class Planner:
    def __init__(self, agent: Agent, spec: Optional[PlannerSpec] = None,
                 policy: Optional[PolicyEngine] = None):
        self.agent = agent
        self.spec = spec or agent.planner or PlannerSpec()
        self.policy = policy or PolicyEngine(agent)
        self.operators: List[ToolDecl] = [t for t in agent.tools if t.is_operator]
        self.uncertain = (any(t.is_uncertain for t in self.operators)
                          or bool(agent.utility))

    # ------------------------------------------------------------------ API
    def goal_conditions(self) -> List[Node]:
        """Buts : `ACHIEVE` explicites, sinon les conditions des `GOAL`."""
        if self.spec.achieve:
            return list(self.spec.achieve)
        conditions: List[Node] = []
        for goal in self.agent.goals:
            if goal.condition is not None:
                conditions.append(goal.condition)
            conditions.extend(goal.targets)
        return conditions

    def utility(self, state: _Overlay) -> float:
        if not self.agent.utility:
            return 0.0
        evaluator = Evaluator(state)
        return sum(term.value for term in self.agent.utility
                   if evaluator.test(term.condition))

    def expected_utility(self, belief: Belief) -> float:
        return sum(p * self.utility(s) for s, p in belief)

    def goal_probability(self, belief: Belief, goals: Sequence[Node]) -> float:
        return sum(p for s, p in belief
                   if all(Evaluator(s).test(g) for g in goals))

    def synthesize(self, state: State) -> PlanningResult:
        goals = self.goal_conditions()
        if not goals:
            return PlanningResult(reason="aucun objectif à atteindre")
        if not self.operators:
            return PlanningResult(reason="aucun outil ne déclare d'EFFECT")

        target = min(max(self.spec.target_confidence, 0.0), 1.0)
        start: Belief = [(_Overlay(state), 1.0)]
        if self.goal_probability(start, goals) >= target - EPS:
            return PlanningResult(reason="objectifs déjà satisfaits",
                                  goal_probability=1.0,
                                  uncertain=self.uncertain)

        tracked = self._tracked_paths(goals)
        counter = itertools.count()
        # Front de Pareto (coût, durée) par état atteint. Un simple ensemble
        # d'états visités — ce qu'on faisait — bloque définitivement un état
        # dès qu'une route y mène, **fût-elle la plus chère** : à effets
        # égaux, le plan retenu dépendait alors de l'ordre de déclaration des
        # outils et non de leur coût. Deux dimensions et non une, parce
        # qu'une route plus chère mais plus rapide reste utile sous
        # `DEADLINE` : on n'écarte que ce qui est dominé sur les deux.
        reached: Dict[Tuple, List[Tuple[float, float]]] = {
            self._key(start, tracked): [(0.0, 0.0)]}
        pruned: List[str] = []
        expanded = 0
        best: Optional[PlanningResult] = None          # meilleur plan partiel
        satisfying: Optional[PlanningResult] = None    # meilleur plan conforme

        frontier: List[Tuple] = [
            (self._priority(start, goals, 0.0), 0.0, next(counter), start, [],
             0.0)
        ]

        while frontier:
            _, cost, _, belief, path, elapsed = heapq.heappop(frontier)
            if len(path) >= self.spec.max_depth:
                continue

            for action, successor, fired in self._successors(belief, pruned,
                                                             tracked):
                expanded += 1
                if expanded > self.spec.max_nodes:
                    return self._settle(
                        satisfying, best,
                        f"budget de {self.spec.max_nodes} nœuds épuisé",
                        expanded, pruned)

                new_cost = cost + action.cost * fired
                # La durée s'ajoute **pleine**, sans pondération par `fired` :
                # une action qui ne se déclenche qu'une fois sur trois prend
                # tout son temps quand elle se déclenche, et une échéance se
                # tient sur le pire cas. Le coût, lui, reste une espérance —
                # c'est un budget, pas une borne.
                new_elapsed = elapsed + action.duration
                if self.spec.deadline is not None \
                        and new_elapsed > self.spec.deadline + EPS:
                    # Écarté à la planification, comme un NEVER : le plan trop
                    # long n'est pas engendré plutôt que rejeté après coup.
                    pruned.append(
                        f"{action.tool} — échéance dépassée "
                        f"({fmt_duration(new_elapsed)} > "
                        f"{fmt_duration(self.spec.deadline)})")
                    continue

                key = self._key(successor, tracked)
                front = reached.get(key)
                if front is not None and any(
                        seen_cost <= new_cost + EPS
                        and seen_elapsed <= new_elapsed + EPS
                        for seen_cost, seen_elapsed in front):
                    continue                   # dominé sur le coût ET le temps
                reached[key] = [
                    pair for pair in (front or [])
                    if not (new_cost <= pair[0] + EPS
                            and new_elapsed <= pair[1] + EPS)
                ] + [(new_cost, new_elapsed)]

                new_path = path + [action]
                probability = self.goal_probability(successor, goals)
                candidate = PlanningResult(
                    new_path, round(new_cost, 6), expanded,
                    "objectifs atteints", pruned, probability,
                    self.expected_utility(successor), self.uncertain,
                    round(new_elapsed, 6))

                # On ne s'arrête PAS au premier plan conforme : le critère
                # annoncé est le score, pas la première solution trouvée.
                # Sortir ici rendrait `isolate_endpoint` (P=0.97, score 59)
                # alors qu'une escalade en deux temps score 81.
                if probability >= target - EPS:
                    if satisfying is None or candidate.score > satisfying.score + EPS:
                        satisfying = candidate
                elif best is None or candidate.score > best.score + EPS:
                    best = candidate

                heapq.heappush(
                    frontier,
                    (self._priority(successor, goals, new_cost), new_cost,
                     next(counter), successor, new_path, new_elapsed))

        reason = ("aucune action applicable dans cet état" if expanded == 0
                  else "espace de recherche épuisé")
        return self._settle(satisfying, best, reason, expanded, pruned)

    def _settle(self, satisfying: Optional[PlanningResult],
                best: Optional[PlanningResult], reason: str,
                expanded: int, pruned: List[str]) -> PlanningResult:
        if satisfying is not None:
            satisfying.expanded = expanded
            satisfying.reason = "objectifs atteints"
            return satisfying
        return self._finish(best, reason, expanded, pruned)

    def _finish(self, best: Optional[PlanningResult], reason: str,
                expanded: int, pruned: List[str]) -> PlanningResult:
        """Sans plan atteignant la cible, on rend le meilleur plan partiel.

        Ne rien rendre serait plus simple et moins honnête : un plan qui
        contient la menace dans 70 % des mondes vaut mieux que l'inaction,
        pourvu qu'on l'annonce comme tel.
        """
        if best is None or best.goal_probability <= EPS:
            return PlanningResult(reason=reason, expanded=expanded,
                                  pruned_by_policy=pruned,
                                  uncertain=self.uncertain)
        best.expanded = expanded
        best.reason = (f"cible P≥{self.spec.target_confidence:.2f} non atteinte "
                       f"— meilleur plan partiel")
        return best

    # ------------------------------------------------------------- interne
    def _successors(self, belief: Belief, pruned: List[str],
                    tracked: Sequence[str]):
        """Applique chaque opérateur à la distribution entière."""
        for tool in self.operators:
            applicable: List[Tuple[int, Dict[str, Any]]] = []
            forbidden = False
            needs_approval = False

            for index, (state, _probability) in enumerate(belief):
                args = self._bind(tool, state)
                if args is None:
                    continue
                if tool.requires is not None and not Evaluator(state).test(
                        tool.requires):
                    continue
                # L'origine doit être celle que l'action portera à
                # l'exécution : un plan synthétisé est matérialisé puis exécuté
                # via CallStmt, donc avec origin="plan" (cf. runtime). Interroger
                # la politique sous une autre origine ("planner", hors du
                # domaine documenté plan|llm|event) rendrait invisible tout
                # NEVER/DENY conditionné à `action.origin == plan` — l'action
                # serait engendrée puis bloquée seulement à l'exécution, en
                # violation de l'invariant §17 (« une action refusée n'est pas
                # engendrée »).
                request = action_from_tool(tool, tool.name, args,
                                           origin="plan")
                decision = self.policy.check(request, state)
                if decision.verdict not in (ALLOWED, APPROVAL_REQUIRED):
                    note = f"{tool.name} — {decision.reason}"
                    if note not in pruned:
                        pruned.append(note)
                    # Un NEVER vaut dans *toutes* les branches : on ne parie
                    # pas sur l'incertitude pour contourner un interdit.
                    if decision.rule is not None \
                            and decision.rule.effect == "NEVER":
                        forbidden = True
                        break
                    continue
                needs_approval |= decision.verdict == APPROVAL_REQUIRED
                applicable.append((index, args))

            if forbidden or not applicable:
                continue

            firing = {index: args for index, args in applicable}
            fired = sum(belief[i][1] for i in firing)
            if fired < EPS:
                continue

            successor: Belief = []
            for index, (state, probability) in enumerate(belief):
                if index not in firing:
                    successor.append((state, probability))
                    continue
                evaluator = Evaluator(state)
                for branch in tool.branches:
                    changes = {e.path: evaluator.eval(e.value)
                               for e in branch.effects}
                    successor.append((_derive(state, changes),
                                      probability * branch.probability))

            successor = _normalise(successor, tracked)
            if self._key(successor, tracked) == self._key(belief, tracked):
                continue                       # action sans effet observable

            base_cost = (tool.cost if tool.cost is not None
                         else RISK_COST.get(tool.risk, 5.0))
            if needs_approval:
                base_cost += self.spec.approval_cost
            # v1.6 — le temps entre dans le score. `TIME_WEIGHT` convertit des
            # secondes en unités de coût ; à 0 (défaut) le score est celui de
            # la v0.7 au bit près, et un programme antérieur planifie à
            # l'identique. Arbitrer entre rapide-et-bruyant et lent-et-sûr est
            # le problème du domaine, pas une préférence câblée : le langage
            # fournit le taux de change, l'auteur le fixe.
            duration = tool.duration or 0.0
            base_cost += self.spec.time_weight * duration
            # Les arguments retenus sont ceux de la branche la plus probable :
            # le plan reste une séquence unique et pleinement instanciée.
            best_index = max(firing, key=lambda i: belief[i][1])
            yield (PlannedAction(tool.name, firing[best_index], base_cost,
                                 needs_approval, fired, duration),
                   successor, fired)

    def _bind(self, tool: ToolDecl, state: _Overlay) -> Optional[Dict[str, Any]]:
        args: Dict[str, Any] = {}
        for param in tool.inputs:
            path = tool.bindings.get(param, param)
            value = state.get(path)
            if value is UNDEFINED:
                return None                    # action non instanciable ici
            args[param] = value
        return args

    def _priority(self, belief: Belief, goals: Sequence[Node],
                  cost: float) -> float:
        """f = coût − E[U] + h, avec h = Σ_buts (1 − P(but_i)).

        Pour un programme déterministe sans `UTILITY`, h redevient le nombre
        de buts encore faux et f le coût uniforme : le comportement v0.5 est
        conservé à l'identique.
        """
        heuristic = sum(1.0 - sum(p for s, p in belief if Evaluator(s).test(g))
                        for g in goals)
        return cost - self.expected_utility(belief) + heuristic

    def _tracked_paths(self, goals: Sequence[Node]) -> List[str]:
        from .analyzer import _collect_paths

        paths: Set[str] = set()
        for goal in goals:
            _collect_paths(goal, paths)
        for term in self.agent.utility:
            _collect_paths(term.condition, paths)
        for tool in self.operators:
            _collect_paths(tool.requires, paths)
            for branch in tool.branches:
                for effect in branch.effects:
                    paths.add(effect.path)
                    _collect_paths(effect.value, paths)
        return sorted(paths)

    def _key(self, belief: Belief, tracked: Sequence[str]) -> Tuple:
        return tuple(sorted(
            (tuple((p, repr(s.get(p))) for p in tracked), round(w, 6))
            for s, w in belief))

    # --------------------------------------------------------- exécutable
    def as_plan(self, result: PlanningResult, name: str) -> Plan:
        """Matérialise le résultat en `PLAN` exécutable par le runtime.

        Le plan se termine toujours par la vérification du but qui l'a
        produit : une action planifiée n'est jamais réputée réussie sur la
        foi de son modèle d'effet — a fortiori quand ce modèle est une
        distribution.
        """
        body = [
            CallStmt(CallExpr(a.tool, kwargs={k: Literal(v)
                                              for k, v in a.args.items()}))
            for a in result.actions
        ]
        steps = [Step("synthesized", body)]
        goals = self.goal_conditions()
        if goals:
            from .nodes import BinOp

            condition = goals[0]
            for extra in goals[1:]:
                condition = BinOp("AND", condition, extra)
            steps.append(Step("check", [VerifyStmt(condition,
                                                   label="but synthétisé")]))
        return Plan(name, steps)
