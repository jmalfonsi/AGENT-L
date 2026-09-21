"""Tests hors ligne du frontal Laya (examples/laya_llm.py) et de sa cascade.

Aucun appel réseau : le transport de Laya (`_post`) et celui de Jev sont
remplacés par des réponses écrites d'avance, au format des deux services.
"""
from __future__ import annotations

import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from agentl import MockLLM  # noqa: E402
from jev_llm import JevLLM  # noqa: E402
from laya_llm import LayaRouter, language, probability, to_laya  # noqa: E402

MESSAGE_EN = {"tick": 1, "focus": {"ticket.body": "The app crashes when I open a report. Fix it today please."}}
MESSAGE_FR = {"tick": 1, "focus": {"ticket.body": "L'application plante à chaque ouverture d'un rapport, corrigez-la aujourd'hui."}}


def judge_q(question, **criteria):
    """Une question de JUDGE, telle que `JevLLM.judge` la transmet."""
    kind = "choice" if criteria else "noul"
    q = {"type": kind, "instructions": {"question": question, "context": "Trier"}}
    if criteria:
        q["criteria"] = criteria
    return q


TEAM = judge_q("Which team should handle this message?",
               billing="invoices, charges, refunds", technical="bugs, crashes, outages",
               account="login, password, profile")


class ScriptedLaya(LayaRouter):
    def __init__(self, answers, fail=None, **kw):
        super().__init__(url="http://laya.test", **kw)
        self.scripted, self.fail, self.sent = answers, fail, []

    def _post(self, state, questions, checkpoint):
        self.sent.append({"checkpoint": checkpoint, "state": state,
                          "questions": questions})
        if self.fail:
            raise self.fail
        return {k: self.scripted[k] for k in questions if k in self.scripted}


class ScriptedJev(JevLLM):
    def __init__(self, answers, fail=None, **kw):
        kw.setdefault("api_key", "test")
        super().__init__(**kw)
        self.scripted, self.fail, self.sent = answers, fail, []

    def _post(self, state, questions):
        self.sent.append(sorted(questions))
        if self.fail:
            raise self.fail
        return {k: self.scripted[k] for k in questions if k in self.scripted}


def choice(value, p):
    return {"type": "choice", "choice": value, "probabilities": {value: p},
            "confidence": p}


class TestLanguage(unittest.TestCase):
    def test_english_french_and_other_scripts(self):
        self.assertEqual(language("The app crashes when I open a report, please fix it."), "en")
        self.assertEqual(language("L'application plante à chaque ouverture, corrigez-la."), "other")
        self.assertEqual(language("Приложение падает при открытии отчёта"), "other")
        self.assertEqual(language("ok 42"), "unknown")


