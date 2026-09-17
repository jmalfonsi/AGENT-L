"""Tests AAA — les contrats `EFFECT` confrontés au réel (v1.7).

Toute la chaîne de preuve repose sur des `EFFECT` écrits à la main : T2
cherche une route en les appliquant, le planificateur enchaîne des `REQUIRES`
dessus, T5 valide un scénario à travers eux. Un `EFFECT` faux rend donc
`verify` vert et la production fausse.

Le runtime savait déjà comparer une perception à la postcondition qui l'avait
précédée (registre de dérive, v1.2). Deux choses manquaient, et c'est ce que
ces tests couvrent :

  * **La réfutabilité.** Sans `OBSERVE` sur le chemin, aucune perception ne
    viendra jamais : la postcondition n'est pas fausse, elle est hors du
    domaine de la preuve. Mesuré avant correction sur le dépôt : 24 des 35
    `EFFECT` déclarés dans `examples/` étaient irréfutables. T9 les compte,
    `INTERNAL` exempte ce qui ne porte pas sur le monde.
  * **La conséquence.** Le registre comptait dans le vide : un effet démenti
    trois fois sur trois reposait sa croyance avec le poids d'un effet
    toujours confirmé (0.80 fixe). La crédibilité est désormais mesurée
    (Jeffreys) et survit à l'exécution via `LONG_TERM.effect_drift`.

    python -m pytest tests/test_effect_contracts_aaa.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.analyzer import Analyzer
from agentl.core import Symbol
from agentl.host import Host
from agentl.parser import parse_source
from agentl.runtime import Runtime
from agentl.verifier import verify

EXAMPLES = ROOT / "examples"


def _agent(source: str):
    return parse_source(source).agents[0]


def _theorem(agent, key: str = "T9"):
    return {t.key: t for t in verify(agent).theorems}[key]


MENTEUR = """
AGENT medic {
    VERSION "1.0"
    GOAL g { ACHIEVE service.state == up }
    OBSERVE { service.state }
    BELIEF { service.state = down CONFIDENCE 0.90 SOURCE prior }
    MEMORY { LONG_TERM { effect_drift } }
    TOOL restart { INPUT { name: String } RISK LOW EFFECT { service.state = up } }
    POLICY { ALLOW restart }
    PLAN fix { STEP s { CALL restart(name: "api") } }
    DECIDE { RULES { IF service.state == down THEN fix } }
}
"""


def _liar(state: str = "down"):
    """Un hôte dont l'outil rend un succès et dont le monde ne bouge pas."""
    host = Host()
    host.tools["restart"] = lambda name: {"ok": True}
    host.sensors["service.state"] = lambda: Symbol(state)
    return host


