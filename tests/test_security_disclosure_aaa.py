"""Tests AAA des deux faiblesses traitées en v1.8.

Elles ne viennent pas d'un contournement mais d'un **trou de déclaration** :
le programme n'avait aucun moyen de dire ce qu'il voulait, et le runtime
choisissait à sa place.

  9. **Fuite vers le fournisseur de modèle.** `USING` borne ce qu'un `REASON`
     montre ; il n'a jamais rien borné de `select_plan`, qui envoie
     l'intégralité des croyances. Un programme ne pouvait donc pas retenir un
     secret : la seule barrière disponible ne couvrait pas le point de sortie
     le plus large. `POLICY { NEVER SEND <chemin> }` porte sur **toutes** les
     sorties.
  5. **Blocages conservateurs.** Un capteur muet laissait le chemin indéfini.
     Les gardes échouaient fermé — la bonne direction — mais sans une ligne de
     trace : une panne de capteur et un programme mal écrit produisaient le
     même silence. `ON UNKNOWN ESCALATE | DEGRADE <valeur>` rend la conduite
     déclarée, et `W131`/`W132` signalent les deux versants du choix.

Comme pour l'audit du 2026-08-02, chaque correctif porte un **test de
mutation** : on rétablit l'ancien comportement et on vérifie que le défaut
revient. Un test qui passerait aussi bien sans le correctif ne prouve rien.

    python -m pytest tests/test_security_disclosure_aaa.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.analyzer import Analyzer
from agentl.host import Host
from agentl.parser import parse_source
from agentl.runtime import Runtime


def _agent(source: str):
    return parse_source(source).agents[0]


# ============================================ 9. sortie vers le fournisseur
REDACTING = """
AGENT scribe {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    BELIEF {
        secret.token   = "sk-live-42" CONFIDENCE 1.0
        secret.pin     = "4242"       CONFIDENCE 1.0
        ticket.subject = "panne"      CONFIDENCE 1.0
    }
    TOOL noop { INPUT { x: String } RISK LOW }
    POLICY {
        NEVER SEND secret
        ALLOW noop
    }
    PLAN act { STEP s { SET done = yes } }
}
"""


class NeverSendCoversEveryExit(unittest.TestCase):
    def setUp(self):
        self.runtime = Runtime(_agent(REDACTING), Host())

    def test_the_secret_never_reaches_the_full_context(self):
        """`select_plan` envoie tout : c'est là que `USING` ne pouvait rien."""
        ctx = self.runtime.llm_context()

        self.assertNotIn("sk-live-42", repr(ctx))

    def test_the_prefix_covers_the_leaves(self):
        """`NEVER SEND secret` retient `secret.token` **et** `secret.pin`."""
        ctx = self.runtime.llm_context()

        self.assertNotIn("4242", repr(ctx))

    def test_the_key_remains_visible_as_withheld(self):
        """Une clé disparue se lit comme une absence de donnée ; le modèle
        doit savoir qu'il raisonne sur un état amputé."""
        ctx = self.runtime.llm_context()

        self.assertIn("secret.token", ctx["beliefs"])
        self.assertEqual(ctx["beliefs"]["secret.token"]["value"],
                         Runtime.REDACTED)

    def test_what_is_not_redacted_still_travels(self):
        ctx = self.runtime.llm_context()

        self.assertEqual(ctx["beliefs"]["ticket.subject"]["value"], "panne")

    def test_using_does_not_defeat_the_redaction(self):
        """`USING` désigne ce qui est utile, pas ce qui est autorisé à sortir.
        Deux déclarations qui se contredisent : la sûreté tranche."""
        ctx = self.runtime.llm_context(["secret.token"])

        self.assertNotIn("sk-live-42", repr(ctx))
        self.assertEqual(ctx["focus"]["secret.token"], Runtime.REDACTED)

    def test_the_redaction_is_traced(self):
        """Une donnée retenue est une décision du moteur : elle se journalise
        comme un blocage, pas comme un silence."""
        self.runtime.llm_context()

        blocked = [e for e in self.runtime.trace.events
                   if e.kind == "BLOCKED" and "NEVER SEND" in e.text]
        self.assertTrue(blocked)
        self.assertGreater(self.runtime.metrics["redactions"], 0)

    def test_the_mutation_restores_the_leak(self):
        """L'ancien comportement : aucune redaction déclarable."""
        self.runtime.agent.redactions = []

        ctx = self.runtime.llm_context()

        self.assertIn("sk-live-42", repr(ctx))

    def test_a_sibling_prefix_is_not_swallowed(self):
        """`NEVER SEND secret` ne doit pas retenir `secrets_publics`."""
        runtime = Runtime(_agent("""
AGENT scribe {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    BELIEF { secretaire.nom = "dupont" CONFIDENCE 1.0 }
    POLICY { NEVER SEND secret }
    PLAN act { STEP s { SET done = yes } }
}
"""), Host())

        ctx = runtime.llm_context()

        self.assertEqual(ctx["beliefs"]["secretaire.nom"]["value"], "dupont")


