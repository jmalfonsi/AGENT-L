"""Quatre mondes, un seul programme AGENT-L.

    python examples/demo.py

Le fichier `soc_analyst.agent` n'est jamais modifié entre les scénarios.
Ce qui change, c'est ce que l'agent perçoit — et donc ce qu'il infère,
ce qu'il a le droit de faire, et ce qu'il planifie.

A — nominal          : l'inférence franchit le seuil, le planificateur
                       synthétise la séquence la moins risquée.
B — actif CRITICAL   : `NEVER` élimine l'isolation *pendant la recherche*.
                       Le planificateur contourne par le confinement de compte.
C — compte inconnu   : le contournement n'est plus instanciable ; repli sur
                       l'isolation, dont la politique exige l'approbation.
D — CRITICAL + compte inconnu : plus aucune action permise. L'agent ne force
                       pas : il constate l'impasse et escalade une seule fois.
E — signal faible    : l'hypothèse bénigne l'emporte, l'agent s'abstient.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentl import Analyzer, Runtime, parse_file
from soc_analyst import build

AGENT_FILE = Path(__file__).with_name("soc_analyst.agent")
SHOWN = {"BAYES", "PLANNER", "TOOL", "BLOCKED", "APPROVAL", "ASK",
         "VERIFY_OK", "VERIFY_FAIL", "PLAN", "MEMORY"}


def scenario(title: str, ticks: int = 3, **kwargs) -> None:
    print("\n" + "═" * 72)
    print(f"  {title}")
    print("═" * 72)
    agent = parse_file(str(AGENT_FILE)).agents[0]
    for diag in Analyzer(agent).run():
        print("  " + diag.render())
    host, llm, world = build(**kwargs)
    runtime = Runtime(agent, host, llm).run(max_ticks=ticks)

    for event in runtime.trace.events:
        if event.kind not in SHOWN:
            continue
        line = runtime.trace.line(event)
        print("  " + (line if len(line) < 150 else line[:147] + "…"))

    outcome = world.action or "aucune action de confinement"
    print(f"\n  → menace contenue par : {outcome}")
    print("  → " + "  ".join(f"{k}={v}" for k, v in runtime.metrics.items() if v))


if __name__ == "__main__":
    scenario("A — nominal : inférence puis synthèse du plan le moins risqué")
    scenario("B — actif CRITICAL : NEVER élagué à la planification, contournement",
             criticality="CRITICAL")
    scenario("C — compte inconnu : repli sur l'isolation, sous approbation",
             account_known=False)
    scenario("D — CRITICAL + compte inconnu : impasse assumée, escalade unique",
             criticality="CRITICAL", account_known=False, ticks=4)
    scenario("E — signal faible : l'hypothèse bénigne l'emporte, abstention",
             alerts=4, anomaly=0.20, integrity="clean", ticks=2)
