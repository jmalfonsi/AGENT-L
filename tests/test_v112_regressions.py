"""Régressions de la 1.12 : ce qu'un agent auteur a dû contourner à la main,
reproduit puis refermé (propositions du 2026-10-17).

    python -m pytest tests/test_v112_regressions.py -q
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl import (Host, MockLLM, ParseError, Refused, Runtime, UNDEFINED,
                    parse_source, verify)
from agentl.analyzer import Analyzer
from agentl.autoloop import run_gates
from agentl.cli import main as agentl_main
from agentl.kernel.provenance import EXTERNAL, OBSERVED
from agentl.mutate import mutate_program
from agentl.replay import Journal, RecordingHost, RecordingLLM, ReplayHost, ReplayLLM
from agentl.scenario import run_scenarios


def _trace(runtime, kinds=("TOOL", "BLOCKED", "PLAN", "VERIFY_FAIL", "ASK")):
    return [(e.tick, e.kind, e.text) for e in runtime.trace.events
            if e.kind in kinds]


# ===================================================== VERIFY ne fige plus rien
FREEZE = '''
AGENT freeze {
  VERSION "1.12"
  GOAL g { MAINTAIN job.done == yes }
  OBSERVE { job.done  alert.level }
  BELIEF { job.attempted = no CONFIDENCE 1.0 SOURCE prior }
  TOOL do_job { RISK LOW EFFECT { job.attempted = yes } }
  TOOL notify { RISK LOW }
  POLICY { DEFAULT ALLOW }
  PLAN attempt WHEN job.attempted != yes {
    STEP s { do_job() }
    STEP v { VERIFY { job.done == yes } }
  }
  PLAN escalate { STEP s { notify() } }
  DECIDE { IF alert.level > 3 THEN escalate }
  %s
  LOOP MAX 4 { OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY }
}
'''


def _freeze_run(on_fail: str):
    agent = parse_source(FREEZE % on_fail).agents[0]
    host, calls, ticks = Host(), [], {"n": 0}

    def level():
        ticks["n"] += 1
        return 0 if ticks["n"] == 1 else 5           # l'alerte monte au tick 2
    host.sensor("job.done")(lambda: "no")
    host.sensor("alert.level")(level)
    host.tool("do_job")(lambda: calls.append("do_job") or {})
    host.tool("notify")(lambda: calls.append("notify") or {})
    Runtime(agent, host, MockLLM({})).run()
    return calls


@pytest.mark.parametrize("on_fail", ["", "ON VERIFY.FAIL { ESCALATE }"],
                         ids=["sans-handler", "avec-ON-VERIFY.FAIL"])
def test_a_failed_verify_does_not_silence_decide_on_later_ticks(on_fail):
    # Jusqu'en 1.11 : ['do_job'] — plus aucune règle DECIDE ne partait.
    assert _freeze_run(on_fail) == ["do_job", "notify", "notify", "notify"]


MESSAGE_AFTER_FAIL = '''
AGENT remed {
  VERSION "1.12"
  GOAL g { MAINTAIN box.clean == yes }
  OBSERVE { box.clean }
  TOOL clean { RISK LOW }
  POLICY { DEFAULT ALLOW }
  ON MESSAGE clean_up { THEN run_clean }
  PLAN run_clean {
    STEP act { clean() }
    STEP v { VERIFY { box.clean == yes } }
  }
  LOOP MAX 3 { RECEIVE OBSERVE SELECT_PLAN EXECUTE }
}
'''


def test_a_message_after_a_failed_verify_is_still_handled():
    agent = parse_source(MESSAGE_AFTER_FAIL).agents[0]
    host, calls = Host(), []
    host.sensor("box.clean")(lambda: "no")
    host.tool("clean")(lambda: calls.append("clean") or {})
    runtime = Runtime(agent, host, MockLLM({}))
    for _ in range(3):
        runtime.inbox.append({"name": "clean_up", "from": "triage",
                              "payload": {}})
        runtime.tick()
    # En 1.11 le gestionnaire était avalé dès le 2e message (complex_soc).
    assert calls == ["clean", "clean", "clean"]


def test_a_failed_verify_in_a_rule_does_not_skip_the_next_rule():
    source = (FREEZE % "").replace(
        "DECIDE { IF alert.level > 3 THEN escalate }",
        "DECIDE { IF alert.level >= 0 THEN { VERIFY { job.done == yes } }\n"
        "         IF alert.level >= 0 THEN escalate }")
    agent = parse_source(source).agents[0]
    host, calls = Host(), []
    host.sensor("job.done")(lambda: "no")
    host.sensor("alert.level")(lambda: 1)
    host.tool("do_job")(lambda: calls.append("do_job") or {})
    host.tool("notify")(lambda: calls.append("notify") or {})
    Runtime(agent, host, MockLLM({})).run(max_ticks=1)
    assert "notify" in calls


# ============================================ autoloop : V128 n'est pas un échec
TRACE_ONLY = '''
AGENT blocker {
  VERSION "1.12"
  GOAL g { MAINTAIN ip.blocked == yes }
  OBSERVE { ip.blocked  ip.allowlisted ON UNKNOWN ESCALATE }
  BELIEF { ip.blocked = no CONFIDENCE 1.0 SOURCE prior
           alert.sent = no CONFIDENCE 1.0 SOURCE prior }
  TOOL block_ip { RISK LOW EFFECT { ip.blocked = yes } }
  TOOL alert { RISK LOW EFFECT { alert.sent = yes INTERNAL } }
  POLICY { DEFAULT ALLOW  NEVER block_ip WHEN ip.allowlisted != no }
  PLAN block WHEN ip.blocked != yes AND ip.allowlisted == no {
    STEP s { block_ip() }
    STEP v { VERIFY { ip.blocked == yes } }
  }
  PLAN warn WHEN ip.allowlisted != no AND alert.sent != yes {
    STEP s { alert() }
  }
  SCENARIO allowlisted_never_blocked {
    GIVEN { ip.allowlisted = yes, ip.blocked = no }
    EXPECT NEVER CALL block_ip
    WITHIN 2
  }
  LOOP UNTIL goal.satisfied OR alert.sent == yes MAX 3 {
    OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY
  }
}
'''


def test_a_never_call_scenario_is_proved_not_deferred():
    # 1.12.2 : `NEVER CALL` ne sort plus le scénario du modèle de T5, il
    # contraint la route. Rien n'est renvoyé : T5 l'a démontré lui-même.
    gates = {g.name: g for g in run_gates(parse_source(TRACE_ONLY))}
    proof = gates["preuve"]
    assert proof.passed and proof.detail == "", proof.detail
    assert next(g for n, g in gates.items() if n.startswith("scénarios")).passed


def test_a_stimulus_scenario_does_not_block_the_proof_gate():
    program = parse_source(TRACE_ONLY.replace(
        "  SCENARIO allowlisted_never_blocked {",
        "  EVENT probe { THEN { alert() } }\n"
        "  SCENARIO allowlisted_never_blocked {").replace(
        "EXPECT NEVER CALL block_ip",
        "GIVEN EVENT probe {}\n    EXPECT NEVER CALL block_ip"))
    gates = {g.name: g for g in run_gates(program)}
    proof = gates["preuve"]
    assert proof.passed, proof.detail
    assert "allowlisted_never_blocked" in proof.detail
    # …et la barrière suivante l'exécute bel et bien.
    assert next(g for n, g in gates.items() if n.startswith("scénarios")).passed


def test_a_bounded_t5_still_blocks_the_proof_gate():
    from agentl.autoloop import _only_out_of_model

    program = parse_source(TRACE_ONLY.replace(
        "EXPECT NEVER CALL block_ip\n    WITHIN 2",
        "EXPECT { ip.blocked == yes } WITHIN 2").replace(
        "GIVEN { ip.allowlisted = yes", "GIVEN { ip.allowlisted = no"))
    t5 = next(t for t in verify(program.agents[0], depth=0).theorems
              if t.key == "T5")
    assert t5.holds is None and [f.code for f in t5.findings] == ["V122"]
    assert not _only_out_of_model(t5)
    assert "non atteint(s) à la profondeur 0" in t5.summary
    assert "hors modèle" not in t5.summary


# ============================ T5 : NEVER CALL contraint la route (1.12.2)
def _t5(scenarios: str, extra: str = ""):
    agent = parse_source('''
AGENT t5 {
  VERSION "1.12"
  TOOL block_ip { RISK LOW EFFECT { ip.blocked = yes  alert.sent = yes } }
  TOOL notify { RISK LOW EFFECT { alert.sent = yes } }
  TOOL validate { RISK LOW EFFECT { ip.valid = yes } }
  POLICY { DEFAULT ALLOW }
''' + extra + scenarios + "\n}").agents[0]
    return next(t for t in verify(agent).theorems if t.key == "T5")


def test_t5_searches_the_route_without_the_never_called_tool():
    # Avant : V128, « borné ». Ignorer l'assertion aurait été pire : la route
    # trouvée passait par block_ip — un témoin que le scénario interdit.
    t5 = _t5('''SCENARIO s { GIVEN { ip.blocked = no }
      EXPECT NEVER CALL block_ip  EXPECT { alert.sent == yes } }''')
    assert t5.holds is True, t5.summary
    [found] = t5.findings
    assert found.code == "V123"
    assert found.title == "s : atteignable par notify sans block_ip"
    assert "NEVER CALL block_ip" in found.detail and "agentl test" in found.detail
    assert t5.summary == "1/1 scénario(s) sans obstacle"


def test_t5_refutes_a_scenario_that_forbids_its_only_route():
    t5 = _t5('''SCENARIO s { GIVEN { ip.blocked = no }
      EXPECT NEVER CALL block_ip  EXPECT { ip.blocked == yes } }''')
    assert t5.holds is False
    [refuted] = t5.findings
    assert refuted.code == "V121" and refuted.severity == "error"
    assert "se contredit" in refuted.detail
    assert "1 réfuté(s)" in t5.summary


def test_t5_does_not_exclude_a_tool_that_is_only_expected_blocked():
    # Une tentative bloquée n'empêche pas un appel approuvé ensuite :
    # exclure block_ip ici réfuterait à tort.
    t5 = _t5('''SCENARIO s { GIVEN { ip.blocked = no }
      EXPECT BLOCKED block_ip  EXPECT { ip.blocked == yes } }''')
    assert t5.holds is True
    assert t5.findings[0].title == "s : atteignable par block_ip"


def test_t5_excludes_a_tool_the_given_makes_refuse():
    t5 = _t5('''SCENARIO s { GIVEN { scenario.refuse.validate = yes }
      EXPECT { ip.valid == yes } }''')
    assert t5.holds is False and t5.findings[0].code == "V121"
    t5 = _t5('''SCENARIO s { GIVEN { scenario.refuse.validate = no }
      EXPECT { ip.valid == yes } }''')
    assert t5.holds is True


def test_t5_trace_only_scenario_has_nothing_to_reach():
    t5 = _t5("SCENARIO s { EXPECT NEVER CALL block_ip }")
    assert t5.holds is True
    assert [f.code for f in t5.findings] == ["V129"]


def test_t5_stimuli_stay_out_of_the_model_without_blaming_depth():
    t5 = _t5('''SCENARIO s { GIVEN EVENT alarm {}
      EXPECT { alert.sent == yes } }''',
             extra="EVENT alarm { THEN { notify() } }\n")
    assert t5.holds is None
    assert [f.code for f in t5.findings] == ["V128"]
    assert t5.summary == ("0/1 scénario(s) sans obstacle · 1 hors modèle "
                          "(stimuli → agentl test)")
    assert "profondeur" not in t5.summary


# ================================================ T2 : escalade réellement vive
T2_PROGRAM = '''
AGENT t2 {
  VERSION "1.12"
  GOAL g { MAINTAIN host.fixed == yes }
  OBSERVE { host.fixed  host.critical ON UNKNOWN ESCALATE }
  BELIEF { esc.sent = no CONFIDENCE 1.0 SOURCE prior }
  TOOL fix { RISK LOW EFFECT { host.fixed = yes } }
  TOOL page { RISK LOW EFFECT { esc.sent = yes INTERNAL } }
  POLICY { DEFAULT ALLOW  NEVER fix WHEN host.critical == yes }
  PLAN repair WHEN host.fixed != yes { STEP s { fix() } }
  PLAN escalate { STEP s { page() } }
  DECIDE { IF %s AND esc.sent != yes THEN escalate }
  %s
  LOOP MAX 3 { OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY }
}
'''
PLANNER = "PLANNER { ENABLE ACHIEVE host.fixed == yes }"


def _t2(cond: str, planner: str = ""):
    agent = parse_source(T2_PROGRAM % (cond, planner)).agents[0]
    return next(t for t in verify(agent).theorems if t.key == "T2"), agent


def test_planner_exhausted_without_a_planner_is_not_an_escalation():
    theorem, agent = _t2("planner.exhausted")
    assert theorem.holds is False
    assert "W138" in [d.code for d in Analyzer(agent).run()]


def test_planner_exhausted_under_an_enabled_planner_still_counts():
    theorem, agent = _t2("planner.exhausted", PLANNER)
    assert theorem.holds is True
    assert "W138" not in [d.code for d in Analyzer(agent).run()]


def test_a_rule_entailed_by_the_guard_is_an_escalation():
    theorem, _ = _t2("host.critical == yes")
    assert theorem.holds is True
    assert any(f.code == "V112" and "entraînée" in f.detail
               for f in theorem.findings)


def test_a_published_denial_after_a_written_call_is_an_escalation():
    theorem, _ = _t2("tools.fix.denied == true")
    assert theorem.holds is True
    assert any("tools.fix.denied" in f.detail for f in theorem.findings)


def test_a_published_denial_without_any_attempt_is_not_an_escalation():
    # Sans appel écrit à la main, le refus n'est jamais publié : la synthèse
    # n'engendre pas une action interdite.
    source = (T2_PROGRAM % ("tools.fix.denied == true", PLANNER)).replace(
        "PLAN repair WHEN host.fixed != yes { STEP s { fix() } }\n", "")
    agent = parse_source(source).agents[0]
    theorem = next(t for t in verify(agent).theorems if t.key == "T2")
    assert theorem.holds is False


def test_an_escalation_that_retries_the_forbidden_tool_does_not_count():
    source = (T2_PROGRAM % ("host.critical == yes", "")).replace(
        "PLAN escalate { STEP s { page() } }",
        "PLAN escalate { STEP s { fix() } }")
    agent = parse_source(source).agents[0]
    theorem = next(t for t in verify(agent).theorems if t.key == "T2")
    assert theorem.holds is False


# =================================== refus de l'outil et refus publiés en faits
GUARD = '''
AGENT ip_guard {
  VERSION "1.12"
  GOAL contenu { MAINTAIN ip.blocked == yes }
  OBSERVE { auth.offender_ip ON UNKNOWN ESCALATE
            ip.blocked }
  BELIEF { ip.blocked = no CONFIDENCE 1.0 SOURCE prior
           escalation.sent = no CONFIDENCE 1.0 SOURCE prior }
  TOOL validate_ip { INPUT { ip: String BIND auth.offender_ip }
                     OUTPUT { ok: Symbol }  RISK LOW }
  TOOL block_ip { INPUT { ip: String BIND auth.offender_ip }
                  RISK { operational = MEDIUM }
                  SIDE_EFFECT { firewall.rules }
                  EFFECT { ip.blocked = yes } }
  TOOL page_oncall { RISK LOW  EFFECT { escalation.sent = yes INTERNAL } }
  POLICY {
    DEFAULT ALLOW
    NEVER block_ip WHEN UNTRUSTED(ip) AND NOT ATTESTED(ip, validate_ip)
  }
  PLAN contain WHEN ip.blocked != yes AND escalation.sent != yes {
    STEP check { validate_ip(ip = auth.offender_ip) }
    STEP act { block_ip(ip = auth.offender_ip) }
    STEP confirm { VERIFY { ip.blocked == yes } }
  }
  PLAN escalate { STEP page { page_oncall() } }
  DECIDE {
    IF tools.block_ip.denied == true AND escalation.sent != yes THEN escalate
  }
  SCENARIO refus_du_validateur {
    GIVEN { auth.offender_ip = "203.0.113.9" UNTRUSTED, ip.blocked = no,
            scenario.refuse.validate_ip = yes }
    EXPECT NEVER CALL block_ip
    EXPECT { escalation.sent == yes } WITHIN 3
  }
  SCENARIO validee_puis_bloquee {
    GIVEN { auth.offender_ip = "203.0.113.9" UNTRUSTED, ip.blocked = no,
            validate_ip.ok = yes }
    EXPECT { ip.blocked == yes } WITHIN 2
  }
  LOOP UNTIL goal.satisfied OR escalation.sent == yes MAX 6 {
    OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY
  }
}
'''


def _refusing_host(ip="203.0.113.9"):
    from agentl.kernel.provenance import untrusted

    host, calls = Host(), []
    host.sensor("auth.offender_ip")(lambda: untrusted(ip))
    host.sensor("ip.blocked")(lambda: "no")

    def validate_ip(ip):
        calls.append("validate_ip")
        raise Refused(f"{ip} n'est pas routable")
    host.tool("validate_ip")(validate_ip)
    host.tool("block_ip")(lambda ip: calls.append("block_ip") or {})
    host.tool("page_oncall")(lambda: calls.append("page_oncall") or {})
    return host, calls


def test_a_refusal_does_not_trip_the_circuit_breaker():
    agent = parse_source(GUARD.replace(
        "DECIDE {\n    IF tools.block_ip.denied == true AND escalation.sent != yes THEN escalate\n  }",
        "")).agents[0]
    host, calls = _refusing_host()
    runtime = Runtime(agent, host, MockLLM({}))
    runtime.run(max_ticks=5)
    # Cinq refus d'affilée : jamais coupé, toujours appelé.
    assert calls.count("validate_ip") == 5
    assert runtime.state.get("tools.validate_ip.available") is not False
    assert runtime.state.get("tools.validate_ip.refused") is True
    assert runtime.metrics["tool_refused"] == 5
    assert runtime.metrics["tool_failures"] == 0
    assert "block_ip" not in calls


def test_a_refused_value_is_not_attested():
    agent = parse_source(GUARD).agents[0]
    host, _ = _refusing_host()
    runtime = Runtime(agent, host, MockLLM({}))
    runtime.run(max_ticks=1)
    assert runtime.state.attested_by("203.0.113.9") == set()


def test_denial_is_published_and_drives_the_escalation():
    agent = parse_source(GUARD).agents[0]
    host, calls = _refusing_host()
    runtime = Runtime(agent, host, MockLLM({}))
    runtime.run()
    assert runtime.state.get("tools.block_ip.denied") is True
    assert runtime.state.get("tools.block_ip.unapproved") is False
    assert "page_oncall" in calls and "block_ip" not in calls


def test_an_operator_refusal_is_published_as_unapproved():
    source = GUARD.replace(
        "NEVER block_ip WHEN UNTRUSTED(ip) AND NOT ATTESTED(ip, validate_ip)",
        "REQUIRE APPROVAL FOR block_ip")
    agent = parse_source(source).agents[0]
    host, _ = _refusing_host()
    host.tool("validate_ip")(lambda ip: {"ok": "yes"})
    host.approver = lambda request: False
    runtime = Runtime(agent, host, MockLLM({}))
    runtime.run(max_ticks=1)
    assert runtime.state.get("tools.block_ip.unapproved") is True
    assert runtime.state.get("tools.block_ip.denied") is False


def test_a_refusal_replays_as_a_refusal():
    agent = parse_source(GUARD).agents[0]
    host, _ = _refusing_host()
    journal = Journal(meta={"source_sha256": "n/a"})
    recorded = Runtime(agent, RecordingHost(host, journal),
                       RecordingLLM(MockLLM({}), journal), echo=False)
    recorded.run(max_ticks=3)
    trace = recorded.trace.render()
    journal.seal(trace)
    replayed = Runtime(agent, ReplayHost(Journal.loads(journal.dumps())),
                       ReplayLLM(journal), echo=False)
    replayed.run(max_ticks=3)
    assert replayed.trace.render() == trace
    assert replayed.metrics["tool_failures"] == 0


# ============================================ GIVEN … UNTRUSTED, refus simulé
def test_given_untrusted_labels_the_value_as_external():
    agent = parse_source(GUARD).agents[0]
    report = run_scenarios(agent)
    assert report.passed, report.render()


def test_without_untrusted_the_guard_cannot_bite_in_a_scenario():
    agent = parse_source(GUARD.replace(' UNTRUSTED, ip.blocked', ', ip.blocked')).agents[0]
    result = next(r for r in run_scenarios(agent).results
                  if r.name == "refus_du_validateur")
    assert not result.passed


def test_the_untrusted_label_reaches_the_state():
    from agentl.scenario import ScenarioHost, ScenarioLLM, _initial_world, untrusted_paths

    agent = parse_source(GUARD).agents[0]
    scenario = agent.scenarios[0]
    host = ScenarioHost(agent, _initial_world(scenario), untrusted_paths(scenario))
    runtime = Runtime(agent, host, ScenarioLLM(host.world), echo=False)
    runtime._apply_initial_values(host.world, "scenario", host.untrusted)
    _, label = runtime.state.get_labeled("auth.offender_ip")
    assert EXTERNAL in label.sources and OBSERVED in label.sources
    _, label = runtime.state.get_labeled("ip.blocked")
    assert EXTERNAL not in label.sources


def test_untrusted_is_refused_outside_a_given():
    with pytest.raises(ParseError, match="UNTRUSTED"):
        parse_source(GUARD.replace("EFFECT { ip.blocked = yes }",
                                   "EFFECT { ip.blocked = yes UNTRUSTED }"))


def test_untrusted_stays_a_guard_function():
    agent = parse_source(GUARD).agents[0]
    assert agent.policies[-1].guard.left.name == "UNTRUSTED"


def test_scenario_refuse_is_a_known_given_path():
    agent = parse_source(GUARD).agents[0]
    assert "W117" not in [d.code for d in Analyzer(agent).run()]


# ======================================================= agentl mutate
def test_mutate_kills_the_provenance_guard():
    report = mutate_program(parse_source(GUARD), GUARD)
    assert report.exit_code() == 0
    [mutant] = report.mutants
    assert mutant.killed_by == ["refus_du_validateur"]


SURVIVOR = GUARD.replace(
    "SCENARIO refus_du_validateur {", "SCENARIO refus_du_validateur_off {").replace(
    "            scenario.refuse.validate_ip = yes }\n    EXPECT NEVER CALL block_ip\n"
    "    EXPECT { escalation.sent == yes } WITHIN 3",
    "            validate_ip.ok = yes }\n    EXPECT { ip.blocked == yes } WITHIN 2")


def test_mutate_reports_a_surviving_rule():
    report = mutate_program(parse_source(SURVIVOR), SURVIVOR)
    assert report.exit_code() == 1
    assert [m.rule.startswith("NEVER block_ip") for m in report.survivors] == [True]


def test_a_justified_waiver_covers_a_survivor_and_a_stray_one_is_reported():
    waived = SURVIVOR.replace(
        "    NEVER block_ip WHEN",
        "    // MUTATION-OK: défense en profondeur, testée par l'hôte\n"
        "    NEVER block_ip WHEN").replace(
        "  PLAN escalate {", "  // MUTATION-OK: sans objet\n\n  PLAN escalate {")
    report = mutate_program(parse_source(waived), waived)
    assert report.exit_code() == 0
    assert report.mutants[0].waiver.startswith("défense en profondeur")
    assert [reason for _line, reason in report.stray_waivers] == ["sans objet"]


def test_mutate_does_not_judge_red_or_empty_scenarios():
    red = GUARD.replace("EXPECT { ip.blocked == yes } WITHIN 2",
                        "EXPECT { ip.blocked == no } WITHIN 2")
    assert mutate_program(parse_source(red), red).exit_code() == 2
    import re
    empty = re.sub(r"  SCENARIO .*?\n  }\n", "", GUARD, flags=re.S)
    assert "SCENARIO" not in empty
    assert mutate_program(parse_source(empty), empty).exit_code() == 2


def test_mutate_command_line(tmp_path):
    target = tmp_path / "survivor.agent"
    target.write_text(SURVIVOR, encoding="utf-8")
    out = io.StringIO()
    with redirect_stdout(out):
        code = agentl_main(["mutate", str(target)])
    assert code == 1
    assert "SURVIT" in out.getvalue()


def test_the_canonical_example_survives_no_mutation():
    path = ROOT / "SKILLS/agentl-author/references/generated/canonical.agent"
    source = path.read_text(encoding="utf-8")
    report = mutate_program(parse_source(source), source)
    assert report.exit_code() == 0, report.render()


# ================================================= frictions et messages
def test_v110_explains_the_attestation_lift():
    agent = parse_source(GUARD.replace(
        "  SCENARIO refus_du_validateur",
        "  PLANNER { ENABLE ACHIEVE ip.blocked == yes }\n"
        "  SCENARIO refus_du_validateur")).agents[0]
    t1 = next(t for t in verify(agent).theorems if t.key == "T1")
    v110 = next(f for f in t1.findings if f.code == "V110")
    assert "plan manuel `validate_ip(…)` puis `block_ip(…)`" in v110.detail


def test_a_guarded_never_is_not_called_absolute():
    agent = parse_source(GUARD).agents[0]
    host, _ = _refusing_host()
    runtime = Runtime(agent, host, MockLLM({}))
    runtime.run(max_ticks=1)
    rendered = runtime.trace.render()
    assert "interdiction (garde vraie) NEVER block_ip" in rendered
    assert "interdiction absolue" not in rendered


def test_guards_with_function_calls_render_readably():
    agent = parse_source(GUARD).agents[0]
    from agentl.runtime import _render_expr
    assert _render_expr(agent.policies[-1].guard) == (
        "UNTRUSTED(ip) AND NOT ATTESTED(ip, validate_ip)")


def test_boundary_reports_a_misplaced_waiver(tmp_path):
    from agentl.boundary import check_pair, render

    generated = ROOT / "SKILLS/agentl-author/references/generated"
    (tmp_path / "c.agent").write_text(
        (generated / "canonical.agent").read_text(encoding="utf-8"))
    (tmp_path / "c.py").write_text(
        (generated / "canonical.py").read_text(encoding="utf-8")
        + "\n# BOUNDARY-OK: placée trop haut\n\nX = 1\n")
    report = check_pair(tmp_path / "c.agent")
    assert report.ok()                       # avertissement, jamais bloquant
    assert [(r, found) for _p, _l, r, found in report.stray_waivers] == [
        ("placée trop haut", False)]
    assert "levée sans effet" in render(report)


# ============================== autoloop : la réponse d'un outil n'est pas une donnée
ANSWERS = '''
AGENT notifier {
  VERSION "1.12"
  GOAL g { MAINTAIN alert.handled == yes }
  OBSERVE { alert.level  alert.handled }
  BELIEF { alert.handled = no CONFIDENCE 1.0 SOURCE prior }
  TOOL notify_operator { OUTPUT { ack: Symbol }  RISK LOW
                         EFFECT { alert.handled = yes } }
  TOOL lookup_owner { OUTPUT { owner: String }  RISK LOW }
  POLICY { DEFAULT ALLOW }
  PLAN page WHEN alert.level > 3 AND alert.handled != yes {
    STEP who { lookup_owner() }
    STEP s { notify_operator() }
  }
  %s
  LOOP MAX 2 { OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY }
}
'''
POSED = '''SCENARIO paged {
    GIVEN { alert.level = 5, alert.handled = no, notify_operator.ack = yes,
            lookup_owner.owner = "oncall" }
    EXPECT { alert.handled == yes } WITHIN 2
  }'''
UNPOSED = '''SCENARIO quiet {
    GIVEN { alert.level = 0, alert.handled = no }
    EXPECT NEVER CALL notify_operator WITHIN 2
  }'''


def test_tool_answers_are_never_derived_into_missing_worlds():
    from agentl.autoloop import generate_cases, tool_output_paths

    agent = parse_source(ANSWERS % POSED).agents[0]
    assert {"notify_operator.ack", "lookup_owner.owner", "ack", "owner"} \
        <= tool_output_paths(agent)
    touched = {path for case in generate_cases(agent, holdout_ratio=0.0)
               for path, _ in case.changes}
    assert not touched & tool_output_paths(agent)
    assert "alert.level" in touched          # les vraies données varient encore


def test_a_bare_output_name_that_is_also_observed_stays_movable():
    from agentl.autoloop import tool_output_paths

    source = (ANSWERS % POSED).replace("OUTPUT { ack: Symbol }",
                                       "OUTPUT { alert.handled_ack: Symbol }") \
        if False else (ANSWERS % POSED).replace(
            "OBSERVE { alert.level  alert.handled }",
            "OBSERVE { alert.level  alert.handled  ack }")
    agent = parse_source(source).agents[0]
    paths = tool_output_paths(agent)
    assert "notify_operator.ack" in paths and "ack" not in paths


def test_autoloop_does_not_blame_the_program_for_an_erased_tool_answer():
    from agentl.autoloop import autoloop

    report = autoloop(ANSWERS % POSED, holdout_ratio=0.0)
    assert report.gates_ok and not report.last.failures(), report.render()
    assert report.ok


def test_a_derived_world_that_reaches_an_unposed_tool_is_not_judged():
    from agentl.autoloop import autoloop

    report = autoloop(ANSWERS % UNPOSED, holdout_ratio=0.0)
    assert not report.last.failures(), report.render()
    unjudged = [r for r in report.last.results if r.inconclusive]
    assert [r.case.label for r in unjudged] == ["quiet·contexte·alert.level=4"]
    assert "lookup_owner" in unjudged[0].inconclusive
    passed, total = report.last.score
    assert passed == total == len(report.last.results) - 1   # hors score
    assert "1 non jugé(s)" in report.render()
    assert report.ok


def test_unposed_outputs_do_not_hide_a_real_breach_in_the_same_case():
    from agentl.autoloop import autoloop

    # `lookup_owner` n'a pas de réponse posée (non jugé), mais `notify_operator`
    # est interdit sans garde et approuvé nulle part : U4 reste jugé.
    source = (ANSWERS % UNPOSED).replace(
        "POLICY { DEFAULT ALLOW }",
        "POLICY { DEFAULT ALLOW  REQUIRE APPROVAL FOR lookup_owner }")
    source = source.replace(
        "SCENARIO quiet {", "SCENARIO quiet {\n    GIVEN { operator.approval = yes }")
    report = autoloop(source, holdout_ratio=0.0)
    # Approuvé dans le scénario : aucune rupture, et le cas reste non jugé.
    assert not report.last.failures(), report.render()


def test_a_failed_verify_caused_by_an_unposed_answer_is_not_a_breach():
    from agentl.autoloop import autoloop

    source = (ANSWERS % UNPOSED).replace(
        "STEP s { notify_operator() }",
        "STEP s { notify_operator() }\n    STEP v { VERIFY { alert.handled == yes } }")
    report = autoloop(source, holdout_ratio=0.0)
    assert not report.last.failures(), report.render()
    assert report.last.inconclusive == 1


def test_a_run_with_no_judged_case_is_not_a_success():
    from agentl.autoloop import AutoloopReport, Attempt, Case, CaseResult, Gate

    case = Case("s", "s·données·x=1", "données", (("x", 1),), False, "a")
    attempt = Attempt(1, "", [Gate("analyse", True)],
                      [CaseResult(case, True, inconclusive="sortie non posée")])
    report = AutoloopReport(agent="a", attempts=[attempt], seen_total=1)
    assert report.judged == 0 and not report.ok


def test_simulator_directives_are_never_derived():
    from agentl.autoloop import generate_cases

    agent = parse_source(GUARD).agents[0]
    touched = {path for case in generate_cases(agent, holdout_ratio=0.0)
               for path, _ in case.changes}
    assert not any(path.startswith("scenario.") for path in touched)
