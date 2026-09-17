from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl import Host, MockLLM, Society, Symbol, parse_file

SANDBOX = Path("/tmp/agentl_complex_sandbox")


def setup_sandbox():
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    target = SANDBOX / "suspicious_logs"
    quarantine = SANDBOX / "quarantine"
    target.mkdir(parents=True)
    quarantine.mkdir(parents=True)

    # Création de faux fichiers malveillants réels sur le serveur Linux
    for i in range(5):
        (target / f"trojan_payload_{i}.sh").write_text("#!/bin/bash\necho malicious")


def build():
    setup_sandbox()

    # --- Hôte 1 : TriageSupervisor ---
    h_triage = Host()
    h_triage.sensors["sandbox.alert_triggered"] = lambda: Symbol("yes")

    # --- Hôte 2 : SecurityAnalyst ---
    h_analyst = Host()
    h_analyst.sensors["sandbox.suspicious_file_count"] = lambda: len(
        list((SANDBOX / "suspicious_logs").glob("*"))
    )

    # Définition du SOUS-AGENT sous-traité (DELEGATE)
    def forensic_subagent(payload):
        target_folder = payload.get("target_folder", "")
        folder_path = SANDBOX / str(target_folder)
        file_count = len(list(folder_path.glob("*"))) if folder_path.exists() else 0

        # Analyse réelle sur le système de fichiers
        if file_count > 0:
            return {
                "threat_level": Symbol("HIGH"),
                "forensic_score": 0.95,
            }
        return {
            "threat_level": Symbol("LOW"),
            "forensic_score": 0.10,
        }

    h_analyst.subagents["forensic_specialist"] = forensic_subagent

    # --- Hôte 3 : RemediationAgent ---
    h_remed = Host()
    h_remed.sensors["sandbox.cleaned"] = lambda: Symbol(
        "yes" if len(list((SANDBOX / "suspicious_logs").glob("*"))) == 0 else "no"
    )

    def quarantine_folder(folder):
        src = SANDBOX / str(folder)
        dst = SANDBOX / "quarantine"
        if not src.exists():
            return {"status": Symbol("failed")}

        for f in src.glob("*"):
            shutil.move(str(f), str(dst / f.name))
        return {"status": Symbol("success")}

    h_remed.tools["quarantine_folder"] = quarantine_folder

    # Approbation interactive dans la console Linux
    def approver(request):
        ans = input(f"\n  🖐  [SÉCURITÉ] AUTORISER ACTION : {request.render()} ? (o/N) : ")
        return ans.strip().lower() == "o"

    h_remed.approver = approver

    # Dictionnaire des hôtes par agent
    hosts = {
        "TriageSupervisor": h_triage,
        "SecurityAnalyst": h_analyst,
        "RemediationAgent": h_remed,
    }
    return hosts, MockLLM()


if __name__ == "__main__":
    # Exécution autonome de la société
    program = parse_file("complex_soc.agent")
    hosts, llm = build()
    society = Society(program.agents, hosts=hosts, echo=True)

    print("🚀 Démarrage de la Société d'Agents...")
    society.run(max_ticks=6)

    print("\n--- ÉTAT FINAL DU BAC À SABLE LINUX ---")
    print(f"Fichiers restants dans /suspicious_logs: {len(list((SANDBOX / 'suspicious_logs').glob('*')))}")
    print(f"Fichiers isolés dans /quarantine: {len(list((SANDBOX / 'quarantine').glob('*')))}")
    print("\n--- MÉMOIRE PARTAGÉE (SHARED) ---")
    print(society.render_shared())