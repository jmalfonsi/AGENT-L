"""Provenance statique : d'où vient l'argument d'une action risquée ? (v1.14)

À l'exécution, chaque valeur porte l'union de ses sources (`kernel/provenance`).
À l'analyse, `W119` et `W140` cherchaient la même chose par des **noms** :
`raw`, `host`, `target`. Ce module calcule la même chose par le **flot** : il
propage les sources à travers `SET`, `FOREACH` et les branches, et rend, pour
chaque appel d'un outil `HIGH`/`CRITICAL`, les arguments qui reçoivent une
source non fiable.

    étiquette(littéral)      = {}                 étiquette(a ∘ b) = ∪
    étiquette(chemin)        = celle de l'environnement
    SET x = e                → env[x] = étiquette(e)        (flux explicites)
    REASON / JUDGE           → champs produits : {LLM}
    sortie d'outil           → {TOOL}               DELEGATE → {DELEGATE}
    payload.* d'un EVENT     → {EVENT}              d'un MESSAGE → {MESSAGE}

Choix assumés, tous du côté de la prudence de l'analyse, pas de celle de
l'exécution :

* **Flux explicites seulement.** Le contrôle implicite (`IF payload.x THEN
  f("const")`) est suivi à l'exécution, pas ici : le suivre statiquement
  marquerait non fiable tout ce que fait un gestionnaire d'événement.
* **Jointure par union.** Après un `IF`, une variable porte l'union de ses deux
  branches ; une boucle est parcourue deux fois (point fixe d'un treillis fini).
* **Un chemin inconnu n'est pas non fiable.** Les capteurs (`OBSERVE`) sont
  réputés fiables : seul l'hôte, par `untrusted(...)`, dit le contraire, et
  l'analyse ne lit pas l'hôte.

Ce n'est pas une preuve de sûreté de l'hôte, ni du détecteur d'injection : c'est
la même relation que le runtime calcule, vue avant l'exécution.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Set, Tuple

from .kernel import provenance as P
from .nodes import (Agent, AskStmt, BinOp, CallExpr, CallStmt, DelegateStmt,
                    ForEachStmt, IfStmt, JudgeStmt, ListExpr, Literal,
                    LoopStmt, MessageStmt, Node, PathExpr, Plan, ReasonStmt,
                    SetStmt, Stmt, UnOp, VerifyStmt)

Label = FrozenSet[str]
EMPTY: Label = frozenset()

#: Les sources qu'un tiers peut avoir écrites (`provenance.UNTRUSTED_SOURCES`
#: sans `UNKNOWN`, que l'analyse n'invente pas, ni `SHARED`/`MEMORY`, qu'elle
#: ne sait pas suivre jusqu'à une lecture).
UNTRUSTED: Label = frozenset({P.LLM, P.EVENT, P.MESSAGE, P.DELEGATE, P.TOOL,
                              P.EXTERNAL})

#: Fonctions de provenance qu'une politique peut lire (`kernel/provenance`).
_PROVENANCE_FUNCS = P.PROVENANCE_FUNCS


@dataclass(frozen=True)
class Flow:
    """Un argument d'outil risqué qui reçoit une source non fiable."""

    unit: str
    tool: str
    param: str
    sources: Tuple[str, ...]
    line: int


class _Env:
    """Association chemin → étiquette, avec jointure par union."""

    def __init__(self, data: Optional[Dict[str, Label]] = None) -> None:
        self.data: Dict[str, Label] = dict(data or {})

    def copy(self) -> "_Env":
        return _Env(self.data)

    def get(self, path: str) -> Label:
        if path in self.data:
            return self.data[path]
        # `item.champ`, `payload.x.y` : le plus long préfixe connu.
        parts = path.split(".")
        while len(parts) > 1:
            parts.pop()
            prefix = ".".join(parts)
            if prefix in self.data:
                return self.data[prefix]
        return EMPTY

    def set(self, path: str, label: Label) -> None:
        self.data[path] = label

    def join(self, other: "_Env") -> "_Env":
        out = _Env(self.data)
        for key, label in other.data.items():
            out.data[key] = out.data.get(key, EMPTY) | label
        return out


def label_of(node: Optional[Node], env: _Env) -> Label:
    if node is None or isinstance(node, Literal):
        return EMPTY
    if isinstance(node, PathExpr):
        return env.get(node.dotted)
    if isinstance(node, BinOp):
        return label_of(node.left, env) | label_of(node.right, env)
    if isinstance(node, UnOp):
        return label_of(node.operand, env)
    if isinstance(node, ListExpr):
        out = EMPTY
        for item in node.items:
            out |= label_of(item, env)
        return out
    if isinstance(node, CallExpr):
        out = EMPTY
        for arg in [*node.args, *node.kwargs.values()]:
            out |= label_of(arg, env)
        return out
    return EMPTY


def _produced(stmt: ReasonStmt) -> Iterable[str]:
    for key in stmt.produce:
        yield key
        yield f"reason.{key}"
        if isinstance(stmt, JudgeStmt):
            yield f"judge.{key}"
            yield f"judge.{key}.value"


