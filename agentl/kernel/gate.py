"""La porte unique : autoriser, puis exécuter.

Tout ce qui décide qu'une action touche le monde vit ici, et nulle part
ailleurs. Le runtime reste l'interpréteur du programme — perception, gardes
de plan, raisonnement, traces, métriques, disjoncteur — mais il ne détient
plus le droit d'appeler l'hôte : il demande un permis, puis demande au noyau
de l'exécuter.

    runtime                          noyau (ce module)
    ───────                          ─────────────────
    CALL outil(args) ──────────────▶ propose_tool   : outil déclaré ? INPUT ?
                                     authorize      : gel des arguments,
                                                      politique fail-closed,
                                                      approbation liée,
                                                      émission du permis
    disjoncteur, traces ◀──────────  Authorization
    ───────────────────────────────▶ execute        : permis actif,
                                                      journal d'intention,
                                                      Host.invoke()

Le sens de sûreté n'a pas bougé d'une ligne — même ordre NEVER → DENY →
ALLOW → DEFAULT → APPROVAL, même logique trivalente, même approbation
explicite. Ce qui change est ce que le runtime **peut** faire : un chemin
qui oublierait la politique n'a plus de permis à présenter, et l'hôte le
refuse (`PermitError`).
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from ..core import Symbol
from .action import (ActionRequest, action_digest, coerce_inputs, typecheck)
from .errors import KernelAbort
from .permit import ExecutionPermit, PermitAuthority

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


def action_from_tool(decl: Any, name: str, args: Dict[str, Any], origin: str,
                     confidence: Any = 1.0) -> ActionRequest:
    """Requête d'action pour un outil, avec le risque **déclaré**."""
    if decl is None:
        return ActionRequest(name, args, "UNKNOWN", [], origin, confidence)
    return ActionRequest(name, args, decl.risk, list(decl.side_effects),
                         origin, confidence)


def policy_digest(agent: Any) -> str:
    """Empreinte des politiques d'un agent — portée par chaque permis.

    Un journal d'intention qui la conserve dit *sous quelle politique* une
    action fut autorisée ; une reprise qui la retrouve différente sait que la
    décision ne se re-dérive plus.
    """
    h = hashlib.sha256(b"agentl.policy.v1\n")
    h.update(repr(getattr(agent, "policy_default", None)).encode("utf-8"))
    for rule in getattr(agent, "policies", []) or []:
        h.update(repr(rule).encode("utf-8") + b"\n")
    return h.hexdigest()


# ------------------------------------------------------------ verdict
#: Étapes auxquelles une autorisation peut s'arrêter. `granted` seule porte
#: un permis.
GRANTED = "granted"
STAGE_ISOLATION = "isolation"          # arguments impossibles à figer
STAGE_POLICY_ERROR = "policy_error"    # politique inévaluable
STAGE_POLICY = "policy"                # DENIED
STAGE_APPROVAL_ISOLATION = "approval_isolation"
STAGE_APPROVAL = "approval"            # approbation refusée ou en erreur


@dataclass(frozen=True)
class Authorization:
    """Réponse du noyau à une demande d'autorisation."""

    stage: str
    request: ActionRequest
    permit: Optional[ExecutionPermit] = None
    decision: Any = None
    detail: str = ""
    approved: bool = False

    @property
    def granted(self) -> bool:
        return self.permit is not None


class _Silent:
    """Observateur par défaut : le noyau ne trace rien lui-même."""