# ---------------------------------------------- T9 : réfutabilité du modèle
class TheoremNineFalsifiability(unittest.TestCase):
    def test_an_effect_no_observe_covers_is_refuted(self):
        agent = _agent("""
AGENT aveugle {
    VERSION "1.0"
    GOAL g { ACHIEVE app.status == healthy }
    TOOL restart { INPUT { pod: String } RISK LOW EFFECT { app.status = healthy } }
    POLICY { ALLOW restart }
    PLAN fix { STEP s { CALL restart(pod: "api") } }
}
""")

        theorem = _theorem(agent)

        self.assertIs(theorem.holds, False)
        self.assertEqual([f.code for f in theorem.findings], ["V150"])

    def test_the_finding_names_the_tool_and_the_path(self):
        agent = _agent("""
AGENT aveugle {
    VERSION "1.0"
    GOAL g { ACHIEVE app.status == healthy }
    TOOL restart { INPUT { pod: String } RISK LOW EFFECT { app.status = healthy } }
    POLICY { ALLOW restart }
    PLAN fix { STEP s { CALL restart(pod: "api") } }
}
""")

        finding = _theorem(agent).findings[0]

        self.assertIn("restart()", finding.title)
        self.assertIn("app.status", finding.title)

    def test_an_observed_effect_is_proved(self):
        agent = _agent("""
AGENT lucide {
    VERSION "1.0"
    GOAL g { ACHIEVE app.status == healthy }
    OBSERVE { app.status }
    TOOL restart { INPUT { pod: String } RISK LOW EFFECT { app.status = healthy } }
    POLICY { ALLOW restart }
    PLAN fix { STEP s { CALL restart(pod: "api") } }
}
""")

        theorem = _theorem(agent)

        self.assertIs(theorem.holds, True)
        self.assertEqual([f.code for f in theorem.findings], ["V151"])

    def test_internal_exempts_what_does_not_bear_on_the_world(self):
        """`cycle.done` est vrai parce que l'agent vient de l'écrire."""
        agent = _agent("""
AGENT comptable {
    VERSION "1.0"
    GOAL g { ACHIEVE cycle.done == yes }
    TOOL close { INPUT { id: String } RISK LOW EFFECT { cycle.done = yes INTERNAL } }
    POLICY { ALLOW close }
    PLAN fin { STEP s { CALL close(id: "c1") } }
}
""")

        self.assertIs(_theorem(agent).holds, True)

    def test_internal_is_not_a_blanket_exemption(self):
        """Un effet marqué n'exempte pas ses voisins du même bloc."""
        agent = _agent("""
AGENT mixte {
    VERSION "1.0"
    GOAL g { ACHIEVE app.status == healthy }
    TOOL restart {
        INPUT { pod: String } RISK LOW
        EFFECT { cycle.done = yes INTERNAL, app.status = healthy }
    }
    POLICY { ALLOW restart }
    PLAN fix { STEP s { CALL restart(pod: "api") } }
}
""")

        theorem = _theorem(agent)

        self.assertIs(theorem.holds, False)
        self.assertIn("app.status", theorem.findings[0].title)

    def test_probabilistic_outcomes_are_covered_too(self):
        """Un `OUTCOME` est un `EFFECT` sous une probabilité, rien de plus."""
        agent = _agent("""
AGENT parieur {
    VERSION "1.0"
    GOAL g { ACHIEVE link.state == cut }
    TOOL isolate {
        INPUT { host: String } RISK HIGH
        OUTCOME marche WITH 0.9 { link.state = cut }
        OUTCOME rate   WITH 0.1 { link.state = up }
    }
    POLICY { ALLOW isolate }
    PLAN fix { STEP s { CALL isolate(host: "h") } }
}
""")

        self.assertIs(_theorem(agent).holds, False)

    def test_an_agent_without_effects_has_nothing_to_confront(self):
        agent = _agent("""
AGENT nu {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    TOOL ping { INPUT { host: String } RISK LOW }
    POLICY { ALLOW ping }
    PLAN p { STEP s { CALL ping(host: "h") } }
}
""")

        self.assertIs(_theorem(agent).holds, True)

    def test_every_shipped_example_is_falsifiable(self):
        """La régression que la v1.7 vient de solder : 24/35 irréfutables."""
        for path in sorted(EXAMPLES.glob("*.agent")):
            if path.name == "broken.agent":
                continue
            for agent in parse_source(path.read_text(encoding="utf-8")).agents:
                with self.subTest(agent=f"{path.name}:{agent.name}"):
                    self.assertIsNot(_theorem(agent).holds, False)

    def test_an_observation_serving_only_the_drift_audit_is_not_dead(self):
        """W103 ne doit pas pousser à retirer la perception qui réfute."""
        agent = _agent("""
AGENT lucide {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    OBSERVE { app.status }
    TOOL restart { INPUT { pod: String } RISK LOW EFFECT { app.status = healthy } }
    POLICY { ALLOW restart }
    PLAN fix { STEP s { CALL restart(pod: "api") } }
}
""")

        codes = [d.code for d in Analyzer(agent).run()]

        self.assertNotIn("W103", codes)


