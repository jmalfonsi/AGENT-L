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
from .provenance import OBSERVED, Prov, unwrap

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


@dataclass(frozen=True)
class Recheck:
    """Verdict d'une action en suspens, rejugée à la reprise (1.15).

    `record` est ce que le journal en garde — capteurs relus, verdict de la
    politique, sort de l'approbation — pour qu'une reprise suivante le serve
    au lieu de le recalculer, et pour qu'un auditeur sache sur quoi l'action
    a été relancée ou refusée.
    """

    ok: bool
    record: Dict[str, Any]
    reason: str = ""
    unapproved: bool = False


def _paths(node: Any, out: set) -> None:
    """Chemins nommés par une expression de garde, à toute profondeur."""
    if hasattr(node, "dotted"):
        out.add(node.dotted)
    fields = getattr(node, "__dataclass_fields__", ())
    children = (node.values() if isinstance(node, dict) else node
                if isinstance(node, (list, tuple)) else
                [getattr(node, name) for name in fields])
    for child in children:
        _paths(child, out)


def _closed(fn: Callable[[], Any], fallback: Any) -> Any:
    """`fn()`, ou `fallback` si elle lève — jamais sur une interruption."""
    try:
        return fn()
    except KernelAbort:
        raise
    except Exception:                                     # noqa: BLE001
        return fallback


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
        #: Requête figée et état jugés, par permis encore en vol : de quoi
        #: rejuger l'action si une reprise doit la relancer (1.15).
        self._judged: Dict[str, Tuple[ActionRequest, Any]] = {}
        #: `(noyau du parent, état du parent)` quand cet agent est un délégué
        #: (v1.16) : la politique du parent plafonne celle de l'enfant.
        self.ceiling: Optional[Tuple["Kernel", Any]] = None

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
                         confidence: Any = 1.0,
                         agent_child: bool = False) -> ActionRequest:
        # Un délégué AGENT-L n'est pas une fonction opaque : ses actions ont
        # chacune leur permis. Déléguer lui-même ne touche pas au monde.
        decl = self.agent.tool(name)
        undeclared = "LOW" if agent_child else self.UNDECLARED_DELEGATE_RISK
        return ActionRequest(
            name, args, decl.risk if decl is not None else undeclared,
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
        cap = self._cap(frozen)          # la politique du parent (v1.16)
        if cap is not None and cap.verdict == DENIED:
            return Authorization(STAGE_POLICY, frozen, decision=cap,
                                 detail=f"plafond du parent : {cap.reason}")
        if cap is not None and cap.verdict == APPROVAL_REQUIRED:
            decision = cap

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
        self._judged[permit.action_id] = (frozen, state)
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
            self._judged.pop(permit.action_id, None)
            self._authority.void(permit)

    def _cap(self, request: ActionRequest, host: Any = None) -> Any:
        """Verdict de la politique du parent, et de ses ancêtres (v1.16).

        Jugé sur l'état du parent et avec le risque que **le parent** déclare :
        un enfant qui sous-déclarerait le risque d'un outil accordé ne
        l'abaisse pas pour le parent. Le plus restrictif des verdicts gagne ;
        une politique inévaluable refuse.
        """
        if self.ceiling is None:
            return None
        from ..policy import APPROVAL_REQUIRED, DENIED, PolicyDecision
        parent, state = self.ceiling
        if host is not None:        # reprise : le monde du parent, relu aussi
            seen = Prov({OBSERVED})
            state = state.observed({
                p: _closed(lambda p=p: unwrap(host.read(p), seen), (None, seen))
                for p in parent.guard_sensors(request.tool)})
        decl = parent.agent.tool(request.tool)
        asked = (action_from_tool(decl, request.tool, deepcopy(request.args),
                                  request.origin, request.confidence)
                 if decl is not None else request)
        asked.provenance = dict(request.provenance)
        mine = _closed(lambda: parent.policy.check(asked, state),
                       PolicyDecision(DENIED, None, "politique inévaluable"))
        above = parent._cap(asked, host)
        for verdict in (DENIED, APPROVAL_REQUIRED):
            for found in (mine, above):
                if found is not None and found.verdict == verdict:
                    return found
        return mine

    # ------------------------------------------------------------ reprise
    def guard_sensors(self, tool: str) -> list:
        """Capteurs déclarés que nomment les gardes des règles visant `tool`."""
        wanted: set = set()
        for rule in self.policy.rules:
            if rule.target in ("*", tool):
                _paths(rule.guard, wanted)
        return [o.path for o in getattr(self.agent, "observers", ()) or ()
                if any(p == o.path or p.startswith(o.path + ".")
                       for p in wanted)]

    def recheck(self, permit: ExecutionPermit, judged: Any, host: Any,
                approval_fresh: bool) -> Recheck:
        """Rejuge une action en suspens sur le monde d'aujourd'hui (1.15).

        Une reprise re-dérive chaque décision depuis le journal, donc depuis
        les lectures du processus mort. C'est ce qui la rend déterministe, et
        c'est juste tant que rien ne s'exécute : relancer une intention en
        suspens, en revanche, c'est agir **maintenant**. Les capteurs que
        nomment les gardes visant l'outil sont relus en direct sur `host`
        (l'hôte réel, pas le journal), la politique juge la même requête figée
        sur l'état vu à travers ces lectures, et une approbation exigée n'est
        reprise telle quelle que si elle est encore fraîche — sinon l'humain
        est reconsulté. L'état du runtime n'est pas touché. Un capteur muet
        efface son chemin : la garde est indéterminée et échoue fermé.
        """
        from ..policy import ALLOWED, APPROVAL_REQUIRED
        record: Dict[str, Any] = {"facts": {}, "verdict": None,
                                  "approval": None}
        if judged is None:
            return Recheck(False, record, "décision d'origine introuvable")
        request, state = judged
        seen = Prov({OBSERVED})
        facts = {path: _closed(lambda p=path: unwrap(host.read(p), seen),
                               (None, seen))
                 for path in self.guard_sensors(request.tool)}
        record["facts"] = {path: value for path, (value, _) in facts.items()}
        decision = _closed(
            lambda: self.policy.check(request, state.observed(facts)), None)
        cap = _closed(lambda: self._cap(request, host), None)
        if cap is not None and (cap.verdict == "DENIED" or (
                cap.verdict == APPROVAL_REQUIRED
                and getattr(decision, "verdict", "") == ALLOWED)):
            decision = cap                       # le plafond du parent (1.16)
        record["verdict"] = getattr(decision, "verdict", "ERROR")
        if record["verdict"] not in (ALLOWED, APPROVAL_REQUIRED):
            return Recheck(False, record, getattr(decision, "reason",
                                                  "politique inévaluable"))
        if record["verdict"] == APPROVAL_REQUIRED:
            if permit.approved and approval_fresh:
                record["approval"] = "kept"
                return Recheck(True, record)
            granted = _closed(
                lambda: approval_granted(host.approve(deepcopy(request))), False)
            record["approval"] = "renewed" if granted else "refused"
            if not granted:
                return Recheck(False, record, "approbation périmée, non "
                                              "renouvelée", unapproved=True)
        return Recheck(True, record)

    # ---------------------------------------------------------- exécution
    def _rechecker(self, permit: ExecutionPermit) -> Callable[..., Recheck]:
        judged = self._judged.pop(permit.action_id, None)
        return lambda host, fresh: self.recheck(permit, judged, host, fresh)

    def execute(self, permit: ExecutionPermit, host: Any) -> Any:
        """Le seul appel à `host.invoke` de tout AGENT-L."""
        recheck = self._rechecker(permit)
        with self._authority.dispatch(permit) as active:
            call = lambda: host.invoke(permit.target, deepcopy(permit.args))  # noqa: E731
            if self.journal is not None:
                return self.journal.dispatch(permit, active, call, host,
                                             recheck=recheck)
            return call()

    def delegate_agent(self, permit: ExecutionPermit, run: Callable[[], Any]) -> Any:
        """Délégation à un agent AGENT-L (v1.16) : la décision est prise, mais
        rien ne touche l'hôte ici — chaque action de l'enfant a son permis, et
        son intention au journal commun. Le permis de délégation est donc
        révoqué, pas présenté."""
        self.void(permit)
        return run()

    def delegate(self, permit: ExecutionPermit, host: Any) -> Tuple[bool, Any]:
        """Le seul appel à un sous-agent. Rend `(enregistré, résultat)`."""
        recheck = self._rechecker(permit)
        with self._authority.dispatch(permit) as active:
            fn = host.subagents.get(permit.target)
            if fn is None:
                return False, None
            call = lambda: fn(deepcopy(permit.args))  # noqa: E731
            if self.journal is not None:
                return True, self.journal.dispatch(permit, active, call, host,
                                                   recheck=recheck)
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
