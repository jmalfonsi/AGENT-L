"""État agentique s_t = (x_t, b_t, m_t) et évaluation des expressions.

Correspondance avec la sémantique formelle :

    x_t : world     — état observé du monde (faits, plats, clés pointées)
    b_t : beliefs   — croyances (valeur + confiance + source + fraîcheur)
    m_t : memory    — court terme / long terme / connaissances
    locals          — portée d'exécution (résultats, payload d'événement)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from .core import UNDEFINED, Belief, EvalError, Symbol, ordinal, truthy
from .kernel import provenance as _prov
from .kernel.provenance import (PROVENANCE_FUNCS, UNKNOWN_LABEL, Prov,
                                attest_key)
from .nodes import BinOp, CallExpr, ListExpr, Literal, Node, PathExpr, UnOp

_DECLARED = Prov({_prov.DECLARED})
_RUNTIME = Prov({_prov.RUNTIME})
_SHARED = Prov({_prov.SHARED})


def label_key(store: str, key: str) -> str:
    """Clé d'étiquette : le magasin et la clé exacte qui ont répondu."""
    return f"{store}\x00{key}"


class State:
    def __init__(self) -> None:
        self.world: Dict[str, Any] = {}
        self.beliefs: Dict[str, Belief] = {}
        self.memory: Dict[str, Dict[str, Any]] = {
            "SHORT_TERM": {}, "LONG_TERM": {}, "KNOWLEDGE": {}, "SHARED": {}
        }
        self.locals: Dict[str, Any] = {}
        #: Noms nus issus d'une charge utile de message ou d'événement —
        #: donnée de **provenance non fiable**. Espace séparé, et consulté en
        #: dernier : lié parmi les `locals` (prioritaires), un payload masquait
        #: silencieusement l'observation ou la croyance homonyme, et pouvait
        #: ainsi désactiver un `NEVER`. Voir `Runtime._bind_payload`.
        self.untrusted: Dict[str, Any] = {}
        self.tick: int = 0
        #: Provenance de chaque valeur écrite (v1.9), par magasin et clé
        #: exacte. Une valeur sans étiquette est `UNKNOWN` — non fiable.
        self.labels: Dict[str, Prov] = {}
        #: Attestations : empreinte d'une valeur → outils qui l'ont acceptée.
        self.attestations: Dict[str, Set[str]] = {}

    # ------------------------------------------------------------------ accès
    def get(self, path: str) -> Any:
        return self._resolve(path)[0]

    def get_labeled(self, path: str) -> Tuple[Any, Prov]:
        """La valeur **et** sa provenance, par la même résolution que `get`."""
        if type(self).get is not State.get:
            # Vue qui redéfinit la lecture (planificateur, scénario) : on lit
            # comme elle, et l'on ne prétend rien savoir de l'origine.
            value = self.get(path)
            return value, UNKNOWN_LABEL
        value, where = self._resolve(path)
        if value is UNDEFINED:
            return UNDEFINED, UNKNOWN_LABEL
        if isinstance(where, Prov):
            return value, where
        return value, self.label_at(where)

    def label_at(self, key: str) -> Prov:
        overlay = getattr(self, "_label_overlay", None)
        if overlay and key in overlay:
            return overlay[key]
        return (getattr(self, "labels", None) or {}).get(key, UNKNOWN_LABEL)

    def _resolve(self, path: str) -> Tuple[Any, Any]:
        """`(valeur, provenance)` — la provenance est une clé d'étiquette,
        ou directement une étiquette pour ce que le runtime calcule.

        L'ordre est **la** règle de précédence du langage : locales, mémoire
        partagée explicite, croyances, monde, mémoire, métadonnées de
        croyance, et en dernier l'espace non fiable. `get` et `get_labeled`
        passent tous deux par ici : la valeur lue et l'étiquette rendue ne
        peuvent pas venir de deux magasins différents.
        """
        hit, key = _dig_key(self.locals, path)
        if hit is not UNDEFINED:
            return hit, label_key("L", key)
        if path.startswith("SHARED."):
            # Lecture explicite du compartiment partagé (v1.6). Explicite, et
            # non par nom nu comme les autres compartiments : deux agents qui
            # partagent une clé `incidents` ne doivent pas la confondre avec
            # leur propre `LONG_TERM.incidents`. Le préfixe dit d'où vient la
            # donnée, ce qui est le minimum pour une décision qu'un autre
            # agent a rendue possible.
            # Écrite par un autre agent : sa provenance ne nous est pas
            # connue, elle vaut celle d'un message.
            return self._shared(path[len("SHARED."):]), _SHARED
        if path in self.beliefs:
            return self.beliefs[path].value, label_key("B", path)
        hit, key = _dig_key(self.world, path)
        if hit is not UNDEFINED:
            return hit, label_key("W", key)
        for bucket in ("SHORT_TERM", "LONG_TERM", "KNOWLEDGE"):
            hit, key = _dig_key(self.memory[bucket], path)
            if hit is not UNDEFINED:
                return hit, label_key(f"M:{bucket}", key)
        # métadonnées de croyance : x.confidence
        if path.endswith(".confidence"):
            base = path[: -len(".confidence")]
            if base in self.beliefs:
                return (self.beliefs[base].confidence,
                        _RUNTIME | self.label_at(label_key("B", base)))
        # …et **en dernier** seulement, les noms nus d'une charge utile. Un
        # message ne peut donc rien masquer : il ne comble que ce que rien
        # d'autre ne renseigne. Sa forme préfixée `payload.x` reste, elle,
        # disponible sans ambiguïté de provenance.
        # `getattr` et non `self.untrusted` : plusieurs vues d'état
        # (`_Overlay` du planificateur, portées de scénario) sous-classent
        # `State` sans passer par ce constructeur. Une résolution de chemin ne
        # doit pas dépendre de la façon dont la vue a été construite.
        hit, key = _dig_key(getattr(self, "untrusted", None) or {}, path)
        if hit is not UNDEFINED:
            return hit, label_key("U", key)
        return UNDEFINED, None

    def _shared(self, suffix: str) -> Any:
        """Résout `SHARED.<clé>[.count|.version|.last[.champ]]`.

        Le compartiment stocke des **suites d'enregistrements** : rendre la
        liste nue suffit à `!= none` mais pas à décider. Quatre formes, et pas
        une de plus — un langage de requête sur la mémoire partagée serait une
        autre fonctionnalité, et celle-ci doit rester lisible dans une garde :

            SHARED.k              la suite d'enregistrements ([] si vide)
            SHARED.k.count        combien
            SHARED.k.version      version de la clé (compteur d'écritures)
            SHARED.k.last[.champ] le dernier enregistrement, ou l'un de ses champs
        """
        bucket = self.memory.get("SHARED") or {}
        parts = suffix.split(".")
        key, rest = parts[0], parts[1:]
        if not rest:
            records = bucket.get(key)
            return records if isinstance(records, list) else UNDEFINED
        if rest == ["version"]:
            return bucket.get("__versions", {}).get(key, 0)

        records = bucket.get(key)
        if not isinstance(records, list):
            return UNDEFINED
        if rest == ["count"]:
            return len(records)
        if rest[0] == "last":
            if not records:
                # Aucun enregistrement : indéfini, et surtout pas `0` ni `[]`
                # qui se compareraient silencieusement.
                return UNDEFINED
            cursor: Any = records[-1]
            for part in rest[1:]:
                if isinstance(cursor, dict) and part in cursor:
                    cursor = cursor[part]
                else:
                    return UNDEFINED
            return cursor
        return UNDEFINED

    def _label(self, store: str, key: str, prov: Optional[Prov]) -> None:
        labels = getattr(self, "labels", None)
        if labels is not None:
            labels[label_key(store, key)] = prov if prov is not None \
                else UNKNOWN_LABEL

    def set_world(self, path: str, value: Any,
                  prov: Optional[Prov] = None) -> None:
        self.world[path] = value
        self._label("W", path, prov)

    def invalidate(self, path: str) -> None:
        """Efface ce qu'on croyait savoir d'un chemin : il redevient indéfini.

        Distinct de `set_world(path, None)` : `None` est une valeur d'état
        légitime, l'absence n'en est pas une. Après cet appel toute lecture du
        chemin rend `UNDEFINED`, donc toute garde qui le mentionne échoue
        fermé au lieu de statuer sur une mesure périmée.
        """
        self.world.pop(path, None)
        self.beliefs.pop(path, None)
        self.locals.pop(path, None)
        labels = getattr(self, "labels", None)
        if labels is not None:
            for store in ("W", "B", "L"):
                labels.pop(label_key(store, path), None)

    def set_belief(self, path: str, value: Any, confidence: float = 1.0,
                   source: str = "runtime", updated: Any = None,
                   prov: Optional[Prov] = None) -> None:
        self.beliefs[path] = Belief(value, confidence, source, updated)
        self._label("B", path, prov)

    def set_local(self, path: str, value: Any,
                  prov: Optional[Prov] = None) -> None:
        self.locals[path] = value
        self._label("L", path, prov)

    def set_untrusted(self, key: str, value: Any, prov: Prov) -> None:
        """Nom nu d'une donnée externe : consulté en dernier (§7.3)."""
        self.untrusted[key] = value
        self._label("U", key, prov)

    def label_memory(self, bucket: str, key: str, prov: Prov) -> None:
        self._label(f"M:{bucket}", key, prov)

    def assign(self, path: str, value: Any,
               prov: Optional[Prov] = None) -> None:
        """SET : écrit dans les croyances si la clé y existe, sinon en local."""
        if path in self.beliefs:
            self.beliefs[path].value = value
            self.beliefs[path].source = "assignment"
            self._label("B", path, prov)
        else:
            self.locals[path] = value
            self._label("L", path, prov)

    def attest(self, tool: str, value: Any) -> None:
        """`tool` a accepté `value` : un fait sur la valeur, pas une confiance."""
        registry = getattr(self, "attestations", None)
        if registry is not None:
            registry.setdefault(attest_key(value), set()).add(tool)

    def attested_by(self, value: Any) -> Set[str]:
        registry = getattr(self, "attestations", None) or {}
        return set(registry.get(attest_key(value), ()))

    def snapshot(self) -> Dict[str, Any]:
        return {
            "tick": self.tick,
            "world": dict(self.world),
            "beliefs": {k: (v.value, v.confidence) for k, v in self.beliefs.items()},
            "memory": {k: dict(v) for k, v in self.memory.items()},
            "locals": dict(self.locals),
        }


