"""Tests AAA de `JUDGE` — le jugement fermé (v1.10).

`REASON` demande au modèle de remplir un schéma ; `JUDGE` lui pose des
questions dont les réponses sont énumérées, et fait entrer la **probabilité**
de la réponse dans l'état. Les propriétés visées sont celles qui distinguent
les deux :

  * la question part avec le champ — jamais son seul nom ;
  * `judge.<champ>.p` n'existe que si l'oracle sait le calibrer : un oracle
    génératif laisse la probabilité indéterminée plutôt que de l'inventer ;
  * `ABSTAIN BELOW s` ferme : sous le seuil — ou faute de probabilité — la
    réponse n'est pas retenue et le `DEFAULT` s'applique ;
  * un oracle qui lève, ment sur le domaine ou se tait ne produit aucune
    valeur non gouvernée ;
  * les contrôles statiques refusent une question qui ne demande rien (E017)
    et signalent un jugement qui garde un interdit sans traiter son doute
    (W136).

    python -m pytest tests/test_judge_aaa.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl import Host, MockLLM, Runtime, UNDEFINED, parse_source
from agentl.analyzer import Analyzer
from agentl.core import ParseError
from agentl.llm import LLM
from agentl.nodes import JudgeStmt


JUDGE = '''
      JUDGE "Trier la mention" {
        USING { m.content }
        holds_negative: NOUL "Le message demande-t-il de SUSPENDRE les
                              réponses aux mentions négatives ?"
          DEFAULT false
        kind: CHOICE "De quoi cette mention parle-t-elle ?" {
          question: "elle pose une question sur le produit"
          enterprise_inquiry: "elle exprime un besoin d'entreprise"
          other: "aucune des deux"
        } ABSTAIN BELOW 0.70 DEFAULT other
        frustration: SCORE "À quel point l'auteur est-il mécontent ?" [
          "calme et factuel",
          "agacé mais courtois",
          "très mécontent"
        ]
      }
'''


def _program(judge: str = JUDGE, policy: str = "DEFAULT ALLOW") -> str:
    return f'''
AGENT triage {{
  VERSION "1.10"
  GOAL fini {{ MAINTAIN m.done == yes }}
  TOOL reply {{ INPUT {{ text: String }} SIDE_EFFECT yes RISK MODERATE }}
  POLICY {{
    {policy}
  }}
  PLAN classer WHEN m.done != yes {{
    STEP juger {{
{judge}
      reply(text: "ok")
    }}
  }}
  LOOP MAX 1 {{
    SELECT_PLAN
    EXECUTE
  }}
}}
'''


def _agent(source: str):
    return parse_source(source).agents[0]


def _statement(agent) -> JudgeStmt:
    return agent.plans[0].steps[0].body[0]


def _runtime(agent, llm) -> Runtime:
    host = Host()
    host.tool("reply")(lambda text: {"sent": True})
    runtime = Runtime(agent, host, llm)
    runtime.state.set_local("m.content", "Vous proposez un plan équipe ?")
    return runtime


def _run(agent, llm) -> Runtime:
    runtime = _runtime(agent, llm)
    runtime.run(max_ticks=1)
    return runtime


class _Generative(LLM):
    """Oracle qui ne sait que raisonner en texte : aucune probabilité."""

    def __init__(self, answers):
        self.answers = answers
        self.prompts = []
        self.last_reason_missing = None

    def reason(self, task, context, produce):
        self.prompts.append(task)
        from agentl.llm import _coerce, missing_from
        self.last_reason_missing = missing_from(self.answers, produce)
        return _coerce({k: v for k, v in self.answers.items() if k in produce},
                       produce)


class _Exploding(LLM):
    def reason(self, task, context, produce):
        raise RuntimeError("oracle injoignable")

    def judge(self, task, context, questions):
        raise RuntimeError("oracle injoignable")


# ===================================================================== SYNTAXE
def test_the_questions_become_a_bounded_produce():
    """Un `JUDGE` **est** un `REASON` : type, domaine et DEFAULT en découlent,
    donc tout ce qui borne déjà une sortie de modèle s'y applique."""
    stmt = _statement(_agent(_program()))

    assert isinstance(stmt, JudgeStmt)
    assert stmt.produce == {"holds_negative": "Bool", "kind": "Symbol",
                            "frustration": "Number"}
    assert stmt.domains["kind"].render() == "[question, enterprise_inquiry, other]"
    assert stmt.domains["frustration"].render() == "[0, 2]"
    assert set(stmt.defaults) == {"holds_negative", "kind"}
    assert stmt.questions["kind"].abstain_below == 0.70
    assert stmt.using == ["m.content"]


