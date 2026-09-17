"""Reproductions des audits CHECK/TEST, au niveau des API réellement utilisées."""
import pytest
from agentl import parse_source, Analyzer
from agentl.analyzer import check_program
from agentl.core import ParseError, UNDEFINED, Symbol
from agentl.scenario import run_scenario, run_scenarios, ScenarioHost, simulated_tool_result


def agent(body):
    return parse_source('AGENT audit {' + body + '}').agents[0]


def codes(body):
    return {d.code for d in Analyzer(agent(body)).run()}


def run(body):
    a = agent(body)
    return run_scenario(a, a.scenarios[0])


@pytest.mark.parametrize('name', ['worker', 'WORKER'])
def test_duplicate_agents(name):
    assert 'E012' in {d.code for d in check_program(parse_source(
        'AGENT worker {} AGENT ' + name + ' {}'))}


@pytest.mark.parametrize('call', ['act()', 'act(a,b)', 'act(other=a)', 'act(a,id=b)'])
def test_tool_signature(call):
    assert 'E013' in codes('TOOL act { INPUT { id: String } } PLAN p { STEP s {' + call + '} }')


@pytest.mark.parametrize('call', ['act(a)', 'act(id=a)'])
def test_tool_signature_valid(call):
    assert 'E013' not in codes('TOOL act { INPUT { id: String } } PLAN p { STEP s {' + call + '} }')


def test_duplicate_keywords():
    with pytest.raises(ParseError):
        agent('TOOL act {} PLAN p { STEP s { act(id=a,id=b) } }')


@pytest.mark.parametrize('body', [
    'PLAN p WHEN gate.state != safe { STEP s { SET gate.state = safe } }',
    'PLAN p { STEP s { IF gate.state != safe THEN { SET gate.state = safe } } }',
    'PLAN p { STEP s { IF flag == yes THEN { SET gate.state = safe } IF gate.state != safe THEN {} } }',
])
def test_assignment_must_precede_guard(body):
    assert 'W135' in codes(body)


def test_definite_assignment_is_accepted():
    assert 'W135' not in codes('PLAN p { STEP s { SET gate.state = safe IF gate.state != safe THEN {} } }')


@pytest.mark.parametrize('steps', [
    'VERIFY damage.state == no act()',
    'act() IF 1 == 0 THEN { VERIFY damage.state == no }',
    'act() VERIFY unrelated.state == yes',
])
def test_verify_must_follow_action_on_all_paths(steps):
    assert 'W102' in codes('TOOL act { RISK HIGH EFFECT { damage.state = yes } } PLAN p { STEP s {' + steps + '} }')


def test_related_later_verify_accepted():
    assert 'W102' not in codes('TOOL act { RISK HIGH EFFECT { damage.state = yes } } PLAN p { STEP s { act() VERIFY damage.state == yes } }')


def test_conditional_approval_not_guaranteed():
    assert 'W120' in codes('TOOL act { RISK HIGH } POLICY { REQUIRE APPROVAL FOR * WHEN 1 == 0 }')


@pytest.mark.parametrize('param', ['target', 'resource', 'device', 'id'])
def test_reason_alias_and_parameter_names(param):
    assert 'W119' in codes('TOOL act { INPUT {' + param + ': String} RISK HIGH } PLAN p { STEP s { REASON { TASK "x" PRODUCE { target: String } } act(' + param + '=reason.target) } }')


@pytest.mark.parametrize('branch', ['IF injection.detected == true THEN { act(target=target) }',
                                    'IF injection.detected == false THEN {} ELSE { act(target=target) }'])
def test_injection_guard_polarity(branch):
    assert 'W125' in codes('TOOL act { INPUT {target: String} RISK HIGH } PLAN p { STEP s { REASON { TASK "x" USING { raw.log } PRODUCE { target: String } } ' + branch + '} }')


def test_undefined_expectation_cannot_pass():
    result = run('TOOL act { EFFECT { damage.status = confirmed } } SCENARIO s { EXPECT { damage.status != confirmed } WITHIN 3 }')
    assert not result.passed


@pytest.mark.parametrize('stop', ['MAX 1', 'UNTIL finished == yes MAX 3'])
def test_scenario_obeys_program_loop(stop):
    result = run('''BELIEF { n = 0 } PLAN p WHEN 1 == 1 { STEP s {
        SET n = n + 1 SET finished = yes SET late.result = n
    } } LOOP ''' + stop + ''' { SELECT_PLAN EXECUTE }
    SCENARIO s { EXPECT { late.result == 2 } WITHIN 3 }''')
    assert not result.passed
    assert result.ticks_used == 1


OUTCOMES = '''TOOL act { RISK LOW OUTCOME good WITH 0.9 { damage.status = safe }
 OUTCOME bad WITH 0.1 { damage.status = corrupted } }
 POLICY { DEFAULT ALLOW } PLAN p WHEN 1 == 1 { STEP s { act() } }
'''