def _dig(store: Dict[str, Any], path: str) -> Any:
    return _dig_key(store, path)[0]


def _dig_key(store: Dict[str, Any], path: str) -> Tuple[Any, str]:
    """Recherche exacte puis descente dans les dictionnaires imbriqués.

    On préfère le préfixe plat le plus long, mais si sa descente échoue on
    **revient** à un préfixe plus court plutôt que d'abandonner : sinon la
    présence d'une clé plate `a.b` (qui ne contient pas `c`) masquerait une
    clé imbriquée `a → {b → {c}}` pourtant résoluble. Cet oubli du repli
    rendait `a.b.c` indéfini alors qu'une résolution existe — une résolution
    de chemin non déterministe vis-à-vis de la forme de stockage.
    """
    if path in store:
        return store[path], path
    parts = path.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        head = ".".join(parts[:cut])
        if head not in store:
            continue
        cursor = store[head]
        for part in parts[cut:]:
            if isinstance(cursor, dict) and part in cursor:
                cursor = cursor[part]
            else:
                cursor = UNDEFINED
                break
        if cursor is not UNDEFINED:
            # La feuille hérite de l'étiquette du composite qui la contient.
            return cursor, head
    return UNDEFINED, path


# ---------------------------------------------------------------- évaluation
EPISTEMIC_FUNCS = {"CONFIDENCE", "UNCERTAINTY", "P", "PROBABILITY", "POSTERIOR"}

