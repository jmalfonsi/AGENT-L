"""Propriétés du noyau et de l'analyse, sur entrées tirées au hasard (v1.9).

Pas de dépendance : un générateur à graine fixe, et un nombre de tirages
réglable (`AGENTL_PROPERTY_RUNS`, 200 par défaut ; la CI nocturne en tire
beaucoup plus). Un échec affiche la graine et l'entrée qui l'a produit — il
se rejoue à l'identique.

Ce que ces propriétés affirment, et contre quoi :

P1  Le moteur de politiques calcule exactement la sémantique de la SPEC
    (§7, §7.1) — confronté à une implémentation de référence indépendante,
    écrite depuis le texte et non depuis le code.
P2  **Moins d'information ne donne jamais plus de permission.** Rendre un
    chemin indéfini ne fait jamais passer un verdict vers moins sévère. C'est
    la définition exécutable de « fail-closed ».
P3  Ajouter un NEVER ou un DENY ne rend jamais un verdict moins sévère.
P4  La logique des gardes obéit aux lois de Kleene (De Morgan, double
    négation, commutativité, absorption par le faux et le vrai).
P5  Le solveur est **sûr d'un côté** : quand il déclare une conjonction
    insatisfiable, aucune affectation ne la satisfait à l'exécution — vérifié
    par énumération exhaustive sur un domaine fini qui contient toutes les
    constantes de la formule.
P6  `entails` n'affirme une implication que si elle tient sur tous les
    modèles de l'énumération.
P7  Parseur et analyseur ne lèvent jamais autre chose qu'une erreur du
    langage sur une entrée mutilée.
P8  Sur une suite aléatoire d'opérations — autoriser, exécuter, rejouer un
    permis, le copier, le révoquer, en forger un — l'hôte n'agit qu'une fois
    par permis légitime, et jamais autrement.
P9  L'empreinte d'une action ne dépend pas de l'ordre des clés, et distingue
    un `Symbol` de la chaîne homonyme.
"""
from __future__ import annotations

import copy
import itertools
import os
import random
from pathlib import Path

import pytest

from agentl import Host, Symbol, parse_source
from agentl.analyzer import Analyzer
from agentl.core import UNDEFINED, AgentLError, ordinal
from agentl.kernel import (ActionRequest, ExecutionPermit, Kernel,
                           PermitError, action_digest)
from agentl.nodes import BinOp, Literal, PathExpr, PolicyRule, UnOp
from agentl.policy import (ALLOWED, APPROVAL_REQUIRED, DENIED, PolicyEngine)
from agentl.solver import entails, satisfiable
from agentl.state import Evaluator, State
from agentl.trivalent import UNKNOWN, evaluate as tri

RUNS = int(os.environ.get("AGENTL_PROPERTY_RUNS", "200"))
ROOT = Path(__file__).resolve().parents[1]

PATHS = ["a.x", "b.y", "c.z"]
NUMBERS = [0, 1, 2.5, 3, 7]
SYMBOLS = [Symbol("LOW"), Symbol("HIGH"), Symbol("CRITICAL"), Symbol("open"),
           Symbol("closed")]
CONSTANTS = NUMBERS + SYMBOLS
OPS = ["==", "!=", ">", ">=", "<", "<="]
SEVERITY = {ALLOWED: 0, APPROVAL_REQUIRED: 1, DENIED: 2}


# =============================================================== générateurs
def gen_atom(rng):
    path = PathExpr(rng.choice(PATHS).split("."))
    const = rng.choice(CONSTANTS)
    node = Literal(const) if not isinstance(const, Symbol) \
        else PathExpr([const.name])               # identifiant nu = constante
    op = rng.choice(OPS)
    return BinOp(op, path, node) if rng.random() < 0.8 else BinOp(op, node, path)


