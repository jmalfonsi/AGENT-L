"""Noyau de satisfiabilité (AGENT-L v1.0).

Un mini-solveur, volontairement limité à la théorie dont le langage a besoin :
égalités et inégalités sur des chemins, comparaisons numériques, et l'échelle
ordinale intégrée (`LOW < MEDIUM < HIGH < CRITICAL`).

**Direction de sûreté.** Le solveur n'est pas complet, et il ne prétend pas
l'être. Il est *conservateur d'un seul côté* :

    satisfiable(φ) peut renvoyer VRAI à tort,
    mais ne renvoie jamais FAUX à tort.

Autrement dit, on ne déclare une formule insatisfiable que lorsqu'on l'a
démontré. Conséquence directe sur le vérificateur : il peut manquer une
propriété vraie (faux négatif), il n'en affirme jamais une fausse. Un outil
d'analyse de sûreté qui se trompe dans l'autre sens ne sert à rien.

Tout ce qu'il ne sait pas traiter — appels de fonction, arithmétique entre
chemins, comparaisons entre deux variables — devient un atome opaque, donc
librement satisfiable.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from .core import Symbol, ordinal
from .nodes import BinOp, CallExpr, ListExpr, Literal, Node, PathExpr, UnOp

MAX_CLAUSES = 256          # au-delà, on renonce et on répond « satisfiable »

NEGATION = {"==": "!=", "!=": "==", ">": "<=", "<=": ">", "<": ">=", ">=": "<"}


# --------------------------------------------------------------------------
# Registre des abandons
# --------------------------------------------------------------------------
#: Nombre d'abandons par explosion combinatoire depuis le chargement du module.
#: Un abandon est *sûr* — on répond « satisfiable », jamais l'inverse — mais il
#: n'est pas neutre : à partir de là le solveur ne réfute plus rien, et un
#: théorème qui s'appuie sur lui n'est plus démontré, seulement non contredit.
#: Rendre cela silencieux revenait à afficher « DÉMONTRÉ » sur une preuve que
#: le solveur avait renoncé à conduire. Le compteur permet à l'appelant de
#: constater l'abandon et de dégrader son verdict en conséquence.
_overflows = 0


class OverflowReport:
    """Ce qu'un bloc surveillé a coûté en abandons de solveur."""

    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0

    def __bool__(self) -> bool:
        return self.count > 0


@contextmanager
def watch_overflow() -> Iterator[OverflowReport]:
    """Observe les abandons du solveur pendant un bloc.

    Réentrant : chaque surveillant compte les abandons survenus dans son
    propre bloc. Déterministe et sans état partagé entre exécutions — le
    compteur est monotone, seul l'écart est lu.
    """
    report = OverflowReport()
    start = _overflows
    try:
        yield report
    finally:
        report.count = _overflows - start


def _give_up() -> DNF:
    """Renonce à la réfutation, en le comptant."""
    global _overflows
    _overflows += 1
    return TRUE_DNF


# --------------------------------------------------------------------------
# Atomes et littéraux
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Atom:
    """Contrainte élémentaire sur un chemin.

    `kind` vaut CMP (comparaison à une constante), TRUTH (chemin utilisé comme
    booléen) ou OPAQUE (rien de décidable : on n'en tire aucune conclusion).
    """

    kind: str
    path: str = ""
    op: str = ""
    value: Any = None
    tag: str = ""

    def render(self) -> str:
        if self.kind == "CMP":
            return f"{self.path} {self.op} {_show(self.value)}"
        if self.kind == "TRUTH":
            return self.path
        return self.tag or "?"


Literal_ = Tuple[Atom, bool]        # (atome, nié)
Clause = Tuple[Literal_, ...]       # conjonction
DNF = List[Clause]                  # disjonction de conjonctions

TRUE_DNF: DNF = [()]                # une clause vide = toujours vraie
FALSE_DNF: DNF = []                 # aucune clause = toujours fausse


def _show(value: Any) -> str:
    return value.name if isinstance(value, Symbol) else repr(value)


