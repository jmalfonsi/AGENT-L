"""Tests hors ligne de l'adaptateur System One (examples/jev_llm.py).

Aucun appel réseau : `_post` est remplacé par un script qui rend des
réponses au format de l'API TypeSafe (`answers` par identifiant de question).
"""
from __future__ import annotations

import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from agentl import Host, MockLLM, Runtime, Symbol, parse_source  # noqa: E402
from jev_llm import (JevLLM, field_definition, number_candidates,  # noqa: E402
                     parse_number)


class ScriptedJev(JevLLM):
    """JevLLM dont le transport rend des réponses écrites d'avance."""

    def __init__(self, answers, **kwargs):
        kwargs.setdefault("api_key", "test")
        super().__init__(**kwargs)
        self.scripted = answers
        self.sent = []

    def _post(self, state, questions):
        self.sent.append({"state": state, "questions": questions})
        if isinstance(self.scripted, Exception):
            raise self.scripted
        return {key: self.scripted[key] for key in questions if key in self.scripted}


def choice(value, **probs):
    probs = probs or {value: 1.0}
    return {"type": "choice", "choice": value, "probabilities": probs,
            "confidence": max(probs.values())}


class TestRouting(unittest.TestCase):
    def test_each_field_goes_to_the_primitive_that_fits_its_type(self):
        llm = ScriptedJev({})
        questions, routes, rest = llm._plan("t", {"note": "limit is 5000"}, {
            "category": "Any IN [billing, technical, unknown]",
            "urgent": "Bool",
            "limit": "Number DEFAULT 0",
            "name": "String",
            "confidence": "Number IN [0, 1]",
            "category_confidence": "Number IN [0, 1]",
            "score": "Number IN [0, 10]",
        })
        self.assertEqual(routes, {
            "category": "choice", "urgent": "noul", "limit": "value",
            "name": "fallback", "confidence": "derived",
            "category_confidence": "derived", "score": "fallback"})
        self.assertEqual(rest, ["name", "score"])
        self.assertEqual(set(questions), {"category", "urgent", "limit"})
        self.assertEqual(questions["category"]["type"], "choice")
        self.assertIn("unknown", questions["category"]["criteria"])
        # Une valeur de repli reçoit une description : lue à la lettre, « unknown »
        # ne dit pas quand la préférer.
        self.assertIsNotNone(questions["category"]["criteria"]["unknown"])
        self.assertIn("5000", questions["limit"]["criteria"])
        self.assertIn("none", questions["limit"]["criteria"])

    def test_a_number_with_no_candidate_in_context_goes_to_the_fallback(self):
        _, routes, rest = ScriptedJev({})._plan(
            "t", {"note": "no figures here"}, {"limit": "Number"})
        self.assertEqual(routes["limit"], "fallback")
        self.assertEqual(rest, ["limit"])

    def test_a_lone_confidence_is_an_ordinary_field(self):
        _, routes, rest = ScriptedJev({})._plan(
            "t", {}, {"confidence": "Number IN [0, 1]"})
        self.assertEqual(routes["confidence"], "fallback")
        self.assertEqual(rest, ["confidence"])

    def test_number_candidates_keep_their_surroundings(self):
        found = number_candidates({"a": "Raises above $5,000 need VP sign-off.",
                                   "b": ["score +30 for a demo request"]})
        self.assertIn("$5,000", found)
        self.assertIn("VP sign-off", found["$5,000"])
        self.assertIn("+30", found)
        self.assertEqual(parse_number("$5,000"), 5000.0)
        self.assertEqual(parse_number("15%"), 15.0)
        self.assertIsNone(parse_number("none"))

    def test_thousands_suffix_stays_part_of_the_number(self):
        # « 50k+ » coupé en « 50 » faisait choisir un seuil mille fois trop bas.
        found = number_candidates({"b": "accounts (50k+) and a 24h window"})
        self.assertIn("50k", found)
        self.assertNotIn("50", found)
        self.assertEqual(parse_number("50k"), 50000.0)
        self.assertIn("24", found)

    def test_field_definition_is_read_from_the_task(self):
        task = ("Analyser ce retour. sentiment : le ton général du retour. "
                "vip_domain : le domaine relève-t-il de la définition entreprise.")
        self.assertEqual(field_definition(task, "sentiment"), "le ton général du retour")
        self.assertIsNone(field_definition("report is_procedure = no", "is_procedure"))
        question = ScriptedJev({})._choice(task, "vip_domain", ["yes", "no"])
        self.assertIn("field_definition", question["instructions"])


