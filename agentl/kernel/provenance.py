"""Provenance portée par les valeurs (v1.9).

Jusqu'en v1.8 la provenance était **positionnelle** : une charge utile, un
retour d'outil ou de sous-agent allait dans l'espace non fiable, consulté en
dernier. Cela empêchait une donnée externe de *masquer* une donnée fiable ; cela
ne disait rien de ce qu'on en *faisait* ensuite. `SET cible = message.to`
recopiait la valeur dans les locales — et la copie avait oublié d'où elle
venait. Les diagnostics `W119`/`W125` rattrapaient une partie du problème par
des heuristiques de **noms** (`raw`, `body`, `target`).

Désormais chaque valeur de l'état porte une étiquette, et chaque expression
calcule la sienne :

    étiquette(littéral)        = {DECLARED}
    étiquette(chemin)          = celle de la valeur lue
    étiquette(a ∘ b)           = étiquette(a) ∪ étiquette(b)
    étiquette(SET x = e)       = étiquette(e) ∪ contexte de contrôle
    étiquette(sortie REASON)   = {LLM} ∪ contexte de contrôle
    étiquette(sortie d'outil)  = {TOOL}
    étiquette(charge utile)    = {MESSAGE} / {EVENT} / {DELEGATE}

Le **contexte de contrôle** est l'étiquette des décisions qui ont mené là :
un plan choisi par le modèle, une branche `IF` prise sur une donnée reçue,
un gestionnaire d'événement. Une valeur affectée dans une telle branche en
dépend, même si elle est littérale — c'est le flux implicite, et l'ignorer
laisserait une injection choisir une constante « de confiance ».

L'union ne retire jamais rien : aucune transformation ne blanchit une
donnée. Une **attestation** ne le fait pas non plus — `validated ≠ trusted`.
Qu'un outil de validation ait accepté une valeur est un fait de plus sur
cette valeur (`ATTESTED(x, resolve_account)`), pas une nouvelle origine.

Les politiques raisonnent dessus :

    NEVER transfer WHEN LLM_DERIVED(target)
                   AND NOT ATTESTED(target, resolve_account)
    NEVER wipe WHEN UNTRUSTED(action)         // arguments ou décision

Sens de sûreté, identique au reste du noyau : une valeur **sans** étiquette
connue est `UNKNOWN`, rangée parmi les sources non fiables ; une valeur
indéfinie rend la fonction indéfinie, donc la garde indéterminée — un `NEVER`
s'applique, un `ALLOW` ne compte pas.
"""
from __future__ import annotations

from typing import Any, FrozenSet, Iterable, Optional

# ------------------------------------------------------------- sources
DECLARED = "DECLARED"      # littéral ou déclaration du programme
RUNTIME = "RUNTIME"        # fait calculé par le runtime lui-même
OBSERVED = "OBSERVED"      # capteur déclaré (OBSERVE)
HUMAN = "HUMAN"            # réponse d'opérateur (ASK)
EFFECT = "EFFECT"          # postcondition prédite par un EFFECT
INFERRED = "INFERRED"      # postérieur bayésien, hypothèse expliquante
FALLBACK = "FALLBACK"      # repli déclaré d'un capteur muet
TOOL = "TOOL"              # sortie d'outil — frontière externe (§28)
MESSAGE = "MESSAGE"        # charge utile de message inter-agents
EVENT = "EVENT"            # charge utile d'événement
DELEGATE = "DELEGATE"      # retour de sous-agent
LLM = "LLM"                # sortie du modèle (REASON, choix de plan)
SHARED = "SHARED"          # mémoire écrite par un autre agent
MEMORY = "MEMORY"          # mémoire rechargée depuis l'hôte
EXTERNAL = "EXTERNAL"      # donnée qu'un hôte déclare non fiable
UNKNOWN = "UNKNOWN"        # aucune étiquette connue — fail-closed

#: Sources dont une valeur peut avoir été écrite par un tiers. `UNKNOWN` en
#: fait partie : ne pas savoir d'où vient une valeur, c'est ne pas pouvoir
#: exclure qu'elle vienne d'un adversaire.
UNTRUSTED_SOURCES = frozenset({TOOL, MESSAGE, EVENT, DELEGATE, LLM, SHARED,
                               MEMORY, EXTERNAL, UNKNOWN})

