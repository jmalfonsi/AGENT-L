"""Tester un hôte sous un vrai permis.

Depuis la v1.9, `host.invoke()` hors du noyau lève `PermitError`. Un test qui
porte sur la **logique d'un outil** appelle simplement sa fonction
(`host.tools[nom](**args)`). Un test qui porte sur le **dispatch** — un hôte
enveloppant, le pont MCP, un jeton de capacité vérifié par l'hôte — a besoin
d'un permis : `dispatch()` en obtient un d'un noyau réel, sous une politique
qui autorise tout, et l'exécute.

Ce n'est pas une porte dérobée : aucun permis n'est fabriqué, il est émis par
`Kernel.authorize` exactement comme pour un programme `.agent` qui déclarerait
cet outil sans aucune politique. Aucun module d'`agentl` ne l'importe — un
test d'invariant y veille.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..state import State
from .action import ActionRequest
from .gate import Kernel


class _PermissiveAgent:
    """Agent minimal : aucune règle, `DEFAULT ALLOW`, aucun outil déclaré."""

    def __init__(self, name: str = "test") -> None:
        self.name = name
        self.policies: list = []
        self.policy_default = "ALLOW"

    def tool(self, name: str) -> None:
        return None


def dispatch(host: Any, name: str, args: Optional[Dict[str, Any]] = None, *,
             kind: str = "invoke") -> Any:
    """Exécute `name(args)` sur `host` sous un permis émis par un noyau."""
    kernel = Kernel(_PermissiveAgent(), run_id="test")
    request = ActionRequest(name, dict(args or {}), "LOW", [], "test")
    auth = kernel.authorize(request, State(), approve=lambda _: False,
                            kind=kind)
    if not auth.granted:                      # pragma: no cover - défensif
        raise AssertionError(f"autorisation de test refusée : {auth.stage}")
    if kind == "delegate":
        registered, result = kernel.delegate(auth.permit, host)
        if not registered:
            raise KeyError(f"sous-agent `{name}` non enregistré")
        return result
    return kernel.execute(auth.permit, host)
