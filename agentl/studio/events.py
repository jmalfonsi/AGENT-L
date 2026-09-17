"""Messages du Studio — schéma §2 du contrat d'intégration.

Ce module ne connaît **ni** le runtime **ni** le serveur : il ne décrit que la
forme des messages serveur→client et la façon de les rendre sérialisables.
M1 (`session.py`) les produit, M2 (`server.py`) les transporte tels quels.

Trois garanties tiennent tout le reste :

* `to_jsonable()` ne lève jamais — une valeur qu'on ne sait pas décrire est
  rendue par son `repr()`, jamais par une exception au milieu d'un run ;
* les `float` non finis (`nan`, `inf`) deviennent `null` : `json.dumps` les
  écrirait en `NaN`, ce que ne relit aucun `JSON.parse` navigateur ;
* l'identité de nœud (`nodeId`) est calculée par la **même** règle que
  `agentl.viz`, de sorte qu'un événement d'exécution désigne exactement un
  nœud du graphe rendu par M4.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
from typing import Any, ClassVar, Dict, List, Optional

# --------------------------------------------------------------------------
# Vocabulaire de trace
# --------------------------------------------------------------------------

#: Genres d'événements de trace émis par `agentl.runtime.Trace` (§2 du contrat).
KINDS: tuple[str, ...] = (
    "TICK", "OBSERVE", "BELIEF", "GOAL", "PLAN", "STEP", "TOOL", "BLOCKED",
    "APPROVAL", "VERIFY_OK", "VERIFY_FAIL", "LLM", "MEMORY", "EVENT", "ASK",
    "DELEGATE", "ERROR", "INFO", "RETRY", "BAYES", "PLANNER", "MESSAGE",
    "SHARED",
)

#: Lane du graphe (`agentl.viz.LANES`) où retomber pour un genre de trace donné.
#: `None` = aucun nœud n'est dérivable de ce genre (il ne désigne rien de
#: déclaré : un tick, une étape, une écriture mémoire…).
KIND_TO_LANE: Dict[str, Optional[str]] = {
    "OBSERVE": "observe",
    "BELIEF": "observe",
    "BAYES": "hypothesis",
    "GOAL": "goal",
    "PLAN": "plan",
    "RETRY": "plan",
    "PLANNER": "plan",
    "TOOL": "tool",
    "BLOCKED": "tool",
    "APPROVAL": "tool",
    "VERIFY_OK": None,
    "VERIFY_FAIL": None,
    "TICK": None,
    "STEP": None,
    "LLM": None,
    "MEMORY": None,
    "EVENT": "trigger",
    "ASK": None,
    "DELEGATE": "society",
    "MESSAGE": "society",
    "SHARED": None,
    "ERROR": None,
    "INFO": None,
}


def node_id(agent: str, lane: str, key: str, *, multi: bool = False) -> str:
    """Identifiant de nœud, **strictement** celui produit par `agentl.viz`.

    `viz._build_agent()` construit ses identifiants ainsi ::

        i = f"{prefix}{lane}:{key}"   # prefix = f"{agent}|" si programme multi-agents

    Le contrat (§3) documente la forme `agent::kind::key` ; c'est une
    divergence connue et signalée. La règle qui fait foi ici est celle du code
    de `viz`, puisque M4 réutilise `build_program()` : les deux doivent
    coïncider par construction, pas par intention.
    """
    prefix = f"{agent}|" if multi else ""
    return f"{prefix}{lane}:{key}"


# --------------------------------------------------------------------------
# Sérialisation défensive
# --------------------------------------------------------------------------
_MAX_DEPTH = 12
_PRIMITIVES = (str, bool, int)


def to_jsonable(obj: Any, _depth: int = 0, _seen: Optional[set] = None) -> Any:
    """Rend `obj` sérialisable en JSON, sans jamais lever.

    Traitements spécifiques : `Symbol` et `UNDEFINED` d'AGENT-L, `Decimal`,
    `float` non finis → `null`, ensembles → listes, dataclasses → objets,
    dictionnaires à clés non textuelles → clés `str()`. Tout le reste retombe
    sur `repr()`. Les cycles et les profondeurs déraisonnables sont coupés :
    un état agentique peut contenir n'importe quel objet rendu par un hôte.
    """
    from ..core import UNDEFINED, Symbol

    if obj is None or obj is UNDEFINED:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, Symbol):
        return obj.name
    if isinstance(obj, str):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, Decimal):
        try:
            value = float(obj)
        except (ValueError, ArithmeticError):
            return None
        return value if math.isfinite(value) else None
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", "replace")

    if _depth >= _MAX_DEPTH:
        return _safe_repr(obj)

    seen = set() if _seen is None else _seen
    marker = id(obj)
    if marker in seen:
        return "<cycle>"
    seen = seen | {marker}

    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return {f.name: to_jsonable(getattr(obj, f.name, None), _depth + 1, seen)
                    for f in fields(obj)}
        except Exception:                              # noqa: BLE001
            return _safe_repr(obj)
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        try:
            items = list(obj.items())
        except Exception:                              # noqa: BLE001
            return _safe_repr(obj)
        for key, value in items:
            name = key if isinstance(key, str) else _safe_repr(key)
            out[name] = to_jsonable(value, _depth + 1, seen)
        return out
    if isinstance(obj, (set, frozenset)):
        return [to_jsonable(v, _depth + 1, seen) for v in _sorted_set(obj)]
    if isinstance(obj, (list, tuple)) or _is_iterable_container(obj):
        try:
            return [to_jsonable(v, _depth + 1, seen) for v in obj]
        except Exception:                              # noqa: BLE001
            return _safe_repr(obj)
    return _safe_repr(obj)


def _sorted_set(values) -> List[Any]:
    """Ordre stable pour un ensemble, sinon ordre d'itération."""
    try:
        return sorted(values, key=repr)
    except Exception:                                  # noqa: BLE001
        return list(values)