SOURCES = frozenset({DECLARED, RUNTIME, OBSERVED, HUMAN, EFFECT, INFERRED,
                     FALLBACK}) | UNTRUSTED_SOURCES


class Prov:
    """Étiquette de provenance : un ensemble de sources, fermé par union."""

    __slots__ = ("sources",)

    def __init__(self, sources: Iterable[str] = ()) -> None:
        clean = frozenset(sources)
        unknown = clean - SOURCES
        if unknown:
            raise ValueError(f"source de provenance inconnue : "
                             f"{', '.join(sorted(unknown))}")
        object.__setattr__(self, "sources", clean)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("une étiquette de provenance est immuable")

    # Immuable : une copie est elle-même. Sans cela `deepcopy` d'une requête
    # d'action — l'isolation de la proposition d'approbation — échouait, et
    # l'action était refusée (fermé, mais à tort).
    def __copy__(self) -> "Prov":
        return self

    def __deepcopy__(self, memo: Any) -> "Prov":
        return self

    def __reduce__(self):
        return (Prov, (tuple(sorted(self.sources)),))

    def __or__(self, other: "Prov") -> "Prov":
        if not isinstance(other, Prov):
            return NotImplemented
        if other.sources <= self.sources:
            return self
        return Prov(self.sources | other.sources)

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Prov) and other.sources == self.sources

    def __hash__(self) -> int:
        return hash(self.sources)

    def __repr__(self) -> str:
        return "{" + ", ".join(sorted(self.sources)) + "}"

    # -------------------------------------------------------- prédicats
    @property
    def untrusted(self) -> bool:
        return bool(self.sources & UNTRUSTED_SOURCES) or not self.sources

    @property
    def llm_derived(self) -> bool:
        return LLM in self.sources

    @property
    def names(self) -> FrozenSet[str]:
        return self.sources


def of(*sources: str) -> Prov:
    return Prov(sources)


def join(*labels: Optional[Prov]) -> Prov:
    out = NONE
    for label in labels:
        out = out | (label if label is not None else UNKNOWN_LABEL)
    return out


#: Étiquette neutre de l'union — rien lu, rien reçu.
NONE = Prov()
UNKNOWN_LABEL = Prov({UNKNOWN})


class Labeled:
    """Valeur qu'un hôte **déclare** d'une provenance particulière.

    Un capteur qui lit un texte rédigé par un tiers — objet de courriel,
    ticket, page web — n'est pas une mesure du monde : il peut le dire.

        @host.sensor("mail.subject")
        def subject():
            return untrusted(inbox.latest().subject)

    L'étiquette **s'ajoute** à celle de la frontière (`OBSERVED`, `TOOL`…) :
    un hôte peut rendre une valeur moins fiable, jamais plus.
    """

    __slots__ = ("value", "prov")

    def __init__(self, value: Any, prov: Prov) -> None:
        self.value, self.prov = value, prov

    def __repr__(self) -> str:
        return f"Labeled({self.value!r}, {self.prov!r})"

    def __eq__(self, other: Any) -> bool:
        return (isinstance(other, Labeled) and other.value == self.value
                and other.prov == self.prov)

    def __hash__(self) -> int:                         # pragma: no cover
        return hash((repr(self.value), self.prov))


def untrusted(value: Any) -> Labeled:
    """Marque une valeur d'hôte comme écrite par un tiers."""
    return Labeled(value, Prov({EXTERNAL}))


def unwrap(value: Any, boundary: Prov) -> tuple:
    """`(valeur, étiquette)` d'une valeur franchissant la frontière."""
    if isinstance(value, Labeled):
        return value.value, boundary | value.prov
    return value, boundary


# ------------------------------------------------------- attestations
def attest_key(value: Any) -> str:
    """Clé d'attestation : une valeur, indépendamment de son habillage.

    `Symbol("acct-9")` perçu et `"acct-9"` passé à un outil `String` sont la
    même valeur pour qui l'a validée.
    """
    from ..core import Symbol
    from .action import value_digest

    if isinstance(value, Symbol):
        value = value.name
    return value_digest(value)


#: Fonctions de provenance reconnues dans une expression.
PROVENANCE_FUNCS = frozenset({"ORIGIN", "UNTRUSTED", "TRUSTED", "LLM_DERIVED",
                              "ATTESTED"})
