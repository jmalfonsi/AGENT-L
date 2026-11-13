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

import math
from typing import Any, Optional

from .core import FALSY_SYMBOLS, UNDEFINED, Symbol, ordinal, truthy
from .nodes import BinOp, CallExpr, ListExpr, Literal, Node, PathExpr, UnOp

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
_ORDERING = {">", ">=", "<", "<="}
_CONTAINERS = (list, tuple, set, frozenset, dict, str)


def _orderable(left: Any, right: Any) -> bool:
    """Deux valeurs que l'évaluateur sait ordonner — deux rangs ordinaux, ou
    deux nombres finis. Tout le reste rend une comparaison d'ordre fausse à
    l'évaluation, ce qui n'en fait pas une réponse."""
    if ordinal(left) is not None and ordinal(right) is not None:
        return True
    if isinstance(left, Symbol) or isinstance(right, Symbol):
        return False
    try:
        a, b = float(left), float(right)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(a) and math.isfinite(b)


#: Symboles qui nomment un booléen : seuls noms simples qu'une garde peut lire
#: comme une valeur de vérité sans que rien ne les ait liés.
_BOOLEAN_WORDS = frozenset({"true", "yes"}) | FALSY_SYMBOLS


def _unbound(ev: Any, side: Any) -> bool:
    """Nom simple que le programme renseigne quelque part, et pas encore ici.

    Un identifiant nu que rien ne résout vaut la constante symbolique du même
    nom — c'est ainsi que `open` ou `contained` sont des valeurs. Mais un nom
    que le **programme lie lui-même** (`SET`, `PRODUCE`, `EXPECT`, `OUTPUT`…)
    est une variable : avant sa première liaison il est *indéfini*, pas égal
    à son propre nom. Sans cette distinction,

        NEVER send_reply WHEN draft_ok == no

    comparait `Symbol("draft_ok")` à `no` tant qu'aucun brouillon n'avait été
    relu : faux, donc l'interdit tombait — exactement le cas qu'il garde. Les
    chemins pointés échouaient déjà fermé ; les noms simples, l'idiome
    recommandé, non (1.17). `verify` lit ces noms comme des variables depuis
    la 1.11 : l'exécution rejoint l'analyse.
    """
    return (isinstance(side, PathExpr) and len(side.parts) == 1
            and side.parts[0] in getattr(ev, "variables", ())
            and ev.state.get(side.parts[0]) is UNDEFINED)


def _nan(value: Any) -> bool:
    return isinstance(value, float) and value != value


