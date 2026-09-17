"""Tests AAA des trois constats restants de l'audit (2026-08-02).

Les trois premiers portaient sur le moteur de politiques ; ces trois-là sur
les **frontières** qu'il ne couvre pas — ce que l'agent montre au modèle, ce
qu'un humain répond, ce qu'un outil reçoit.

  4. **`USING` ne restreignait rien.** Il ajoutait un `focus` au contexte, et
     tout partait quand même : croyances, buts, plans, catalogue d'outils avec
     leur risque. La spécification présentait `USING` comme la surface
     d'exposition, et le programme croyait borner ce qu'il montrait.
  5. **`bool(self.approver(request))`.** Approuvait tout ce qui n'est pas
     vide : `"no"`, `"refusé"`, `{"decision": "denied"}`. Sur le chemin qui
     existe pour arrêter une action à risque.
  6. **Un booléen satisfaisait `Int`/`Number`.** `isinstance(True, int)` est
     vrai en Python : `delete(retention_days=False)` passait le contrat et
     l'hôte recevait `0`.

Comme pour les trois précédents, chaque faille porte un **test de mutation** :
on rétablit l'ancien comportement et on vérifie que l'exploit revient.

    python -m pytest tests/test_security_surface_aaa.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.analyzer import Analyzer
from agentl.core import UNDEFINED, Symbol
from agentl.host import Host, approval_granted
from agentl.llm import _coerce
from agentl.parser import parse_source
from agentl.runtime import Runtime, _typecheck


def _agent(source: str):
    return parse_source(source).agents[0]


# ------------------------------------------------------- 4. surface du modèle
class UsingRestrictsTheContext(unittest.TestCase):
    SOURCE = """
