"""Tests AAA des SCENARIO (scenario.py, parser, analyzer, verifier T5).

Un test qui ment est pire qu'un test absent : il fait passer pour vérifié ce
qui ne l'est pas. Les propriétés visées portent donc d'abord sur ce qui
pourrait rendre un scénario **vert à tort** :

  * un scénario sans `EXPECT` est refusé à la lecture ;
  * une attente déjà vraie au départ est traitée en **invariant** — elle doit
    tenir à chaque tick, et un scénario ne peut donc pas passer sans qu'aucun
    tick n'ait tourné ;
  * l'humain et l'oracle ne sont jamais complaisants par défaut : sans
    `operator.approval`, l'approbation est refusée ;
  * les politiques s'appliquent en scénario comme en production — retirer un
    `NEVER` doit faire tomber le scénario qui le teste ;
  * T5 ne réfute que sur démonstration, et jamais un invariant.

    python -m pytest tests/test_scenario_aaa.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.analyzer import Analyzer
from agentl.core import ParseError
from agentl.parser import parse_source
from agentl.scenario import (ScenarioHost, initial_expectations, run_scenario,
                             run_scenarios)
from agentl.verifier import verify

EXAMPLES = ROOT / "examples"

#: Agent minimal mais complet : une attente qui doit advenir, un interdit qui
#: peut l'en empêcher, une approbation exigée.
BASE = """
AGENT T {{
    VERSION "1.4"
    OBSERVE {{ alert.count }}
    BELIEF {{ escalation.sent = no CONFIDENCE 0.9 SOURCE prior }}
    GOAL safe {{ MAINTAIN escalation.sent == yes }}
    TOOL escalate {{
        OUTPUT {{ ok: Symbol }}
        RISK LOW
        REQUIRES alert.count > 10
        EFFECT {{ escalation.sent = yes }}
    }}
    POLICY {{ DEFAULT DENY  ALLOW escalate {policy} }}
    PLAN react WHEN alert.count > 10 {{ STEP go {{ escalate() }} }}
    SCENARIO nominal {{
        GIVEN  {{ alert.count = 37, escalate.ok = yes {given} }}
        EXPECT {{ escalation.sent == yes }} WITHIN 3
    }}
    LOOP UNTIL goal.satisfied MAX 3 {{
        OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY
    }}
}}
"""


def _agent(policy: str = "", given: str = ""):
    return parse_source(BASE.format(policy=policy, given=given)).agents[0]


# --------------------------------------------------------------------------
# M1 — lecture
# --------------------------------------------------------------------------
class TestParsing(unittest.TestCase):

    def test_scenario_is_parsed(self):
        scenario = _agent().scenarios[0]
        self.assertEqual(scenario.name, "nominal")
        self.assertEqual(scenario.within, 3)
        self.assertEqual([e.path for e in scenario.given], ["alert.count", "escalate.ok"])
        self.assertEqual(len(scenario.expect), 1)

    def test_scenario_without_expect_is_refused(self):
        """Il serait toujours satisfait : le refuser coûte moins cher que
        l'expliquer plus tard."""
        with self.assertRaises(ParseError):
            parse_source("""
AGENT X { VERSION "1"
    SCENARIO vide { GIVEN { a = 1 } }
}""")

    def test_within_defaults_to_one_tick(self):
        agent = parse_source("""
AGENT X { VERSION "1"
    SCENARIO s { EXPECT { a == 1 } }
}""").agents[0]
        self.assertEqual(agent.scenarios[0].within, 1)

    def test_within_may_stand_alone_in_the_body(self):
        agent = parse_source("""
AGENT X { VERSION "1"
    SCENARIO s { EXPECT { a == 1 } WITHIN 5 }
}""").agents[0]
        self.assertEqual(agent.scenarios[0].within, 5)


# --------------------------------------------------------------------------
# M2 — exécution
# --------------------------------------------------------------------------
class TestExecution(unittest.TestCase):

    def test_nominal_scenario_passes_and_calls_the_tool(self):
        result = run_scenario(_agent(), _agent().scenarios[0])
        self.assertTrue(result.passed, result.failed)
        self.assertIn("escalate", result.calls)
        self.assertGreaterEqual(result.ticks_used, 1)

    def test_a_never_makes_the_scenario_fail(self):
        """La politique s'applique en scénario comme en production."""
        agent = _agent(policy="NEVER escalate")
        result = run_scenario(agent, agent.scenarios[0])
        self.assertFalse(result.passed)
        self.assertNotIn("escalate", result.calls)

    def test_approval_is_refused_unless_the_scenario_grants_it(self):
        agent = _agent(policy="REQUIRE APPROVAL FOR escalate")
        self.assertFalse(run_scenario(agent, agent.scenarios[0]).passed)

        granted = _agent(policy="REQUIRE APPROVAL FOR escalate",
                         given=", operator.approval = yes")
        self.assertTrue(run_scenario(granted, granted.scenarios[0]).passed)

    def test_a_tool_without_effect_moves_nothing(self):
        agent = parse_source("""
AGENT X {
    VERSION "1"
    OBSERVE { a }
    GOAL g { MAINTAIN done == yes }
    TOOL noop { OUTPUT { ok: Symbol } RISK LOW }
    POLICY { DEFAULT DENY  ALLOW noop }
    PLAN p WHEN a > 0 { STEP s { noop() } }
    SCENARIO s { GIVEN { a = 1 } EXPECT { done == yes } WITHIN 2 }
    LOOP UNTIL goal.satisfied MAX 2 {
        OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE
    }
}""").agents[0]
        result = run_scenario(agent, agent.scenarios[0])
        self.assertIn("W118", result.error)     # scénario sans effet testable refusé
        self.assertFalse(result.passed)          # …mais le monde ne bouge pas

    def test_the_run_is_hermetic(self):
        """Aucun hôte réel : l'hôte de scénario n'a ni capteur ni outil."""
        agent = _agent()
        host = ScenarioHost(agent, {})
        self.assertFalse(hasattr(host, "sensors"))
        self.assertFalse(hasattr(host, "tools"))

    def test_report_is_red_when_one_scenario_fails(self):
        agent = _agent(policy="NEVER escalate")
        report = run_scenarios(agent)
        self.assertFalse(report.passed)
        self.assertIn("✘", report.render())