PURE_FUNCS = {
    "len": lambda x: len(x) if hasattr(x, "__len__") else 0,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
}


class Evaluator:
    """Évalue une expression AGENT-L sur un état. Sans effet de bord.

    `strict_undefined` durcit **toute** comparaison portant sur une valeur
    absente, dans les deux sens. C'est le régime de `VERIFY` : une
    vérification affirme que le monde a atteint un état, et ne peut donc pas
    réussir par ignorance (SPEC §11). Une garde de plan, elle, demande « dois-
    je agir ? », et « pas encore fait » est une réponse légitime à partir d'un
    chemin jamais posé — d'où deux régimes plutôt qu'un.
    """

    def __init__(self, state: State, strict_undefined: bool = False):
        self.state = state
        self.notes: List[str] = []
        self.strict_undefined = strict_undefined

    def eval(self, node: Optional[Node]) -> Any:
        if node is None:
            return UNDEFINED
        if isinstance(node, Literal):
            return node.value
        if isinstance(node, ListExpr):
            return [self.eval(i) for i in node.items]
        if isinstance(node, PathExpr):
            return self._path(node)
        if isinstance(node, UnOp):
            return self._unop(node)
        if isinstance(node, BinOp):
            return self._binop(node)
        if isinstance(node, CallExpr):
            if node.name in EPISTEMIC_FUNCS:
                return self._epistemic(node)
            if node.name in PROVENANCE_FUNCS:
                return self._provenance(node)
            fn = PURE_FUNCS.get(node.name)
            if fn is None:
                raise EvalError(
                    f"appel non pur `{node.name}()` interdit dans une expression"
                )
            return fn(*[self.eval(a) for a in node.args])
        raise EvalError(f"nœud non évaluable : {type(node).__name__}")

    def _epistemic(self, node: CallExpr) -> Any:
        """Fonctions épistémiques : elles portent sur le *chemin*, pas sa valeur.

            CONFIDENCE(threat.status)   → confiance de la croyance
            UNCERTAINTY(threat.status)  → 1 − confiance
            P(credential_attack)        → postérieur d'une hypothèse
        """
        if not node.args or not isinstance(node.args[0], PathExpr):
            raise EvalError(f"{node.name}() attend un chemin, pas une valeur")
        path = node.args[0].dotted
        if node.name in ("P", "PROBABILITY", "POSTERIOR"):
            value = self.state.get(f"{path}.posterior")
            if value is UNDEFINED:
                self.notes.append(f"hypothèse inconnue : {path}")
                return 0.0
            return value
        belief = self.state.beliefs.get(path)
        if belief is None:
            self.notes.append(f"croyance inconnue : {path}")
            return 0.0 if node.name == "CONFIDENCE" else 1.0
        return belief.confidence if node.name == "CONFIDENCE" \
            else 1.0 - belief.confidence

    def _provenance(self, node: CallExpr) -> Any:
        """`ORIGIN`, `UNTRUSTED`, `TRUSTED`, `LLM_DERIVED`, `ATTESTED` (v1.9).

        Portent sur la valeur que désigne le **chemin**, telle qu'elle est
        lue — même précédence que partout ailleurs. `action` désigne l'action
        en cours de jugement : ses arguments et la décision qui l'a produite.
        Une valeur absente rend la fonction indéfinie, donc la garde
        indéterminée : le sens fermé est celui de chaque effet.
        """
        name = node.name
        if not node.args or not isinstance(node.args[0], PathExpr):
            raise EvalError(f"{name}() attend un chemin, pas une valeur")
        path = node.args[0].dotted
        if path == "action":
            if name == "ATTESTED":
                raise EvalError("ATTESTED() porte sur une valeur, pas sur "
                                "l'action entière")
            label = getattr(self.state, "action_label", None)
            if label is None:
                self.notes.append("action hors d'un jugement de politique")
                return UNDEFINED
            value = True
        else:
            value, label = self.state.get_labeled(path)
            if value is UNDEFINED:
                self.notes.append(f"provenance d'un chemin indéfini : {path}")
                return UNDEFINED
        if name == "ORIGIN":
            return [Symbol(src) for src in sorted(label.sources)]
        if name == "UNTRUSTED":
            return label.untrusted
        if name == "TRUSTED":
            return not label.untrusted
        if name == "LLM_DERIVED":
            return label.llm_derived
        # ATTESTED(x) / ATTESTED(x, validateur)
        validators = self.state.attested_by(value) \
            if hasattr(self.state, "attested_by") else set()
        if len(node.args) < 2:
            return bool(validators)
        who = node.args[1]
        if not (isinstance(who, PathExpr) and len(who.parts) == 1):
            raise EvalError("ATTESTED(x, outil) : le second argument nomme "
                            "un outil")
        return who.parts[0] in validators

    def label(self, node: Optional[Node]) -> Prov:
        """Étiquette de provenance d'une expression : l'union de ce qu'elle lit.

        Sur-approximation voulue : `a AND b` porte l'étiquette des deux côtés
        même quand le premier suffit à conclure. Une étiquette trop large
        refuse davantage ; trop étroite, elle blanchirait.
        """
        if node is None or isinstance(node, Literal):
            return _DECLARED
        if isinstance(node, PathExpr):
            value, label = self.state.get_labeled(node.dotted) \
                if hasattr(self.state, "get_labeled") \
                else (self.state.get(node.dotted), UNKNOWN_LABEL)
            if value is UNDEFINED:
                # Identifiant nu non résolu : une constante du programme.
                return _DECLARED if len(node.parts) == 1 else UNKNOWN_LABEL
            return label
        if isinstance(node, ListExpr):
            return _prov.join(_DECLARED, *[self.label(i) for i in node.items])
        if isinstance(node, UnOp):
            return self.label(node.operand)
        if isinstance(node, BinOp):
            return self.label(node.left) | self.label(node.right)
        if isinstance(node, CallExpr):
            if node.name in PROVENANCE_FUNCS:
                return _RUNTIME          # un fait sur l'étiquette, calculé ici
            if node.name in EPISTEMIC_FUNCS:
                return _prov.join(_RUNTIME, *[self.label(a) for a in node.args])
            return _prov.join(_DECLARED, *[self.label(a) for a in node.args])
        return UNKNOWN_LABEL

    def test(self, node: Optional[Node]) -> bool:
        if node is None:
            return True
        return truthy(self.eval(node))

    # ------------------------------------------------------------- internes
    def _path(self, node: PathExpr) -> Any:
        value = self.state.get(node.dotted)
        if value is UNDEFINED:
            if len(node.parts) == 1:
                # identifiant nu non résolu = constante symbolique
                return Symbol(node.parts[0])
            self.notes.append(f"chemin non résolu : {node.dotted}")
        return value

    def _unop(self, node: UnOp) -> Any:
        val = self.eval(node.operand)
        if node.op == "NOT":
            return not truthy(val)
        if node.op == "-":
            return -_num(val, node)
        raise EvalError(f"opérateur unaire inconnu {node.op}")

    def _binop(self, node: BinOp) -> Any:
        op = node.op
        if op == "AND":
            return truthy(self.eval(node.left)) and truthy(self.eval(node.right))
        if op == "OR":
            return truthy(self.eval(node.left)) or truthy(self.eval(node.right))

        left = self.eval(node.left)
        right = self.eval(node.right)

        if op == "IN":
            try:
                return left in right
            except TypeError:
                return False
        if op in ("==", "!="):
            # Deux absences ne se comparent pas : ni égales, ni différentes.
            # Rendre `!=` vrai par simple complément de `==` ferait déclencher
            # un plan sur l'ignorance des *deux* côtés — le défaut corrigé,
            # retourné. Un seul côté absent reste comparable : voir `_eq`.
            if left is UNDEFINED and right is UNDEFINED:
                self.notes.append(
                    f"comparaison de deux valeurs indéfinies ligne {node.line}")
                return False
            if self.strict_undefined and (left is UNDEFINED
                                          or right is UNDEFINED):
                self.notes.append(
                    f"comparaison sur valeur indéfinie ligne {node.line}")
                return False
            return _eq(left, right) if op == "==" else not _eq(left, right)
        if op in (">", ">=", "<", "<="):
            return self._compare(op, left, right, node)
        if op in ("+", "-", "*", "/"):
            a, b = _num(left, node), _num(right, node)
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            return a / b if b else float("inf")
        raise EvalError(f"opérateur inconnu {op}")

    def _compare(self, op: str, left: Any, right: Any, node: BinOp) -> bool:
        lo, ro = ordinal(left), ordinal(right)
        if lo is not None and ro is not None:
            a, b = lo, ro
        elif left is UNDEFINED or right is UNDEFINED:
            self.notes.append(f"comparaison sur valeur indéfinie ligne {node.line}")
            return False
        else:
            try:
                a, b = float(left), float(right)
            except (TypeError, ValueError):
                self.notes.append(
                    f"comparaison non ordonnable {left!r} {op} {right!r}"
                )
                return False
        return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[op]