class RedactionDiagnostics(unittest.TestCase):
    def test_w130_flags_a_using_that_contradicts_a_never_send(self):
        agent = _agent("""
AGENT scribe {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    BELIEF { secret.token = "sk" CONFIDENCE 1.0 }
    POLICY { NEVER SEND secret }
    PLAN act {
        STEP s {
            REASON {
                TASK "trie"
                USING { secret.token }
                PRODUCE { verdict: Symbol IN [a, b] }
            }
        }
    }
}
""")

        codes = [d.code for d in Analyzer(agent).run()]

        self.assertIn("W130", codes)

    def test_a_coherent_using_is_silent(self):
        agent = _agent("""
AGENT scribe {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    BELIEF { ticket.subject = "panne" CONFIDENCE 1.0 }
    POLICY { NEVER SEND secret }
    PLAN act {
        STEP s {
            REASON {
                TASK "trie"
                USING { ticket.subject }
                PRODUCE { verdict: Symbol IN [a, b] }
            }
        }
    }
}
""")

        codes = [d.code for d in Analyzer(agent).run()]

        self.assertNotIn("W130", codes)


# ================================================= 5. capteur muet, conduite
def _mute_host():
    """Un hôte dont le capteur ne rend rien — la panne, pas l'erreur."""
    class Mute(Host):
        def read(self, path):
            return None
    return Mute()


SENSOR = """
AGENT vigie {{
    VERSION "1.0"
    GOAL g {{ ACHIEVE done == yes }}
    OBSERVE {{ asset.criticality{clause} }}
    TOOL wipe {{ INPUT {{ target: String }} RISK CRITICAL }}
    POLICY {{
        NEVER wipe IF asset.criticality == CRITICAL
        ALLOW wipe
    }}
    PLAN act {{ STEP s {{ SET done = yes }} }}
}}
"""


