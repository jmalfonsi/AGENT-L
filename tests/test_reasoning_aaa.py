"""Tests AAA de la COUCHE RAISONNEMENT (croyances / inférence / politiques).

Cible exclusive : `agentl/bayes.py`, `agentl/state.py`, `agentl/policy.py`.

Deux exigences de niveau produit gouvernent ces tests :

  * **Sûreté** — la couche de politiques ne doit jamais échouer *ouverte* ni
    laisser une interdiction `NEVER` passer, quelles que soient les erreurs
    d'évaluation d'une garde.
  * **Déterminisme** — l'inférence bayésienne et la résolution de chemins
    doivent produire le même résultat quel que soit l'ordre de déclaration ou
    la forme de stockage, sans quoi l'audit et le rejeu perdent leur sens.

Chaque test ci-dessous échouait (exception ou mauvaise valeur) avant les
corrections apportées à ces trois modules.
"""
from __future__ import annotations

import itertools
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentl import bayes                                        # noqa: E402
from agentl.core import Belief                                  # noqa: E402
from agentl.nodes import (                                      # noqa: E402
    Agent, BinOp, EvidenceItem, Hypothesis, Literal, PathExpr, PolicyRule,
)
from agentl.policy import (                                     # noqa: E402
    ALLOWED, APPROVAL_REQUIRED, DENIED, ActionRequest, PolicyEngine,
)
from agentl.state import State, _dig                            # noqa: E402


# --------------------------------------------------------------------------
# Fabriques d'expressions
# --------------------------------------------------------------------------
def _p(*parts: str) -> PathExpr:
    return PathExpr(list(parts))


def _lit(value):
    return Literal(value)


def _gt(name: str, threshold=0) -> BinOp:
    return BinOp(">", _p(name), _lit(threshold))


def _raising_guard() -> BinOp:
    """Garde arithmétiquement indécidable : `action.args.x + 1 > 5`.

    Avec `x` non numérique, l'évaluateur lève `EvalError` — le cas qui faisait
    autrefois planter `PolicyEngine.check`.
    """
    return BinOp(">", BinOp("+", _p("action", "args", "x"), _lit(1)), _lit(5))


def _engine(rules, default="ALLOW") -> PolicyEngine:
    agent = Agent("t", policies=list(rules), policy_default=default)
    return PolicyEngine(agent)


def _ev(name, likelihood, given_not, group=""):
    return EvidenceItem(_gt(name), likelihood, given_not, group=group)


# ==========================================================================
# policy.py — la couche de sûreté échoue fermé, jamais ouvert
# ==========================================================================
def test_never_guard_that_raises_denies_instead_of_crashing():
    """Une garde `NEVER` indécidable interdit l'action ; elle ne propage pas."""
    eng = _engine([PolicyRule("NEVER", "danger", _raising_guard())],
                  default="ALLOW")
    req = ActionRequest("danger", {"x": "not-a-number"}, "HIGH")
    decision = eng.check(req, State())
    assert decision.verdict == DENIED


def test_deny_guard_that_raises_denies():
    eng = _engine([PolicyRule("DENY", "danger", _raising_guard())],
                  default="ALLOW")
    req = ActionRequest("danger", {"x": "not-a-number"}, "HIGH")
    assert eng.check(req, State()).verdict == DENIED


def test_allow_guard_that_raises_does_not_grant():
    """Une autorisation dont la garde est indécidable ne compte pas :
    en deny-by-default, l'action reste refusée."""
    eng = _engine([PolicyRule("ALLOW", "danger", _raising_guard())],
                  default="DENY")
    req = ActionRequest("danger", {"x": "not-a-number"}, "HIGH")
    assert eng.check(req, State()).verdict == DENIED


def test_require_approval_guard_that_raises_routes_to_human():
    """Une garde d'approbation indécidable échoue fermé → APPROVAL_REQUIRED."""
    eng = _engine([PolicyRule("REQUIRE_APPROVAL", "danger", _raising_guard())],
                  default="ALLOW")
    req = ActionRequest("danger", {"x": "not-a-number"}, "HIGH")
    assert eng.check(req, State()).verdict == APPROVAL_REQUIRED


def test_never_beats_allow_regardless_of_order_and_confidence():
    """`NEVER` est irrévocable : ni l'ordre des règles ni une confiance de 1
    ne le lèvent."""
    rules = [PolicyRule("ALLOW", "wipe", None), PolicyRule("NEVER", "wipe", None)]
    for ordering in itertools.permutations(rules):
        eng = _engine(ordering, default="ALLOW")
        req = ActionRequest("wipe", {}, "LOW", confidence=1.0)
        assert eng.check(req, State()).verdict == DENIED


def test_good_guard_still_allows():
    """Non-régression : une garde saine et vraie autorise toujours."""
    eng = _engine([PolicyRule("ALLOW", "run", _gt("ready"))], default="DENY")
    state = State()
    state.world["ready"] = 1
    assert eng.check(ActionRequest("run", {}), state).verdict == ALLOWED


# ==========================================================================
# state.py — résolution de chemins ℓ ≺ b ≺ x ≺ m, avec repli de préfixe
# ==========================================================================
def test_dig_backtracks_to_shorter_prefix():
    """Une clé plate `a.b` (dict sans `c`) ne doit pas masquer `a → {b:{c}}`."""
    store = {"a.b": {"z": 1}, "a": {"b": {"c": 42}}}
    assert _dig(store, "a.b.c") == 42


def test_get_resolves_nested_despite_conflicting_flat_key():
    state = State()
    state.world["svc"] = {"region": {"code": "eu"}}
    state.world["svc.region"] = "opaque"        # clé plate concurrente
    assert state.get("svc.region.code") == "eu"