def gen_guard(rng, depth=0):
    roll = rng.random()
    if depth >= 3 or roll < 0.4:
        return gen_atom(rng)
    if roll < 0.55:
        return UnOp("NOT", gen_guard(rng, depth + 1))
    return BinOp(rng.choice(["AND", "OR"]), gen_guard(rng, depth + 1),
                 gen_guard(rng, depth + 1))


def gen_state(rng):
    """Chaque chemin est défini (valeur tirée) ou absent."""
    return {p: rng.choice(CONSTANTS) for p in PATHS if rng.random() < 0.75}


def state_of(values):
    st = State()
    for path, value in values.items():
        st.set_world(path, value)
    return st


def gen_rules(rng):
    rules = []
    for _ in range(rng.randint(0, 5)):
        effect = rng.choice(["NEVER", "DENY", "ALLOW", "REQUIRE_APPROVAL"])
        target = rng.choice(["wipe", "wipe", "*", "other"])
        guard = gen_guard(rng) if rng.random() < 0.85 else None
        rules.append(PolicyRule(effect, target, guard, line=len(rules) + 1))
    return rules


class _Agent:
    def __init__(self, rules, default):
        self.name, self.policies, self.policy_default = "p", rules, default

    def tool(self, name):
        return None


# ====================================================== référence (SPEC §7)
def _rank(value):
    """(échelle, rang) : ordinal et numérique sont deux échelles disjointes."""
    rank = ordinal(value)
    if rank is not None:
        return ("ord", float(rank))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return ("num", float(value))
    return None


def ref_value(node, values):
    if isinstance(node, Literal):
        return node.value
    name = node.dotted
    if name in values:
        return values[name]
    if len(node.parts) == 1:
        return Symbol(node.parts[0])
    return UNDEFINED


def ref_truth(node, values):
    """Kleene fort, écrit depuis le texte de la SPEC §7.1."""
    if node is None:
        return True
    if isinstance(node, UnOp):
        inner = ref_truth(node.operand, values)
        return UNKNOWN if inner is UNKNOWN else (not inner)
    if node.op in ("AND", "OR"):
        l, r = ref_truth(node.left, values), ref_truth(node.right, values)
        if node.op == "AND":
            if l is False or r is False:
                return False
            return UNKNOWN if UNKNOWN in (l, r) else True
        if l is True or r is True:
            return True
        return UNKNOWN if UNKNOWN in (l, r) else False
    left, right = ref_value(node.left, values), ref_value(node.right, values)
    if left is UNDEFINED or right is UNDEFINED:
        return UNKNOWN
    if node.op in ("==", "!="):
        same = (str(left) == str(right)) if (isinstance(left, Symbol)
                                             or isinstance(right, Symbol)) \
            else left == right
        return same if node.op == "==" else not same
    lr, rr = _rank(left), _rank(right)
    if lr is None or rr is None or lr[0] != rr[0]:
        # Non ordonnable : ni vraie ni fausse dans une garde (SPEC §7.1) —
        # une confusion de type ne doit pas désarmer un NEVER.
        return UNKNOWN
    lr, rr = lr[1], rr[1]
    return {">": lr > rr, ">=": lr >= rr, "<": lr < rr, "<=": lr <= rr}[node.op]


def ref_verdict(rules, default, tool, values):
    def applies(rule):
        if rule.target not in ("*", tool):
            return False
        truth = ref_truth(rule.guard, values)
        # NEVER, DENY et APPROVAL s'appliquent sous l'indétermination.
        return True if truth is UNKNOWN else truth
    if any(r.effect == "NEVER" and applies(r) for r in rules):
        return DENIED
    if any(r.effect == "DENY" and applies(r) for r in rules):
        return DENIED
    allows = [r for r in rules if r.effect == "ALLOW"
              and r.target in ("*", tool)]
    if allows:
        if not any(ref_truth(r.guard, values) is True for r in allows):
            return DENIED
    elif default == "DENY":
        return DENIED
    if any(r.effect == "REQUIRE_APPROVAL" and applies(r) for r in rules):
        return APPROVAL_REQUIRED
    return ALLOWED


