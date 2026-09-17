"""Tests AAA du RUNTIME (runtime.py / host.py / llm.py).

Propriétés de sûreté d'exécution visées, adverses par construction :

  * aucune action n'atteint l'hôte sans traverser le moteur de politiques ;
  * fail-closed : toute erreur d'une politique, d'une précondition, d'un
    approbateur ou de l'hôte se referme (refus / repli sûr), jamais vers le
    permissif ni vers un crash du runtime ;
  * robustesse LLM : un oracle qui lève, renvoie n'importe quoi ou ment sur un
    plan ne crashe pas le runtime et ne laisse passer aucune action non
    gouvernée ;
  * terminaison : les bornes de boucle restent finies même sur AST malformé.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl import Host, MockLLM, Runtime, Symbol, UNDEFINED
from agentl.llm import LLM
from agentl.runtime import Trace
from agentl.nodes import (
    Agent, AskStmt, BinOp, CallExpr, CallStmt, Decide, LoopSpec, LoopStmt,
    Literal, MemorySpec, Observer, PathExpr, Plan, PolicyRule, ReasonStmt,
    Step, ToolDecl,
)


# --- un noeud dont l'évaluation lève toujours EvalError (arith sur symbole) ---
def RAISE():
    return BinOp(op="*", left=PathExpr(parts=["never_resolves"]),
                 right=Literal(value=2))


def _agent(**kw) -> Agent:
    kw.setdefault("name", "T")
    kw.setdefault("memory", MemorySpec())
    return Agent(**kw)


def _recording_host(tool_name="danger", result=None):
    host = Host()
    calls = []

    @host.tool(tool_name)
    def _impl(**kwargs):
        calls.append(kwargs)
        return result if result is not None else {"ok": True}

    return host, calls


# ===================================================================== POLICY
def test_policy_guard_that_raises_fails_closed():
    """Une garde de politique qui lève doit REFUSER (fail-closed), pas crasher,
    et surtout ne jamais atteindre l'hôte."""
    tool = ToolDecl(name="danger", inputs={}, risk="LOW")
    pol = PolicyRule(effect="ALLOW", target="danger", guard=RAISE())
    agent = _agent(tools=[tool], policies=[pol], policy_default="ALLOW")
    host, calls = _recording_host()
    rt = Runtime(agent, host=host)

    out = rt.call_tool("danger", {}, origin="plan")

    assert out is None                       # action refusée
    assert calls == []                       # HÔTE JAMAIS ATTEINT
    assert rt.metrics["blocked"] >= 1
    assert rt.metrics["tool_calls"] == 0


def test_policy_error_does_not_leak_to_permissive():
    """Même avec DEFAULT ALLOW, une politique en erreur ne doit pas dégrader
    vers le permissif : le repli est fermé."""
    tool = ToolDecl(name="danger", inputs={}, risk="HIGH")
    # Une DENY dont la garde lève : en cas d'erreur on ne doit pas 'louper' la
    # DENY et laisser filer l'action.
    pol = PolicyRule(effect="DENY", target="danger", guard=RAISE())
    agent = _agent(tools=[tool], policies=[pol], policy_default="ALLOW")
    host, calls = _recording_host()
    rt = Runtime(agent, host=host)

    out = rt.call_tool("danger", {}, origin="plan")
    assert out is None
    assert calls == []


def test_unevaluable_arguments_block_the_call():
    """Un argument d'appel inévaluable bloque l'action sans crasher, et
    surtout sans l'exécuter à moitié."""
    tool = ToolDecl(name="danger", inputs={"x": "Number"}, risk="LOW")
    agent = _agent(tools=[tool], policies=[], policy_default="ALLOW")
    host, calls = _recording_host()
    rt = Runtime(agent, host=host)
    rt.state.tick = 1
    # danger(<arith sur symbole>) : l'évaluation de l'argument lève.
    stmt = CallStmt(call=CallExpr(name="danger", args=[RAISE()]))
    rt.exec_stmt(stmt)                        # ne doit pas lever
    assert calls == []
    assert rt.metrics["blocked"] >= 1


# =================================================================== REQUIRES
def test_requires_that_raises_skips_tool():
    """Une précondition REQUIRES inévaluable ne doit pas exécuter l'action."""
    tool = ToolDecl(name="danger", inputs={}, risk="LOW", requires=RAISE())
    agent = _agent(tools=[tool], policies=[], policy_default="ALLOW")
    host, calls = _recording_host()
    rt = Runtime(agent, host=host)

    out = rt.call_tool("danger", {}, origin="plan")
    assert out is None
    assert calls == []


# ====================================================================== HÔTE
def test_host_invoke_exception_is_contained():
    """Un outil qui lève ne crashe pas le runtime (propriété déjà tenue)."""
    tool = ToolDecl(name="boom", inputs={}, risk="LOW")
    agent = _agent(tools=[tool], policies=[], policy_default="ALLOW")
    host = Host()

    @host.tool("boom")
    def _boom(**kw):
        raise RuntimeError("hardware on fire")

    rt = Runtime(agent, host=host)
    out = rt.call_tool("boom", {}, origin="plan")
    assert out is None
    assert rt.metrics["tool_calls"] == 0