def _member(left: Any, right: Any) -> Any:
    """`left IN right`, en Kleene : un élément indéfini peut être celui-là."""
    if isinstance(right, (list, tuple)) and any(
            item is UNDEFINED or _nan(item) for item in right):
        known = [item for item in right
                 if item is not UNDEFINED and not _nan(item)]
        return True if left in known else UNKNOWN
    return left in right


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
        if _epistemic_unknown(ev, node):
            return UNKNOWN
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
        if _unbound(ev, node.left) or _unbound(ev, node.right):
            return UNKNOWN
        try:
            left = ev.eval(node.left)
            right = ev.eval(node.right)
        except Exception:                              # noqa: BLE001
            return UNKNOWN
        if left is UNDEFINED or right is UNDEFINED:
            return UNKNOWN
        # `NaN` n'est égal à rien, pas même à lui-même : `x != 5` était donc
        # *vrai* sur une mesure invalide, et un `ALLOW … IF x != 5` s'ouvrait
        # sur une panne de capteur. Même famille que le P0 NaN des
        # comparaisons d'ordre (v1.8.2), côté égalité (1.17).
        if _nan(left) or _nan(right):
            return UNKNOWN
        # Une comparaison que les types rendent **indécidable** n'est pas
        # fausse : elle est indéterminée. L'évaluateur rend `False` pour
        # `cpu.load > 90` quand le capteur répond `unavailable`, `"N/A"` ou
        # `NaN` — et dans une garde de NEVER, ce faux **désarmait
        # l'interdit**. Même famille que le P0 NaN de la v1.8.2, trouvée par
        # le test de propriété P1 (v1.9) : une confusion de type ne doit
        # jamais valoir permission.
        if node.op in _ORDERING and not _orderable(left, right):
            return UNKNOWN
        if node.op == "IN" and not isinstance(right, _CONTAINERS):
            return UNKNOWN
        try:
            if node.op == "IN":
                return _member(left, right)
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
            # Un nom simple que rien ne lie revient de l'évaluateur sous la
            # forme d'un symbole, donc « vrai » : `ALLOW wipe IF approved`
            # autorisait sans que personne n'ait posé `approved`, et `NEVER
            # wipe WHEN NOT safe_mode` tombait. Le commentaire ci-dessus
            # annonçait l'inverse depuis la v1.6 ; seul un chemin pointé le
            # tenait. Une constante n'est pas une valeur de vérité, hors les
            # mots qui en nomment une (1.17).
            if len(node.parts) == 1 and isinstance(value, Symbol) \
                    and ev.state.get(node.dotted) is UNDEFINED \
                    and node.dotted.lower() not in _BOOLEAN_WORDS:
                return UNKNOWN
        except Exception:                              # noqa: BLE001
            return UNKNOWN
        return UNKNOWN if value is UNDEFINED else truthy(value)

    if isinstance(node, Literal):
        return truthy(node.value)

    # Tout le reste — appels épistémiques `P(...)`, arithmétique — délègue.
    # Ce que l'évaluateur ne sait pas traiter est indéterminé, jamais faux.
    if _epistemic_unknown(ev, node):
        return UNKNOWN
    try:
        value = ev.eval(node)
    except Exception:                                  # noqa: BLE001
        return UNKNOWN
    return UNKNOWN if value is UNDEFINED else truthy(value)


_BELIEF_FUNCS = frozenset({"CONFIDENCE", "UNCERTAINTY"})
_POSTERIOR_FUNCS = frozenset({"P", "PROBABILITY", "POSTERIOR"})


def _epistemic_unknown(ev: Any, node: Optional[Node]) -> bool:
    """La garde interroge-t-elle une croyance ou une hypothèse **inconnue** ?

    L'évaluateur rend `CONFIDENCE(x) = 0` pour une croyance absente et
    `P(h) = 0` pour une hypothèse jamais publiée : une convention raisonnable
    pour un plan (« je ne sais rien, donc je ne suis pas sûr »), un fail-open
    pour une politique. `ALLOW wipe WHEN CONFIDENCE(x) < 0.8` s'ouvrait dès
    qu'on *oubliait* `x`, et `NEVER restart WHEN P(benign) > 0.5` tombait si
    la phase UPDATE_HYPOTHESES manquait à la boucle — moins d'information,
    plus de permission. Trouvé par la propriété P2b (v1.9) : dans une garde
    de politique, une telle question est indéterminée, et chaque effet en
    décide dans son sens de sûreté.
    """
    state = getattr(ev, "state", None)
    if state is None:
        return False
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, CallExpr):
            first = current.args[0] if current.args else None
            if isinstance(first, PathExpr):
                path = first.dotted
                if current.name in _BELIEF_FUNCS and \
                        path not in (getattr(state, "beliefs", None) or {}):
                    return True
                if current.name in _POSTERIOR_FUNCS:
                    try:
                        if state.get(f"{path}.posterior") is UNDEFINED:
                            return True
                    except Exception:                  # noqa: BLE001
                        return True
            stack.extend(current.args)
        elif isinstance(current, BinOp):
            stack.extend((current.left, current.right))
        elif isinstance(current, UnOp):
            stack.append(current.operand)
        elif isinstance(current, ListExpr):
            stack.extend(current.items)
    return False


def applies_when_unknown(effect: str) -> bool:
    """Une garde indéterminée déclenche-t-elle cette espèce de règle ?

    C'est ici que « fermé » prend un sens par effet. Le centraliser évite qu'un
    ajout de règle plus tard tranche par accident dans le mauvais sens.
    """
    return effect in ("NEVER", "DENY", "REQUIRE_APPROVAL")


def render(value: Any) -> str:
    return "indéterminée" if value is UNKNOWN else ("vraie" if value else "fausse")
