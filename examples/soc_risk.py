"""Hôte simulé pour `soc_risk.agent` (v0.7).

Le monde tire réellement au sort l'issue de chaque action selon les
probabilités déclarées : le plan est conforme, l'exécution ne l'est pas.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl import Host, MockLLM, Symbol


class World:
    def __init__(self, criticality="MEDIUM", seed=7):
        self.rng = random.Random(seed)
        self.criticality = Symbol(criticality)
        self.contained = False
        self.actions = []


def build(criticality: str = "MEDIUM", seed: int = 7,
          quarantine_works: bool = False):
    world = World(criticality, seed)
    h = Host()

    h.sensors["threat.status"] = \
        lambda: Symbol("contained") if world.contained else Symbol("active")
    h.sensors["asset.criticality"] = lambda: world.criticality
    h.sensors["suspected_host"] = lambda: "PC-042"
    h.sensors["suspected_account"] = lambda: "svc-backup"

    def quarantine_account(account):
        world.actions.append("quarantine_account")
        # 30 % des mondes : l'attaquant dispose d'un second accès.
        world.contained = quarantine_works
        return {"suspended": Symbol("confirmed")}

    def isolate_endpoint(host):
        world.actions.append("isolate_endpoint")
        world.contained = True
        return {"isolated": Symbol("confirmed")}

    def notify_operator(message):
        return {"delivered": Symbol("ok")}

    for fn in (quarantine_account, isolate_endpoint, notify_operator):
        h.tools[fn.__name__] = fn
    h.approver = lambda request: True
    return h, MockLLM(), world
