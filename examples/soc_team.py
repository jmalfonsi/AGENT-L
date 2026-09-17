"""Hôtes simulés pour `soc_team.agent` (v0.6, multi-agent)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl import Host, MockLLM, Symbol


def build(verdict: str = "hostile", confidence: float = 0.93,
          severity: str = "HIGH"):
    """Retourne (hosts, llms) indexés par nom d'agent."""
    world = {"contained": False, "pending": 1}

    analyst = Host()
    analyst.sensors["alert.host"] = lambda: "PC-042"
    analyst.sensors["alert.severity"] = lambda: Symbol(severity)
    analyst.sensors["threat.status"] = \
        lambda: Symbol("contained") if world["contained"] else Symbol("active")

    def block_host(host):
        world["contained"] = True
        return {"blocked": Symbol("confirmed")}

    analyst.tools["block_host"] = block_host
    analyst.approver = lambda request: True

    network = Host()
    network.sensors["queue.pending"] = lambda: world["pending"]

    def inspect_flows(host):
        world["pending"] = 0
        return {"verdict": Symbol(verdict), "confidence": confidence,
                "analyzed_host": host}

    network.tools["inspect_flows"] = inspect_flows

    hosts = {"soc_analyst": analyst, "network_agent": network}
    llms = {"soc_analyst": MockLLM(), "network_agent": MockLLM()}
    return hosts, llms, world
