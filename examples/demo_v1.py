"""Tour d'horizon des versions 0.6, 0.7 et 1.0.

    python examples/demo_v1.py

v0.6 — deux agents, messages asynchrones, mémoire partagée versionnée.
v0.7 — effets probabilistes : le planificateur découvre une escalade.
v1.0 — vérification hors ligne : un programme bien formé mais non sûr.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from agentl import (Analyzer, Planner, Runtime, Society, parse_file, verify)


def banner(title: str) -> None:
    print("\n" + "═" * 74)
    print(f"  {title}")
    print("═" * 74)


# ---------------------------------------------------------------- v0.6
def demo_society() -> None:
    from soc_team import build

    banner("v0.6 — deux agents, messagerie asynchrone, mémoire partagée")
    agents = parse_file(str(HERE / "soc_team.agent")).agents
    hosts, llms, world = build()
    society = Society(agents, hosts, llms).run(max_ticks=6, until="soc_analyst")

    for name in society.order:
        print(f"\n  ── {name} " + "─" * (60 - len(name)))
        for event in society.runtimes[name].trace.events:
            if event.kind in {"MESSAGE", "PLANNER", "TOOL", "SHARED",
                              "VERIFY_OK", "VERIFY_FAIL", "INFO"}:
                print("  " + society.runtimes[name].trace.line(event))

    print("\n  échanges :")
    print(society.render_messages())
    print("\n" + society.render_shared())
    print(f"\n  → menace contenue : {world['contained']}  ·  "
          f"conflits d'écriture détectés : "
          f"{society.metrics['shared_conflicts']}")


# ---------------------------------------------------------------- v0.7
def demo_probabilistic() -> None:
    from soc_risk import build

    banner("v0.7 — effets probabilistes et utilité espérée")
    for label, kwargs in [("actif MEDIUM", {}),
                          ("actif CRITICAL (isolation interdite)",
                           {"criticality": "CRITICAL"})]:
        agent = parse_file(str(HERE / "soc_risk.agent")).agents[0]
        host, llm, _ = build(**kwargs)
        runtime = Runtime(agent, host, llm)
        runtime.state.tick = 1
        runtime.phase_observe()
        runtime.phase_update_beliefs()
        result = Planner(agent, agent.planner, runtime.policy).synthesize(
            runtime.state)
        print(f"\n  ── {label}")
        for note in result.pruned_by_policy:
            print(f"     écarté : {note}")
        print(f"     {result.render()}")
        print(f"     {result.reason}")

    print("\n  ── exécution : le plan conforme rencontre un monde qui a tranché")
    agent = parse_file(str(HERE / "soc_risk.agent")).agents[0]
    host, llm, world = build(quarantine_works=True)
    runtime = Runtime(agent, host, llm).run(max_ticks=2)
    for event in runtime.trace.events:
        if event.kind in {"PLANNER", "TOOL", "INFO", "VERIFY_OK"}:
            print("  " + runtime.trace.line(event))
    print(f"\n  → actions réellement exécutées : {world.actions}")


# ---------------------------------------------------------------- v1.0
def demo_verifier() -> None:
    banner("v1.0 — bien formé n'est pas sûr")
    agent = parse_file(str(HERE / "unsafe.agent")).agents[0]

    diagnostics = Analyzer(agent).run()
    print("\n  agentl check :")
    print("    " + ("aucun diagnostic — le programme est bien formé"
                    if not diagnostics
                    else "\n    ".join(d.render() for d in diagnostics)))

    print("\n  agentl verify :")
    print(verify(agent).render())

    print("\n  ── par contraste, l'agent de référence")
    reference = parse_file(str(HERE / "soc_analyst.agent")).agents[0]
    report = verify(reference)
    for theorem in report.theorems:
        badge = {True: "✔", False: "✘", None: "◐"}[theorem.holds]
        print(f"    {badge} {theorem.key} — {theorem.title}")


if __name__ == "__main__":
    demo_society()
    demo_probabilistic()
    demo_verifier()
