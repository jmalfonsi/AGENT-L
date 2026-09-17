"""Ce que corrigent les versions 1.1 et 1.2.

    python examples/demo_v11.py

v1.1 — un défaut de calibration devenu défaut de sûreté, et sa correction.
v1.2 — la mémoire cesse d'être en écriture seule, le modèle d'effets est
       confronté au monde, la mémoire partagée retrouve la bonne granularité.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from agentl import (Analyzer, Host, MockLLM, Runtime, Society, Symbol, infer,
                    parse_file, parse_source, verify)
from agentl.bayes import reachable_range
from agentl.policy import ActionRequest, PolicyEngine
from agentl.state import State

SOC = HERE / "soc_analyst.agent"


def banner(title: str) -> None:
    print("\n" + "═" * 76)
    print(f"  {title}")
    print("═" * 76)


# --------------------------------------------------------------- v1.1
TEMPLATE = """
AGENT Demo {{
    HYPOTHESIS credential_attack {{
        PRIOR 0.05
        EVIDENCE {{
            {evidence}
            endpoint.integrity == compromised LIKELIHOOD 0.80 GIVEN_NOT 0.05
        }}
        THRESHOLD 0.90
    }}
    TOOL isolate_endpoint {{ OUTPUT {{ ok: Symbol }} RISK HIGH }}
    POLICY {{ DEFAULT DENY
              ALLOW isolate_endpoint IF P(credential_attack) >= 0.95 }}
}}
"""
PAIR = ("wazuh.alert_count > 20        LIKELIHOOD 0.92 GIVEN_NOT 0.06\n"
        "            network.anomaly_score > 0.75  LIKELIHOOD 0.85 GIVEN_NOT 0.20")


def observed_state() -> State:
    state = State()
    state.set_world("wazuh.alert_count", 37)
    state.set_world("network.anomaly_score", 0.91)
    state.set_world("endpoint.integrity", Symbol("compromised"))
    state.set_world("asset.criticality", Symbol("MEDIUM"))
    return state


def demo_calibration() -> None:
    banner("v1.1 — quand une erreur de calibration devient une erreur "
           "d'autorisation")
    print("""
  Deux capteurs décrivent la MÊME rafale d'attaque. Les traiter comme deux
  témoins indépendants multiplie leurs rapports de vraisemblance. Le
  postérieur gonfle — et ce postérieur garde une action irréversible :

      ALLOW isolate_endpoint IF P(credential_attack) >= 0.95
""")
    for label, evidence in [("naïf (v1.0)", PAIR),
                            ("corrélation déclarée (v1.1)",
                             f"GROUP rafale {{ {PAIR} }}")]:
        agent = parse_source(TEMPLATE.format(evidence=evidence)).agents[0]
        hypothesis = agent.hypotheses[0]
        state = observed_state()
        result = infer(hypothesis, state)
        state.set_world("credential_attack.posterior", result.posterior)
        decision = PolicyEngine(agent).check(
            ActionRequest("isolate_endpoint", {}, risk="HIGH"), state)
        print(f"  ── {label}")
        print(f"     P(credential_attack) = {result.posterior:.3f}")
        for outcome in sorted(result.outcomes,
                              key=lambda o: -abs(o.weight_bits)):
            print(f"       {outcome.render()}")
        print(f"     → isolation d'un poste : {decision.verdict}\n")

    print("  Le même monde, les mêmes capteurs, la même politique.")
    print("  Seule change l'honnêteté du modèle probabiliste.")


def demo_reachability() -> None:
    banner("v1.1 — le vérificateur relie le modèle d'inférence aux seuils")
    agent = parse_file(str(SOC)).agents[0]
    print()
    for hypothesis in agent.hypotheses:
        low, high = reachable_range(hypothesis)
        print(f"  {hypothesis.name:20} P ∈ [{low:.3f}, {high:.3f}]   "
              f"THRESHOLD {hypothesis.threshold:g}")
    print("""
  Les vraisemblances étant déclarées, l'amplitude atteignable se calcule
  statiquement. Une garde `P(h) >= 0.95` sur un modèle plafonnant à 0.928
  est une capacité morte — que ni le typage ni la logique des gardes ne
  révèlent. C'est ce que trouve V109, et c'est ainsi que les seuils de
  l'exemple de référence ont été recalés.
