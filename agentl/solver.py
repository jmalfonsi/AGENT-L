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

import math
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
#: Deux échelles d'ordre, **disjointes** — comme dans l'évaluateur, où
#: `state._compare` compare deux rangs ordinaux ou deux nombres, et rend faux
#: tout le reste. `LOW <= 7` n'est pas « 2 <= 7 » : c'est une comparaison
#: impossible, donc fausse, et sa négation est vraie.
ORD, NUM = "ord", "num"


def _scale(value: Any) -> Optional[Tuple[str, float]]:
    """Échelle et rang d'une constante, ou `None` si elle n'est ordonnable."""
    rank = ordinal(value)
    if rank is not None:
        return ORD, float(rank)
    if isinstance(value, Symbol):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return (NUM, number) if math.isfinite(number) else None


@dataclass
class _Domain:
    """Ce qu'on sait d'un chemin dans une conjonction donnée."""

    equals: Set[Any] = field(default_factory=set)
    #: Valeurs brutes des égalités — pour situer leur échelle.
    equal_values: List[Any] = field(default_factory=list)
    differs: Set[Any] = field(default_factory=set)
    #: Échelles qu'une comparaison d'ordre **vraie** impose au chemin.
    scales: Set[str] = field(default_factory=set)
    #: Bornes par échelle : `échelle → [bas, bas strict, haut, haut strict]`.
    bounds: Dict[str, List[Any]] = field(default_factory=dict)
    #: Comparaisons d'ordre niées, en attente d'une échelle fixée.
    negated: List[Tuple[str, str, float]] = field(default_factory=list)
    truth: Optional[bool] = None

    def bound(self, scale: str, op: str, value: float) -> None:
        low, low_strict, high, high_strict = self.bounds.setdefault(
            scale, [None, False, None, False])
        if op in (">", ">="):
            if low is None or value > low:
                low, low_strict = value, op == ">"
            elif value == low and op == ">":
                low_strict = True
        else:
            if high is None or value < high:
                high, high_strict = value, op == "<"
            elif value == high and op == "<":
                high_strict = True
        self.bounds[scale] = [low, low_strict, high, high_strict]

    def pinned(self) -> Optional[Tuple[str, bool]]:
        """L'échelle du chemin, si la conjonction la fixe.

        `(échelle, True)` : fixée sur cette échelle. `("", True)` : fixée sur
        une valeur non ordonnable (toute comparaison d'ordre y est fausse).
        `None` : rien ne la fixe.
        """
        if len(self.scales) == 1:
            return next(iter(self.scales)), True
        for value in self.equal_values:
            scale = _scale(value)
            return (scale[0] if scale else ""), True
        return None

    def empty(self) -> bool:
        if len(self.equals) > 1:
            return True
        if self.equals & self.differs:
            return True
        # Deux comparaisons d'ordre vraies sur deux échelles : aucune valeur
        # n'est à la fois un nombre et un rang ordinal.
        if len(self.scales) > 1:
            return True
        for low, low_strict, high, high_strict in self.bounds.values():
            if low is not None and high is not None:
                if low > high:
                    return True
                if low == high and (low_strict or high_strict):
                    return True
        for value in self.equal_values:
            scale = _scale(value)
            for bound_scale, (low, low_strict, high, high_strict) in \
                    self.bounds.items():
                if bound_scale not in self.scales:
                    continue          # borne issue d'une négation : déjà filtrée
                if scale is None or scale[0] != bound_scale:
                    # Une comparaison d'ordre vraie exige une valeur de son
                    # échelle : une valeur d'une autre échelle la rend fausse.
                    return True
                rank = scale[1]
                if low is not None and (rank < low or (rank == low and low_strict)):
                    return True
                if high is not None and (rank > high
                                         or (rank == high and high_strict)):
                    return True
        return False

    def settle_negations(self) -> bool:
        """Applique les comparaisons niées là où l'échelle est connue.

        `NOT (x > 7)` n'est `x <= 7` **que si** `x` est un nombre : pour un
        rang ordinal ou un symbole, la comparaison est fausse et sa négation
        vraie, sans rien borner. On ne conclut donc qu'échelle fixée — sinon
        on ne sait pas, et ne pas savoir n'autorise jamais à répondre
        « impossible ». Rend `False` si la conjonction devient vide.
        """
        pinned = self.pinned()
        if pinned is None:
            return True
        scale = pinned[0]
        for op, neg_scale, rank in self.negated:
            if neg_scale == scale:
                self.bound(scale, op, rank)
                if scale not in self.scales:
                    self.scales.add(scale)
        return not self.empty()


def _rank(value: Any) -> Optional[float]:
    scale = _scale(value)
    return scale[1] if scale is not None else None


def consistent(clause: Sequence[Literal_]) -> bool:
    """Une conjonction est-elle satisfiable ? Faux ⇒ démontré contradictoire.

    Sémantique de référence : celle de l'évaluateur sur un état où les chemins
    sont définis. Deux échelles d'ordre disjointes (`ORD`, `NUM`) ; une
    comparaison d'ordre entre échelles différentes est fausse.
    """
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

        value = atom.value
        if atom.op in ("==", "!="):
            op = NEGATION[atom.op] if negated else atom.op
            if op == "==":
                domain.equals.add(_hashable(value))
                domain.equal_values.append(value)
            else:
                domain.differs.add(_hashable(value))
        else:
            scale = _scale(value)
            if scale is None:
                continue          # constante non ordonnable : on ne conclut rien
            if negated:
                domain.negated.append((NEGATION[atom.op], scale[0], scale[1]))
                continue
            domain.scales.add(scale[0])
            domain.bound(scale[0], atom.op, scale[1])

        if domain.empty():
            return False

    return all(not d.empty() and d.settle_negations()
               for d in domains.values())


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