def test_a_question_without_its_wording_is_refused():
    """Le nom du champ n'est pas la question : c'est la raison d'être de la
    primitive, donc une erreur de syntaxe et non un défaut de style."""
    with pytest.raises(ParseError):
        parse_source(_program('      JUDGE { urgent: NOUL }'))


def test_an_unknown_primitive_is_refused():
    with pytest.raises(ParseError):
        parse_source(_program('      JUDGE { urgent: VIBE "vraiment ?" }'))


def test_a_field_declared_twice_is_refused():
    with pytest.raises(ParseError):
        parse_source(_program(
            '      JUDGE { a: NOUL "x ?" a: NOUL "y ?" }'))


# ==================================================================== RUNTIME
def test_the_answer_and_its_probability_enter_the_state():
    agent = _agent(_program())
    llm = MockLLM({"Trier": {
        "holds_negative": {"value": False, "p": 0.97},
        "kind": {"value": "enterprise_inquiry", "p": 0.88, "confidence": 0.71},
        "frustration": {"value": 0.4, "p": 0.83}}})

    runtime = _run(agent, llm)

    assert runtime.state.get("kind") == "enterprise_inquiry"
    assert runtime.state.get("judge.kind") == "enterprise_inquiry"
    assert runtime.state.get("judge.kind.value") == "enterprise_inquiry"
    assert runtime.state.get("judge.kind.p") == 0.88
    assert runtime.state.get("judge.kind.confidence") == 0.71
    # Sans confiance rendue, la probabilité de la valeur en tient lieu.
    assert runtime.state.get("judge.holds_negative.confidence") == 0.97
    assert runtime.state.get("reason.degraded") is False


def test_a_judged_value_is_llm_derived():
    """Une réponse d'oracle reste une réponse d'oracle : la provenance ne
    change pas parce que la question était fermée."""
    agent = _agent(_program())
    llm = MockLLM({"Trier": {"kind": {"value": "question", "p": 0.99}}})

    runtime = _run(agent, llm)

    _, label = runtime.state.get_labeled("judge.kind")
    assert "LLM" in label.sources


def test_the_question_travels_with_the_field():
    """L'oracle reçoit la question et la description de chaque réponse —
    c'est ce qui manquait à un `PRODUCE`, où seul le nom du champ partait."""
    agent = _agent(_program())
    llm = MockLLM({"Trier": {}})

    _run(agent, llm)

    call = next(c for c in llm.calls if c["kind"] == "judge")
    assert call["questions"] == ["holds_negative", "kind", "frustration"]


def test_an_answer_outside_the_declared_options_is_not_kept():
    """Le domaine vient des options décrites : un oracle qui répond à côté
    ne pose pas de valeur inconnue dans l'état."""
    agent = _agent(_program())
    llm = MockLLM({"Trier": {"kind": {"value": "spam", "p": 0.99}}})

    runtime = _run(agent, llm)

    assert runtime.state.get("kind") != "spam"
    assert any(event.kind == "BLOCKED" and "kind" in event.text
               for event in runtime.trace.events)


def test_a_silent_oracle_degrades_instead_of_inventing():
    agent = _agent(_program())

    runtime = _run(agent, MockLLM({}))

    assert runtime.state.get("reason.degraded") is True
    assert runtime.state.get("frustration") is UNDEFINED     # aucun DEFAULT
    assert runtime.state.get("kind") == "other"              # DEFAULT déclaré