class TestReason(unittest.TestCase):
    PRODUCE = {"category": "Any IN [billing, technical, unknown]",
               "urgent": "Bool",
               "confidence": "Number IN [0, 1]",
               "category_confidence": "Number IN [0, 1]"}

    def test_closed_fields_are_answered_without_the_fallback(self):
        fallback = MockLLM()
        llm = ScriptedJev({
            "category": choice("billing", billing=0.84, technical=0.16, unknown=0.0),
            "urgent": {"type": "noul", "noul": 0.9},
        }, fallback=fallback)
        out = llm.reason("route", {"msg": "charged twice"}, self.PRODUCE)
        self.assertEqual(out["category"], "billing")
        self.assertIs(out["urgent"], True)
        self.assertAlmostEqual(out["category_confidence"], 0.84)
        # `confidence` global = le maillon le plus faible.
        self.assertAlmostEqual(out["confidence"], 0.84)
        self.assertEqual(llm.last_reason_missing, [])
        self.assertEqual(fallback.calls, [])
        # Un seul appel pour toutes les questions : elles partent en parallèle.
        self.assertEqual(len(llm.sent), 1)

    def test_derived_confidence_is_never_asked_to_the_model(self):
        llm = ScriptedJev({"category": choice("billing")})
        llm.reason("route", {}, self.PRODUCE)
        self.assertNotIn("confidence", llm.sent[0]["questions"])
        self.assertNotIn("category_confidence", llm.sent[0]["questions"])

    def test_open_fields_go_to_the_fallback_and_only_them(self):
        fallback = MockLLM({"route": {"name": "Dana"}})
        llm = ScriptedJev({"category": choice("billing")}, fallback=fallback)
        out = llm.reason("route", {}, {"category": "Any IN [billing, unknown]",
                                       "name": "String"})
        self.assertEqual(out, {"category": "billing", "name": "Dana"})
        self.assertEqual(llm.calls[-1]["routes"]["name"], "fallback")

    def test_open_fields_without_fallback_are_reported_missing(self):
        llm = ScriptedJev({"category": choice("billing")})
        llm.reason("route", {}, {"category": "Any IN [billing, unknown]",
                                 "name": "String"})
        self.assertEqual(llm.last_reason_missing, ["name"])

    def test_value_selection_copies_the_number_written_in_context(self):
        llm = ScriptedJev({"limit": choice("$5,000")})
        out = llm.reason("limit", {"p": "Raises up to $5,000 are processed by HR; "
                                        "the request is for $7,500."},
                         {"limit": "Number DEFAULT 0"})
        self.assertEqual(out["limit"], 5000.0)

    def test_value_selection_none_falls_back(self):
        fallback = MockLLM({"limit": {"limit": 0}})
        llm = ScriptedJev({"limit": choice("none")}, fallback=fallback)
        out = llm.reason("limit", {"p": "Case 42 is open."},
                         {"limit": "Number DEFAULT 0"})
        self.assertEqual(out["limit"], 0.0)
        self.assertEqual(len(fallback.calls), 1)

    def test_uncertain_field_is_escalated_and_keeps_a_calibrated_probability(self):
        fallback = MockLLM({"route": {"category": "technical"}})
        llm = ScriptedJev({"category": choice(
            "billing", billing=0.55, technical=0.45, unknown=0.0)},
            fallback=fallback, escalate_below=0.7)
        out = llm.reason("route", {}, {"category": "Any IN [billing, technical, unknown]",
                                       "category_confidence": "Number IN [0, 1]"})
        self.assertEqual(out["category"], "technical")
        # La confiance rendue est celle que Jev accordait à la valeur retenue.
        self.assertAlmostEqual(out["category_confidence"], 0.45)
        self.assertEqual(llm.calls[-1]["escalated"], ["category"])

    def test_uncertain_field_is_left_to_the_program_default(self):
        fallback = MockLLM({"route": {"category": "technical"}})
        llm = ScriptedJev({"category": choice(
            "billing", billing=0.55, technical=0.45, unknown=0.0)},
            fallback=fallback, abstain_below=0.7, escalate_below=0.7)
        out = llm.reason("route", {}, {"category": "Any IN [billing, technical, unknown]",
                                       "category_confidence": "Number IN [0, 1]"})
        # Ni la valeur de Jev ni celle du repli : le champ est déclaré absent,
        # le runtime appliquera le DEFAULT ; la probabilité reste lisible.
        self.assertEqual(llm.last_reason_missing, ["category"])
        self.assertAlmostEqual(out["category_confidence"], 0.55)
        self.assertEqual(fallback.calls, [])

    def test_confident_field_is_not_escalated(self):
        fallback = MockLLM({"route": {"category": "technical"}})
        llm = ScriptedJev({"category": choice("billing", billing=0.95, technical=0.05)},
                          fallback=fallback, escalate_below=0.7)
        out = llm.reason("route", {}, {"category": "Any IN [billing, technical]"})
        self.assertEqual(out["category"], "billing")
        self.assertEqual(fallback.calls, [])

    def test_jev_outage_hands_the_whole_reason_to_the_fallback(self):
        fallback = MockLLM({"route": {"category": "technical", "confidence": 0.6}})
        llm = ScriptedJev(urllib.error.HTTPError("u", 401, "no", {}, None),
                          fallback=fallback)
        out = llm.reason("route", {}, {"category": "Any IN [billing, technical]",
                                       "confidence": "Number IN [0, 1]"})
        self.assertEqual(out, {"category": "technical", "confidence": 0.6})
        self.assertIn("HTTPError", llm.calls[-1]["jevError"])

    def test_jev_outage_without_fallback_raises_with_everything_missing(self):
        llm = ScriptedJev(urllib.error.HTTPError("u", 401, "no", {}, None))
        with self.assertRaises(urllib.error.HTTPError):
            llm.reason("route", {}, {"category": "Any IN [billing, technical]"})
        self.assertEqual(llm.last_reason_missing, ["category"])