# --------------------------------------------------------------------------
# Mise en forme normale disjonctive
# --------------------------------------------------------------------------
def to_dnf(node: Optional[Node], negated: bool = False) -> DNF:
    """Convertit une expression en DNF. Renonce (→ vrai) si l'explosion menace."""
    if node is None:
        return FALSE_DNF if negated else TRUE_DNF

    if isinstance(node, UnOp) and node.op == "NOT":
        return to_dnf(node.operand, not negated)

    if isinstance(node, BinOp) and node.op in ("AND", "OR"):
        operator = node.op
        if negated:                                   # De Morgan
            operator = "OR" if operator == "AND" else "AND"
        left = to_dnf(node.left, negated)
        right = to_dnf(node.right, negated)
        if operator == "OR":
            merged = left + right
        else:
            merged = [a + b for a in left for b in right]
        if len(merged) > MAX_CLAUSES:
            return _give_up()                          # abandon conservateur
        return merged

    atom = _atomise(node)
    if atom is None:
        return TRUE_DNF
    return [((atom, negated),)]


def _atomise(node: Node) -> Optional[Atom]:
    if isinstance(node, BinOp) and node.op in NEGATION:
        left, right = node.left, node.right
        if isinstance(left, PathExpr) and _is_constant(right):
            return Atom("CMP", left.dotted, node.op, _constant(right))
        if isinstance(right, PathExpr) and _is_constant(left):
            return Atom("CMP", right.dotted, _flip(node.op), _constant(left))
        # Comparaison entre deux chemins : indécidable ici, mais on la garde
        # comme atome opaque stable, ce qui permet au moins de repérer φ ∧ ¬φ.
        return Atom("OPAQUE", tag=_signature(node))
    if isinstance(node, PathExpr):
        return Atom("TRUTH", node.dotted)
    if isinstance(node, Literal):
        return None if node.value else Atom("OPAQUE", tag="__false__")
    if isinstance(node, (CallExpr, ListExpr, BinOp, UnOp)):
        return Atom("OPAQUE", tag=_signature(node))
    return None


def _flip(op: str) -> str:
    return {"<": ">", ">": "<", "<=": ">=", ">=": "<=",
            "==": "==", "!=": "!="}[op]


def _is_constant(node: Node) -> bool:
    if isinstance(node, Literal):
        return True
    # Un identifiant nu non pointé s'évalue en symbole (`healthy`, `HIGH`).
    return isinstance(node, PathExpr) and len(node.parts) == 1 \
        and not node.parts[0].islower() or _is_bare_symbol(node)


def _is_bare_symbol(node: Node) -> bool:
    return isinstance(node, PathExpr) and len(node.parts) == 1


def _constant(node: Node) -> Any:
    if isinstance(node, Literal):
        return node.value
    return Symbol(node.parts[0])


def _signature(node: Node) -> str:
    if isinstance(node, PathExpr):
        return node.dotted
    if isinstance(node, Literal):
        return repr(node.value)
    if isinstance(node, BinOp):
        return f"({_signature(node.left)}{node.op}{_signature(node.right)})"
    if isinstance(node, UnOp):
        return f"({node.op}{_signature(node.operand)})"
    if isinstance(node, CallExpr):
        return f"{node.name}({','.join(_signature(a) for a in node.args)})"
    return type(node).__name__