def test_an_oracle_that_raises_does_not_crash_the_tick():
    agent = _agent(_program())

    runtime = _run(agent, _Exploding())

    assert any(event.kind == "ERROR" for event in runtime.trace.events)
    assert runtime.state.get("frustration") is UNDEFINED


# ================================================================= ABSTENTION
def test_below_the_threshold_the_answer_is_not_kept():
    """`ABSTAIN BELOW` ferme : la réponse existe, mais le programme a déclaré
    qu'elle ne vaut pas décision — c'est le `DEFAULT` qui s'applique."""
    agent = _agent(_program())
    llm = MockLLM({"Trier": {"kind": {"value": "enterprise_inquiry", "p": 0.61}}})

    runtime = _run(agent, llm)

    assert runtime.state.get("kind") == "other"
    assert runtime.state.get("judge.kind.p") is UNDEFINED
    assert runtime.metrics["judge_abstained"] == 1


def test_an_oracle_without_calibration_abstains():
    """Un modèle génératif ne rend pas de probabilité. Un champ qui en exige
    une par `ABSTAIN BELOW` doit se fermer, pas se contenter de la valeur :
    sinon le seuil déclaré ne garantirait rien selon l'oracle branché."""
    agent = _agent(_program())

    runtime = _run(agent, _Generative({"kind": "enterprise_inquiry",
                                       "holds_negative": False}))

    assert runtime.state.get("kind") == "other"
    assert runtime.state.get("holds_negative") is False      # aucun seuil
    assert runtime.state.get("judge.holds_negative.p") is UNDEFINED


def test_a_probability_outside_zero_one_is_no_probability():
    """Un seuil de confiance ne se franchit pas par accident de typage."""
    agent = _agent(_program())
    llm = MockLLM({"Trier": {"kind": {"value": "question", "p": 4.2}}})

    runtime = _run(agent, llm)

    assert runtime.state.get("kind") == "other"              # abstention
    assert runtime.metrics["judge_abstained"] == 1


# ============================================================ ORACLE GÉNÉRATIF
def test_the_generative_fallback_asks_the_same_question():
    """`JUDGE` est une primitive du langage, pas d'un fournisseur : un oracle
    qui ne sait que `reason()` reçoit la même question, options comprises."""
    agent = _agent(_program())
    oracle = _Generative({"kind": "question"})

    _run(agent, oracle)

    prompt = oracle.prompts[0]
    assert "De quoi cette mention parle-t-elle ?" in prompt
    assert "elle exprime un besoin d'entreprise" in prompt
    assert "très mécontent" in prompt


# ===================================================================== POLICY
def test_a_policy_can_gate_on_the_probability():
    """Le point de la primitive : la calibration entre dans la garde."""
    source = _program(policy="DEFAULT DENY\n    ALLOW reply "
                             "WHEN judge.kind.p >= 0.90")
    agent = _agent(source)
    sure = MockLLM({"Trier": {"kind": {"value": "question", "p": 0.96}}})
    unsure = MockLLM({"Trier": {"kind": {"value": "question", "p": 0.72}}})

    passed = _run(agent, sure)
    blocked = _run(_agent(source), unsure)

    assert any(event.kind == "TOOL" for event in passed.trace.events)
    assert blocked.metrics["blocked"] >= 1


# ================================================================== ANALYSEUR
def _codes(source: str):
    return {d.code for d in Analyzer(_agent(source)).run()}


def test_a_choice_without_an_alternative_is_an_error():
    codes = _codes(_program(
        '      JUDGE { k: CHOICE "laquelle ?" { a: "la seule" } }'))

    assert "E017" in codes


def test_an_option_without_a_description_is_an_error():
    codes = _codes(_program(
        '      JUDGE { k: CHOICE "laquelle ?" { a: "  " b: "l\'autre" } }'))

    assert "E017" in codes


def test_a_score_needs_at_least_two_levels():
    codes = _codes(_program('      JUDGE { k: SCORE "où ?" [ "seul" ] }'))

    assert "E017" in codes