#: Les deux vocabulaires par lesquels un `.agent` peut nommer un booléen.
#: `yes`/`no` est l'idiome dominant du langage (`read_ok = yes`) ; `true`/
#: `false` est celui qu'écrit spontanément quiconque lit une valeur booléenne
#: dans une trace. Les deux doivent mordre, sans quoi la garde correcte
#: dépend de la culture de l'auteur.
_TRUE_SYMBOLS = frozenset({"true", "yes"})
_FALSE_SYMBOLS = frozenset({"false", "no"})


def _bool_symbol(a: Any, b: Any) -> Optional[bool]:
    """Pont entre un `bool` du runtime et le symbole qui le nomme.

    Rend `None` quand la paire n'est pas (booléen, symbole) — l'appelant
    reprend alors sa comparaison habituelle. `isinstance(True, int)` étant
    vrai en Python, le test de type doit rester strict : `1 == true` ne doit
    pas devenir vrai au passage.
    """
    for value, other in ((a, b), (b, a)):
        if value is True or value is False:
            if not isinstance(other, Symbol):
                continue
            name = str(other).lower()
            if name in _TRUE_SYMBOLS:
                return value is True
            if name in _FALSE_SYMBOLS:
                return value is False
            # Symbole hors vocabulaire booléen : un fait booléen ne peut pas
            # y être égal. `false == pending` est faux, pas indéterminé.
            return False
    return None


