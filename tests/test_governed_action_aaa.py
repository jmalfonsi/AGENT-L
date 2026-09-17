"""Tests différentiels du Module d'autorisation gouvernée."""
from __future__ import annotations

import pytest

from agentl import Host, Runtime, Symbol, UNDEFINED, parse_source


def _runtime(kind: str, *, approved: bool):
    body = (
        'DELEGATE worker { TASK "work" EXPECT { ok } }'
        if kind == "delegate" else "worker()"
    )
    source = f"""
    AGENT governed {{
      TOOL worker {{ RISK HIGH }}
      POLICY {{
        DEFAULT ALLOW
        REQUIRE APPROVAL FOR worker
      }}
      PLAN execute WHEN go == yes {{
        {body}
      }}
    }}
    """
    agent = parse_source(source).agents[0]
    host = Host()
    calls = []

    @host.tool("worker")
    def worker():
        calls.append("tool")
        return {"ok": Symbol("yes")}

    def delegate(payload):
        calls.append("delegate")
        return {"ok": Symbol("yes")}

    host.subagents["worker"] = delegate
    host.approver = lambda request: approved
    runtime = Runtime(agent, host)
    runtime.state.set_world("go", Symbol("yes"))
    return runtime, calls


@pytest.mark.parametrize("kind", ["tool", "delegate"])
def test_approval_refusal_is_identical_for_every_action_adapter(kind):
    runtime, calls = _runtime(kind, approved=False)

    runtime.tick()

    assert calls == []
    assert runtime.metrics["approvals"] == 1
    assert runtime.metrics["blocked"] == 1
    assert runtime.state.get("last_action.blocked") is True
    assert "non approuvé" in runtime.trace.render()


@pytest.mark.parametrize("kind", ["tool", "delegate"])
def test_approval_success_is_identical_for_every_action_adapter(kind):
    runtime, calls = _runtime(kind, approved=True)

    runtime.tick()

    assert calls == [kind]
    assert runtime.metrics["approvals"] == 1
    assert runtime.metrics["blocked"] == 0
    assert runtime.state.get("last_action.blocked") is False


@pytest.mark.parametrize("kind", ["tool", "delegate"])
def test_policy_is_checked_once_and_approval_cannot_rewrite_invocation(kind):
    runtime, calls = _runtime(kind, approved=True)
    checked = []
    approved = []
    original_check = runtime.policy.check

    def check_once(request, state):
        checked.append(request)
        return original_check(request, state)

    def mutating_approver(request):
        approved.append(request)
        request.args["injected"] = Symbol("unsafe")
        return True

    runtime.policy.check = check_once
    runtime.host.approver = mutating_approver

    runtime.tick()

    assert calls == [kind]
    assert len(checked) == 1
    assert len(approved) == 1
    assert approved[0] is not checked[0]
    assert approved[0].tool == checked[0].tool
    assert approved[0].risk == checked[0].risk
    assert approved[0].origin == checked[0].origin
    assert "injected" not in checked[0].args


@pytest.mark.parametrize("kind", ["tool", "delegate"])
def test_approver_exception_fails_closed_for_every_action_adapter(kind):
    runtime, calls = _runtime(kind, approved=True)

    def broken_approver(request):
        raise RuntimeError("approver unavailable")

    runtime.host.approver = broken_approver
    runtime.tick()

    assert calls == []
    assert runtime.metrics["approvals"] == 1
    assert runtime.metrics["blocked"] == 1
    assert runtime.state.get("last_action.blocked") is True
    assert "approbateur en erreur" in runtime.trace.render()


@pytest.mark.parametrize("kind", ["tool", "delegate"])
def test_policy_exception_fails_closed_for_every_action_adapter(kind):
    runtime, calls = _runtime(kind, approved=True)

    def broken_policy(request, state):
        raise RuntimeError("policy unavailable")

    runtime.policy.check = broken_policy
    runtime.tick()

    assert calls == []
    assert runtime.metrics["approvals"] == 0
    assert runtime.metrics["blocked"] == 1
    assert runtime.state.get("last_action.blocked") is True
    assert "repli fermé (DENY)" in runtime.trace.render()


@pytest.mark.parametrize("kind", ["tool", "delegate"])
def test_indeterminate_confidence_reaches_every_action_adapter(kind):
    body = (
        'DELEGATE worker { TASK "work" EXPECT { ok } }'
        if kind == "delegate" else "worker()"
    )
    source = f"""
    AGENT confidence_guard {{
      TOOL worker {{ RISK HIGH }}
      POLICY {{
        DEFAULT ALLOW
        NEVER worker WHEN action.confidence < 0.9
      }}
      PLAN execute WHEN go == yes {{
        REASON {{
          TASK "confidence"
          PRODUCE {{ confidence: Number }}
        }}
        {body}
      }}
    }}
    """
    agent = parse_source(source).agents[0]
    host = Host()
    calls = []
    host.tools["worker"] = lambda: calls.append("tool") or {}
    host.subagents["worker"] = (
        lambda payload: calls.append("delegate")
        or {"ok": Symbol("yes")}
    )
    runtime = Runtime(agent, host)
    runtime.state.set_world("go", Symbol("yes"))

    runtime.tick()

    assert runtime.state.get("confidence") is UNDEFINED
    assert calls == []
    assert runtime.metrics["blocked"] == 1