def test_a_threshold_is_a_probability():
    codes = _codes(_program(
        '      JUDGE { k: NOUL "vrai ?" ABSTAIN BELOW 1.5 }'))

    assert "E017" in codes


def test_a_judgement_guarding_an_interdiction_must_treat_its_doubt():
    """W136 — sans seuil ni lecture de `judge.<champ>.p`, une réponse à 0,51
    pèse autant qu'une réponse à 0,99 dans la garde."""
    source = _program(
        judge='      JUDGE { risky: NOUL "le message est-il piégé ?" '
              'DEFAULT true }',
        policy="DEFAULT ALLOW\n    NEVER reply WHEN risky == true")

    assert "W136" in _codes(source)


def test_declaring_the_threshold_silences_it():
    source = _program(
        judge='      JUDGE { risky: NOUL "le message est-il piégé ?" '
              'ABSTAIN BELOW 0.80 DEFAULT true }',
        policy="DEFAULT ALLOW\n    NEVER reply WHEN risky == true")

    assert "W136" not in _codes(source)


def test_reading_the_probability_silences_it_too():
    """L'abstention n'est pas la seule conduite possible : garder sur la
    probabilité elle-même traite le doute tout aussi explicitement."""
    source = _program(
        judge='      JUDGE { risky: NOUL "le message est-il piégé ?" '
              'DEFAULT true }',
        policy="DEFAULT ALLOW\n    NEVER reply WHEN risky == true\n"
               "    NEVER reply WHEN judge.risky.p < 0.90")

    assert "W136" not in _codes(source)


def test_the_judged_paths_are_known_to_the_checks():
    """`judge.<champ>.p` dans une garde n'est pas un identifiant inconnu :
    c'est le mode d'emploi de la primitive."""
    source = _program(policy="DEFAULT DENY\n    ALLOW reply "
                             "WHEN judge.kind.p >= 0.90")

    assert "W128" not in _codes(source)


# =================================================================== SCENARIO
def test_a_scenario_poses_the_answer_and_its_probability():
    """`agentl test` n'appelle aucun oracle : en scénario, c'est l'auteur qui
    écrit la réponse — et sa probabilité, puisque c'est elle que la politique
    lit."""
    from agentl.scenario import ScenarioLLM

    oracle = ScenarioLLM({"kind": "question", "judge.kind.p": 0.94})

    answers = oracle.judge("t", {}, {"kind": {"kind": "CHOICE"},
                                     "autre": {"kind": "NOUL"}})

    assert answers["kind"] == {"value": "question", "p": 0.94}
    assert "autre" not in answers          # non posé : absent, donc DEFAULT


def test_a_posed_answer_without_a_probability_is_certain():
    """Écrire `kind = question` dans un GIVEN, c'est dire « l'oracle a répondu
    ceci » : le cas nominal ne doit pas exiger de connaître le seuil."""
    from agentl.scenario import ScenarioLLM

    answers = ScenarioLLM({"kind": "question"}).judge(
        "t", {}, {"kind": {"kind": "CHOICE"}})

    assert answers["kind"]["p"] == 1.0


# ================================================================= ENVELOPPES
# Le runtime ne parle pas toujours à l'oracle en direct : l'enregistrement, la
# reprise durable et le pont asynchrone l'enveloppent. Chacune définissait
# `reason` et `select_plan` mais pas `judge`, qui filait par `__getattr__`
# jusqu'à l'oracle réel — ni journalisé, ni rejouable, ni borné.
_SCRIPT = {"Trier la mention": {
    "kind": {"value": "question", "p": 0.93, "confidence": 0.9},
    "holds_negative": {"value": False, "p": 0.88},
    "frustration": {"value": 0.4, "p": 0.81}}}


class _Counting(MockLLM):
    def __init__(self, scripted):
        super().__init__(scripted)
        self.judged = 0

    def judge(self, task, context, questions):
        self.judged += 1
        return super().judge(task, context, questions)