# --------------------------------------------------------------------------
# M3 — invariants : le piège du scénario vert sans exécution
# --------------------------------------------------------------------------
class TestInvariants(unittest.TestCase):

    def test_given_overrides_a_declared_belief_during_classification(self):
        agent = parse_source("""
AGENT GIVEN_BELIEF {
    VERSION "1.8.2"
    BELIEF { status = pending CONFIDENCE 0.4 SOURCE prior }
    TOOL change { EFFECT { status = ready } }
    SCENARIO observed {
        GIVEN { status = ready }
        EXPECT { status == ready }
    }
}
""").agents[0]

        invariants, eventualities = initial_expectations(
            agent, agent.scenarios[0])
        result = run_scenario(agent, agent.scenarios[0])

        self.assertEqual(len(invariants), 1)
        self.assertEqual(eventualities, [])
        self.assertTrue(result.passed, result.failed)

    def _agent_with_invariant(self, policy: str):
        return parse_source("""
AGENT INV {{
    VERSION "1.4"
    OBSERVE {{ alert.count }}
    GOAL g {{ MAINTAIN done == yes }}
    TOOL act {{
        OUTPUT {{ ok: Symbol }}
        RISK HIGH
        EFFECT {{ done = yes, dangerous = confirmed }}
    }}
    POLICY {{ DEFAULT DENY  ALLOW act {policy} }}
    PLAN p WHEN alert.count > 0 {{ STEP s {{ act() }} }}
    SCENARIO jamais_dangereux {{
        GIVEN  {{ alert.count = 5, dangerous = safe, act.ok = yes }}
        EXPECT {{ dangerous != confirmed }} WITHIN 3
    }}
    LOOP UNTIL goal.satisfied MAX 3 {{
        OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE
    }}
}}""".format(policy=policy)).agents[0]

    def test_an_expectation_true_at_start_is_an_invariant(self):
        agent = self._agent_with_invariant("NEVER act")
        invariants, eventualities = initial_expectations(
            agent, agent.scenarios[0])
        self.assertEqual(len(invariants), 1)
        self.assertEqual(eventualities, [])

    def test_an_invariant_is_checked_at_every_tick_not_just_at_zero(self):
        """Sans interdit, l'action survient : l'invariant doit tomber.

        C'est le test du test : s'il passait dans les deux cas, la primitive
        ne prouverait rien.
        """
        protected = self._agent_with_invariant("NEVER act")
        result = run_scenario(protected, protected.scenarios[0])
        self.assertTrue(result.passed, result.failed)
        self.assertEqual(result.ticks_used, 3)      # la borne entière a tourné

        unprotected = self._agent_with_invariant("")
        broken = run_scenario(unprotected, unprotected.scenarios[0])
        self.assertFalse(broken.passed)
        self.assertTrue(any("rompu au tick" in f for f in broken.failed),
                        broken.failed)

    def test_a_broken_invariant_is_reported_once(self):
        unprotected = self._agent_with_invariant("")
        broken = run_scenario(unprotected, unprotected.scenarios[0])
        self.assertEqual(len(broken.failed), 1, broken.failed)


