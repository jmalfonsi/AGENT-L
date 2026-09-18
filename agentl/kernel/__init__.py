"""Noyau de confiance d'AGENT-L (v1.9).

Le noyau est la plus petite partie du code dont dépendent les garanties
d'exécution — « aucune action interdite n'atteint l'hôte », « une action
approuvée est celle qui s'exécute », « un effet ne se produit pas deux fois
après une reprise ». Il est volontairement petit, sans dépendance au runtime,
et chiffré : `TCB_FILES` en donne la liste, et un test échoue si elle grossit
au-delà de son budget (`tests/test_kernel_invariants_aaa.py`).

Ce qui n'en fait **pas** partie : l'interpréteur (`runtime.py`), le
planificateur, le raisonneur bayésien, les traces et métriques, le
disjoncteur, le Studio. Un défaut dans ces modules peut produire une
mauvaise *proposition* ; il ne peut plus produire une exécution que la
politique n'a pas autorisée.
"""
from __future__ import annotations

from .action import (ActionRequest, action_digest, canonical, canonical_bytes,
                     coerce_inputs, typecheck, value_digest)
from .errors import ActionInDoubt, Cancelled, KernelAbort, PermitError
from .gate import (APPROVAL_WORDS, Authorization, Kernel, action_from_tool,
                   approval_granted, policy_digest)
from .permit import (ActionContext, ExecutionPermit, activated,
                     active_dispatch, current_action, require_permit)

#: Fichiers dont dépendent les garanties d'exécution — la TCB d'exécution.
#: La TCB d'**analyse** (`verifier`, `analyzer`, `solver`) est distincte :
#: un faux négatif y est une faille, mais elle ne s'exécute jamais en
#: production.
TCB_FILES = (
    "agentl/kernel/__init__.py",
    "agentl/kernel/action.py",
    "agentl/kernel/errors.py",
    "agentl/kernel/gate.py",
    "agentl/kernel/permit.py",
    "agentl/kernel/provenance.py",
    "agentl/policy.py",
    "agentl/trivalent.py",
    "agentl/state.py",
    "agentl/core.py",
)

__all__ = [
    "ActionContext", "ActionInDoubt", "ActionRequest", "APPROVAL_WORDS",
    "Authorization", "Cancelled", "ExecutionPermit", "Kernel", "KernelAbort",
    "PermitError", "TCB_FILES", "action_digest", "action_from_tool",
    "activated", "active_dispatch", "approval_granted", "canonical",
    "canonical_bytes", "coerce_inputs", "current_action", "policy_digest",
    "require_permit", "typecheck", "value_digest",
]