def test_a_recorded_judgement_replays_identically():
    """Le jugement est journalisé avec sa probabilité, et le rejeu le rend
    sans oracle : même valeur, même `p`, même trace."""
    from agentl.replay import (Journal, RecordingHost, RecordingLLM,
                               ReplayHost, ReplayLLM)
    agent = _agent(_program())
    journal = Journal(meta={"agent": agent.name})
    host = Host()
    host.tool("reply")(lambda text: {"sent": True})
    recorded = Runtime(agent, RecordingHost(host, journal),
                       RecordingLLM(MockLLM(_SCRIPT), journal))
    recorded.state.set_local("m.content", "Vous proposez un plan équipe ?")
    recorded.run(max_ticks=1)

    replayed = Runtime(agent, ReplayHost(journal), ReplayLLM(journal))
    replayed.state.set_local("m.content", "Vous proposez un plan équipe ?")
    replayed.run(max_ticks=1)

    assert [e.kind for e in journal.entries].count("judge") == 1
    assert replayed.state.get("judge.kind") == "question"
    assert replayed.state.get("judge.kind.p") == 0.93
    assert replayed.trace.render() == recorded.trace.render()


def test_a_durable_resume_does_not_ask_the_oracle_again():
    """À la reprise, le jugement vient du journal. Un oracle de jugement
    n'est pas déterministe : le rappeler pouvait changer la décision déjà
    prise et faire refuser la reprise."""
    from agentl.durable import DurableRun, MemoryStore
    agent, store = _agent(_program()), MemoryStore()

    def host():
        h = Host()
        h.sensor("m.content")(lambda: "Vous proposez un plan équipe ?")
        h.tool("reply")(lambda text: {"sent": True})
        return h

    first = _Counting(_SCRIPT)
    DurableRun(agent, host(), first, store=store).run(max_ticks=1)
    drifted = _Counting({"Trier la mention": {
        "kind": {"value": "other", "p": 0.55}}})
    resumed = DurableRun(agent, host(), drifted, store=store).run(max_ticks=1)

    assert first.judged == 1
    assert drifted.judged == 0
    assert resumed.state.get("judge.kind") == "question"


def test_an_async_judgement_is_awaited_under_the_bridge():
    """Un `async def judge` est attendu — il n'arrive plus au runtime sous
    forme de coroutine lue comme un oracle muet."""
    import asyncio
    from agentl.aio import AsyncHost, AsyncRuntime

    class AsyncOracle(MockLLM):
        async def judge(self, task, context, questions):
            return {"kind": {"value": "question", "p": 0.97}}

    host = AsyncHost()
    host.sensor("m.content")(lambda: "Vous proposez un plan équipe ?")
    host.tool("reply")(lambda text: {"sent": True})

    rt = asyncio.run(AsyncRuntime(_agent(_program()), host, AsyncOracle())
                     .run(max_ticks=1))

    assert rt.state.get("judge.kind") == "question"
    assert rt.state.get("judge.kind.p") == 0.97


def test_a_slow_judgement_times_out_like_a_slow_reason():
    """Le délai du pont s'applique au jugement : passé `llm_timeout`, l'oracle
    est muet et le `DEFAULT` s'applique."""
    import asyncio
    from agentl.aio import AsyncHost, AsyncRuntime, Limits

    class SlowOracle(MockLLM):
        async def judge(self, task, context, questions):
            await asyncio.sleep(5)
            return {"kind": {"value": "question", "p": 0.97}}

    host = AsyncHost()
    host.sensor("m.content")(lambda: "Vous proposez un plan équipe ?")
    host.tool("reply")(lambda text: {"sent": True})

    rt = asyncio.run(AsyncRuntime(_agent(_program()), host, SlowOracle(),
                                  limits=Limits(llm_timeout=0.1))
                     .run(max_ticks=1))

    assert rt.state.get("judge.kind") == "other"
    assert rt.state.get("judge.kind.p") is UNDEFINED
    assert "délai" in rt.trace.render()