class TestSelectPlan(unittest.TestCase):
    def test_plan_is_chosen_among_candidates(self):
        llm = ScriptedJev({"plan": choice("restart", restart=0.9, escalate=0.1)})
        self.assertEqual(llm.select_plan({}, ["restart", "escalate"]), "restart")

    def test_uncertain_plan_goes_to_the_fallback(self):
        fallback = MockLLM(plan_choice=lambda ctx, cands: "escalate")
        llm = ScriptedJev({"plan": choice("restart", restart=0.5, escalate=0.5)},
                          fallback=fallback, escalate_below=0.8)
        self.assertEqual(llm.select_plan({}, ["restart", "escalate"]), "escalate")


class TestRuntimeIntegration(unittest.TestCase):
    """La confiance dérivée arrive dans l'état comme n'importe quelle sortie
    de REASON — et une POLICY peut la lire."""

    SRC = """
    AGENT Gate {
        GOAL g { MAINTAIN done == yes }
        TOOL act { INPUT { k: Symbol } OUTPUT { done: Symbol } RISK HIGH }
        POLICY { DEFAULT DENY  ALLOW act IF kind_confidence >= 0.90 }
        PLAN p WHEN 1 == 1 {
            STEP s {
                REASON { TASK "classify"
                         PRODUCE { kind IN [scan, stuffing, unknown]
                                   kind_confidence: Number IN [0, 1] } }
                act(k = kind)
            }
        }
    }
    """

    def run_with(self, probs):
        agent = parse_source(self.SRC).agents[0]
        host = Host()
        host.tools["act"] = lambda k: {"done": Symbol("yes")}
        value = max(probs, key=probs.get)
        llm = ScriptedJev({"kind": choice(value, **probs)})
        return Runtime(agent, host, llm).run(max_ticks=1)

    def test_confident_judgment_passes_the_gate(self):
        runtime = self.run_with({"scan": 0.97, "stuffing": 0.03, "unknown": 0.0})
        self.assertEqual(runtime.metrics["tool_calls"], 1)
        self.assertAlmostEqual(runtime.state.get("kind_confidence"), 0.97)

    def test_uncertain_judgment_is_held_by_the_policy(self):
        runtime = self.run_with({"scan": 0.6, "stuffing": 0.4, "unknown": 0.0})
        self.assertEqual(runtime.metrics["tool_calls"], 0)
        self.assertAlmostEqual(runtime.state.get("kind_confidence"), 0.6)


if __name__ == "__main__":
    unittest.main()
