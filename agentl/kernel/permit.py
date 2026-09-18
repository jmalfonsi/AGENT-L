"""Permis d'exécution — la seule clé qui ouvre l'hôte.

Jusqu'en v1.8, « aucune action n'atteint un outil sans traverser le moteur
de politiques » était une propriété du code Python : `Runtime.call_tool`
appelait `host.invoke` après `_authorize_action`, à un seul endroit, et rien
n'empêchait un second chemin de s'y ajouter. La SPEC le disait (§28) : les
théorèmes tiendraient encore si quelqu'un appelait `host.invoke` directement.

Un permis transforme cette convention en contrôle exécuté :

    ActionRequest → Kernel.authorize() → ExecutionPermit → Kernel.execute()
                                                               ↓
                                          Host.invoke() ← require_permit()

* il ne se **fabrique** que par une autorité du noyau — le constructeur exige
  un jeton privé, et un objet construit autrement n'est de toute façon pas
  dans le registre de l'autorité ;
* il est **lié** à une action : genre, cible et empreinte canonique des
  arguments. Des arguments modifiés après la décision ne le présentent plus ;
* il est **à usage unique**, et **périssable** : en émettre un nouveau révoque
  celui qui n'a pas servi, de sorte qu'un permis oublié ne se réveille pas
  plus tard ;
* il n'est **présentable** que pendant son exécution : le noyau l'active dans
  une variable de contexte le temps de l'appel. `Host.invoke` sans permis
  actif lève `PermitError`, comme un outil qui tente d'appeler un autre
  outil depuis l'intérieur de l'hôte.

Ce que cela ne fait pas : empêcher du Python **malveillant** exécuté dans le
même processus de contourner le contrôle — il peut lire les variables
privées de ce module comme n'importe quelle autre. La frontière protège
contre le chemin oublié, l'adjonction imprudente et le député confus ; la
confiance dans le code hôte lui-même reste celle de `SECURITY.md`.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

from .action import action_digest
from .errors import PermitError

#: Jeton de fabrication. Détenu par ce module ; `ExecutionPermit(…)` sans lui
#: lève immédiatement.
_MINT = object()

ISSUED, DISPATCHING, SPENT, VOID = "issued", "dispatching", "spent", "void"


class ExecutionPermit:
    """Autorisation d'exécuter **une** action, exactement celle-là."""

    __slots__ = ("kind", "target", "args", "action_hash", "action_id",
                 "idempotency_key", "policy_digest", "approved", "nonce",
                 "issuer", "_status")

    def __init__(self, _token: Any, *, kind: str, target: str,
                 args: Dict[str, Any], action_id: str, idempotency_key: str,
                 policy_digest: str, approved: bool, nonce: str,
                 issuer: str) -> None:
        if _token is not _MINT:
            raise PermitError("un permis d'exécution ne se fabrique que dans "
                              "le noyau (Kernel.authorize)")
        for name, value in (("kind", kind), ("target", target), ("args", args),
                            ("action_hash", action_digest(kind, target, args)),
                            ("action_id", action_id),
                            ("idempotency_key", idempotency_key),
                            ("policy_digest", policy_digest),
                            ("approved", approved), ("nonce", nonce),
                            ("issuer", issuer), ("_status", ISSUED)):
            object.__setattr__(self, name, value)

    def __setattr__(self, name: str, value: Any) -> None:
        raise PermitError("un permis d'exécution est immuable")

    def __copy__(self) -> "ExecutionPermit":
        # Copier une capacité n'en crée pas une seconde : la copie *est* le
        # même permis, avec le même registre et le même usage unique.
        return self

    def __deepcopy__(self, memo: Dict[int, Any]) -> "ExecutionPermit":
        return self

    def __reduce__(self):
        raise PermitError("un permis d'exécution ne se sérialise pas : il ne "
                          "vaut que dans le processus qui l'a émis")

    @property
    def status(self) -> str:
        return self._status

    def __repr__(self) -> str:
        return (f"<permis {self.kind} {self.target} {self.action_id} "
                f"{self._status}>")


@dataclass(frozen=True)
class ActionContext:
    """Ce qu'un outil peut savoir de l'action qu'il exécute.

    `idempotency_key` est stable d'une exécution à sa reprise après panne :
    un hôte qui la transmet à son service (en-tête `Idempotency-Key`, clé
    d'unicité en base) rend la reprise exactement-une-fois.
    """

    kind: str
    tool: str
    action_id: str
    idempotency_key: str
    attempt: int = 1


