"""Liaison au monde réel.

Le fichier `.agent` déclare des **contrats** (OBSERVE, TOOL). L'hôte fournit
les **implémentations**. Cette séparation est ce qui rend un programme AGENT-L
analysable statiquement et rejouable en simulation.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from .core import Symbol
# La sémantique d'approbation appartient au noyau (v1.9) ; réexportée ici
# pour les hôtes et les tests qui l'importaient depuis `agentl.host`.
from .kernel.gate import APPROVAL_WORDS, approval_granted  # noqa: F401
from .kernel.permit import require_permit


class _GuardedSubagents(dict):
    """Registre de sous-agents qui exige un permis à l'appel.

    Reste un `dict` — `host.subagents["x"] = fn` s'écrit comme avant — mais
    ce qu'on en retire contrôle, au moment de l'appel, qu'un permis du noyau
    couvre exactement ce sous-agent et cette charge utile.
    """

    def get(self, name: str, default: Any = None) -> Any:
        fn = super().get(name)
        if fn is None:
            return default
        return _guarded(name, fn)

    def __getitem__(self, name: str) -> Any:
        return _guarded(name, super().__getitem__(name))


def _guarded(name: str, fn: Callable[[Dict[str, Any]], Any]) -> Callable:
    def dispatch(payload: Dict[str, Any]) -> Any:
        require_permit("delegate", name, payload)
        return fn(payload)
    dispatch.__wrapped__ = fn  # type: ignore[attr-defined]
    return dispatch


class Host:
    def __init__(self) -> None:
        self.trace_sink: Optional[Callable[[Any], None]] = None
        self.sensors: Dict[str, Callable[[], Any]] = {}
        self.tools: Dict[str, Callable[..., Any]] = {}
        self.approver: Optional[Callable[[Any], bool]] = None
        self.asker: Optional[Callable[[str, str], Any]] = None
        self.subagents: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = \
            _GuardedSubagents()
        self.events: Deque[Dict[str, Any]] = deque()
        #: Outils qui honorent la clé d'idempotence (`current_action()`) :
        #: une reprise durable peut les relancer sans doubler l'effet (v1.9).
        self.idempotent_tools: set = set()
        #: `outil → fn(args, contexte)` : dit, après une panne, si l'action
        #: a eu lieu — son résultat — ou `agentl.durable.NOT_EXECUTED`.
        self.reconcilers: Dict[str, Callable[[Dict[str, Any], Any], Any]] = {}

    # ---------------------------------------------------------- décorateurs
    def sensor(self, path: str):
        def wrap(fn):
            self.sensors[path] = fn
            return fn
        return wrap

    def tool(self, name: str, *, idempotent: bool = False):
        """Enregistre un outil.

        `idempotent=True` est une **promesse de l'hôte** : l'outil transmet
        `current_action().idempotency_key` au service qu'il appelle, et ce
        service ignore une seconde requête portant la même clé. C'est ce qui
        permet à une reprise après panne de relancer une action restée sans
        résultat — exactement une fois au lieu d'au plus une fois.
        """
        def wrap(fn):
            self.tools[name] = fn
            if idempotent:
                self.idempotent_tools.add(name)
            return fn
        return wrap

    def reconciler(self, name: str):
        """Enregistre la réconciliation d'un outil (exécution durable).

        `fn(args, contexte)` interroge le monde : l'action portant
        `contexte.idempotency_key` a-t-elle eu lieu ? Rendre son résultat si
        oui, `agentl.durable.NOT_EXECUTED` si non. Lever, c'est avouer qu'on
        ne sait pas — l'action reste alors indéterminée.
        """
        def wrap(fn):
            self.reconcilers[name] = fn
            return fn
        return wrap

    def subagent(self, name: str):
        def wrap(fn):
            self.subagents[name] = fn
            return fn
        return wrap

    # ---------------------------------------------------------------- events
    def emit(self, source: str, **payload: Any) -> None:
        self.events.append({"source": source, "payload": payload})

    def drain(self) -> List[Dict[str, Any]]:
        out = list(self.events)
        self.events.clear()
        return out

    # ------------------------------------------------------------- exécution
    def read(self, path: str) -> Any:
        fn = self.sensors.get(path)
        return fn() if fn else None

    def invoke(self, name: str, args: Dict[str, Any]) -> Any:
        """Dispatch réel vers un outil — sous permis du noyau seulement.

        Depuis la v1.9, appeler `host.invoke` hors de `Kernel.execute` lève
        `PermitError`. Pour tester la logique d'un outil, appeler la fonction
        enregistrée (`host.tools[nom](**args)`) : c'est elle qu'on teste, pas
        le passage gouverné.
        """
        fn = self.tools.get(name)
        if fn is None:
            raise KeyError(f"outil `{name}` non implémenté par l'hôte")
        require_permit("invoke", name, args)
        return fn(**args)

    def reconcile(self, name: str, args: Dict[str, Any], context: Any) -> Any:
        fn = self.reconcilers.get(name)
        if fn is None:
            raise LookupError(f"aucune réconciliation déclarée pour `{name}`")
        return fn(args, context)

    def ask(self, question: str, reason: str = "") -> Any:
        if self.asker is None:
            return Symbol("no_answer")
        return self.asker(question, reason)

    def approve(self, request: Any) -> bool:
        if self.approver is None:
            return False          # pas d'approbateur = refus (fail-closed)
        return approval_granted(self.approver(request))