# --------------------------------------------------------------------------
# Consistance d'une conjonction
# --------------------------------------------------------------------------
@dataclass
class _Domain:
    """Ce qu'on sait d'un chemin dans une conjonction donnée."""

    equals: Set[Any] = field(default_factory=set)
    differs: Set[Any] = field(default_factory=set)
    low: Optional[float] = None
    low_strict: bool = False
    high: Optional[float] = None
    high_strict: bool = False
    numeric: bool = False
    symbolic: bool = False
    truth: Optional[bool] = None

    def bound(self, op: str, value: float) -> None:
        if op in (">", ">="):
            if self.low is None or value > self.low:
                self.low, self.low_strict = value, op == ">"
            elif value == self.low and op == ">":
                self.low_strict = True
        else:
            if self.high is None or value < self.high:
                self.high, self.high_strict = value, op == "<"
            elif value == self.high and op == "<":
                self.high_strict = True

    def empty(self) -> bool:
        if self.truth is not None and False:            # placeholder lisible
            return True
        if len(self.equals) > 1:
            return True
        if self.equals & self.differs:
            return True
        if self.low is not None and self.high is not None:
            if self.low > self.high:
                return True
            if self.low == self.high and (self.low_strict or self.high_strict):
                return True
        for value in self.equals:
            rank = _rank(value)
            if rank is None:
                continue
            if self.low is not None:
                if rank < self.low or (rank == self.low and self.low_strict):
                    return True
            if self.high is not None:
                if rank > self.high or (rank == self.high and self.high_strict):
                    return True
        if self.numeric and self.symbolic:
            return False            # mélange : on ne conclut pas
        return False


def _rank(value: Any) -> Optional[float]:
    scale = ordinal(value)
    if scale is not None:
        return float(scale)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def consistent(clause: Sequence[Literal_]) -> bool:
    """Une conjonction est-elle satisfiable ? Faux ⇒ démontré contradictoire."""
    domains: Dict[str, _Domain] = {}
    opaque: Dict[str, bool] = {}

    for atom, negated in clause:
        if atom.kind == "OPAQUE":
            if atom.tag == "__false__":
                return negated
            if atom.tag in opaque and opaque[atom.tag] != (not negated):
                return False                   # φ ∧ ¬φ
            opaque[atom.tag] = not negated
            continue

        domain = domains.setdefault(atom.path, _Domain())

        if atom.kind == "TRUTH":
            wanted = not negated
            if domain.truth is not None and domain.truth != wanted:
                return False
            domain.truth = wanted
            continue

        op = NEGATION[atom.op] if negated else atom.op
        value = atom.value
        rank = _rank(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            domain.numeric = True
        if isinstance(value, (Symbol, str)):
            domain.symbolic = True

        if op == "==":
            domain.equals.add(_hashable(value))
        elif op == "!=":
            domain.differs.add(_hashable(value))
        elif rank is not None:
            domain.bound(op, rank)
        # comparaison d'ordre sur une valeur non ordonnable : sans effet

        if domain.empty():
            return False

    return all(not d.empty() for d in domains.values())


def _hashable(value: Any) -> Any:
    return value.name if isinstance(value, Symbol) else value


# --------------------------------------------------------------------------
# Interface
# --------------------------------------------------------------------------
def satisfiable(*conditions: Optional[Node]) -> bool:
    """La conjonction des conditions est-elle satisfiable ?

    Répond VRAI par défaut ; ne répond FAUX que sur démonstration.
    """
    clauses: DNF = TRUE_DNF
    for condition in conditions:
        piece = to_dnf(condition)
        clauses = [a + b for a in clauses for b in piece]
        if len(clauses) > MAX_CLAUSES:
            _give_up()
            return True
    return any(consistent(clause) for clause in clauses)


def satisfiable_with_negation(base: Sequence[Optional[Node]],
                              negated: Optional[Node]) -> bool:
    clauses: DNF = TRUE_DNF
    for condition in base:
        piece = to_dnf(condition)
        clauses = [a + b for a in clauses for b in piece]
        if len(clauses) > MAX_CLAUSES:
            _give_up()
            return True
    piece = to_dnf(negated, negated=True)
    clauses = [a + b for a in clauses for b in piece]
    if len(clauses) > MAX_CLAUSES:
        _give_up()
        return True
    return any(consistent(clause) for clause in clauses)


def entails(premises: Sequence[Optional[Node]],
            conclusion: Optional[Node]) -> bool:
    """`premises ⊨ conclusion` — démontré seulement, jamais supposé."""
    if conclusion is None:
        return True
    return not satisfiable_with_negation(premises, conclusion)


def contradictory(premises: Sequence[Optional[Node]],
                  conclusion: Optional[Node]) -> bool:
    """Les prémisses excluent-elles la conclusion ?"""
    return not satisfiable(*premises, conclusion)
