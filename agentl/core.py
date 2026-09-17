"""Noyau : valeurs, erreurs, échelles ordinales d'AGENT-L."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# --------------------------------------------------------------------------
# Erreurs
# --------------------------------------------------------------------------
class AgentLError(Exception):
    """Erreur de base."""


class LexError(AgentLError):
    pass


class ParseError(AgentLError):
    pass


class EvalError(AgentLError):
    pass


class PolicyViolation(AgentLError):
    pass


class RuntimeAgentError(AgentLError):
    pass


# --------------------------------------------------------------------------
# Valeurs
# --------------------------------------------------------------------------
class _Undefined:
    """Valeur absente. Distincte de None (qui est une valeur légitime)."""

    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self) -> str:
        return "UNDEFINED"

    def __bool__(self) -> bool:
        return False


UNDEFINED = _Undefined()


@dataclass(frozen=True)
class Symbol:
    """Constante symbolique non quotée : healthy, HIGH, unknown, none..."""

    name: str

    def __repr__(self) -> str:
        return self.name

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Symbol):
            return self.name == other.name
        if isinstance(other, str):
            return self.name == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.name)


@dataclass
class Belief:
    """Croyance : valeur + incertitude + provenance + fraîcheur."""

    value: Any
    confidence: float = 1.0
    source: str = "declared"
    updated: Any = None

    def render(self) -> str:
        return f"{self.value} (c={self.confidence:.2f}, src={self.source})"


# --------------------------------------------------------------------------
# Échelle ordinale intégrée : permet `severity >= HIGH`
# --------------------------------------------------------------------------
ORDINAL_SCALE = {
    "NONE": 0,
    "INFO": 1,
    "LOW": 2,
    "MODERATE": 3,
    "MEDIUM": 3,
    "HIGH": 4,
    "SEVERE": 5,
    "CRITICAL": 6,
    # `UNSET` — risque non tranché, produit par l'import MCP (§29). Rangé
    # **au-dessus** de `CRITICAL` et non hors échelle : un risque inconnu qui
    # ne se compare à rien laisserait `action.risk >= HIGH` indécidable, donc
    # une garde ouverte. Ici, toute borne supérieure le refuse et toute borne
    # inférieure l'attrape — la garde échoue fermée dans les deux sens.
    # `E011` l'interdit bien avant l'exécution ; ceci est la seconde barrière,
    # pour le code qui appelle le runtime sans passer par l'analyseur.
    "UNSET": 7,
}


def ordinal(value: Any):
    """Rang ordinal d'un symbole d'échelle, sinon None."""
    if isinstance(value, Symbol):
        return ORDINAL_SCALE.get(value.name.upper())
    if isinstance(value, str):
        return ORDINAL_SCALE.get(value.upper())
    return None


def truthy(value: Any) -> bool:
    if value is UNDEFINED:
        return False
    if isinstance(value, Symbol):
        return value.name.lower() not in ("none", "false", "unknown", "null")
    return bool(value)


def fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list):
        return "[" + ", ".join(fmt(v) for v in value) + "]"
    return str(value)