# -------------------------------------------- crédibilité mesurée (Jeffreys)
class DriftHasConsequences(unittest.TestCase):
    def setUp(self):
        self.agent = _agent(MENTEUR)

    def test_a_denied_effect_is_counted(self):
        """Un démenti par cycle — la perception qui juge vient au tick suivant."""
        runtime = Runtime(self.agent, _liar())

        runtime.run(max_ticks=4)

        self.assertEqual(runtime.drift["restart→service.state"]["démenti"], 3)

    def test_a_denied_effect_loses_credibility(self):
        runtime = Runtime(self.agent, _liar())

        runtime.run(max_ticks=4)

        # Jeffreys : (0 + ½) / (3 + 1)
        self.assertAlmostEqual(
            runtime._effect_confidence("restart", "service.state"), 0.125)

    def test_an_unjudged_effect_keeps_the_declared_confidence(self):
        """Sans historique, on retombe sur la valeur déclarée — pas sur ½."""
        runtime = Runtime(self.agent, _liar())

        self.assertEqual(runtime._effect_confidence("restart", "service.state"),
                         Runtime.EFFECT_CONFIDENCE)

    def test_a_confirmed_effect_is_not_a_certainty(self):
        """Deux confirmations ne valent pas 1.0 : c'est le point du lissage."""
        runtime = Runtime(self.agent, _liar("up"))
        runtime.drift["restart→service.state"] = {"confirmé": 2, "démenti": 0}

        self.assertAlmostEqual(
            runtime._effect_confidence("restart", "service.state"), 0.8333, 3)

    def test_the_belief_posted_by_the_effect_carries_that_credibility(self):
        runtime = Runtime(self.agent, _liar())

        runtime.run(max_ticks=4)
        runtime.call_tool("restart", {"name": "api"}, origin="plan")

        self.assertAlmostEqual(
            runtime.state.beliefs["service.state"].confidence, 0.125)

    def test_the_mutation_restores_the_indifference(self):
        """L'ancien comportement : 0.80, démenti ou non."""
        runtime = Runtime(self.agent, _liar())
        runtime.run(max_ticks=4)

        self.assertEqual(Runtime.EFFECT_CONFIDENCE, 0.80)
        self.assertNotEqual(
            runtime._effect_confidence("restart", "service.state"), 0.80)


# ------------------------------------------------ persistance du registre
class DriftOutlivesTheRun(unittest.TestCase):
    def test_the_ledger_is_written_to_declared_long_term_memory(self):
        runtime = Runtime(_agent(MENTEUR), _liar())

        runtime.run(max_ticks=3)

        self.assertEqual(
            runtime.state.memory["LONG_TERM"]["effect_drift"],
            [{"tool": "restart", "path": "service.state",
              "confirmé": 0, "démenti": 2}])

    def test_nothing_is_written_when_the_key_is_not_declared(self):
        """On n'invente pas de la mémoire dans le dos de l'auteur."""
        source = MENTEUR.replace("    MEMORY { LONG_TERM { effect_drift } }\n", "")
        runtime = Runtime(_agent(source), _liar())

        runtime.run(max_ticks=2)

        self.assertNotIn("effect_drift",
                         runtime.state.memory.get("LONG_TERM", {}))

    def test_the_ledger_is_a_current_account_not_a_log(self):
        runtime = Runtime(_agent(MENTEUR), _liar())

        runtime.run(max_ticks=4)

        self.assertEqual(len(runtime.state.memory["LONG_TERM"]["effect_drift"]), 1)

    def test_a_seeded_ledger_makes_the_next_run_start_wary(self):
        runtime = Runtime(_agent(MENTEUR), _liar())

        runtime.seed_memory("LONG_TERM", "effect_drift",
                            [{"tool": "restart", "path": "service.state",
                              "confirmé": 0, "démenti": 4}])

        self.assertAlmostEqual(
            runtime._effect_confidence("restart", "service.state"), 0.1)

    def test_a_corrupt_ledger_is_ignored_rather_than_fatal(self):
        """Repartir crédule est un défaut ; refuser de démarrer en est un pire."""
        runtime = Runtime(_agent(MENTEUR), _liar())

        runtime.seed_memory("LONG_TERM", "effect_drift",
                            ["bruit", {"path": "sans outil"}, None])

        self.assertEqual(runtime.drift, {})


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