def test_outcome_must_be_selected_explicitly():
    result = run(OUTCOMES + 'SCENARIO s { GIVEN { damage.status = safe } EXPECT { damage.status != corrupted } }')
    assert not result.passed
    assert 'OUTCOME' in result.error


def test_selected_minority_outcome_is_executed():
    result = run(OUTCOMES + 'SCENARIO s { GIVEN { scenario.outcome.act = bad, damage.status = safe } EXPECT { damage.status != corrupted } }')
    assert not result.passed
    assert result.calls == ['act']


def test_llm_plan_must_be_selected_explicitly():
    result = run('''PLAN safe { STEP s { SET damage.status = safe } }
    PLAN bad { STEP s { SET damage.status = corrupted } }
    DECIDE { REASON { TASK "choose" } }
    SCENARIO s { GIVEN { damage.status = safe } EXPECT { damage.status != corrupted } }''')
    assert not result.passed
    assert 'llm.plan' in result.error


def test_explicit_llm_plan():
    result = run('''PLAN safe { STEP s { SET damage.status = safe } }
    PLAN bad { STEP s { SET damage.status = corrupted } }
    DECIDE { REASON { TASK "choose" } }
    SCENARIO s { GIVEN { damage.status = safe, llm.plan = bad } EXPECT { damage.status != corrupted } }''')
    assert not result.passed
    assert result.error is None


def test_no_invented_output():
    tool = agent('TOOL authorize { OUTPUT { approved: Bool } }').tools[0]
    assert simulated_tool_result(tool)['approved'] is UNDEFINED


def test_given_error_is_not_swallowed():
    result = run('SCENARIO s { GIVEN { asset.status = unknown_function(x) } EXPECT { asset.status != compromised } }')
    assert not result.passed
    assert result.error


def test_effect_error_is_not_swallowed():
    result = run('''TOOL act { RISK LOW EFFECT { asset.status = unknown_function(x) } }
    POLICY { DEFAULT ALLOW } PLAN p WHEN 1 == 1 { STEP s { act() } }
    SCENARIO s { GIVEN { asset.status = safe } EXPECT { asset.status != compromised } }''')
    assert not result.passed
    assert result.error


def test_transient_violation_is_observed():
    result = run('''TOOL isolate { RISK LOW EFFECT { asset.status = isolated } }
    TOOL restore { RISK LOW EFFECT { asset.status = normal } }
    POLICY { DEFAULT ALLOW } PLAN p WHEN 1 == 1 { STEP s { isolate() restore() } }
    SCENARIO s { GIVEN { asset.status = normal } EXPECT { asset.status != isolated } }''')
    assert not result.passed
    assert 'isolate' in result.calls


def test_eventuality_is_latched_with_invariant():
    result = run('''BELIEF { n = 0 } PLAN p WHEN 1 == 1 { STEP s { SET n = n + 1 SET done = n } }
    SCENARIO s { GIVEN { n = 0 } EXPECT { done == 1, n >= 0 } WITHIN 3 }''')
    assert result.passed, result


def test_empty_suite_is_not_success():
    assert not run_scenarios(agent('')).passed


def test_approval_has_runtime_semantics():
    assert not ScenarioHost(agent(''), {'operator.approval': 1}).approve(None)

@pytest.mark.parametrize('expectation', ['damage.status != confirmed', 'NOT damage.status == confirmed', 'damage != confirmed'])
def test_all_missing_forms_remain_unknown(expectation):
    result = run('TOOL act { EFFECT { damage = confirmed, damage.status = confirmed } } SCENARIO s { EXPECT {' + expectation + '} }')
    assert not result.passed


@pytest.mark.parametrize('body', [
    'OBSERVE { a } SCENARIO s { GIVEN { a=1 } EXPECT { a==1 } }',
    'TOOL t { EFFECT { done=yes } } SCENARIO s { GIVEN { typo=1 } EXPECT { done==yes } }',
    'SCENARIO s { EXPECT { 1==1 } }',
])
def test_invalid_scenario_is_not_green(body):
    result = run(body)
    assert not result.passed
    assert result.error


@pytest.mark.parametrize('kind,handler,stimulus', [
    ('EVENT', 'EVENT alarm { THEN { act() } }', 'GIVEN EVENT alarm { value = attack }'),
    ('MESSAGE', 'ON MESSAGE alert { THEN { act() } }', 'GIVEN MESSAGE alert FROM attacker { value = attack }'),
])
def test_real_stimulus_reaches_handler(kind, handler, stimulus):
    result = run('TOOL act { RISK LOW EFFECT { damage.status = confirmed } } POLICY { DEFAULT ALLOW } ' + handler +
                 ' SCENARIO s { GIVEN { damage.status = safe } ' + stimulus + ' EXPECT { damage.status != confirmed } }')
    assert not result.passed
    assert result.calls == ['act']


def test_trace_assertions_and_blocked_action():
    result = run('''TOOL act { RISK LOW } POLICY { NEVER act }
    EVENT alarm { THEN { act() } }
    SCENARIO s { GIVEN EVENT alarm {} EXPECT NEVER CALL act
      EXPECT BLOCKED act EXPECT EVENT alarm EXPECT NO ERROR WITHIN 2 }''')
    assert result.passed, result