def _is_iterable_container(obj: Any) -> bool:
    from collections import deque
    return isinstance(obj, deque)


def _safe_repr(obj: Any) -> str:
    try:
        return repr(obj)
    except Exception:                                  # noqa: BLE001
        return f"<{type(obj).__name__} irreprésentable>"


# --------------------------------------------------------------------------
# Messages (§2)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Message:
    """Base commune : `to_dict()` rend un objet JSON `{"type": ..., ...}`."""

    TYPE: ClassVar[str] = "message"

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"type": self.TYPE}
        for f in fields(self):
            payload[f.name] = to_jsonable(getattr(self, f.name))
        return payload


@dataclass(frozen=True)
class RunStarted(Message):
    TYPE: ClassVar[str] = "run.started"
    runId: str
    agent: str
    maxTicks: Optional[int]


@dataclass(frozen=True)
class Tick(Message):
    TYPE: ClassVar[str] = "tick"
    runId: str
    tick: int
    seq: int


@dataclass(frozen=True)
class Phase(Message):
    TYPE: ClassVar[str] = "phase"
    runId: str
    tick: int
    phase: str
    status: str            # enter | exit
    seq: int


@dataclass(frozen=True)
class TraceMsg(Message):
    TYPE: ClassVar[str] = "trace"
    runId: str
    seq: int
    tick: int
    kind: str
    text: str
    detail: str = ""
    nodeId: Optional[str] = None
    ts: float = field(default_factory=time.time)
    #: Agent émetteur. `None` hors société : un run mono-agent n'a rien à
    #: désambiguïser, et le champ resterait du bruit dans le journal.
    agent: Optional[str] = None


@dataclass(frozen=True)
class StateMsg(Message):
    TYPE: ClassVar[str] = "state"
    runId: str
    tick: int
    beliefs: Dict[str, Any]
    hypotheses: Dict[str, Any]
    goals: Dict[str, Any]
    metrics: Dict[str, Any]
    seq: int = 0


@dataclass(frozen=True)
class RunFinished(Message):
    TYPE: ClassVar[str] = "run.finished"
    runId: str
    status: str            # done | stopped | error
    metrics: Dict[str, Any]
    error: Optional[str] = None


@dataclass(frozen=True)
class RunPaused(Message):
    TYPE: ClassVar[str] = "run.paused"
    runId: str
    tick: int


@dataclass(frozen=True)
class RunResumed(Message):
    TYPE: ClassVar[str] = "run.resumed"
    runId: str
    tick: int


@dataclass(frozen=True)
class Prompt(Message):
    TYPE: ClassVar[str] = "prompt"
    runId: str
    promptId: str
    mode: str              # approve | ask
    question: str
    reason: str = ""
    nodeId: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)
    timeout: float = 300.0


@dataclass(frozen=True)
class Diagnostics(Message):
    TYPE: ClassVar[str] = "diagnostics"
    diags: List[Dict[str, Any]]


@dataclass(frozen=True)
class Graph(Message):
    TYPE: ClassVar[str] = "graph"
    graph: Dict[str, Any]


@dataclass(frozen=True)
class Source(Message):
    TYPE: ClassVar[str] = "source"
    text: str
    rev: int


@dataclass(frozen=True)
class Hello(Message):
    TYPE: ClassVar[str] = "hello"
    session: Dict[str, Any]


@dataclass(frozen=True)
class ErrorMsg(Message):
    TYPE: ClassVar[str] = "error"
    message: str


def diagnostic(code: str, severity: str, message: str, line: int = 0,
               col: Optional[int] = None,
               node: Optional[str] = None) -> Dict[str, Any]:
    """Un diagnostic au format §2.

    `agentl.analyzer.Diagnostic` ne porte pas de colonne : `col` vaut `null`
    tant que le lexer ne la remonte pas. Divergence signalée, non corrigée.
    """
    return {"severity": severity, "code": code, "message": message,
            "line": int(line or 0), "col": col, "nodeId": node}