class _Active:
    """Exécution en cours d'un permis. Créée par l'autorité, seulement."""

    __slots__ = ("permit", "presented", "closed", "attempt")

    def __init__(self, permit: ExecutionPermit) -> None:
        self.permit = permit
        self.presented = False
        self.closed = False
        self.attempt = 1

    @property
    def context(self) -> ActionContext:
        p = self.permit
        return ActionContext(p.kind, p.target, p.action_id,
                             p.idempotency_key, self.attempt)


_ACTIVE: ContextVar[Optional[_Active]] = ContextVar("agentl_active_permit",
                                                    default=None)


class PermitAuthority:
    """Registre des permis d'**un** noyau."""

    def __init__(self, issuer: str) -> None:
        self.issuer = issuer
        self._issued: Dict[str, ExecutionPermit] = {}
        self.minted = 0
        self.unpresented = 0

    def mint(self, **fields: Any) -> ExecutionPermit:
        # Au plus un permis en attente : celui qu'on n'a pas exécuté avant
        # d'en demander un autre ne servira plus. Un disjoncteur ouvert, une
        # exception entre la décision et l'appel — le permis meurt avec.
        for stale in list(self._issued.values()):
            self.void(stale)
        permit = ExecutionPermit(_MINT, issuer=self.issuer, **fields)
        self._issued[permit.nonce] = permit
        self.minted += 1
        return permit

    def void(self, permit: ExecutionPermit) -> None:
        if self._issued.get(getattr(permit, "nonce", None)) is permit:
            del self._issued[permit.nonce]
            object.__setattr__(permit, "_status", VOID)

    @contextmanager
    def dispatch(self, permit: Any) -> Iterator[_Active]:
        """Active le permis le temps d'un appel, puis le consume."""
        nonce = getattr(permit, "nonce", None)
        if not isinstance(permit, ExecutionPermit) \
                or self._issued.get(nonce) is not permit:
            raise PermitError(
                "permis inconnu de ce noyau : fabriqué hors du noyau, émis "
                "par un autre, déjà utilisé ou révoqué")
        del self._issued[nonce]
        object.__setattr__(permit, "_status", DISPATCHING)
        active = _Active(permit)
        token = _ACTIVE.set(active)
        try:
            yield active
        finally:
            _ACTIVE.reset(token)
            active.closed = True
            object.__setattr__(permit, "_status", SPENT)
            if not active.presented:
                self.unpresented += 1


def require_permit(kind: str, target: str, args: Dict[str, Any]) -> ActionContext:
    """Contrôle posé **dans l'hôte**, à chaque point de dispatch réel.

    `Host.invoke`, les sous-agents d'un `Host` et le pont MCP l'appellent
    avant de toucher au monde. Un hôte sur mesure qui dispatche lui-même
    devrait faire de même : c'est ce qui rend le contournement bruyant.
    """
    active = _ACTIVE.get()
    if active is None or active.closed:
        raise PermitError(
            f"{kind} `{target}` sans permis d'exécution : seul le noyau "
            f"AGENT-L appelle l'hôte (Kernel.execute)")
    permit = active.permit
    if active.presented:
        raise PermitError(
            f"permis déjà présenté pour {permit.kind} `{permit.target}` : un "
            f"permis vaut pour un seul appel à l'hôte")
    if permit.kind != kind or permit.target != target:
        raise PermitError(
            f"permis émis pour {permit.kind} `{permit.target}`, présenté pour "
            f"{kind} `{target}`")
    if action_digest(kind, target, args) != permit.action_hash:
        raise PermitError(
            f"{kind} `{target}` : arguments différents de ceux que la "
            f"politique a jugés")
    active.presented = True
    return active.context


def current_action() -> Optional[ActionContext]:
    """Contexte de l'action en cours d'exécution, ou `None`."""
    active = _ACTIVE.get()
    if active is None or active.closed:
        return None
    return active.context


def active_dispatch() -> Optional[_Active]:
    """Exécution en cours — pour la transporter d'un fil à l'autre (aio)."""
    return _ACTIVE.get()


@contextmanager
def activated(active: Optional[_Active]) -> Iterator[None]:
    """Réactive une exécution en cours dans un autre contexte.

    Sert au pont asynchrone : une coroutine d'hôte s'exécute dans la boucle
    d'événements, pas dans le fil du runtime, et `ContextVar` ne traverse pas
    `run_coroutine_threadsafe`. Seul un objet créé par une autorité — donc un
    permis en cours d'exécution — peut être réactivé ; un permis consumé
    reste refusé par `require_permit`.
    """
    if active is not None and not isinstance(active, _Active):
        raise PermitError("seule une exécution émise par le noyau se réactive")
    token = _ACTIVE.set(active)
    try:
        yield
    finally:
        _ACTIVE.reset(token)
