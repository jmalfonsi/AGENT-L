"""Hôte simulé pour `maintenance.agent` — illustre DELEGATE et l'approbation.

Un service de production dégradé : le LLM identifie la cause racine, un
sous-agent confirme que la base est saine, la politique exige une approbation
humaine avant redémarrage.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl import Host, MockLLM, Symbol


class Service:
    def __init__(self):
        self.healthy = False
        self.latency = 820.0
        self.availability = 0.972


def build(approve: bool = True, confidence: float = 0.88):
    svc = Service()
    h = Host()

    h.sensors["service.status"] = \
        lambda: Symbol("healthy") if svc.healthy else Symbol("degraded")
    h.sensors["service.availability"] = lambda: svc.availability
    h.sensors["service.latency"] = lambda: svc.latency
    h.sensors["service.environment"] = lambda: Symbol("production")
    h.sensors["server.cpu"] = lambda: 31.0 if svc.healthy else 94.0

    def inspect_logs():
        return {"errors": 412, "pattern": Symbol("connection_pool_exhausted")}

    def query_metrics(service):
        return {"cpu": 94.0, "latency": svc.latency}

    def restart_service(service):
        svc.healthy = True
        svc.latency = 110.0
        svc.availability = 0.9993
        return {"status": Symbol("healthy")}

    def create_ticket(title, description):
        return {"ticket_id": "OPS-2210"}

    for fn in (inspect_logs, query_metrics, restart_service, create_ticket):
        h.tools[fn.__name__] = fn

    # Sous-agent : reçoit un contrat INPUT, doit honorer le contrat EXPECT.
    h.subagents["db_agent"] = lambda payload: {
        "db_healthy": Symbol("yes"),
        "evidence": "aucun verrou > 5s sur les 15 dernières minutes",
    }

    h.approver = lambda request: approve
    h.asker = lambda question, reason: Symbol("acknowledged")

    # La sévérité de l'incident conditionne l'ALLOW en production.
    h.sensors["incident.severity"] = lambda: Symbol("HIGH")

    llm = MockLLM({
        "cause racine": {"root_cause": "connection_pool_exhausted",
                         "confidence": confidence},
    })
    return h, llm, svc