def engine_verdict(rules, default, values):
    engine = PolicyEngine(_Agent(rules, default))
    return engine.check(ActionRequest("wipe", {}), state_of(values)).verdict


# ======================================================================= P1
def test_p1_policy_engine_matches_the_reference_semantics():
    rng = random.Random(0x19A1)
    for i in range(RUNS * 5):
        rules, default = gen_rules(rng), rng.choice(["ALLOW", "DENY"])
        values = gen_state(rng)
        expected = ref_verdict(rules, default, "wipe", values)
        got = engine_verdict(rules, default, values)
        assert got == expected, (i, rules, default, values)


# ======================================================================= P2
def test_p2_less_information_never_grants_more_permission():
    rng = random.Random(0x19A2)
    for i in range(RUNS * 5):
        rules, default = gen_rules(rng), rng.choice(["ALLOW", "DENY"])
        values = gen_state(rng)
        if not values:
            continue
        before = engine_verdict(rules, default, values)
        forgotten = dict(values)
        del forgotten[rng.choice(sorted(forgotten))]
        after = engine_verdict(rules, default, forgotten)
        assert SEVERITY[after] >= SEVERITY[before], \
            (i, rules, default, values, forgotten, before, after)


# ======================================================================= P3
def test_p3_adding_a_prohibition_never_relaxes_a_verdict():
    rng = random.Random(0x19A3)
    for i in range(RUNS * 5):
        rules, default = gen_rules(rng), rng.choice(["ALLOW", "DENY"])
        values = gen_state(rng)
        before = engine_verdict(rules, default, values)
        extra = PolicyRule(rng.choice(["NEVER", "DENY"]),
                           rng.choice(["wipe", "*", "other"]), gen_guard(rng),
                           line=99)
        after = engine_verdict(rules + [extra], default, values)
        assert SEVERITY[after] >= SEVERITY[before], (i, rules, extra, values)


# ======================================================================= P4
def test_p4_guards_obey_kleene_laws():
    rng = random.Random(0x19A4)
    for i in range(RUNS * 3):
        a, b = gen_guard(rng), gen_guard(rng)
        ev = Evaluator(state_of(gen_state(rng)))
        va, vb = tri(ev, a), tri(ev, b)
        laws = [
            (tri(ev, UnOp("NOT", BinOp("AND", a, b))),
             tri(ev, BinOp("OR", UnOp("NOT", a), UnOp("NOT", b)))),
            (tri(ev, UnOp("NOT", BinOp("OR", a, b))),
             tri(ev, BinOp("AND", UnOp("NOT", a), UnOp("NOT", b)))),
            (tri(ev, UnOp("NOT", UnOp("NOT", a))), va),
            (tri(ev, BinOp("AND", a, b)), tri(ev, BinOp("AND", b, a))),
            (tri(ev, BinOp("OR", a, b)), tri(ev, BinOp("OR", b, a))),
            (tri(ev, BinOp("AND", a, Literal(False))), False),
            (tri(ev, BinOp("OR", a, Literal(True))), True),
            (tri(ev, BinOp("AND", a, Literal(True))), va),
        ]
        for n, (left, right) in enumerate(laws):
            assert left is right or left == right, (i, n, a, b, va, vb)


# ================================================================== P5, P6
DOMAIN = sorted({*NUMBERS, 1.5, 5, 10, -1}) + SYMBOLS + [Symbol("other")]
MODELS = [dict(zip(PATHS, combo))
          for combo in itertools.product(DOMAIN, repeat=len(PATHS))]


def _holds(guard, values):
    return Evaluator(state_of(values)).test(guard)