def test_approver_that_raises_fails_closed():
    """Un approbateur humain qui lève doit valoir REFUS, pas crash ni passage."""
    tool = ToolDecl(name="danger", inputs={}, risk="HIGH")
    pol = PolicyRule(effect="REQUIRE_APPROVAL", target="danger", guard=None)
    agent = _agent(tools=[tool], policies=[pol], policy_default="ALLOW")
    host, calls = _recording_host()

    def _raiser(request):
        raise RuntimeError("approver process died")

    host.approver = _raiser
    rt = Runtime(agent, host=host)

    out = rt.call_tool("danger", {}, origin="plan")
    assert out is None
    assert calls == []                       # non approuvé ⇒ non exécuté
    assert rt.metrics["blocked"] >= 1


def test_asker_that_raises_falls_back_to_default():
    """Un opérateur (ASK) qui lève ne crashe pas ; le DEFAULT s'applique."""
    agent = _agent(policy_default="ALLOW")
    host = Host()

    def _raiser(q, r):
        raise RuntimeError("operator console offline")

    host.asker = _raiser
    rt = Runtime(agent, host=host)

    stmt = AskStmt(addressee="operator", question="go?", reason="test",
                   default=Literal(value=Symbol("no")))
    answer = rt._exec_ask(stmt)
    assert str(answer) == "no"               # repli sur DEFAULT, pas de crash


def test_sensor_that_raises_does_not_crash_tick():
    """Un capteur défaillant ne doit pas faire tomber la boucle."""
    agent = _agent(observers=[Observer(path="disk.usage")],
                   policy_default="ALLOW")
    host = Host()

    @host.sensor("disk.usage")
    def _sensor():
        raise RuntimeError("sensor unreachable")

    rt = Runtime(agent, host=host)
    rt.phase_observe()                       # ne doit pas lever
    assert "disk.usage" not in rt.state.world


# ======================================================================= LLM
class _ExplodingLLM(LLM):
    def reason(self, task, context, produce):
        raise RuntimeError("LLM 429 rate limit")

    def select_plan(self, context, candidates):
        raise RuntimeError("LLM timeout")


def test_llm_reason_exception_without_explicit_default_is_indeterminate():
    """Un LLM.reason qui lève ne crashe pas et n'invente aucune valeur."""
    agent = _agent(policy_default="ALLOW")
    rt = Runtime(agent, host=Host(), llm=_ExplodingLLM())
    stmt = ReasonStmt(task="triage", produce={"confidence": "Number",
                                              "root_cause": "Symbol"})
    produced = rt._run_reason(stmt)
    assert produced.get("confidence") is UNDEFINED
    assert rt.state.get("confidence") is UNDEFINED


def test_llm_select_plan_exception_enqueues_nothing():
    """Un LLM.select_plan qui lève ne crashe pas le tick et ne met aucun plan
    en file."""
    plan = Plan(name="p", steps=[Step(name="1", body=[])])
    decide = Decide(rules=[], reason=ReasonStmt(task="x", produce={}))
    agent = _agent(plans=[plan], decide=decide, policy_default="ALLOW")
    rt = Runtime(agent, host=Host(), llm=_ExplodingLLM())
    rt.state.tick = 1
    rt.phase_select_plan()                   # ne doit pas lever
    assert len(rt.plan_queue) == 0


def test_llm_cannot_inject_undeclared_plan():
    """Le LLM propose un plan inexistant : il doit être rejeté, jamais mis en
    file (le LLM ne contrôle pas le runtime)."""
    real = Plan(name="real", steps=[Step(name="1", body=[])])
    decide = Decide(rules=[], reason=ReasonStmt(task="x", produce={}))
    agent = _agent(plans=[real], decide=decide, policy_default="ALLOW")
    llm = MockLLM(plan_choice=lambda ctx, cands: "rm_rf_slash")
    rt = Runtime(agent, host=Host(), llm=llm)
    rt.state.tick = 1
    rt.phase_select_plan()
    assert "rm_rf_slash" not in rt.plan_queue
    assert len(rt.plan_queue) == 0


# =============================================================== TERMINAISON
def test_loopstmt_with_none_max_iter_terminates():
    """Un LoopStmt dont MAX est absent (None) ne doit pas crasher ni boucler
    à l'infini."""
    body = [CallStmt(call=CallExpr(name="noop"))]
    loop = LoopStmt(body=body, until=None, max_iter=None)  # AST malformé
    tool = ToolDecl(name="noop", inputs={}, risk="LOW")
    agent = _agent(tools=[tool], policy_default="ALLOW")
    host = Host()

    @host.tool("noop")
    def _n(**kw):
        return {"ok": True}

    rt = Runtime(agent, host=host)
    rt.state.tick = 1
    rt.exec_stmt(loop)                        # ne doit ni lever ni pendre
    # borne finie appliquée : au moins une itération, jamais infinie
    assert rt.metrics["tool_calls"] >= 1


def test_loop_until_that_raises_still_terminates():
    """Une condition UNTIL inévaluable ne doit pas empêcher la terminaison."""
    loop = LoopSpec(body=[], until=RAISE(), max_iter=3)
    agent = _agent(loop=loop, policy_default="ALLOW")
    rt = Runtime(agent, host=Host())
    rt.run()                                  # ne doit pas lever
    assert rt.state.tick == 3                 # borne MAX respectée


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