# --------------------------------------------------------------------------
# M4 — analyse statique
# --------------------------------------------------------------------------
class TestAnalyzer(unittest.TestCase):

    def _codes(self, source: str):
        agent = parse_source(source).agents[0]
        return {d.code for d in Analyzer(agent).run()}

    def test_within_zero_is_an_error(self):
        self.assertIn("E010", self._codes("""
AGENT X { VERSION "1"
    SCENARIO s { EXPECT { a == 1 } WITHIN 0 }
}"""))

    def test_duplicate_scenario_is_an_error(self):
        self.assertIn("E004", self._codes("""
AGENT X { VERSION "1"
    SCENARIO s { EXPECT { a == 1 } }
    SCENARIO s { EXPECT { a == 2 } }
}"""))

    def test_given_on_an_unknown_path_warns(self):
        self.assertIn("W117", self._codes("""
AGENT X { VERSION "1"
    SCENARIO s { GIVEN { nulle.part = 1 } EXPECT { a == 1 } }
}"""))

    def test_operator_paths_are_not_reported_as_unknown(self):
        codes = self._codes("""
AGENT X { VERSION "1"
    OBSERVE { a }
    TOOL t { OUTPUT { ok: Symbol } RISK LOW EFFECT { done = yes } }
    POLICY { DEFAULT DENY ALLOW t }
    SCENARIO s {
        GIVEN { a = 1, operator.approval = yes, operator.answer = go }
        EXPECT { done == yes }
    }
}""")
        self.assertNotIn("W117", codes)

    def test_expectation_on_no_produced_path_warns(self):
        self.assertIn("W118", self._codes("""
AGENT X { VERSION "1"
    OBSERVE { a }
    TOOL t { OUTPUT { ok: Symbol } RISK LOW EFFECT { done = yes } }
    POLICY { DEFAULT DENY ALLOW t }
    SCENARIO s { GIVEN { a = 1 } EXPECT { a == 1 } }
}"""))

    def test_the_reference_example_stays_clean(self):
        agent = parse_source(
            (EXAMPLES / "soc_analyst.agent").read_text(encoding="utf-8")
        ).agents[0]
        self.assertEqual([d.render() for d in Analyzer(agent).run()], [])


# --------------------------------------------------------------------------
# M5 — théorème T5
# --------------------------------------------------------------------------
class TestTheoremFive(unittest.TestCase):

    def _t5(self, agent):
        return next(t for t in verify(agent).theorems if t.key == "T5")

    def test_reachable_expectation_is_proved(self):
        theorem = self._t5(_agent())
        self.assertIs(theorem.holds, True)
        self.assertTrue(any(f.code == "V123" for f in theorem.findings))

    def test_a_never_that_kills_the_expectation_is_refuted(self):
        theorem = self._t5(_agent(policy="NEVER escalate"))
        self.assertIs(theorem.holds, False)
        self.assertTrue(any(f.code == "V121" and f.severity == "error"
                            for f in theorem.findings))

    def test_an_invariant_is_never_refuted_by_t5(self):
        """Chercher « quelle action rend vrai que rien n'a eu lieu » n'a pas
        de sens : T5 doit passer la main, pas réfuter."""
        agent = parse_source("""
AGENT INV {
    VERSION "1.4"
    OBSERVE { a }
    GOAL g { MAINTAIN done == yes }
    TOOL act { OUTPUT { ok: Symbol } RISK LOW
               EFFECT { done = yes, dangerous = confirmed } }
    POLICY { DEFAULT DENY  ALLOW act  NEVER act }
    PLAN p WHEN a > 0 { STEP s { act() } }
    SCENARIO jamais { GIVEN { a = 5, dangerous = safe } EXPECT { dangerous != confirmed } WITHIN 2 }
    LOOP UNTIL goal.satisfied MAX 2 {
        OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE
    }
}""").agents[0]
        theorem = self._t5(agent)
        self.assertIsNot(theorem.holds, False)
        self.assertTrue(any(f.code == "V124" for f in theorem.findings))

    def test_no_scenario_is_vacuously_true_but_signalled(self):
        agent = parse_source("""
AGENT X { VERSION "1"
    GOAL g { MAINTAIN a == 1 }
    TOOL t { OUTPUT { ok: Symbol } RISK LOW EFFECT { a = 1 } }
    POLICY { DEFAULT DENY ALLOW t }
}""").agents[0]
        theorem = self._t5(agent)
        self.assertIs(theorem.holds, True)
        self.assertTrue(any(f.code == "V120" for f in theorem.findings))

    def test_the_reference_example_proves_every_theorem(self):
        agent = parse_source(
            (EXAMPLES / "soc_analyst.agent").read_text(encoding="utf-8")
        ).agents[0]
        report = verify(agent)
        self.assertEqual(len(report.theorems), 8)
        self.assertEqual([t.key for t in report.theorems if t.holds is not True],
                         [], report.render())


# --------------------------------------------------------------------------
# M6 — l'exemple de référence, exécuté
# --------------------------------------------------------------------------
class TestShippedExample(unittest.TestCase):

    def test_soc_analyst_scenarios_pass(self):
        agent = parse_source(
            (EXAMPLES / "soc_analyst.agent").read_text(encoding="utf-8")
        ).agents[0]
        report = run_scenarios(agent)
        self.assertEqual(len(report.results), 2)
        self.assertTrue(report.passed, report.render())

    def test_removing_the_never_breaks_the_shipped_scenario(self):
        """Test de mutation : sans l'interdit, le scénario qui le teste doit
        tomber. Sinon il ne teste rien."""
        source = (EXAMPLES / "soc_analyst.agent").read_text(encoding="utf-8")
        mutated = source.replace(
            "        NEVER isolate_endpoint\n"
            "            WHEN asset.criticality == CRITICAL\n", "")
        self.assertNotEqual(mutated, source, "mutation non appliquée")
        report = run_scenarios(parse_source(mutated).agents[0])
        self.assertFalse(report.passed, report.render())


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