def test_p5_the_solver_never_calls_a_satisfiable_formula_impossible():
    rng = random.Random(0x19A5)
    checked = 0
    for i in range(RUNS):
        guards = [gen_guard(rng) for _ in range(rng.randint(1, 3))]
        if satisfiable(*guards):
            continue
        checked += 1
        for model in MODELS:
            assert not all(_holds(g, model) for g in guards), \
                (i, guards, model, "le solveur a déclaré impossible une "
                                   "conjonction qui a un modèle")
    assert checked > 0          # la propriété a bien été exercée


def test_p6_entailment_is_only_claimed_when_it_holds():
    rng = random.Random(0x19A6)
    claimed = 0
    for i in range(RUNS):
        premise, conclusion = gen_guard(rng), gen_guard(rng)
        if not entails([premise], conclusion):
            continue
        claimed += 1
        for model in MODELS:
            if _holds(premise, model):
                assert _holds(conclusion, model), (i, premise, conclusion, model)
    assert claimed > 0


# ======================================================================= P7
def _mutate(src, rng):
    n = len(src)
    i = rng.randrange(n)
    kind = rng.randrange(5)
    if kind == 0:
        return src[:i] + src[min(n, i + rng.randint(1, 30)):]
    if kind == 1:
        return src[:i] + rng.choice(["{", "}", "(", ")", "WHEN", "AND", "==",
                                     "\"", "NEVER", ",", ":", "."]) + src[i:]
    if kind == 2:
        return src[:i]
    if kind == 3:
        j = min(n, i + rng.randint(1, 60))
        return src[:j] + src[i:j] + src[j:]
    lines = src.split("\n")
    del lines[rng.randrange(len(lines))]
    return "\n".join(lines)


def test_p7_the_parser_and_analyzer_fail_only_with_language_errors():
    rng = random.Random(0x19A7)
    sources = [p.read_text(encoding="utf-8")
               for p in sorted((ROOT / "examples").glob("*.agent"))]
    for i in range(RUNS * 3):
        src = rng.choice(sources)
        for _ in range(rng.randint(1, 3)):
            if src:
                src = _mutate(src, rng)
        try:
            program = parse_source(src)
            for agent in program.agents:
                Analyzer(agent).run()
        except AgentLError:
            pass
        except Exception as exc:                  # noqa: BLE001
            pytest.fail(f"tirage {i} : {type(exc).__name__}: {exc}\n---\n"
                        f"{src[:3000]}")


# ======================================================================= P8
def test_p8_the_host_acts_once_per_legitimate_permit_and_never_otherwise():
    rng = random.Random(0x19A8)
    for run in range(RUNS):
        effects, host = [], Host()
        host.tools["wipe"] = lambda **kw: effects.append(kw) or {}
        kernel = Kernel(_Agent([], "ALLOW"))
        other = Kernel(_Agent([], "ALLOW"))
        legitimate, pool = 0, []
        for _ in range(rng.randint(1, 12)):
            op = rng.choice(["grant", "execute", "replay", "copy", "void",
                             "foreign", "forge", "direct"])
            try:
                if op == "grant":
                    auth = kernel.authorize(
                        ActionRequest("wipe", {"n": rng.randint(0, 9)}),
                        State(), approve=lambda _: False)
                    pool.append(auth.permit)
                elif op == "execute" and pool:
                    permit = pool.pop()
                    was_live = permit.status == "issued"
                    kernel.execute(permit, host)
                    assert was_live, "un permis non vivant a été exécuté"
                    legitimate += 1
                elif op == "replay" and pool:
                    kernel.execute(pool[-1], host)
                    legitimate += 1
                elif op == "copy" and pool:
                    pool.append(copy.deepcopy(pool[-1]))
                elif op == "void" and pool:
                    kernel.void(pool[-1])
                elif op == "foreign":
                    auth = other.authorize(ActionRequest("wipe", {}), State(),
                                           approve=lambda _: False)
                    kernel.execute(auth.permit, host)
                    pytest.fail("un permis d'un autre noyau a été exécuté")
                elif op == "forge":
                    ExecutionPermit(object(), kind="invoke", target="wipe",
                                    args={}, action_id="f",
                                    idempotency_key="f", policy_digest="",
                                    approved=False, nonce="f", issuer="p")
                    pytest.fail("un permis a été forgé")
                elif op == "direct":
                    host.invoke("wipe", {})
                    pytest.fail("l'hôte a agi sans permis")
            except PermitError:
                pass
        assert len(effects) == legitimate


