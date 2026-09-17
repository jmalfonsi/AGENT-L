"""Tests AAA de la VIVACITÉ d'une société (liveness.py, théorème T8).

T1–T7 se démontrent un agent à la fois. Le trou que T8 comble est précis :
deux agents irréprochables séparément peuvent former un programme bloqué, et
T2 concluait « le but reste atteignable » pour chacun des deux.

Les propriétés visées sont donc adverses :

  * **le blocage est démontré, pas soupçonné** : un cycle d'attente sans point
    d'entrée est réfuté, et le cycle est nommé ;
  * **le programme sain n'est pas accusé** : un cycle de messages amorcé par
    une garde, un événement ou une règle `DECIDE` reste démontré ;
  * **la direction de sûreté tient** : T8 peut manquer un blocage, il n'en
    invente pas — un chemin de forme suffit à conclure « productible » ;
  * **l'échappatoire LLM est avouée** : avec `DECIDE.REASON`, T8 rend « non
    prouvé » plutôt qu'un verdict rassurant ;
  * **les deux moitiés du graphe sont couvertes** : une attente sur une clé
    `SHARED` que nul n'écrit est réfutée au même titre qu'un message, et une
    clé écrite que personne ne lit est signalée comme donnée morte.

    python -m pytest tests/test_liveness_aaa.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.liveness import (MSG, Context, producible, signal_label,
                             verify_liveness)
from agentl.parser import parse_source

EXAMPLES = ROOT / "examples"


def _report(source: str):
    return verify_liveness(parse_source(source, "<test>"))


def _codes(report):
    return {f.code for f in report.findings}


#: Deux agents qui s'attendent mutuellement : personne ne commence.
DEADLOCK = """
AGENT alpha {
    VERSION "1.0"
    GOAL done { ACHIEVE state == ok }
    PLAN send_ping { STEP go { MESSAGE ping { TO beta PAYLOAD { x = 1 } } } }
    ON MESSAGE pong { THEN send_ping }
}
AGENT beta {
    VERSION "1.0"
    GOAL done { ACHIEVE state == ok }
    PLAN send_pong { STEP go { MESSAGE pong { TO alpha PAYLOAD { x = 1 } } } }
    ON MESSAGE ping { THEN send_pong }
}
"""

#: Le même, avec un point d'entrée : alpha démarre sur une garde de plan.
STARTED = DEADLOCK.replace("PLAN send_ping {",
                           "PLAN send_ping WHEN alert.raised == yes {")


# --------------------------------------------------------------------------
# M1 — le blocage est démontré et nommé
# --------------------------------------------------------------------------
class TestDeadlockIsProved(unittest.TestCase):

    def test_a_mutual_wait_is_refuted(self):
        """La propriété centrale : ce que T2 déclarait atteignable par agent
        ne l'est pas à l'échelle du programme."""
        report = _report(DEADLOCK)

        self.assertFalse(report.holds)
        self.assertTrue(report.refuted)

    def test_the_cycle_is_named(self):
        """« le programme peut se bloquer » n'aide personne."""
        report = _report(DEADLOCK)

        cycles = [f for f in report.findings if f.code == "V130"]
        self.assertEqual(len(cycles), 1)
        self.assertIn("MESSAGE ping", cycles[0].message)
        self.assertIn("MESSAGE pong", cycles[0].message)

    def test_a_cycle_is_reported_once_not_once_per_participant(self):
        """Un cycle à deux trouvé deux fois serait deux fois le même fait."""
        report = _report(DEADLOCK)

        self.assertEqual(len([f for f in report.findings
                              if f.code == "V130"]), 1)

    def test_the_dead_contexts_are_pointed_at_with_a_line(self):
        report = _report(DEADLOCK)

        dead = [f for f in report.findings if f.code == "V135"]
        self.assertTrue(dead)
        self.assertTrue(all(f.line > 0 for f in dead))
        self.assertIn("alpha.send_ping", {f.message.split(" ne ")[0]
                                          for f in dead})

    def test_a_signal_nobody_emits_is_reported_separately(self):
        """Attendre un message que personne n'émet est une étourderie ; un
        interblocage est autre chose. Les confondre perdrait le diagnostic."""
        report = _report("""
AGENT a {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN act { STEP go { SET s = ok } }
    ON MESSAGE never_sent { THEN act }
}
AGENT b {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN idle WHEN t == 1 { STEP go { SET s = ok } }
}
""")

        self.assertFalse(report.holds)
        self.assertIn("V132", _codes(report))

    def test_a_chain_of_three_agents_is_detected(self):
        """Le point fixe n'est pas limité aux cycles à deux."""
        report = _report("""
AGENT a {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN pa { STEP go { MESSAGE m1 { TO b PAYLOAD { x = 1 } } } }
    ON MESSAGE m3 { THEN pa }
}
AGENT b {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN pb { STEP go { MESSAGE m2 { TO c PAYLOAD { x = 1 } } } }
    ON MESSAGE m1 { THEN pb }
}
AGENT c {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN pc { STEP go { MESSAGE m3 { TO a PAYLOAD { x = 1 } } } }
    ON MESSAGE m2 { THEN pc }
}
""")

        self.assertFalse(report.holds)
        cycle = next(f for f in report.findings if f.code == "V130")
        for name in ("m1", "m2", "m3"):
            self.assertIn(name, cycle.message)