class _Walker:
    def __init__(self, agent: Agent, unit: str, payload: Label) -> None:
        self.agent = agent
        self.unit = unit
        self.risky = {t.name for t in agent.tools
                      if t.risk in ("HIGH", "CRITICAL")}
        self.out: List[Flow] = []
        base: Dict[str, Label] = {}
        if payload:
            base["payload"] = payload
        self.env = _Env(base)

    # ------------------------------------------------------------------
    def run(self, stmts: Sequence[Stmt]) -> None:
        self.env = self.block(stmts, self.env)

    def block(self, stmts: Sequence[Stmt], env: _Env) -> _Env:
        for stmt in stmts:
            env = self.stmt(stmt, env)
        return env

    def stmt(self, stmt: Stmt, env: _Env) -> _Env:
        if isinstance(stmt, SetStmt):
            env = env.copy()
            env.set(stmt.target, label_of(stmt.value, env))
        elif isinstance(stmt, ReasonStmt):
            env = env.copy()
            for path in _produced(stmt):
                env.set(path, frozenset({P.LLM}))
        elif isinstance(stmt, DelegateStmt):
            env = env.copy()
            for key in stmt.expect:
                env.set(key, frozenset({P.DELEGATE}))
        elif isinstance(stmt, AskStmt):
            env = env.copy()
            env.set(f"answer.{stmt.addressee}", EMPTY)
        elif isinstance(stmt, CallStmt):
            self.call(stmt, env)
            env = env.copy()
            tool = self.agent.tool(stmt.call.name)
            for field_name in (tool.outputs if tool else {}):
                label = frozenset({P.TOOL})
                env.set(field_name, label)
                env.set(f"{stmt.call.name}.{field_name}", label)
        elif isinstance(stmt, IfStmt):
            left = self.block(stmt.then, env.copy())
            right = self.block(stmt.otherwise, env.copy())
            env = left.join(right)
        elif isinstance(stmt, ForEachStmt):
            env = env.copy()
            env.set("item", label_of(stmt.source, env))
            if stmt.var != "item":
                env.set(stmt.var, label_of(stmt.source, env))
            env = env.join(self.block(stmt.body, env.copy()))
            env = env.join(self.block(stmt.body, env.copy()))    # point fixe
        elif isinstance(stmt, LoopStmt):
            env = env.join(self.block(stmt.body, env.copy()))
            env = env.join(self.block(stmt.body, env.copy()))
        elif isinstance(stmt, VerifyStmt):
            env = env.join(self.block(stmt.on_fail, env.copy()))
        return env

    # ------------------------------------------------------------------
    def call(self, stmt: CallStmt, env: _Env) -> None:
        name = stmt.call.name
        if name not in self.risky:
            return
        tool = self.agent.tool(name)
        names = list(tool.inputs)
        args = {names[i]: expr for i, expr in enumerate(stmt.call.args)
                if i < len(names)}
        args.update(stmt.call.kwargs)
        for param, expr in args.items():
            hit = label_of(expr, env) & UNTRUSTED
            if hit:
                self.out.append(Flow(self.unit, name, param,
                                     tuple(sorted(hit)), stmt.line))


# --------------------------------------------------------------------------
def covered_by_policy(agent: Agent, tool: str, param: str) -> bool:
    """Une règle de la politique lit-elle la provenance de cet appel ?

    Crédite ce que `W119`/`W125` ne créditaient pas (« limite actuelle » du
    §35) : un `NEVER outil WHEN …` dont la garde appelle une fonction de
    provenance (`UNTRUSTED`, `LLM_DERIVED`, `ORIGIN`, `ATTESTED`).
    """
    from .analyzer import _walk_expr

    for rule in agent.policies:
        if rule.effect not in ("NEVER", "DENY") or rule.target not in ("*", tool):
            continue
        for node in _walk_expr(rule.guard):
            if isinstance(node, CallExpr) and node.name in _PROVENANCE_FUNCS:
                return True
    return False


def flows(agent: Agent) -> List[Flow]:
    """Tous les arguments d'outils risqués qui reçoivent une source non fiable."""
    found: List[Flow] = []

    def walk(unit: str, stmts: Sequence[Stmt], payload: Label) -> None:
        walker = _Walker(agent, unit, payload)
        walker.run(stmts)
        for flow in walker.out:            # le point fixe repasse sur le corps
            if flow not in found:
                found.append(flow)

    for plan in agent.plans:
        walk(plan.name, [s for step in plan.steps for s in step.body], EMPTY)
    for event in agent.events:
        walk(f"EVENT {event.source}", event.body, frozenset({P.EVENT}))
    for handler in agent.messages:
        walk(f"ON MESSAGE {handler.name}", handler.body,
             frozenset({P.MESSAGE}))
    if agent.decide:
        for rule in agent.decide.rules:
            walk("DECIDE", [rule], EMPTY)
    if agent.on_verify_fail:
        walk("ON VERIFY.FAIL", agent.on_verify_fail, EMPTY)
    if agent.loop:
        walk("LOOP", agent.loop.body, EMPTY)
    return found