# ======================================================================= P9
def test_p9_action_digests_are_canonical():
    rng = random.Random(0x19A9)
    for _ in range(RUNS):
        keys = rng.sample(["a", "b", "c", "d", "e"], rng.randint(1, 5))
        args = {k: rng.choice([1, 2.5, "s", Symbol("s"), [1, {"x": 2}],
                               {"z": 1, "y": 2}]) for k in keys}
        shuffled = dict(rng.sample(list(args.items()), len(args)))
        assert action_digest("invoke", "t", args) == \
            action_digest("invoke", "t", shuffled)
    assert action_digest("invoke", "t", {"x": "s"}) != \
        action_digest("invoke", "t", {"x": Symbol("s")})
    assert action_digest("invoke", "t", {"x": float("nan")}) == \
        action_digest("invoke", "t", {"x": float("nan")})


# ====================================================================== P2b
def gen_epistemic_guard(rng, depth=0):
    """Gardes sur la confiance des croyances et le postérieur des hypothèses."""
    if depth >= 2 or rng.random() < 0.5:
        fn = rng.choice(["CONFIDENCE", "UNCERTAINTY", "P"])
        from agentl.nodes import CallExpr
        target = PathExpr(rng.choice(["a.x", "b.y"]).split(".")) \
            if fn != "P" else PathExpr([rng.choice(["h1", "h2"])])
        return BinOp(rng.choice([">", ">=", "<", "<="]), CallExpr(fn, [target]),
                     Literal(rng.choice([0.2, 0.5, 0.8])))
    if rng.random() < 0.3:
        return UnOp("NOT", gen_epistemic_guard(rng, depth + 1))
    return BinOp(rng.choice(["AND", "OR"]), gen_epistemic_guard(rng, depth + 1),
                 gen_epistemic_guard(rng, depth + 1))


def epistemic_state(facts):
    st = State()
    for key, value in facts.items():
        if key in ("h1", "h2"):
            st.set_world(f"{key}.posterior", value)
        else:
            st.set_belief(key, Symbol("v"), value)
    return st


def test_p2b_an_unknown_belief_or_hypothesis_never_grants_permission():
    """`CONFIDENCE(x)` d'une croyance absente, `P(h)` d'une hypothèse non
    publiée : dans une garde de politique, ignorer n'est pas savoir zéro."""
    rng = random.Random(0x19B2)
    for i in range(RUNS * 5):
        rules = [PolicyRule(rng.choice(["NEVER", "DENY", "ALLOW",
                                        "REQUIRE_APPROVAL"]),
                            "wipe", gen_epistemic_guard(rng), line=n + 1)
                 for n in range(rng.randint(1, 4))]
        default = rng.choice(["ALLOW", "DENY"])
        facts = {k: rng.choice([0.1, 0.3, 0.6, 0.9, 0.99])
                 for k in ("a.x", "b.y", "h1", "h2") if rng.random() < 0.8}
        if not facts:
            continue
        engine = PolicyEngine(_Agent(rules, default))
        before = engine.check(ActionRequest("wipe", {}),
                              epistemic_state(facts)).verdict
        forgotten = dict(facts)
        del forgotten[rng.choice(sorted(forgotten))]
        after = engine.check(ActionRequest("wipe", {}),
                             epistemic_state(forgotten)).verdict
        assert SEVERITY[after] >= SEVERITY[before], \
            (i, rules, default, facts, forgotten, before, after)