# --------------------------------------------------------------------------
# M2 — un programme sain n'est pas accusé
# --------------------------------------------------------------------------
class TestHealthyProgramsHold(unittest.TestCase):

    def test_a_cycle_with_an_entry_point_holds(self):
        """Un cycle de messages n'est pas un défaut : c'est une conversation.
        Il ne le devient que sans point d'amorce."""
        report = _report(STARTED)

        self.assertTrue(report.holds, report.render())

    def test_an_event_is_an_entry_point(self):
        report = _report(DEADLOCK.replace(
            "ON MESSAGE pong { THEN send_ping }",
            "EVENT wazuh.alert { THEN send_ping }\n"
            "    ON MESSAGE pong { THEN send_ping }"))

        self.assertTrue(report.holds, report.render())

    def test_a_decide_rule_is_an_entry_point(self):
        report = _report(DEADLOCK.replace(
            "ON MESSAGE pong { THEN send_ping }",
            "DECIDE { RULES { IF alert.severity >= HIGH THEN send_ping } }\n"
            "    ON MESSAGE pong { THEN send_ping }"))

        self.assertTrue(report.holds, report.render())

    def test_the_repository_society_example_holds(self):
        """La propriété vaut sur le vrai programme du dépôt, pas seulement
        sur des cas jouets."""
        report = verify_liveness(
            __import__("agentl.parser", fromlist=["parse_file"]).parse_file(
                str(EXAMPLES / "soc_team.agent")))

        self.assertTrue(report.holds, report.render())

    def test_a_single_agent_program_is_true_vacuously(self):
        report = _report("""
AGENT solo {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN act WHEN t == 1 { STEP go { SET s = ok } }
}
""")

        self.assertTrue(report.holds)
        self.assertIn("V134", _codes(report))

    def test_an_indirect_plan_call_propagates_reachability(self):
        """`THEN p` depuis un plan atteignable rend `p` atteignable — sans
        quoi T8 crierait sur du code parfaitement vivant."""
        report = _report("""
AGENT a {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN entry WHEN t == 1 { STEP go { THEN relay } }
    PLAN relay { STEP go { MESSAGE m { TO b PAYLOAD { x = 1 } } } }
}
AGENT b {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN handle { STEP go { SET s = ok } }
    ON MESSAGE m { THEN handle }
}
""")

        self.assertTrue(report.holds, report.render())

    def test_a_message_emitted_under_a_guard_still_counts(self):
        """Supposer une branche morte serait se tromper dans le mauvais sens."""
        report = _report("""
AGENT a {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN entry WHEN t == 1 {
        STEP go { IF risk >= HIGH THEN { MESSAGE m { TO b PAYLOAD { x = 1 } } } }
    }
}
AGENT b {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN handle { STEP go { SET s = ok } }
    ON MESSAGE m { THEN handle }
}
""")

        self.assertTrue(report.holds, report.render())


