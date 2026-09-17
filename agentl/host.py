"""Liaison au monde réel.

Le fichier `.agent` déclare des **contrats** (OBSERVE, TOOL). L'hôte fournit
les **implémentations**. Cette séparation est ce qui rend un programme AGENT-L
analysable statiquement et rejouable en simulation.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional

from .core import Symbol


#: Réponses d'un approbateur qui valent **accord**, et rien d'autre.
#: Volontairement court : ce sont les formes qu'un humain ou une interface
#: écrit pour dire oui, pas une tentative d'interpréter une phrase.
APPROVAL_WORDS = frozenset({"yes", "y", "true", "ok", "approve", "approved",
                            "oui", "accord", "accepte", "accepté"})


def approval_granted(answer: Any) -> bool:
    """Cette réponse d'approbateur autorise-t-elle l'action ?

    `bool(answer)` était le contrôle jusqu'en v1.6, et il approuvait tout ce
    qui n'est pas vide : la chaîne `"no"`, `"refusé"`, un dict
    `{"decision": "denied"}`, un objet de réponse HTTP d'un service qui vient
    de refuser. Le seul cas correctement traité était `False` — celui qu'un
    approbateur bien typé rend déjà. Sur le chemin qui existe précisément
    pour arrêter une action à risque, l'ambiguïté doit se résoudre en refus.

    On n'accepte donc que ce qui dit oui sans ambiguïté : le booléen `True`,
    ou un mot d'accord reconnu (symbole ou chaîne). Toute autre valeur —
    inconnue, structurée, vide — refuse.
    """
    if answer is True:
        return True
    if isinstance(answer, (str, Symbol)):
        return str(answer).strip().lower() in APPROVAL_WORDS
    return False


class Host:
    def __init__(self) -> None:
        self.trace_sink: Optional[Callable[[Any], None]] = None
        self.sensors: Dict[str, Callable[[], Any]] = {}
        self.tools: Dict[str, Callable[..., Any]] = {}
        self.approver: Optional[Callable[[Any], bool]] = None
        self.asker: Optional[Callable[[str, str], Any]] = None
        self.subagents: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
        self.events: Deque[Dict[str, Any]] = deque()

    # ---------------------------------------------------------- décorateurs
    def sensor(self, path: str):
        def wrap(fn):
            self.sensors[path] = fn
            return fn
        return wrap

    def tool(self, name: str):
        def wrap(fn):
            self.tools[name] = fn
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
        fn = self.tools.get(name)
        if fn is None:
            raise KeyError(f"outil `{name}` non implémenté par l'hôte")
        return fn(**args)

    def ask(self, question: str, reason: str = "") -> Any:
        if self.asker is None:
            return Symbol("no_answer")
        return self.asker(question, reason)

    def approve(self, request: Any) -> bool:
        if self.approver is None:
            return False          # pas d'approbateur = refus (fail-closed)
        return approval_granted(self.approver(request))
