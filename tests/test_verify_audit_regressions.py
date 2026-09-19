"""Régressions issues de l'audit de VERIFY (AUDIT_VERIFY.md).

Chaque test décrit un agent où `DÉMONTRÉ` était produit alors que la
propriété concrète n'était pas établie.
"""
from __future__ import annotations

import unittest
from unittest import mock

from agentl import parse_source, satisfiable, verify
from agentl import solver
from agentl.nodes import BinOp, ListExpr, Literal, PathExpr, UnOp
from agentl.verifier import Verifier


def agent(src: str):
    return parse_source(src).agents[0]


def theorem(report, key):
    return next(t for t in report.theorems if t.key == key)


def _in(path: str, *items):
    return BinOp("IN", PathExpr([path]),
                 ListExpr([Literal(i) for i in items]))


class TestListSignature(unittest.TestCase):
    """VFY-01 : `r IN ["EU"]` et `r IN ["US"]` ne sont pas le même atome."""

    def test_distinct_lists_are_not_contradictory(self):
        # region = "US" rend les deux vraies : NOT (US IN [EU]) ∧ US IN [US].
        self.assertTrue(satisfiable(UnOp("NOT", _in("region", "EU")),
                                    _in("region", "US")))

    def test_same_list_is_still_contradictory(self):
        self.assertFalse(satisfiable(UnOp("NOT", _in("region", "EU")),
                                     _in("region", "EU")))


class TestBeliefInitialValue(unittest.TestCase):
    """VFY-03 : un BELIEF que rien ne modifie garde sa valeur déclarée."""

    TEMPLATE = """
    AGENT B {{
        GOAL g {{ ACHIEVE done == yes }}
        BELIEF {{ gate = locked }}
        TOOL finish {{ RISK LOW REQUIRES gate == unlocked
                      EFFECT {{ done = yes }} }}
        POLICY {{ DEFAULT ALLOW }}
        {extra}
    }}
    """

    def test_frozen_belief_blocks_the_route(self):
        report = verify(agent(self.TEMPLATE.format(extra="")))
        self.assertIsNot(theorem(report, "T2").holds, True)

    def test_observed_belief_stays_abstract(self):
        report = verify(agent(self.TEMPLATE.format(extra="OBSERVE { gate }")))
        self.assertTrue(theorem(report, "T2").holds)


class TestZeroProbabilityOutcome(unittest.TestCase):
    """VFY-05 : une issue de probabilité 0 n'est pas une route."""

    def test_impossible_outcome_is_not_a_route(self):
        src = """
        AGENT P {
            GOAL g { ACHIEVE done == yes }
            TOOL act { RISK LOW
                OUTCOME miracle WITH 0 { done = yes }
                OUTCOME rien WITH 1 { done = no } }
            POLICY { DEFAULT ALLOW }
        }
        """
        self.assertIs(theorem(verify(agent(src)), "T2").holds, False)


class TestSolverAbandonDegradesEveryTheorem(unittest.TestCase):
    """VFY-04 : un abandon du solveur ne laisse aucun théorème DÉMONTRÉ."""

    def test_overflow_downgrades_proved_theorem(self):
        ag = agent("""
        AGENT O {
            GOAL g { ACHIEVE done == yes }
            TOOL act { RISK LOW EFFECT { done = yes } }
            POLICY { DEFAULT ALLOW }
        }
        """)
        original = Verifier.theorem_reachability

        def abandoning(self):
            result = original(self)
            solver._give_up()
            return result

        with mock.patch.object(Verifier, "theorem_reachability", abandoning):
            t2 = theorem(verify(ag), "T2")
        self.assertIsNone(t2.holds)
        self.assertIn("V114", {f.code for f in t2.findings})


if __name__ == "__main__":
    unittest.main()