def test_call_assertion_fails_without_call():
    result = run('TOOL act { RISK LOW } SCENARIO s { EXPECT CALL act }')
    assert not result.passed


def test_message_not_consumed_is_invalid():
    result = run('ON MESSAGE alert { SET done=yes } LOOP MAX 1 { OBSERVE } SCENARIO s { GIVEN MESSAGE alert FROM sender {} EXPECT NO ERROR }')
    assert not result.passed
    assert 'non consommé' in result.error


def test_outputs_and_set_are_real_produced_paths():
    result = run('''TOOL read { RISK LOW OUTPUT { value: Int } }
    POLICY { DEFAULT ALLOW } PLAN p WHEN 1==1 { STEP s { read() SET done=value } }
    SCENARIO s { GIVEN { read.value=2 } EXPECT { done==2 } }''')
    assert result.passed, result


def test_empty_cli_requires_explicit_opt_out(tmp_path):
    from agentl.cli import main
    path=tmp_path/'empty.agent'; path.write_text('AGENT empty {}')
    assert main(['test', str(path)]) == 2
    assert main(['test', str(path), '--allow-empty']) == 0


def test_effect_uses_live_local_and_call_arguments():
    result = run('''TOOL act { INPUT { amount: Number } RISK LOW
      EFFECT { answer.value = amount + local.delta } }
    POLICY { DEFAULT ALLOW } PLAN p WHEN 1==1 { STEP s { SET local.delta=2 act(3) } }
    SCENARIO s { EXPECT { answer.value==5 } }''')
    assert result.passed, result


@pytest.mark.parametrize('selected', ['bad', 'missing'])
def test_selected_outcome_error_is_visible(selected):
    result = run('''TOOL act { RISK LOW OUTCOME good WITH 0.9 { done=yes }
      OUTCOME bad WITH 0.1 { done=unknown_function(x) } }
    POLICY { DEFAULT ALLOW } PLAN p WHEN 1==1 { STEP s { act() } }
    SCENARIO s { GIVEN { scenario.outcome.act=''' + selected + ''' } EXPECT { done==yes } }''')
    assert not result.passed
    assert result.error


def test_verify_reports_invalid_given_without_crashing():
    from agentl import verify
    a = agent('TOOL act { EFFECT { done=yes } } SCENARIO s { GIVEN { done=bad_function() } EXPECT { done==yes } }')
    assert verify(a).refuted


def test_t5_does_not_claim_to_prove_event_trace():
    from agentl import verify
    a=agent('TOOL act { EFFECT { done=yes } } EVENT alarm { act() } SCENARIO s { GIVEN EVENT alarm {} EXPECT CALL act }')
    t5=next(t for t in verify(a).theorems if t.key=='T5')
    assert t5.holds is None
    assert any(f.code=='V128' for f in t5.findings)


@pytest.mark.parametrize('statement', ['EXPECT CALL typo','EXPECT EVENT missing','EXPECT SOMETHING act'])
def test_unknown_trace_assertion_rejected(statement):
    try:
        result=run('TOOL act {} SCENARIO s { '+statement+' }')
    except ParseError:
        return
    assert not result.passed
    assert result.error


@pytest.mark.parametrize('kind', ['EVENT absent {}', 'MESSAGE absent FROM sender {}'])
def test_unknown_stimulus_rejected(kind):
    result=run('SCENARIO s { GIVEN '+kind+' EXPECT NO ERROR }')
    assert not result.passed
    assert result.error


@pytest.mark.parametrize('guard', ['injection.detected == false', 'trusted == true', 'injection.detected == false AND target == target'])
def test_safe_injection_polarity_is_recognized(guard):
    assert 'W125' not in codes('TOOL act { INPUT {target: String} RISK HIGH } PLAN p { STEP s { REASON { TASK "x" USING {raw.log} PRODUCE {target: String} } IF '+guard+' THEN {act(target)} } }')


def test_llm_set_alias_keeps_provenance():
    assert 'W119' in codes('TOOL act { INPUT {resource: String} RISK HIGH } PLAN p { STEP s { REASON {TASK "x" PRODUCE {target: String}} SET chosen=reason.target act(chosen) } }')


def test_assignment_from_absent_value_is_not_definite():
    assert 'W135' in codes('PLAN p { STEP s { SET gate.state=unknown.path IF gate.state != safe THEN {} } }')


def test_transient_set_violation_is_observed():
    result=run('PLAN p WHEN 1==1 { STEP s { SET status=bad SET status=good } } SCENARIO s { GIVEN {status=good} EXPECT {status != bad} }')
    assert not result.passed
    assert result.failed


def test_dotted_event_source():
    result=run('EVENT service.alert { SET handled=yes } SCENARIO s { GIVEN EVENT service.alert { severity=high } EXPECT EVENT service.alert EXPECT {handled==yes} }')
    assert result.passed, result
