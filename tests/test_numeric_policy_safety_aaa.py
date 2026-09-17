"""Régressions P0 : une sortie numérique LLM invalide ne désarme jamais
une politique.

Ces tests restent volontairement au seam Runtime/Policy : l'oracle produit la
valeur hostile ou se tait, puis un vrai appel d'outil traverse le moteur de
politiques jusqu'à un hôte enregistreur.
"""
from __future__ import annotations

import math

import pytest

from agentl import Host, MockLLM, Runtime
from agentl.analyzer import Analyzer
from agentl.core import UNDEFINED
from agentl.parser import parse_source


PROGRAM = """
AGENT numeric_guard {
  TOOL deploy { RISK HIGH }
  POLICY {
    DEFAULT ALLOW
    NEVER deploy WHEN risk_score > 0.2
  }
  PLAN assess WHEN 1 == 1 {
    STEP decide {
      REASON {
        TASK "score"
        PRODUCE { risk_score: Number IN [0, 1] }
      }
      deploy()
    }
  }
}
"""


def run_with(answer):
    agent = parse_source(PROGRAM).agents[0]
    calls = []
    host = Host()

    @host.tool("deploy")
    def deploy():
        calls.append("deploy")
        return {}

    llm = MockLLM() if answer is None else MockLLM({"score": {
        "risk_score": answer,
    }})
    runtime = Runtime(agent, host, llm).run(max_ticks=1)
    return runtime, calls


@pytest.mark.parametrize("value", [
    float("nan"), float("inf"), float("-inf"),
    "NaN", "Infinity", "-Infinity",
])
def test_non_finite_llm_number_is_indeterminate_and_policy_fails_closed(value):
    runtime, calls = run_with(value)

    assert runtime.state.get("risk_score") is UNDEFINED
    assert calls == []
    assert runtime.metrics["blocked"] >= 1
    assert all(
        not isinstance(v, float) or math.isfinite(v)
        for v in runtime.state.locals.values()
    )


def test_silent_oracle_without_explicit_default_is_indeterminate_and_blocked():
    runtime, calls = run_with(None)

    assert runtime.state.get("risk_score") is UNDEFINED
    assert calls == []
    assert runtime.metrics["blocked"] >= 1


def test_analyzer_requires_explicit_default_for_policy_guarding_reason_output():
    diagnostics = Analyzer(parse_source(PROGRAM).agents[0]).run()

    assert "W134" in {diagnostic.code for diagnostic in diagnostics}


def test_explicit_safe_default_is_used_for_a_non_finite_answer():
    source = PROGRAM.replace(
        "Number IN [0, 1]", "Number IN [0, 1] DEFAULT 1",
    )
    agent = parse_source(source).agents[0]
    calls = []
    host = Host()

    @host.tool("deploy")
    def deploy():
        calls.append("deploy")
        return {}

    runtime = Runtime(
        agent, host, MockLLM({"score": {"risk_score": "NaN"}}),
    ).run(max_ticks=1)

    assert runtime.state.get("risk_score") == 1.0
    assert calls == []
    assert "W134" not in {d.code for d in Analyzer(agent).run()}


@pytest.mark.parametrize("guard", ["flag", "reason.flag"])
def test_silent_boolean_is_unknown_even_as_a_bare_policy_guard(guard):
    source = f"""
    AGENT boolean_guard {{
      TOOL deploy {{ RISK HIGH }}
      POLICY {{
        DEFAULT DENY
        ALLOW deploy WHEN {guard}
      }}
      PLAN assess WHEN 1 == 1 {{
        REASON {{
          TASK "boolean"
          PRODUCE {{ flag: Bool }}
        }}
        deploy()
      }}
    }}
    """
    agent = parse_source(source).agents[0]
    calls = []
    host = Host()

    @host.tool("deploy")
    def deploy():
        calls.append("deploy")
        return {}

    runtime = Runtime(agent, host, MockLLM()).run(max_ticks=1)

    assert runtime.state.get("flag") is UNDEFINED
    assert calls == []
    assert runtime.metrics["blocked"] >= 1


def test_out_of_range_default_cannot_bypass_the_declared_range():
    source = PROGRAM.replace(
        "Number IN [0, 1]", "Number IN [0, 1] DEFAULT 9",
    )
    agent = parse_source(source).agents[0]
    calls = []
    host = Host()

    @host.tool("deploy")
    def deploy():
        calls.append("deploy")
        return {}

    runtime = Runtime(
        agent, host, MockLLM({"score": {"risk_score": 500}}),
    ).run(max_ticks=1)

    assert runtime.state.get("risk_score") is UNDEFINED
    assert calls == []
    assert runtime.metrics["blocked"] >= 1


@pytest.mark.parametrize("typ", ["Bool", "String", "Symbol"])
def test_non_finite_value_is_rejected_before_type_specific_coercion(typ):
    source = f"""
    AGENT typed_guard {{
      TOOL deploy {{ RISK HIGH }}
      POLICY {{
        DEFAULT DENY
        ALLOW deploy WHEN value
      }}
      PLAN assess WHEN 1 == 1 {{
        REASON {{
          TASK "typed"
          PRODUCE {{ value: {typ} }}
        }}
        deploy()
      }}
    }}
    """
    agent = parse_source(source).agents[0]
    calls = []
    host = Host()

    @host.tool("deploy")
    def deploy():
        calls.append("deploy")
        return {}

    runtime = Runtime(
        agent, host, MockLLM({"typed": {"value": float("nan")}}),
    ).run(max_ticks=1)

    assert runtime.state.get("value") is UNDEFINED
    assert calls == []


def test_runtime_revalidates_third_party_adapter_and_drops_extra_fields():
    class RawLLM:
        last_reason_missing = []

        def reason(self, task, context, produce):
            return {"risk_score": float("nan"), "admin_override": True}

        def select_plan(self, context, candidates):
            return None

    agent = parse_source(PROGRAM).agents[0]
    calls = []
    host = Host()

    @host.tool("deploy")
    def deploy():
        calls.append("deploy")
        return {}

    runtime = Runtime(agent, host, RawLLM()).run(max_ticks=1)

    assert runtime.state.get("risk_score") is UNDEFINED
    assert "admin_override" not in runtime.state.locals
    assert calls == []


@pytest.mark.parametrize("path", ["risk_score", "reason.risk_score"])
def test_w134_covers_bare_and_reason_qualified_paths(path):
    source = PROGRAM.replace(
        "risk_score > 0.2", f"{path} > 0.2",
    ).replace(
        "Number IN [0, 1]", "Number IN [0, 1] DEFAULT 0",
    )

    diagnostics = Analyzer(parse_source(source).agents[0]).run()

    assert "W134" in {diagnostic.code for diagnostic in diagnostics}


@pytest.mark.parametrize(
    "path", ["confidence", "reason.confidence", "action.confidence"],
)
def test_indeterminate_confidence_stays_unknown_in_every_policy_alias(path):
    source = PROGRAM.replace("risk_score", "confidence").replace(
        "confidence > 0.2", f"{path} < 0.9",
    )
    agent = parse_source(source).agents[0]
    calls = []
    host = Host()

    @host.tool("deploy")
    def deploy():
        calls.append("deploy")
        return {}

    runtime = Runtime(agent, host, MockLLM()).run(max_ticks=1)

    assert runtime.state.get("confidence") is UNDEFINED
    assert calls == []
    assert runtime.metrics["blocked"] >= 1
