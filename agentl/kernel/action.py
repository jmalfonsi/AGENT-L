"""Action canonique : ce que la politique juge est ce que l'hôte reçoit.

Trois choses vivent ici, et rien d'autre :

  * `ActionRequest` — la proposition soumise au moteur de politiques ;
  * le **contrat INPUT** (coercition puis contrôle de type), parce qu'un hôte
    ne doit jamais recevoir un argument que le contrat refuse, quel que soit
    le chemin qui l'a produit ;
  * l'**empreinte canonique** d'une action, qui lie un permis à exactement
    les arguments jugés. Deux arguments égaux s'encodent en octets égaux ; un
    `Symbol` ne se confond pas avec la chaîne homonyme ; un `NaN` se marque au
    lieu de disparaître.

Aucune dépendance au runtime : ce module fait partie du noyau de confiance.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List

from ..core import Symbol


@dataclass
class ActionRequest:
    """Proposition d'action soumise au moteur de politiques."""

    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    risk: str = "LOW"
    side_effects: List[str] = field(default_factory=list)
    origin: str = "plan"          # plan | llm | event | decide | delegate
    confidence: Any = 1.0
    #: Provenance des arguments (`Prov` par nom de paramètre), et sous la clé
    #: `$control` celle de la décision qui a mené à l'action (v1.9). Vide pour
    #: une requête construite à la main : `ORIGIN()` y lira alors une
    #: provenance inconnue, donc non fiable — jamais une confiance présumée.
    provenance: Dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        rendered = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.tool}({rendered})"


# ----------------------------------------------------------- empreinte
def canonical(value: Any) -> Any:
    """Forme JSON déterministe d'une valeur d'argument.

    Les dictionnaires deviennent des paires triées : l'ordre d'insertion ne
    change pas une décision, il ne doit pas changer une empreinte. Une valeur
    que JSON ne sait pas porter est désignée par son type et son `repr` —
    ce qui suffit à lier un permis, pas à reconstruire la valeur.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return {"$float": repr(value)}
        return value
    if isinstance(value, Symbol):
        return {"$sym": value.name}
    if isinstance(value, dict):
        pairs = [[canonical(k), canonical(v)] for k, v in value.items()]
        pairs.sort(key=lambda kv: json.dumps(kv[0], sort_keys=True,
                                             ensure_ascii=False))
        return {"$dict": pairs}
    if isinstance(value, (list, tuple)):
        return {"$list" if isinstance(value, list) else "$tuple":
                [canonical(v) for v in value]}
    if isinstance(value, (set, frozenset)):
        items = [canonical(v) for v in value]
        items.sort(key=lambda v: json.dumps(v, sort_keys=True,
                                            ensure_ascii=False))
        return {"$set": items}
    return {"$opaque": repr(value), "$type": type(value).__name__}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(canonical(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def action_digest(kind: str, target: str, args: Dict[str, Any]) -> str:
    """Empreinte d'une action : genre, cible, arguments canoniques."""
    h = hashlib.sha256(b"agentl.action.v1\n")
    h.update(kind.encode("utf-8") + b"\n")
    h.update(target.encode("utf-8") + b"\n")
    h.update(canonical_bytes(args))
    return h.hexdigest()


def value_digest(value: Any) -> str:
    """Empreinte d'une valeur seule — clé des attestations (v1.9)."""
    return hashlib.sha256(b"agentl.value.v1\n" + canonical_bytes(value)).hexdigest()


# ------------------------------------------------------- contrat INPUT
_TYPE_CHECKS = {
    "number": (int, float), "float": (int, float), "int": (int,),
    "string": (str,), "bool": (bool,), "boolean": (bool,),
    "symbol": (Symbol, str), "list": (list,),
}

#: Types numériques auxquels un booléen **ne** satisfait **pas**.
#:
#: `bool` hérite de `int` en Python : `isinstance(True, int)` est vrai, donc
#: le contrôle acceptait `True` là où un outil déclare `count: Int`. L'hôte
#: recevait alors 1 ou 0 sans le savoir — un `delete(retention_days=False)`
#: passait le contrat et supprimait tout. Un contrat d'outil est une frontière
#: avec le monde réel : il doit refuser une valeur d'un autre genre, pas la
#: convertir en silence. L'inverse (`Bool` recevant `1`) est déjà refusé,
#: puisque `isinstance(1, bool)` est faux.
_NUMERIC_TYPES = frozenset({"number", "float", "int"})


def coerce_inputs(spec: Dict[str, str], args: Dict[str, Any]) -> None:
    """Un symbole perçu satisfait un contrat `String`.

    Depuis que `FOREACH` projette des éléments perçus, un identifiant réel
    (`tkt_101`) arrive dans l'état sous forme de `Symbol` — c'est ce qui
    permet de le comparer à une constante non quotée. Le refuser à l'entrée
    d'un outil déclaré `String` bloquerait toute action sur une donnée
    perçue. On l'accepte donc, en le rendant au monde comme une chaîne :
    l'hôte ne voit jamais de type propre à AGENT-L.
    """
    for key, typ in spec.items():
        value = args.get(key)
        kind = typ.lower()
        if kind == "string" and isinstance(value, Symbol):
            args[key] = str(value)
        elif kind in ("bool", "boolean") and isinstance(value, Symbol):
            # `yes`/`no` sont le vocabulaire naturel d'un programme AGENT-L ;
            # un contrat booléen doit les accepter plutôt que d'obliger à
            # écrire un littéral d'un autre monde. Tout autre symbole reste
            # refusé — on convertit ce qui a un sens, on ne devine pas.
            name = str(value).lower()
            if name in ("yes", "true", "on"):
                args[key] = True
            elif name in ("no", "false", "off"):
                args[key] = False
        elif kind == "symbol" and isinstance(value, str) and not isinstance(value, Symbol):
            args[key] = Symbol(value)


def typecheck(spec: Dict[str, str], args: Dict[str, Any]) -> str:
    """Rend la première violation du contrat, ou `""`."""
    for key, typ in spec.items():
        if key not in args:
            return f"argument manquant : {key}: {typ}"
        kind = typ.lower()
        expected = _TYPE_CHECKS.get(kind)
        if kind in _NUMERIC_TYPES and isinstance(args[key], bool):
            return f"{key} attendu {typ}, reçu bool"
        if expected and not isinstance(args[key], expected):
            return (f"{key} attendu {typ}, reçu "
                    f"{type(args[key]).__name__}")
        if (kind in _NUMERIC_TYPES
                and isinstance(args[key], (int, float))
                and not math.isfinite(float(args[key]))):
            return f"{key} attendu {typ} fini, reçu {args[key]!r}"
    unknown = [k for k in args if k not in spec]
    if unknown and spec:
        return f"argument(s) non déclaré(s) : {', '.join(unknown)}"
    return ""