class TestRoute(unittest.TestCase):
    def setUp(self):
        self.router = LayaRouter(url="http://laya.test")

    def test_short_english_judgement_goes_to_typed_decisions(self):
        route = self.router.route(MESSAGE_EN, TEAM)
        self.assertEqual(route.checkpoint, "typed-decisions")
        # La question de l'auteur part telle quelle, en chaîne.
        self.assertEqual(route.question["instructions"],
                         "Which team should handle this message?")

    def test_any_other_language_goes_to_multilingual(self):
        self.assertEqual(self.router.route(MESSAGE_FR, TEAM).checkpoint, "multilingual")
        french_question = judge_q("Quelle équipe doit traiter ce message ?",
                                  billing="factures", technical="pannes", account="compte")
        self.assertEqual(self.router.route(MESSAGE_EN, french_question).checkpoint,
                         "multilingual")

    def test_non_english_can_be_kept_away_from_laya(self):
        router = LayaRouter(url="http://laya.test", other_model=None)
        self.assertIsNone(router.route(MESSAGE_FR, TEAM).checkpoint)

    def test_a_long_state_stays_with_jev(self):
        big = {"tick": 1, "focus": {"doc": "The contract says many things. " * 20}}
        route = self.router.route(big, TEAM)
        self.assertIsNone(route.checkpoint)
        self.assertIn("état long", route.why)

    def test_a_long_question_stays_with_jev(self):
        long_q = judge_q("In `a` and in that message only, which decision does the "
                         "current manager express about the transfer, ignoring "
                         "the receiving manager's reply entirely?", yes="y", no="n")
        self.assertIn("question longue", self.router.route(MESSAGE_EN, long_q).why)

    def test_too_many_options_stay_with_jev(self):
        many = judge_q("Which intent?", **{f"intent{i}": "" for i in range(10)})
        self.assertIn("options", self.router.route(MESSAGE_EN, many).why)

    def test_reason_fields_need_an_explicit_opt_in(self):
        field = {"type": "choice", "criteria": {"billing": None, "technical": None},
                 "instructions": {"task": "Classify.", "field": "category",
                                  "question": "Carry out `task` on the state. Which option?"}}
        self.assertIn("REASON", self.router.route(MESSAGE_EN, field).why)
        opted = LayaRouter(url="http://laya.test", reason_fields=True)
        route = opted.route(MESSAGE_EN, field)
        self.assertEqual(route.checkpoint, "typed-decisions")
        self.assertEqual(route.task, "Classify.")   # la consigne passe dans l'état

    def test_to_laya_keeps_the_field_definition_in_the_short_question(self):
        field = {"type": "noul", "instructions": {
            "task": "t", "field": "urgent", "field_definition": "action needed today",
            "question": "Carry out `task` on the state. Is `field` true?"}}
        question, task, from_reason = to_laya(field)
        self.assertTrue(from_reason)
        self.assertEqual(question["instructions"],
                         "Value of `urgent` (action needed today): is it true?")

    def test_probability_of_each_answer_type(self):
        self.assertAlmostEqual(probability({"noul": 0.2}), 0.8)
        self.assertEqual(probability(choice("a", 0.7)), 0.7)
        self.assertEqual(probability({"score": 1.8, "probabilities": {"2": 0.6}}), 0.6)


