"""Régressions de la 1.13 : ce que `verify` certifiait sans l'avoir examiné
(audit du 2026-10-19), reproduit puis refermé.

    python -m pytest tests/test_v113_regressions.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl import parse_source, verify
from agentl.analyzer import Analyzer
from agentl.nodes import CallStmt
from agentl.verifier import Verifier

BODIES = '''
AGENT bodies {
  GOAL g { MAINTAIN svc.ok == yes }
  OBSERVE { svc.ok  asset.critical }
  BELIEF { svc.ok = no CONFIDENCE 1.0 SOURCE prior }
  TOOL wipe {
    INPUT { host: String }
    OUTPUT { done: Symbol }
    SIDE_EFFECT { disk.data }
    RISK { operational = CRITICAL }
    EFFECT { svc.ok = yes }
  }
  TOOL ping { OUTPUT { done: Symbol } RISK LOW EFFECT { svc.ok = yes } }
  POLICY {
    DEFAULT ALLOW
    NEVER wipe WHEN asset.critical == yes
  }
  EVENT alarm.fired { wipe(host = "h1") }
  ON MESSAGE order { wipe(host = "h4") }
  ON VERIFY.FAIL { wipe(host = "h2") }
  PLAN fix WHEN svc.ok != yes {
    STEP a { ping() }
    STEP b { VERIFY "ok" { CONDITION svc.ok == yes ON FAIL { wipe(host = "h3") } } }
  }
  DECIDE { RULES { IF svc.ok != yes THEN { wipe(host = "h5") } } }
  LOOP UNTIL goal.satisfied MAX 3 { OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY }
}
'''


def _agent(source=BODIES):
    return parse_source(source).agents[0]


def _ast_calls(agent, tool):
    count = 0
    from agentl.analyzer import Analyzer
    for stmt, _ in Analyzer(agent)._all_statements():
        if isinstance(stmt, CallStmt) and stmt.call.name == tool:
            count += 1
    return count


def test_t1_examines_every_call_site_of_the_ast():
    agent = _agent()
    verifier = Verifier(agent)
    sites = {(s.plan, s.line) for s in verifier.call_sites("wipe")}
    assert len(sites) == _ast_calls(agent, "wipe") == 5
    labels = {s.plan for s in verifier.call_sites("wipe")}
    assert {"fix", "EVENT alarm.fired", "ON MESSAGE order", "ON VERIFY.FAIL",
            "DECIDE"} <= labels


def test_t1_reports_the_exposure_of_a_call_hidden_in_an_event():
    report = verify(_agent())
    t1 = next(t for t in report.theorems if t.key == "T1")
    assert "5 site(s) d'appel examiné(s)" in t1.summary
    lines = {f.line for f in t1.findings if f.code == "V102"}
    assert len(lines) == 5


# ================================ W102 et T6 couvrent tous les corps exécutables
def _codes(source):
    return [d.code for d in Analyzer(_agent(source)).run()]


_HANDLER = '''
AGENT h {
  GOAL g { MAINTAIN svc.ok == yes }
  OBSERVE { svc.ok }
  BELIEF { svc.ok = no CONFIDENCE 1.0 SOURCE prior }
  TOOL wipe_host {
    INPUT { host: String%s }
    OUTPUT { done: Symbol }
    SIDE_EFFECT { disk.data }
    RISK { operational = CRITICAL }
    EFFECT { svc.ok = yes }
  }
  POLICY { DEFAULT ALLOW }
  EVENT ticket.created { %s }
  LOOP MAX 2 { SELECT_PLAN EXECUTE }
}
'''


def test_w102_sees_an_unverified_action_in_an_event_handler():
    codes = _codes(_HANDLER % ("", 'wipe_host(host = "h1")'))
    assert "W102" in codes


def test_a_payload_choosing_the_target_of_a_critical_tool_breaks_t6():
    source = _HANDLER % ("", "wipe_host(host = payload.requested_host)")
    assert "W140" in _codes(source)
    report = verify(_agent(source))
    t6 = next(t for t in report.theorems if t.key == "T6")
    assert t6.holds is False


def test_the_payload_taint_follows_a_set_alias():
    source = _HANDLER % ("", "SET victim = payload.requested_host\n    "
                             "wipe_host(host = victim)")
    assert "W140" in _codes(source)


def test_an_attested_target_is_not_flagged():
    source = (_HANDLER % (" ATTESTS host", "wipe_host(host = payload.requested_host)"))
    source = source.replace("host: String ATTESTS host",
                            "host: String\n    proof: String ATTESTS host")
    assert "W140" not in _codes(source.replace(
        "wipe_host(host = payload.requested_host)",
        "wipe_host(host = payload.requested_host, proof = payload.proof)"))


def test_a_literal_target_is_not_flagged():
    assert "W140" not in _codes(_HANDLER % ("", 'wipe_host(host = "h1")'))


# ============================================================ T4 : ce qui borne
_LLM = '''
AGENT t4 {
  GOAL g { MAINTAIN svc.ok == yes }
  OBSERVE { svc.ok  env.name  ticket.text }
  BELIEF { svc.ok = no CONFIDENCE 1.0 SOURCE prior }
  TOOL wipe_db {
    OUTPUT { done: Symbol }
    SIDE_EFFECT { db.rows }
    RISK { operational = CRITICAL }
    EFFECT { svc.ok = yes }
  }
  POLICY {
    DEFAULT ALLOW
    %s
  }
  PLAN nuke { STEP a { wipe_db() } STEP b { VERIFY { svc.ok == yes } } }
  PLAN idle { STEP a { SET svc.ok = no } }
  DECIDE { REASON { TASK "pick a plan" USING { ticket.text } } }
  LOOP MAX 2 { SELECT_PLAN EXECUTE }
}
'''


def _t4(rules):
    report = verify(_agent(_LLM % rules))
    return next(t for t in report.theorems if t.key == "T4")


def test_a_conditional_approval_does_not_bound_the_llm():
    t4 = _t4("REQUIRE APPROVAL FOR wipe_db WHEN env.name == prod")
    assert t4.holds is False
    assert any(f.code == "V104" for f in t4.findings)


def test_an_unconditional_approval_bounds_the_llm():
    assert _t4("REQUIRE APPROVAL FOR wipe_db").holds is True


def test_a_state_guarded_allow_bounds_the_llm():
    assert _t4("ALLOW wipe_db IF env.name == staging").holds is True


def test_an_allow_without_condition_does_not():
    assert _t4("ALLOW wipe_db").holds is False


# ================================================ gouvernance dans verify
_GOV = '''
AGENT gov {
  GOAL g { MAINTAIN svc.ok == yes }
  OBSERVE { svc.ok }
  BELIEF { svc.ok = no CONFIDENCE 1.0 SOURCE prior }
  TOOL wipe { OUTPUT { done: Symbol } SIDE_EFFECT disk.data RISK CRITICAL
              EFFECT { svc.ok = yes } }
  %s
  PLAN go WHEN svc.ok != yes { STEP a { wipe() } STEP b { VERIFY { svc.ok == yes } } }
  LOOP MAX 2 { SELECT_PLAN EXECUTE }
}
'''


def _t1(policy):
    return next(t for t in verify(_agent(_GOV % policy)).theorems
                if t.key == "T1")


def test_a_critical_tool_without_any_policy_is_flagged_by_verify():
    t1 = _t1("")
    assert any(f.code == "V153" for f in t1.findings)
    assert t1.holds is True          # un avertissement, pas une réfutation


def test_a_covered_critical_tool_is_not_flagged():
    assert not any(f.code == "V153" for f in _t1(
        "POLICY { DEFAULT ALLOW  REQUIRE APPROVAL FOR wipe }").findings)


def test_default_deny_governs_an_uncovered_tool():
    assert not any(f.code == "V153" for f in _t1(
        "POLICY { DEFAULT DENY }").findings)


# ===================== autoloop rejoue les stimuli du scénario comme `test`
from agentl.autoloop import Case, run_case

_STIM = '''
AGENT s {
  GOAL g { MAINTAIN svc.ok == yes }
  OBSERVE { svc.ok  load.level }
  BELIEF { svc.ok = no CONFIDENCE 1.0 SOURCE prior }
  TOOL ping { OUTPUT { done: Symbol } RISK LOW EFFECT { svc.ok = yes } }
  POLICY { DEFAULT ALLOW }
  EVENT alarm.fired { ping() }
  SCENARIO alarme {
    GIVEN { svc.ok = no  load.level = 3  ping.done = yes }
    GIVEN EVENT alarm.fired { severity = high }
    EXPECT CALL ping
    WITHIN 3
  }
  LOOP UNTIL goal.satisfied MAX 3 { OBSERVE UPDATE_BELIEFS EVALUATE_GOALS RECEIVE SELECT_PLAN EXECUTE VERIFY }
}
'''


def test_run_case_injects_the_scenario_stimuli():
    agent = _agent(_STIM)
    scenario = agent.scenarios[0]
    case = Case("alarme", "load.level=4", "données", (("load.level", 4),))
    result = run_case(agent, scenario, case)
    assert "ping" in result.calls, result


# ==================================================== W103 et USING
_USING = '''
AGENT u {
  GOAL g { MAINTAIN svc.ok == yes }
  OBSERVE { svc.ok  ticket.text  %s }
  BELIEF { svc.ok = no CONFIDENCE 1.0 SOURCE prior }
  TOOL ping { OUTPUT { done: Symbol } RISK LOW EFFECT { svc.ok = yes } }
  POLICY { DEFAULT ALLOW }
  PLAN go WHEN svc.ok != yes { STEP a { %s ping() } }
  %s
  LOOP MAX 2 { SELECT_PLAN EXECUTE }
}
'''


def _w103(extra_obs, step, decide):
    codes = [d for d in Analyzer(_agent(_USING % (extra_obs, step, decide))).run()
             if d.code == "W103"]
    return sorted(d.message for d in codes)


def test_an_observation_listed_in_decide_reason_using_is_used():
    msgs = _w103("", "SET x = 1",
                 'DECIDE { REASON { TASK "pick" USING { ticket.text } } }')
    assert not any("ticket.text" in m for m in msgs)


def test_an_observation_listed_in_a_plan_reason_using_is_used():
    msgs = _w103("", 'REASON { TASK "t" USING { ticket.text } '
                     'PRODUCE { s: String DEFAULT "x" } }', "")
    assert not any("ticket.text" in m for m in msgs)


def test_a_really_unused_observation_is_still_flagged():
    msgs = _w103("", "SET x = 1", "")
    assert any("ticket.text" in m for m in msgs)


# ================================================ --version, stop_reason
def test_cli_has_a_version_flag(capsys):
    from agentl import __version__
    from agentl.cli import main
    with pytest.raises(SystemExit) as stop:
        main(["--version"])
    assert stop.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_a_refused_or_truncated_anthropic_answer_is_not_read_as_json():
    from agentl.llm import AnthropicLLM, LLMError
    ok = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "{}"}]}
    assert AnthropicLLM.text_of(ok) == "{}"
    for reason in ("refusal", "max_tokens"):
        with pytest.raises(LLMError, match=reason):
            AnthropicLLM.text_of({"stop_reason": reason, "content": [
                {"type": "text", "text": '{"a": 1'}]})
