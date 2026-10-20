"""Régressions de la 1.15.1 : un capteur muet rend vraiment son chemin indéfini.

Jusqu'en 1.15.0, un capteur qui se taisait après avoir parlé gardait sa
dernière valeur dans le monde, et `phase_update_beliefs` la recopiait en
croyance comme une observation du tick courant (confiance 0.95). La trace
annonçait pourtant « chemin indéfini — toute garde portant sur ce chemin
échoue fermé » (SPEC §31) : un `NEVER` dont la garde lisait ce chemin jugeait
sur la mesure d'avant la panne, et laissait passer l'action.

    python -m pytest tests/test_v1151_regressions.py -q
"""
from __future__ import annotations

import pytest

from agentl import Host, MockLLM, Symbol, parse_source
from agentl.core import UNDEFINED
from agentl.runtime import Runtime

SOURCE = """
AGENT payments {
  OBSERVE { account.frozen%s }
  TOOL transfer {
    INPUT  { amount: Number }
    OUTPUT { receipt: String }
    RISK   HIGH
  }
  POLICY {
    DEFAULT ALLOW
    NEVER transfer WHEN account.frozen == yes
  }
  PLAN pay WHEN tick.go != no {
    STEP send { transfer(amount=100) }
  }
}
"""


def run(readings, conduct="", ticks=None):
    """`readings` : une valeur par tick, `None` pour un capteur muet."""
    agent = parse_source(SOURCE % conduct).agents[0]
    calls, asked, seen = [], [], iter(readings)
    host = Host()

    def frozen():
        value = next(seen)
        if value is None:
            raise ConnectionError("capteur muet")
        return Symbol(value)

    host.sensors["account.frozen"] = frozen
    host.tool("transfer")(lambda amount: calls.append(amount) or {"receipt": "r"})
    host.ask = lambda question, reason="": asked.append(question) or "ok"
    rt = Runtime(agent, host, MockLLM())
    rt.run(max_ticks=ticks or len(readings))
    return rt, calls, asked


def test_a_muted_sensor_no_longer_keeps_its_last_value():
    rt, calls, _ = run(["no", None])

    assert calls == [100]                      # tick 1 seulement
    assert rt.state.get("account.frozen") is UNDEFINED
    assert "account.frozen" not in rt.state.beliefs
    assert rt.state.get("sensors.account.frozen.available") is False
    assert rt.metrics["blocked"] == 1          # le NEVER échoue fermé au tick 2


def test_escalate_is_undefined_too_while_the_human_decides():
    rt, calls, asked = run(["no", None, None], " ON UNKNOWN ESCALATE")

    assert calls == [100]
    assert rt.state.get("account.frozen") is UNDEFINED
    assert asked == ["capteur muet : account.frozen"]   # une seule fois


def test_degrade_still_substitutes_its_declared_fallback():
    rt, calls, _ = run(["no", None], " ON UNKNOWN DEGRADE yes")

    assert calls == [100]                      # repli « gelé » : bloqué
    belief = rt.state.beliefs["account.frozen"]
    assert belief.value == Symbol("yes") and belief.source == "fallback"


def test_a_sensor_that_comes_back_is_believed_again():
    rt, calls, _ = run(["no", None, "no"])

    assert calls == [100, 100]                 # ticks 1 et 3
    assert rt.state.get("account.frozen") == Symbol("no")
    assert rt.state.get("sensors.account.frozen.available") is True


def test_a_sensor_not_read_by_choice_keeps_its_value():
    """Perception sélective : `OBSERVE … WHEN` faux n'est pas une panne."""
    source = SOURCE.replace("OBSERVE { account.frozen%s }",
                            "OBSERVE { account.frozen WHEN tick.first != no }"
                            "\n  OBSERVE { tick.first }")
    agent = parse_source(source).agents[0]
    host, calls = Host(), []
    host.sensors["account.frozen"] = lambda: Symbol("no")
    first = iter([Symbol("yes"), Symbol("no")])
    host.sensors["tick.first"] = lambda: next(first)
    host.tool("transfer")(lambda amount: calls.append(amount) or {"receipt": "r"})
    rt = Runtime(agent, host, MockLLM())
    rt.run(max_ticks=2)

    assert calls == [100, 100]
    assert rt.state.get("account.frozen") == Symbol("no")


@pytest.mark.parametrize("conduct", ["", " ON UNKNOWN ESCALATE"])
def test_the_trace_now_tells_the_truth(conduct):
    rt, _, _ = run(["no", None], conduct)
    text = rt.trace.render()
    assert "refusé" in text.split("tick 2")[1]


def test_a_declared_prior_survives_a_silent_sensor():
    """Un a priori n'est pas une mesure : la panne ne l'efface pas."""
    source = (SOURCE % "").replace(
        "  TOOL transfer", "  BELIEF { account.frozen = no CONFIDENCE 0.9 "
        "SOURCE prior }\n  TOOL transfer")
    agent = parse_source(source).agents[0]
    host, calls = Host(), []

    def mute():
        raise ConnectionError("capteur muet")
    host.sensors["account.frozen"] = mute
    host.tool("transfer")(lambda amount: calls.append(amount) or {"receipt": "r"})
    rt = Runtime(agent, host, MockLLM())
    rt.run(max_ticks=2)

    belief = rt.state.beliefs["account.frozen"]
    assert (belief.value, belief.source) == (Symbol("no"), "prior")
    assert calls == [100, 100]


def test_an_expected_effect_is_forgotten_when_no_perception_confirms_it():
    """Une perception ultérieure prime sur un EFFECT ; sans elle, rien ne le
    confirme : la garde échoue fermé au lieu de croire la postcondition."""
    agent = parse_source("""
AGENT payments {
  OBSERVE { account.frozen, phase }
  TOOL unfreeze {
    INPUT { }  OUTPUT { done: Symbol }  EFFECT { account.frozen = no }
    RISK LOW
  }
  TOOL transfer { INPUT { amount: Number } OUTPUT { receipt: String } RISK HIGH }
  POLICY {
    DEFAULT ALLOW
    NEVER transfer WHEN account.frozen == yes
  }
  PLAN thaw WHEN phase == thaw { STEP a { unfreeze() } }
  PLAN pay WHEN phase == pay { STEP send { transfer(amount=100) } }
}
""").agents[0]
    readings = iter([Symbol("yes"), None])
    phases = iter([Symbol("thaw"), Symbol("pay")])
    host, calls = Host(), []

    def frozen():
        value = next(readings)
        if value is None:
            raise ConnectionError("capteur muet")
        return value
    host.sensors["account.frozen"] = frozen
    host.sensors["phase"] = lambda: next(phases)
    host.tool("unfreeze")(lambda: {"done": Symbol("yes")})
    host.tool("transfer")(lambda amount: calls.append(amount) or {"receipt": "r"})
    rt = Runtime(agent, host, MockLLM())
    rt.run(max_ticks=2)

    assert rt.state.get("account.frozen") is UNDEFINED
    assert calls == []
    assert rt.metrics["blocked"] == 1