""")
    report = verify(agent)
    for theorem in report.theorems:
        badge = {True: "✔", False: "✘", None: "◐"}[theorem.holds]
        print(f"    {badge} {theorem.key} — {theorem.summary}")


def demo_domains() -> None:
    banner("v1.1 — un type ne borne pas une valeur")
    src = """
    AGENT Bound {{
        GOAL g {{ MAINTAIN done == yes }}
        TOOL act {{ OUTPUT {{ done: Symbol }} RISK HIGH }}
        POLICY {{ DEFAULT DENY  ALLOW act IF confidence >= 0.90 }}
        PLAN p WHEN 1 == 1 {{
            STEP s {{
                REASON {{ TASK "évaluer" PRODUCE {{ confidence: Number{d} }} }}
                act()
                VERIFY done == yes
            }}
        }}
    }}
    """
    for label, domain in [("PRODUCE { confidence: Number }", ""),
                          ("PRODUCE { confidence: Number IN [0, 1] }",
                           " IN [0, 1]")]:
        agent = parse_source(src.format(d=domain)).agents[0]
        host = Host()
        host.tools["act"] = lambda: {"done": Symbol("yes")}
        runtime = Runtime(agent, host, MockLLM({"évaluer": {"confidence": 5000}}))
        runtime.run(max_ticks=1)
        codes = sorted({d.code for d in Analyzer(agent).run()})
        print(f"\n  ── {label}")
        print(f"     le modèle renvoie 5000 → confidence = "
              f"{runtime.state.get('confidence')}")
        print(f"     action exécutée : {runtime.metrics['tool_calls'] == 1}"
              f"   ·   diagnostics : {', '.join(codes) or 'aucun'}")


# --------------------------------------------------------------- v1.2
def demo_memory() -> None:
    banner("v1.2 — une mémoire qui se relit")
    hypothesis = next(h for h in parse_file(str(SOC)).agents[0].hypotheses
                      if h.name == "credential_attack")
    print("\n  PRIOR FROM LONG_TERM.incidents WHERE threat.kind "
          "== credential_attack\n")
    for attacks, benign in [(0, 0), (2, 8), (8, 2), (20, 0)]:
        state = State()
        state.memory["LONG_TERM"] = {
            "incidents": [{"threat.kind": Symbol("credential_attack")}] * attacks
            + [{"threat.kind": Symbol("benign_scan")}] * benign}
        result = infer(hypothesis, state)
        print(f"    {attacks:2} attaques / {benign:2} bénins → "
              f"a priori {result.prior:.3f}   ({result.prior_origin})")
    print("\n  Lissage de Jeffreys : un historique unanime ne produit ni 0 ni 1.")


def demo_drift() -> None:
    banner("v1.2 — le modèle d'effets confronté au monde")
    src = """
    AGENT Drift {
        GOAL g { MAINTAIN state == fixed }
        OBSERVE { state }
        BELIEF { state = broken CONFIDENCE 0.5 SOURCE prior }
        TOOL repair { OUTPUT { ok: Symbol } RISK LOW
                      EFFECT { state = fixed } COST 1 }
        POLICY { ALLOW repair }
        PLANNER { ENABLE ACHIEVE state == fixed }
        LOOP MAX 3 { OBSERVE UPDATE_BELIEFS EVALUATE_GOALS
                     SELECT_PLAN EXECUTE VERIFY }
    }
    """
    agent = parse_source(src).agents[0]
    host = Host()
    host.sensors["state"] = lambda: Symbol("broken")   # l'outil ne répare rien
    host.tools["repair"] = lambda: {"ok": Symbol("done")}
    runtime = Runtime(agent, host, MockLLM()).run(max_ticks=3)
    print()
    for event in runtime.trace.events:
        if event.kind in {"ERROR", "VERIFY_FAIL"}:
            print("  " + runtime.trace.line(event))
    print(f"\n  registre : {runtime.drift}")
    print("  Un EFFECT faux corrompt silencieusement tous les plans. "
          "Il ne le fait plus en silence.")


def demo_shared() -> None:
    banner("v1.2 — la mémoire partagée retrouve la bonne granularité")
    from soc_team import build

    agents = parse_file(str(HERE / "soc_team.agent")).agents
    hosts, llms, _ = build()
    society = Society(agents, hosts, llms).run(max_ticks=5, until="soc_analyst")
    print()
    for name in society.order:
        for event in society.runtimes[name].trace.events:
            if event.kind == "SHARED":
                print(f"  {name:14} " + society.runtimes[name].trace.line(event))
    print()
    print(society.render_shared())
    print(f"""
  `blocked_hosts` n'est écrit que par l'analyste : version {society.shared_versions['blocked_hosts']}, aucun conflit.
  `incidents` est alimenté par les deux : le conflit est réel et signalé.

  En v0.6 la version était globale au compartiment — deux agents écrivant
  des faits sans rapport se déclaraient mutuellement en conflit. À dix
  agents, le signal se serait noyé dans le bruit.""")


if __name__ == "__main__":
    demo_calibration()
    demo_reachability()
    demo_domains()
    demo_memory()
    demo_drift()
    demo_shared()
