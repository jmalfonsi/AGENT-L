"""Régressions du relais de traces Valmont vers ENTERPRISE-SIM."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from agentl import Host, Runtime
from agentl.nodes import Agent, MemorySpec
from agentl.runtime import Trace
from valmont_sim import SimClient


def test_trace_sink_recoit_les_evenements_sans_modifier_le_journal_local():
    received = []
    trace = Trace(received.append)

    trace.log(3, "TOOL", "production.rate(line=L2)", "ok")

    assert trace.events == received


def test_trace_sink_indisponible_ne_bloque_jamais_le_runtime():
    trace = Trace(lambda event: (_ for _ in ()).throw(OSError("hors ligne")))

    trace.log(1, "OBSERVE", "site.read_ok = yes")

    assert len(trace.events) == 1


def test_runtime_branche_automatiquement_le_relais_de_l_hote():
    received = []
    host = Host()
    host.trace_sink = received.append
    runtime = Runtime(Agent(name="TRACE_TEST", memory=MemorySpec()), host=host)

    runtime.trace.log(2, "INFO", "relais actif")

    assert received == runtime.trace.events


def test_valmont_envoie_les_traces_par_lots_corriges_par_run():
    client = SimClient(base_url="http://127.0.0.1:1", dry_run=True)
    with patch.object(client, "_post_agent_events") as post:
        sink = client.trace_sink("VALMONT_SUPERVISION")
        sink(SimpleNamespace(tick=4, kind="TOOL", text="production.rate(line=L2)", detail="risk=MEDIUM"))
        client.flush_agent_events()

    assert post.call_count == 2
    run = post.call_args_list[0].args[0][0]
    action = post.call_args_list[1].args[0][0]
    assert run["runId"] == action["runId"] == client.run_id
    assert run["kind"] == "RUN"
    assert action["kind"] == "TOOL"
    assert action["tick"] == 4
    assert action["direction"] == "AGENT_TO_SIM"


def test_dialogue_conserve_message_reponse_et_commandes_proposees():
    client = SimClient(base_url="http://127.0.0.1:1", dry_run=True)
    response = {
        "reply": "Oui, relancez L2.",
        "commands": [{"command": "production.rate", "args": {"line": "L2", "rate_pct": 100}}],
        "refused": [],
    }
    with patch.object(client, "_request", return_value=response), \
         patch.object(client, "agent_event") as journal:
        result = client.chat_full("Puis-je relancer L2 ?")

    assert result == response
    assert journal.call_count == 2
    outbound, inbound = journal.call_args_list
    assert outbound.args == ("DIALOGUE", "Puis-je relancer L2 ?")
    assert outbound.kwargs["direction"] == "AGENT_TO_HUMAN"
    assert inbound.args == ("DIALOGUE", "Oui, relancez L2.")
    assert inbound.kwargs["direction"] == "HUMAN_TO_AGENT"
    assert inbound.kwargs["payload"]["commands"][0]["args"]["line"] == "L2"