# --------------------------------------------------------------------------
# M3 — l'échappatoire LLM est avouée
# --------------------------------------------------------------------------
class TestLLMEscapeHatchIsDeclared(unittest.TestCase):

    def test_reason_makes_the_theorem_unproved_not_proved(self):
        """`REASON` autorise le modèle à proposer n'importe quel plan : on ne
        peut plus rien démontrer, et rendre « démontré » serait mentir."""
        report = _report(DEADLOCK.replace(
            'AGENT beta {\n    VERSION "1.0"',
            'AGENT beta {\n    VERSION "1.0"\n'
            '    DECIDE { REASON { TASK "choisir" PRODUCE { verdict: Symbol } } }'))

        self.assertIsNone(report.holds)
        self.assertIn("V136", _codes(report))

    def test_the_unproved_verdict_is_not_a_refutation(self):
        """« non prouvé » et « réfuté » sont deux verdicts distincts ; les
        confondre ferait échouer une CI sur une absence de preuve."""
        report = _report(DEADLOCK.replace(
            'AGENT beta {\n    VERSION "1.0"',
            'AGENT beta {\n    VERSION "1.0"\n'
            '    DECIDE { REASON { TASK "choisir" PRODUCE { verdict: Symbol } } }'))

        self.assertFalse(report.refuted)


# --------------------------------------------------------------------------
# M4 — le point fixe lui-même
# --------------------------------------------------------------------------
class TestFixedPoint(unittest.TestCase):

    def test_an_autonomous_context_produces_its_signals(self):
        ctx = Context("a", "plan", "p", gates=[set()], emits={MSG + "m"})

        self.assertEqual(producible([ctx]), {MSG + "m"})

    def test_a_gated_context_produces_nothing_without_its_gate(self):
        ctx = Context("a", "plan", "p", gates=[{MSG + "n"}], emits={MSG + "m"})

        self.assertEqual(producible([ctx]), set())

    def test_saturation_follows_a_chain(self):
        """Le point fixe doit propager, pas seulement regarder le premier tour."""
        chain = [
            Context("a", "plan", "p1", gates=[set()], emits={MSG + "m1"}),
            Context("a", "plan", "p2", gates=[{MSG + "m1"}], emits={MSG + "m2"}),
            Context("a", "plan", "p3", gates=[{MSG + "m2"}], emits={MSG + "m3"}),
        ]

        self.assertEqual(producible(chain),
                         {MSG + "m1", MSG + "m2", MSG + "m3"})

    def test_a_disjunction_of_gates_needs_only_one_branch(self):
        """`gates` est une disjonction : deux façons d'atteindre un plan
        suffisent à une seule d'entre elles."""
        ctx = Context("a", "plan", "p", gates=[{MSG + "absent"}, set()],
                      emits={MSG + "m"})

        self.assertEqual(producible([ctx]), {MSG + "m"})

    def test_a_conjunction_needs_every_signal(self):
        contexts = [
            Context("a", "plan", "p1", gates=[set()], emits={MSG + "m1"}),
            Context("a", "plan", "p2", gates=[{MSG + "m1", MSG + "m9"}],
                    emits={MSG + "m2"}),
        ]

        self.assertEqual(producible(contexts), {MSG + "m1"})

    def test_the_computation_terminates_on_a_self_referential_context(self):
        """Un contexte gardé par ce qu'il émet lui-même ne doit pas boucler."""
        ctx = Context("a", "plan", "p", gates=[{MSG + "m"}], emits={MSG + "m"})

        self.assertEqual(producible([ctx]), set())

    def test_signal_labels_are_readable(self):
        self.assertEqual(signal_label("msg:check"), "MESSAGE check")
        self.assertEqual(signal_label("shared:hosts"), "SHARED.hosts")