def test_path_resolution_order_local_belief_world_memory():
    """L'ordre normatif ℓ ≺ b ≺ x ≺ m est respecté, palier par palier."""
    state = State()
    state.memory["LONG_TERM"]["k"] = "memory"
    state.world["k"] = "world"
    state.beliefs["k"] = Belief("belief")
    state.locals["k"] = "local"
    assert state.get("k") == "local"
    del state.locals["k"]
    assert state.get("k") == "belief"
    del state.beliefs["k"]
    assert state.get("k") == "world"
    del state.world["k"]
    assert state.get("k") == "memory"


def test_zero_confidence_belief_is_readable():
    state = State()
    state.beliefs["threat.status"] = Belief("bad", 0.0, "sensor")
    assert state.get("threat.status.confidence") == 0.0


# ==========================================================================
# bayes.py — inférence déterministe, calibrée et bornée
# ==========================================================================
def test_posterior_is_independent_of_evidence_order():
    """Rejouabilité : le postérieur ne dépend pas de l'ordre de déclaration."""
    evs = [_ev("a", 0.92, 0.06), _ev("b", 0.85, 0.20), _ev("c", 0.75, 0.08)]
    state = State()
    state.world = {"a": 10, "b": 10, "c": 10}
    posteriors = set()
    for order in itertools.permutations(range(3)):
        hyp = Hypothesis("h", prior=0.05, threshold=0.9,
                         evidence=[evs[i] for i in order])
        posteriors.add(round(bayes.infer(hyp, state).posterior, 12))
    assert len(posteriors) == 1


def test_group_absorption_is_order_independent_under_ties():
    """Deux évidences corrélées de force identique : l'absorption reste
    déterministe quel que soit l'ordre."""
    evs = [_ev("a", 0.9, 0.1, "g"), _ev("b", 0.9, 0.1, "g")]
    state = State()
    state.world = {"a": 10, "b": 10}
    results = set()
    for order in itertools.permutations(range(2)):
        hyp = Hypothesis("h", prior=0.1, threshold=0.9,
                         evidence=[evs[i] for i in order])
        results.add(round(bayes.infer(hyp, state).posterior, 12))
    assert len(results) == 1


def test_posterior_stays_within_reachable_range():
    """La borne statique du vérificateur encadre réellement le runtime."""
    hyp = Hypothesis("h", prior=0.05, threshold=0.9, evidence=[
        _ev("a", 0.92, 0.06), _ev("b", 0.85, 0.20), _ev("c", 0.75, 0.08),
    ])
    lo, hi = bayes.reachable_range(hyp)
    for combo in itertools.product([True, False], repeat=3):
        state = State()
        state.world = {name: (10 if flag else -1)
                       for name, flag in zip("abc", combo)}
        post = bayes.infer(hyp, state).posterior
        assert lo - 1e-9 <= post <= hi + 1e-9


def test_max_evidence_caps_symmetrically_and_is_finite():
    hyp = Hypothesis("h", prior=0.1, threshold=0.9, max_evidence=2.0, evidence=[
        _ev("a", 0.99, 0.01), _ev("b", 0.99, 0.01), _ev("c", 0.99, 0.01),
    ])
    state = State()
    state.world = {"a": 10, "b": 10, "c": 10}
    inf = bayes.infer(hyp, state)
    assert inf.capped is True
    assert math.isclose(inf.shift_bits, 2.0, abs_tol=1e-9)
    assert math.isfinite(inf.posterior)


def test_degenerate_priors_stay_in_open_unit_interval():
    """Un a priori de 0 ou 1 ne fige pas le postérieur hors de (0, 1) et
    reste fini malgré les cotes infinies."""
    for prior in (0.0, 1.0):
        hyp = Hypothesis("h", prior=prior, threshold=0.9,
                         evidence=[_ev("a", 0.9, 0.1)])
        state = State()
        state.world = {"a": 10}
        post = bayes.infer(hyp, state).posterior
        assert math.isfinite(post)
        assert 0.0 <= post <= 1.0


def test_extreme_likelihoods_do_not_overflow():
    hyp = Hypothesis("h", prior=0.5, threshold=0.9,
                     evidence=[_ev("a", 1.0, 0.0)])
    state = State()
    state.world = {"a": 10}
    post = bayes.infer(hyp, state).posterior
    assert math.isfinite(post) and 0.0 < post < 1.0


def test_indeterminate_evidence_leaves_odds_unchanged():
    """`indéterminé ≠ faux` : une évidence non observée laisse le prior tel quel."""
    hyp = Hypothesis("h", prior=0.3, threshold=0.9,
                     evidence=[_ev("missing_path", 0.9, 0.1)])
    inf = bayes.infer(hyp, State())      # monde vide → évidence indéterminée
    assert math.isclose(inf.posterior, 0.3, abs_tol=1e-6)
    assert inf.outcomes[0].observed is None


def test_empirical_prior_uses_jeffreys_smoothing():
    """Un historique unanime ne produit ni 0 ni 1 (lissage de Jeffreys)."""
    hyp = Hypothesis("h", prior=0.5, threshold=0.9,
                     prior_bucket="LONG_TERM", prior_key="incidents")
    state = State()
    state.memory["LONG_TERM"]["incidents"] = [{"kind": "x"}] * 40
    value, _origin = bayes.empirical_prior(hyp, state)
    assert 0.0 < value < 1.0
    assert math.isclose(value, 40.5 / 41.0, abs_tol=1e-9)