# -------------------------------------------------------------- noyau
class Kernel:
    """Autorité d'exécution d'un agent.

    Une instance par runtime. Elle partage son `PolicyEngine` avec le runtime
    (qui s'en sert pour le planificateur et l'affichage) ; ce qui ne se
    partage pas, c'est le droit d'émettre et d'exécuter un permis.
    """

    #: Risque prêté à un sous-agent qu'aucun `TOOL` ne décrit. Un sous-agent
    #: est une fonction Python opaque : rien dans l'interface n'empêche d'y
    #: écrire en base, d'appeler le réseau ou de lancer une commande. Sans
    #: contrat, on ne *devine* pas son innocuité — on échoue fermé.
    UNDECLARED_DELEGATE_RISK = "CRITICAL"

    def __init__(self, agent: Any, *, run_id: str = "run") -> None:
        from ..policy import PolicyEngine        # la politique, enfermée

        self.agent = agent
        self.policy = PolicyEngine(agent)
        self.run_id = run_id
        self.policy_digest = policy_digest(agent)
        self._authority = PermitAuthority(getattr(agent, "name", "agent"))
        self._seq = 0
        #: Journal d'intention (exécution durable, `agentl.durable`). `None`
        #: = exécution éphémère : l'hôte est appelé directement.
        self.journal: Any = None

    # ------------------------------------------------------ propositions
    def propose_tool(self, name: str, args: Dict[str, Any],
                     origin: str = "plan",
                     confidence: Any = 1.0) -> Tuple[Optional[ActionRequest], str]:
        """Requête pour un outil déclaré, contrat INPUT appliqué.

        Rend `(None, raison)` pour un outil non déclaré ou des arguments que
        le contrat refuse. Le risque est lu dans la déclaration, jamais reçu
        de l'appelant : un runtime ne peut pas présenter un outil `CRITICAL`
        comme `LOW` à la politique.
        """
        decl = self.agent.tool(name)
        if decl is None:
            return None, "outil non déclaré"
        coerce_inputs(decl.inputs, args)
        error = typecheck(decl.inputs, args)
        if error:
            return None, error
        return action_from_tool(decl, name, args, origin, confidence), ""

    def propose_delegate(self, name: str, args: Dict[str, Any],
                         confidence: Any = 1.0) -> ActionRequest:
        decl = self.agent.tool(name)
        return ActionRequest(
            name, args,
            decl.risk if decl is not None else self.UNDECLARED_DELEGATE_RISK,
            list(decl.side_effects) if decl is not None else [],
            "delegate", confidence)

    # ------------------------------------------------------ autorisation
    def authorize(self, request: ActionRequest, state: Any, *,
                  approve: Callable[[ActionRequest], Any],
                  kind: str = "invoke",
                  observer: Any = None) -> Authorization:
        """Décide, et émet un permis si — et seulement si — tout passe.

        `approve` est l'approbateur **brut** de l'hôte : sa réponse est
        interprétée ici (`approval_granted`), son exception vaut refus. Un
        observateur reçoit les étapes pour les tracer ; il ne peut pas
        changer l'issue — le permis n'est émis qu'à la toute fin.
        """
        observer = observer if observer is not None else _Silent()
        # 1. Gel. La politique juge une copie, l'approbateur une autre, et le
        #    permis porte la première : rien de ce qui se passe ensuite ne
        #    peut modifier ce qui sera exécuté.
        try:
            frozen = ActionRequest(request.tool, deepcopy(request.args),
                                   request.risk, list(request.side_effects),
                                   request.origin, request.confidence,
                                   dict(request.provenance))
        except KernelAbort:
            raise
        except Exception as exc:                          # noqa: BLE001
            return Authorization(STAGE_ISOLATION, request,
                                 detail=f"{type(exc).__name__}: {exc}")

        # 2. Politique, fermée sur exception.
        try:
            decision = self.policy.check(frozen, state)
        except KernelAbort:
            raise
        except Exception as exc:                          # noqa: BLE001
            return Authorization(STAGE_POLICY_ERROR, frozen,
                                 detail=f"{type(exc).__name__}: {exc}")
        _notify(observer, "decided", frozen, decision)

        from ..policy import APPROVAL_REQUIRED, DENIED
        if decision.verdict == DENIED:
            return Authorization(STAGE_POLICY, frozen, decision=decision,
                                 detail=decision.reason)

        # 3. Approbation explicite, sur une copie isolée.
        approved = False
        if decision.verdict == APPROVAL_REQUIRED:
            _notify(observer, "pending", frozen, decision)
            try:
                proposal = deepcopy(frozen)
            except KernelAbort:
                raise
            except Exception as exc:                      # noqa: BLE001
                return Authorization(STAGE_APPROVAL_ISOLATION, frozen,
                                     decision=decision,
                                     detail=f"{type(exc).__name__}: {exc}")
            try:
                granted = approval_granted(approve(proposal))
            except KernelAbort:
                raise
            except Exception as exc:                      # noqa: BLE001
                _notify(observer, "approver_failed", frozen, exc)
                granted = False
            if not granted:
                return Authorization(STAGE_APPROVAL, frozen, decision=decision)
            approved = True

        # 4. Émission — en dernier, pour qu'aucune exception d'un
        #    observateur ne laisse un permis derrière elle.
        permit = self._mint(kind, frozen, approved, state)
        return Authorization(GRANTED, frozen, permit=permit,
                             decision=decision, approved=approved)

    def _mint(self, kind: str, request: ActionRequest, approved: bool,
              state: Any) -> ExecutionPermit:
        self._seq += 1
        tick = getattr(state, "tick", 0)
        agent = getattr(self.agent, "name", "agent")
        action_id = f"{self.run_id}:{agent}:t{tick}:a{self._seq}"
        digest = action_digest(kind, request.tool, request.args)
        key = hashlib.sha256(
            f"agentl.idem.v1\n{self.run_id}\n{agent}\n{self._seq}\n{digest}"
            .encode("utf-8")).hexdigest()[:32]
        return self._authority.mint(
            kind=kind, target=request.tool, args=request.args,
            action_id=action_id, idempotency_key=key,
            policy_digest=self.policy_digest, approved=approved,
            nonce=f"{agent}#{self._seq}")

    def void(self, permit: Optional[ExecutionPermit]) -> None:
        """Révoque un permis qui ne sera pas exécuté (disjoncteur ouvert…)."""
        if permit is not None:
            self._authority.void(permit)

    # ---------------------------------------------------------- exécution
    def execute(self, permit: ExecutionPermit, host: Any) -> Any:
        """Le seul appel à `host.invoke` de tout AGENT-L."""
        with self._authority.dispatch(permit) as active:
            call = lambda: host.invoke(permit.target, deepcopy(permit.args))  # noqa: E731
            if self.journal is not None:
                return self.journal.dispatch(permit, active, call, host)
            return call()

    def delegate(self, permit: ExecutionPermit, host: Any) -> Tuple[bool, Any]:
        """Le seul appel à un sous-agent. Rend `(enregistré, résultat)`."""
        with self._authority.dispatch(permit) as active:
            fn = host.subagents.get(permit.target)
            if fn is None:
                return False, None
            call = lambda: fn(deepcopy(permit.args))  # noqa: E731
            if self.journal is not None:
                return True, self.journal.dispatch(permit, active, call, host)
            return True, call()

    # ------------------------------------------------------------- état
    @property
    def stats(self) -> Dict[str, int]:
        """Permis émis, et exécutés sans que l'hôte ne les ait contrôlés.

        Le second compte est un signal pour l'opérateur : un hôte sur mesure
        qui ne passe pas par `Host.invoke` (ni par `require_permit`) reçoit
        les actions autorisées sans vérifier qu'elles le sont. La garantie
        structurelle tient toujours — seul le noyau l'appelle — mais la
        défense en profondeur manque.
        """
        return {"permits": self._authority.minted,
                "unpresented": self._authority.unpresented}


def _notify(observer: Any, event: str, *args: Any) -> None:
    hook = getattr(observer, event, None)
    if hook is not None:
        hook(*args)
