"""Logique trivalente des gardes de politique (v1.6).

Le moteur de politiques annonçait échouer *fermé* : « une interdiction dont la
garde est indécidable s'applique — on ne peut pas prouver la condition
dangereuse fausse ». L'implémentation ne tenait cette promesse que pour les
gardes qui **lèvent une exception**. Or le cas de loin le plus fréquent ne lève
rien :

    NEVER restart_service WHEN maintenance.window == open

Si `maintenance.window` n'a pas été lu — capteur indisponible, chemin mal
orthographié, observation pas encore faite — la comparaison rend `False` en
silence. L'interdit ne s'applique pas, et **l'action passe**. Le mode de
défaillance le plus dangereux du langage : la trace montre un appel autorisé,
le certificat reste intact, et il est vide.

Ce module donne aux gardes de politique la sémantique que la documentation
leur prêtait déjà : la **logique trivalente forte de Kleene**, avec `UNKNOWN`
pour « la valeur de vérité de cette garde n'est pas déterminée par l'état
courant ».

    ¬UNKNOWN            = UNKNOWN
    UNKNOWN ∧ FALSE     = FALSE          (un seul faux suffit)
    UNKNOWN ∧ TRUE      = UNKNOWN
    UNKNOWN ∨ TRUE      = TRUE           (un seul vrai suffit)
    UNKNOWN ∨ FALSE     = UNKNOWN

Kleene et non « inconnu = vrai » : `x == open OR service.critical` reste vrai
dès que le second membre l'est, même si `x` est indéfini. Une logique plus
grossière transformerait toute garde touchant un chemin absent en interdiction,
et un outil qui bloque tout n'est pas plus sûr — il est débranché.

**Le sens de la sûreté dépend de l'effet**, et c'est le moteur qui l'applique :

    NEVER, DENY          UNKNOWN s'applique   (on ne peut pas prouver l'innocuité)
    ALLOW                UNKNOWN ne satisfait pas
    REQUIRE APPROVAL     UNKNOWN route vers l'humain

Une exception à l'évaluation devient `UNKNOWN` elle aussi : elle relevait déjà
du même traitement, par un chemin séparé (`on_error`) qui n'a plus lieu d'être.

**Portée délibérément limitée aux gardes de politique.** Pour une garde de
plan, « indéfini → faux » est *déjà* fermé : le plan ne se déclenche pas, donc
rien n'agit. C'est seulement pour un `NEVER` que la même convention s'ouvre.
"""
from __future__ import annotations

from typing import Any, Optional

from .core import UNDEFINED, truthy
from .nodes import BinOp, Literal, Node, PathExpr, UnOp

#: Troisième valeur. Une classe plutôt que `None` : `None` est une valeur
#: d'état légitime, et les confondre rendrait indiscernables « absent » et
#: « indéterminé ».
class _Unknown:
    __slots__ = ()

    def __repr__(self) -> str:            # pragma: no cover - confort de debug
        return "UNKNOWN"

    def __bool__(self) -> bool:
        raise TypeError(
            "UNKNOWN n'a pas de valeur de vérité : le convertir en booléen "
            "est exactement l'erreur que ce module corrige. Interroger "
            "`is TRUE` / `is FALSE`, ou passer par le moteur de politiques.")


UNKNOWN = _Unknown()

#: Comparaisons dont un opérande indéfini rend le résultat indéterminé.
_COMPARISONS = {"==", "!=", ">", ">=", "<", "<=", "IN"}


def evaluate(ev: Any, node: Optional[Node]) -> Any:
    """Valeur de vérité trivalente d'une garde : `True`, `False` ou `UNKNOWN`.

    `ev` est un `Evaluator` déjà lié à l'état ; on ne descend que dans ce qui
    change la donne — connecteurs et comparaisons — et on délègue le reste,
    pour ne pas réimplémenter l'évaluateur et risquer d'en diverger.
    """
    if node is None:
        # Une règle sans garde s'applique toujours : c'est un fait de la
        # grammaire, pas une indétermination.
        return True

    if isinstance(node, UnOp) and node.op == "NOT":
        inner = evaluate(ev, node.operand)
        return UNKNOWN if inner is UNKNOWN else (not inner)

    if isinstance(node, BinOp) and node.op in ("AND", "OR"):
        left = evaluate(ev, node.left)
        right = evaluate(ev, node.right)
        if node.op == "AND":
            # Un seul faux suffit à conclure, même face à un indéterminé.
            if left is False or right is False:
                return False
            if left is UNKNOWN or right is UNKNOWN:
                return UNKNOWN
            return True
        if left is True or right is True:
            return True
        if left is UNKNOWN or right is UNKNOWN:
            return UNKNOWN
        return False

    if isinstance(node, BinOp) and node.op in _COMPARISONS:
        # Une sortie REASON invalide est posée explicitement à UNDEFINED. Un
        # identifiant nu absent sert aussi de constante symbolique dans le
        # langage ; distinguer la présence explicite évite de transformer
        # `risk_score` en symbole et de conclure faussement `False`.
        locals_ = getattr(getattr(ev, "state", None), "locals", {})
        for side in (node.left, node.right):
            if (isinstance(side, PathExpr)
                    and side.dotted in locals_
                    and locals_[side.dotted] is UNDEFINED):
                return UNKNOWN
        try:
            left = ev.eval(node.left)
            right = ev.eval(node.right)
        except Exception:                              # noqa: BLE001
            return UNKNOWN
        if left is UNDEFINED or right is UNDEFINED:
            return UNKNOWN
        try:
            return bool(ev.eval(node))
        except Exception:                              # noqa: BLE001
            return UNKNOWN

    # Chemin nu utilisé comme booléen : `WHEN dry_run`. Absent = indéterminé,
    # pas faux — c'est le même piège, sous une autre forme syntaxique.
    if isinstance(node, PathExpr):
        locals_ = getattr(getattr(ev, "state", None), "locals", {})
        if (node.dotted in locals_
                and locals_[node.dotted] is UNDEFINED):
            return UNKNOWN
        try:
            value = ev.eval(node)
        except Exception:                              # noqa: BLE001
            return UNKNOWN
        return UNKNOWN if value is UNDEFINED else truthy(value)

    if isinstance(node, Literal):
        return truthy(node.value)

    # Tout le reste — appels épistémiques `P(...)`, arithmétique — délègue.
    # Ce que l'évaluateur ne sait pas traiter est indéterminé, jamais faux.
    try:
        value = ev.eval(node)
    except Exception:                                  # noqa: BLE001
        return UNKNOWN
    return UNKNOWN if value is UNDEFINED else truthy(value)


def applies_when_unknown(effect: str) -> bool:
    """Une garde indéterminée déclenche-t-elle cette espèce de règle ?

    C'est ici que « fermé » prend un sens par effet. Le centraliser évite qu'un
    ajout de règle plus tard tranche par accident dans le mauvais sens.
    """
    return effect in ("NEVER", "DENY", "REQUIRE_APPROVAL")


def render(value: Any) -> str:
    return "indéterminée" if value is UNKNOWN else ("vraie" if value else "fausse")