# --------------------------------------------------------------------------
# M5 — ce que T8 déclare ne pas couvrir
# --------------------------------------------------------------------------
class TestSharedIsCovered(unittest.TestCase):
    """`SHARED` se lit depuis la v1.6 : T8 couvre donc les deux moitiés du
    graphe d'attente, pas seulement `MESSAGE`."""

    SHARED_WAIT = """
AGENT alpha {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN idle WHEN t == 1 { STEP go { SET s = ok } }
}
AGENT beta {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN act WHEN SHARED.verdict.count > 0 { STEP go { SET s = ok } }
}
"""

    def test_a_wait_on_a_key_nobody_writes_is_refuted(self):
        report = _report(self.SHARED_WAIT)

        self.assertFalse(report.holds)
        self.assertIn("V132", _codes(report))

    def test_adding_a_writer_restores_the_proof(self):
        report = _report(self.SHARED_WAIT.replace(
            'GOAL g { ACHIEVE s == ok }\n    PLAN idle',
            'GOAL g { ACHIEVE s == ok }\n'
            '    MEMORY { SHARED { verdict }\n'
            '             WRITE { WHEN s == ok STORE { s } INTO SHARED.verdict } }\n'
            '    PLAN idle', 1))

        self.assertTrue(report.holds, report.render())

    def test_a_mutual_wait_on_shared_keys_is_a_deadlock(self):
        """Le même interblocage que par messages, par la mémoire partagée."""
        report = _report("""
AGENT alpha {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    MEMORY { SHARED { x }
             WRITE { WHEN SHARED.y.count > 0 STORE { s } INTO SHARED.x } }
    PLAN idle WHEN SHARED.y.count > 0 { STEP go { SET s = ok } }
}
AGENT beta {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    MEMORY { SHARED { y }
             WRITE { WHEN SHARED.x.count > 0 STORE { s } INTO SHARED.y } }
    PLAN idle WHEN SHARED.x.count > 0 { STEP go { SET s = ok } }
}
""")

        self.assertFalse(report.holds)
        cycle = next(f for f in report.findings if f.code == "V130")
        self.assertIn("SHARED.x", cycle.message)
        self.assertIn("SHARED.y", cycle.message)

    def test_a_key_written_but_never_read_is_a_warning_not_a_blockage(self):
        """Donnée morte : on paie l'écriture et le versionnement pour rien."""
        report = _report("""
AGENT a {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    MEMORY { SHARED { hosts }
             WRITE { WHEN s == ok STORE { host } INTO SHARED.hosts } }
    PLAN act WHEN t == 1 { STEP go { SET s = ok } }
}
AGENT b {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN act WHEN t == 1 { STEP go { SET s = ok } }
}
""")

        self.assertIn("V137", _codes(report))
        self.assertTrue(report.holds)

    def test_a_key_that_is_read_raises_no_warning(self):
        """Une alarme qui se déclenche pour tout le monde ne se lit plus."""
        report = _report(self.SHARED_WAIT.replace(
            'GOAL g { ACHIEVE s == ok }\n    PLAN idle',
            'GOAL g { ACHIEVE s == ok }\n'
            '    MEMORY { SHARED { verdict }\n'
            '             WRITE { WHEN s == ok STORE { s } INTO SHARED.verdict } }\n'
            '    PLAN idle', 1))

        self.assertNotIn("V137", _codes(report))

    def test_a_program_without_shared_says_nothing_about_shared(self):
        self.assertNotIn("V137", _codes(_report(STARTED)))