class AMuteSensorIsAnEvent(unittest.TestCase):
    def _run(self, clause: str) -> Runtime:
        runtime = Runtime(_agent(SENSOR.format(clause=clause)), _mute_host())
        runtime.phase_observe()
        return runtime

    def test_the_silence_is_named_in_the_trace(self):
        """Sans ON UNKNOWN, le comportement ne change pas — mais il se voit.
        Une panne de capteur et un programme mal écrit ne doivent pas
        produire le même silence."""
        runtime = self._run("")

        muet = [e for e in runtime.trace.events if "capteur muet" in e.text]
        self.assertTrue(muet)
        self.assertEqual(runtime.metrics["sensor_unknown"], 1)

    def test_the_outage_is_readable_by_a_guard(self):
        """`sensors.<chemin>.available` : le programme peut décider lui-même
        quoi faire d'un capteur mort."""
        runtime = self._run("")

        self.assertIs(
            runtime.state.world.get("sensors.asset.criticality.available"),
            False)

    def test_without_on_unknown_the_path_stays_undefined(self):
        """Le fail-closed n'est pas assoupli : rien n'est inventé."""
        runtime = self._run("")

        self.assertNotIn("asset.criticality", runtime.state.world)

    def test_escalate_asks_a_human(self):
        asked = []

        class Asking(Host):
            def read(self, path):
                return None

            def ask(self, question, reason=""):
                asked.append(question)
                return None

        runtime = Runtime(_agent(SENSOR.format(clause=" ON UNKNOWN ESCALATE")),
                          Asking())
        runtime.phase_observe()

        self.assertTrue(any("asset.criticality" in q for q in asked))

    def test_escalate_wakes_the_operator_once_per_outage(self):
        """Un capteur mort réveillerait l'astreinte à chaque tick, et une
        alerte répétée n'est plus lue."""
        asked = []

        class Asking(Host):
            def read(self, path):
                return None

            def ask(self, question, reason=""):
                asked.append(question)
                return None

        runtime = Runtime(_agent(SENSOR.format(clause=" ON UNKNOWN ESCALATE")),
                          Asking())
        runtime.phase_observe()
        runtime.phase_observe()
        runtime.phase_observe()

        self.assertEqual(len(asked), 1)

    def test_degrade_substitutes_the_declared_value(self):
        runtime = self._run(" ON UNKNOWN DEGRADE CRITICAL")

        self.assertEqual(str(runtime.state.world["asset.criticality"]),
                         "CRITICAL")
        self.assertEqual(runtime.metrics["sensor_degraded"], 1)

    def test_a_degraded_value_is_not_a_measurement(self):
        """Confiance basse et source `fallback` : une garde peut distinguer
        une mesure d'une hypothèse."""
        runtime = self._run(" ON UNKNOWN DEGRADE CRITICAL")

        belief = runtime.state.beliefs["asset.criticality"]
        self.assertEqual(belief.source, "fallback")
        self.assertLess(belief.confidence, 0.5)

    def test_a_recovered_sensor_clears_the_outage(self):
        runtime = Runtime(_agent(SENSOR.format(clause="")), _mute_host())
        runtime.phase_observe()

        runtime.host.read = lambda path: "LOW"
        runtime.phase_observe()

        self.assertIs(
            runtime.state.world.get("sensors.asset.criticality.available"),
            True)


class SensorDiagnostics(unittest.TestCase):
    def test_w131_flags_a_guarded_path_without_a_declared_conduct(self):
        codes = [d.code for d in
                 Analyzer(_agent(SENSOR.format(clause=""))).run()]

        self.assertIn("W131", codes)

    def test_w132_flags_a_fallback_under_a_prohibition(self):
        """Le versant dangereux : un repli mal choisi rouvre §7.1 — une
        donnée absente qui n'active plus le NEVER, cette fois avec la
        bénédiction du programme."""
        codes = [d.code for d in
                 Analyzer(_agent(SENSOR.format(
                     clause=" ON UNKNOWN DEGRADE CRITICAL"))).run()]

        self.assertIn("W132", codes)
        self.assertNotIn("W131", codes)

    def test_escalate_satisfies_w131(self):
        codes = [d.code for d in
                 Analyzer(_agent(SENSOR.format(
                     clause=" ON UNKNOWN ESCALATE"))).run()]

        self.assertNotIn("W131", codes)
        self.assertNotIn("W132", codes)

    def test_an_unguarded_sensor_is_not_flagged(self):
        """W131 ne porte que sur les chemins dont dépend un interdit : crier
        sur tous les capteurs ferait ignorer le signal."""
        agent = _agent("""
AGENT vigie {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    OBSERVE { weather.temp }
    PLAN act { STEP s { SET done = yes } }
}
""")

        codes = [d.code for d in Analyzer(agent).run()]

        self.assertNotIn("W131", codes)


if __name__ == "__main__":
    unittest.main()