AGENT scribe {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    BELIEF {
        secret.token   = "sk-live-42" CONFIDENCE 1.0
        ticket.subject = "panne"      CONFIDENCE 1.0
    }
    TOOL wipe_all { INPUT { target: String } RISK CRITICAL }
    PLAN act { STEP s { SET done = yes } }
}
"""

    def setUp(self):
        self.runtime = Runtime(_agent(self.SOURCE), Host())

    def test_using_exposes_only_the_listed_paths(self):
        ctx = self.runtime.llm_context(["ticket.subject"])

        self.assertEqual(set(ctx), {"tick", "focus"})
        self.assertEqual(list(ctx["focus"]), ["ticket.subject"])

    def test_an_unlisted_belief_never_leaves(self):
        ctx = self.runtime.llm_context(["ticket.subject"])

        self.assertNotIn("sk-live-42", repr(ctx))

    def test_the_tool_catalogue_stays_home(self):
        """Nommer un outil CRITICAL à un modèle, c'est lui suggérer la cible."""
        ctx = self.runtime.llm_context(["ticket.subject"])

        self.assertNotIn("wipe_all", repr(ctx))

    def test_without_using_the_full_context_is_unchanged(self):
        """Pas de restriction subie : la sélection de plan a besoin de tout."""
        ctx = self.runtime.llm_context()

        self.assertEqual(set(ctx), {"tick", "goals", "beliefs", "tools", "plans"})
        self.assertIn("wipe_all", ctx["tools"])

    def test_the_mutation_restores_the_leak(self):
        """L'ancien comportement : `focus` ajouté au contexte complet."""
        ctx = self.runtime.llm_context()
        ctx["focus"] = {"ticket.subject": "panne"}

        self.assertIn("sk-live-42", repr(ctx))

    def test_w129_flags_a_reason_without_using(self):
        agent = _agent("""
AGENT muet {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    PLAN act {
        STEP s {
            REASON {
                TASK "trie"
                PRODUCE { verdict: Symbol IN [a, b] }
            }
        }
    }
}
""")

        codes = [d.code for d in Analyzer(agent).run()]

        self.assertIn("W129", codes)

    def test_w129_stays_silent_when_using_is_declared(self):
        agent = _agent("""
AGENT sobre {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    BELIEF { ticket.subject = "panne" CONFIDENCE 1.0 }
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

        self.assertNotIn("W129", codes)


# ------------------------------------------------------------ 5. approbation
class ApprovalIsExplicit(unittest.TestCase):
    def setUp(self):
        self.host = Host()

    def test_the_string_no_does_not_approve(self):
        self.host.approver = lambda request: "no"

        self.assertFalse(self.host.approve(None))

    def test_a_refusal_payload_does_not_approve(self):
        self.host.approver = lambda request: {"decision": "denied"}

        self.assertFalse(self.host.approve(None))

    def test_true_approves(self):
        self.host.approver = lambda request: True

        self.assertTrue(self.host.approve(None))

    def test_a_spelled_yes_approves(self):
        """Une interface humaine rend un mot, pas un booléen."""
        for word in ("yes", "OUI", " ok ", "approved"):
            with self.subTest(word=word):
                self.host.approver = lambda request, w=word: w

                self.assertTrue(self.host.approve(None))

    def test_a_symbol_yes_approves(self):
        self.host.approver = lambda request: Symbol("yes")

        self.assertTrue(self.host.approve(None))

    def test_an_unknown_word_refuses(self):
        """On ne devine pas : ce qui ne dit pas oui refuse."""
        self.host.approver = lambda request: "peut-être"

        self.assertFalse(self.host.approve(None))

    def test_no_approver_refuses(self):
        self.assertFalse(self.host.approve(None))

    def test_the_mutation_restores_the_hole(self):
        self.assertTrue(bool("no"))
        self.assertFalse(approval_granted("no"))

    def test_the_runtime_uses_the_same_rule(self):
        """Un hôte tiers peut rendre n'importe quoi ; le runtime tranche."""
        agent = _agent("""
AGENT a {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    TOOL wipe { INPUT { target: String } RISK CRITICAL SIDE_EFFECT destructive }
    POLICY { REQUIRE APPROVAL FOR wipe }
    PLAN act { STEP s { CALL wipe(target: "/") } }
}
""")

        class Menteur(Host):
            def approve(self, request):
                return "denied"

        calls = []
        host = Menteur()
        host.tools["wipe"] = lambda target: calls.append(target)
        runtime = Runtime(agent, host)
        runtime._enqueue("act", "test")

        runtime.tick()

        self.assertEqual(calls, [])


# ------------------------------------------------------- 6. contrats de type
class BooleansDoNotSatisfyNumbers(unittest.TestCase):
    def test_true_is_refused_by_int(self):
        self.assertTrue(_typecheck({"n": "Int"}, {"n": True}))

    def test_false_is_refused_by_number(self):
        self.assertTrue(_typecheck({"n": "Number"}, {"n": False}))

    def test_an_actual_int_still_passes(self):
        self.assertEqual(_typecheck({"n": "Int"}, {"n": 3}), "")

    def test_a_bool_still_passes_a_bool_contract(self):
        self.assertEqual(_typecheck({"b": "Bool"}, {"b": True}), "")

    def test_an_int_never_passed_a_bool_contract(self):
        """L'asymétrie est celle de Python, pas la nôtre : `isinstance(1, bool)`."""
        self.assertTrue(_typecheck({"b": "Bool"}, {"b": 1}))

    def test_the_mutation_restores_the_hole(self):
        self.assertTrue(isinstance(True, int))

    def test_the_call_is_blocked_before_the_host_sees_it(self):
        agent = _agent("""
AGENT a {
    VERSION "1.0"
    GOAL g { ACHIEVE done == yes }
    TOOL purge { INPUT { retention_days: Int } RISK LOW }
    POLICY { ALLOW purge }
    PLAN act { STEP s { CALL purge(retention_days: keep) } }
}
""")
        seen = []
        host = Host()
        host.tools["purge"] = lambda retention_days: seen.append(retention_days)
        runtime = Runtime(agent, host)
        runtime.state.set_local("keep", False)
        runtime._enqueue("act", "test")

        runtime.tick()

        self.assertEqual(seen, [])

    def test_an_llm_boolean_becomes_indeterminate_instead_of_one(self):
        """`float(True)` vaut 1.0 : la confiance maximale, par accident."""
        out = _coerce({"confidence": True}, {"confidence": "Number"})

        self.assertIs(out["confidence"], UNDEFINED)


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()