# --------------------------------------------------------------------------
# M6 — la lecture de SHARED (v1.6)
# --------------------------------------------------------------------------
class TestSharedIsReadable(unittest.TestCase):
    """Le compartiment partagé s'écrivait sans pouvoir se lire : aucune garde
    ne pouvait en dépendre, donc aucune décision non plus."""

    def setUp(self):
        from agentl.state import State

        self.state = State()
        self.state.memory["SHARED"]["hosts"] = [{"h": "PC-1"}, {"h": "PC-42"}]
        self.state.memory["SHARED"]["__versions"] = {"hosts": 2}

    def test_the_bare_key_yields_the_record_sequence(self):
        self.assertEqual(self.state.get("SHARED.hosts"),
                         [{"h": "PC-1"}, {"h": "PC-42"}])

    def test_count_version_and_last(self):
        for path, expected in (("SHARED.hosts.count", 2),
                               ("SHARED.hosts.version", 2),
                               ("SHARED.hosts.last", {"h": "PC-42"}),
                               ("SHARED.hosts.last.h", "PC-42")):
            with self.subTest(path):
                self.assertEqual(self.state.get(path), expected)

    def test_an_absent_key_is_undefined_not_empty(self):
        """`0` ou `[]` se compareraient silencieusement ; UNDEFINED non."""
        from agentl.core import UNDEFINED

        self.assertIs(self.state.get("SHARED.absent"), UNDEFINED)
        self.assertIs(self.state.get("SHARED.absent.count"), UNDEFINED)
        self.assertIs(self.state.get("SHARED.absent.last"), UNDEFINED)

    def test_an_absent_version_reads_zero(self):
        """La version est un compteur d'écritures : jamais écrit = 0."""
        self.assertEqual(self.state.get("SHARED.absent.version"), 0)

    def test_an_empty_key_has_no_last_record(self):
        from agentl.core import UNDEFINED

        self.state.memory["SHARED"]["empty"] = []
        self.assertEqual(self.state.get("SHARED.empty.count"), 0)
        self.assertIs(self.state.get("SHARED.empty.last"), UNDEFINED)

    def test_a_shared_key_does_not_leak_into_the_bare_namespace(self):
        """Deux agents partageant `incidents` ne doivent pas le confondre avec
        leur propre `LONG_TERM.incidents`."""
        from agentl.core import UNDEFINED

        self.assertIs(self.state.get("hosts"), UNDEFINED)

    def test_a_guard_can_be_parsed_and_evaluated_on_shared(self):
        from agentl.state import Evaluator

        agent = parse_source("""
AGENT a {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN act WHEN SHARED.hosts.count > 1 { STEP go { SET s = ok } }
}
""", "<test>").agents[0]

        self.assertTrue(Evaluator(self.state).test(agent.plans[0].when))

    def test_the_society_really_coordinates_through_shared(self):
        """Bout en bout : alpha écrit, la garde de beta le lit, beta agit."""
        from agentl.society import Society

        program = parse_source("""
AGENT alpha {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    MEMORY { SHARED { verdict }
             WRITE { WHEN s == ok STORE { s } INTO SHARED.verdict } }
    PLAN idle WHEN t == 1 { STEP go { SET s = ok } }
}
AGENT beta {
    VERSION "1.0"
    GOAL g { ACHIEVE seen == yes }
    PLAN act WHEN SHARED.verdict.count > 0 { STEP go { SET seen = yes } }
}
""", "<test>")
        society = Society(program.agents)
        society.runtimes["alpha"].state.set_world("t", 1)

        society.run(max_ticks=3)

        self.assertEqual(society.runtimes["beta"].state.get("seen"),
                         __import__("agentl.core", fromlist=["Symbol"]).Symbol("yes"))

    def test_a_declared_but_unwritten_key_reads_as_an_empty_sequence(self):
        """Sans ce semis, `SHARED.k.count == 0` serait faux au premier tick,
        alors qu'il est précisément vrai."""
        from agentl.society import Society

        program = parse_source("""
AGENT a {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    MEMORY { SHARED { pending } }
    PLAN act WHEN t == 1 { STEP go { SET s = ok } }
}
AGENT b {
    VERSION "1.0"
    GOAL g { ACHIEVE s == ok }
    PLAN act WHEN t == 1 { STEP go { SET s = ok } }
}
""", "<test>")
        society = Society(program.agents)

        self.assertEqual(society.runtimes["b"].state.get("SHARED.pending"), [])
        self.assertEqual(society.runtimes["b"].state.get("SHARED.pending.count"), 0)


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