class TestCascade(unittest.TestCase):
    def judge(self, llm, context=MESSAGE_EN):
        payload = {"team": {"kind": "CHOICE", "instructions": "Which team should handle this message?",
                            "criteria": TEAM["criteria"]},
                   "urgent": {"kind": "NOUL", "instructions": "Does the customer need action today?"}}
        return llm.judge("Trier", context, payload)

    def test_confident_local_answers_never_reach_jev(self):
        laya = ScriptedLaya({"team": choice("technical", 0.9), "urgent": {"noul": 0.95}})
        jev = ScriptedJev({}, local=laya)
        out = self.judge(jev)
        self.assertEqual(out["team"]["value"], "technical")
        self.assertEqual(out["team"]["p"], 0.9)
        self.assertIs(out["urgent"]["value"], True)
        self.assertEqual(jev.sent, [])
        self.assertEqual(jev.calls[-1]["engines"],
                         {"team": "laya:typed-decisions", "urgent": "laya:typed-decisions"})

    def test_a_weak_local_answer_is_asked_again_to_jev(self):
        laya = ScriptedLaya({"team": choice("billing", 0.5), "urgent": {"noul": 0.95}})
        jev = ScriptedJev({"team": choice("technical", 0.97)}, local=laya)
        out = self.judge(jev)
        self.assertEqual(out["team"]["value"], "technical")
        self.assertEqual(jev.sent, [["team"]])
        self.assertEqual(jev.calls[-1]["engines"]["team"], "jev (cascade)")

    def test_laya_down_means_jev_answers_everything(self):
        laya = ScriptedLaya({}, fail=urllib.error.URLError("refused"))
        jev = ScriptedJev({"team": choice("technical", 0.97), "urgent": {"noul": 0.9}},
                          local=laya)
        out = self.judge(jev)
        self.assertEqual(out["team"]["value"], "technical")
        self.assertEqual(jev.sent, [["team", "urgent"]])
        self.assertEqual(len(laya.sent), 1)        # une tentative, pas trois

    def test_a_refused_connection_opens_the_breaker(self):
        # Service arrêté : les jugements suivants vont droit à Jev, sans
        # payer une connexion refusée chacun.
        laya = ScriptedLaya({}, fail=urllib.error.URLError("refused"), cooldown=60)
        jev = ScriptedJev({"team": choice("technical", 0.97), "urgent": {"noul": 0.9}},
                          local=laya)
        self.judge(jev)
        self.judge(jev)
        self.assertEqual(len(laya.sent), 1)
        self.assertIn("indisponible", jev.calls[-1]["localRoutes"]["team"])

    def test_a_weak_local_answer_is_not_kept_when_jev_is_down(self):
        # Sous le seuil, Laya n'a pas répondu : Jev tombé, le repli génératif
        # répond sans probabilité — et `ABSTAIN BELOW` fermera ce qui l'exige.
        laya = ScriptedLaya({"team": choice("billing", 0.5), "urgent": {"noul": 0.95}})
        fallback = MockLLM({"Trier": {"team": "account"}})
        jev = ScriptedJev({}, fail=urllib.error.HTTPError("u", 401, "down", {}, None),
                          local=laya, fallback=fallback)
        out = self.judge(jev)
        self.assertIs(out["urgent"]["value"], True)       # local et sûr : gardé
        self.assertEqual(out["team"], {"value": "account"})  # sans p

    def test_a_fallback_outage_keeps_the_local_judgements(self):
        class Down(MockLLM):
            def reason(self, task, context, produce):
                raise urllib.error.HTTPError("u", 503, "down", {}, None)

        laya = ScriptedLaya({"team": choice("billing", 0.5), "urgent": {"noul": 0.95}})
        jev = ScriptedJev({}, fail=urllib.error.HTTPError("u", 401, "down", {}, None),
                          local=laya, fallback=Down())
        out = self.judge(jev)
        self.assertIs(out["urgent"]["value"], True)
        self.assertNotIn("team", out)                      # absent : DEFAULT

    def test_long_contexts_go_straight_to_jev(self):
        laya = ScriptedLaya({})
        jev = ScriptedJev({"team": choice("technical", 0.97), "urgent": {"noul": 0.9}},
                          local=laya)
        self.judge(jev, {"tick": 1, "focus": {"doc": "Long report text. " * 60}})
        self.assertEqual(laya.sent, [])
        self.assertEqual(jev.sent, [["team", "urgent"]])

    def test_plan_selection_never_goes_local(self):
        laya = ScriptedLaya({"plan": choice("a", 0.99)})
        jev = ScriptedJev({"plan": choice("b", 0.9)}, local=laya)
        self.assertEqual(jev.select_plan(MESSAGE_EN, ["a", "b"]), "b")
        self.assertEqual(laya.sent, [])

    def test_reason_closed_fields_stay_with_jev_by_default(self):
        laya = ScriptedLaya({"category": choice("billing", 0.99)})
        jev = ScriptedJev({"category": choice("technical", 0.9)}, local=laya)
        out = jev.reason("Classify the ticket.", MESSAGE_EN,
                         {"category": "Any IN [billing, technical]"})
        self.assertEqual(out["category"], "technical")
        self.assertEqual(laya.sent, [])

    def test_opted_in_reason_fields_carry_the_task_in_the_state(self):
        laya = ScriptedLaya({"category": choice("technical", 0.9)}, reason_fields=True)
        jev = ScriptedJev({}, local=laya)
        out = jev.reason("Classify the ticket.", MESSAGE_EN,
                         {"category": "Any IN [billing, technical]"})
        self.assertEqual(out["category"], "technical")
        self.assertEqual(laya.sent[0]["state"],
                         {"task": "Classify the ticket.", "state": MESSAGE_EN})


if __name__ == "__main__":
    unittest.main()