def _eq(a: Any, b: Any) -> bool:
    """Égalité de l'évaluateur bivalent.

    **Deux chemins absents ne sont pas égaux.** Jusqu'en v1.8 le repli était
    `a is b`, vrai sur le singleton `UNDEFINED` : `PLAN p WHEN missing.a ==
    missing.b` se déclenchait, et sous `DEFAULT ALLOW` cela conduisait à une
    action réelle. Deux ignorances ne se confirment pas l'une l'autre — une
    garde ne peut pas réussir par ignorance.

    **Une absence face à une valeur connue reste comparable**, et vaut
    « différent ». C'est l'idiome porteur du langage : `request.sent != yes`,
    `folder != unknown`, `service.status != healthy` disent tous « pas
    encore » ou « pas dans l'état attendu », et un chemin jamais posé est
    précisément cela. Étendre « indéfini → faux » aux deux sens de la
    comparaison rendrait ces gardes définitivement fausses : les plans
    d'amorçage ne se déclencheraient plus jamais, et un agent resterait
    inerte au lieu de commencer son travail. Le sens sûr n'est pas ici de
    tout refuser — c'est de ne pas conclure d'une ignorance *bilatérale*.

    Les gardes de **politique** ne passent pas par ici : `trivalent.evaluate`
    rend `UNKNOWN` dès qu'un opérande est absent, et chaque effet décide de ce
    qu'il fait d'une indétermination — un `NEVER` s'applique, un `ALLOW` ne
    compte pas. La sûreté des interdits ne dépend donc pas de cette fonction.

    **Un booléen du runtime vaut le symbole qui le nomme.** Les faits que le
    moteur pose lui-même — `sensors.<chemin>.available`, `tools.<nom>.available`,
    `reason.degraded`, `last_action.blocked` — sont des `bool` Python, tandis
    qu'un `.agent` ne sait écrire que des symboles. Sans le pont ci-dessous,
    `str(False)` valait `"False"` et `tools.x.available == false` était
    **faux alors que l'outil était bien indisponible** : l'interdit ne
    s'appliquait pas, `check` ne disait rien, et l'auteur croyait la panne
    gardée. C'est le pire mode de défaillance du langage — une garantie qui
    a disparu sans un seul signal — et il touchait `sensors.*.available`
    depuis son introduction.
    """
    if a is UNDEFINED and b is UNDEFINED:
        return False
    if isinstance(a, Symbol) or isinstance(b, Symbol):
        # `str(UNDEFINED)` vaut `"UNDEFINED"` : une absence ne peut pas
        # coïncider avec un symbole que le programme aurait nommé ainsi.
        if a is UNDEFINED or b is UNDEFINED:
            return False
        bridged = _bool_symbol(a, b)
        if bridged is not None:
            return bridged
        return str(a) == str(b)
    if a is UNDEFINED or b is UNDEFINED:
        return False
    try:
        return bool(a == b)
    except Exception:  # pragma: no cover
        return False


def _num(value: Any, node: Node) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise EvalError(f"valeur non numérique {value!r} ligne {node.line}")
