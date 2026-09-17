"""Hôte simulé pour `soc_analyst.agent`.

Le fichier `.agent` déclare les contrats ; ce module fournit les
implémentations. Le même programme agentique peut donc être rejoué contre un
monde simulé, un banc de test ou la production, sans être modifié.

Les paramètres Python portent exactement les noms déclarés dans les blocs
`INPUT` : le runtime appelle toujours les outils par mot-clé.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl import Host, MockLLM, Symbol


class SimulatedWorld:
    def __init__(self, criticality: str = "MEDIUM"):
        self.criticality = Symbol(criticality)
        self.contained = False
        self.clock = 45.0
        self.anomaly = 0.91
        self.action = None          # quelle action a effectivement confiné


def build(criticality: str = "MEDIUM", operator_approves: bool = True,
          account_known: bool = True, alerts: int = 37,
          anomaly: float = 0.91, integrity: str = "compromised"):
    """Retourne (host, llm, world) prêts à être branchés sur le runtime."""
    world = SimulatedWorld(criticality)
    world.anomaly = anomaly
    h = Host()

    # ----------------------------------------------------------- capteurs
    h.sensors["wazuh.alert_count"] = lambda: 0 if world.contained else alerts
    h.sensors["network.anomaly_score"] = \
        lambda: 0.12 if world.contained else world.anomaly
    h.sensors["endpoint.integrity"] = \
        lambda: Symbol("isolated") if world.contained else Symbol(integrity)
    h.sensors["asset.criticality"] = lambda: world.criticality
    h.sensors["threat.status"] = \
        lambda: Symbol("contained") if world.contained else Symbol("active")

    def _response_time():
        world.clock += 30.0
        return world.clock

    h.sensors["incident.response_time"] = _response_time
    # L'isolement n'est pas cru sur parole : `isolate_endpoint` *prédit*
    # `isolated = confirmed`, le capteur le confirme ou le dément, et le
    # runtime tient le compte (registre de dérive, T9). Un EFFECT que rien
    # ne perçoit ne peut jamais être démenti.
    h.sensors["isolated"] = \
        lambda: Symbol("confirmed") if world.contained else Symbol("no")

    # -------------------------------------------------------------- outils
    def query_wazuh(since):
        return {"alerts": 37, "top_rule": Symbol("brute_force_ssh")}

    def inspect_network():
        return {"anomaly_score": world.anomaly, "peer": "203.0.113.44"}

    def inspect_endpoint(host):
        return {"integrity": Symbol("compromised"), "persistence": True}

    def isolate_endpoint(host):
        world.contained = True
        world.action = "isolate_endpoint"
        return {"isolated": Symbol("confirmed")}

    def quarantine_account(account):
        world.contained = True
        world.action = "quarantine_account"
        return {"suspended": Symbol("confirmed")}

    def create_ticket(title, severity):
        return {"ticket_id": "SOC-4711"}

    def notify_operator(message):
        return {"delivered": Symbol("ok")}

    for fn in (query_wazuh, inspect_network, inspect_endpoint,
               isolate_endpoint, quarantine_account, create_ticket,
               notify_operator):
        h.tools[fn.__name__] = fn

    # ------------------------------------------- humain dans la boucle
    h.approver = lambda request: operator_approves
    h.asker = lambda question, reason: Symbol("acknowledged")

    # ---------------------------------------------------------------- LLM
    # Le LLM identifie des *entités*. Il n'attribue plus de probabilité :
    # c'est le moteur d'inférence bayésienne qui s'en charge (v0.4).
    identified = {"suspected_host": "PC-042"}
    if account_known:
        identified["suspected_account"] = "svc-backup"
    llm = MockLLM({"identifier l": identified})

    return h, llm, world
