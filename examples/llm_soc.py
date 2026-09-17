from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from gemini_llm import GeminiLLM

# Auto-résolution du chemin Python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl import Host, MockLLM, Society, Symbol, parse_file

SANDBOX = Path("/tmp/agentl_llm_sandbox")


def setup_sandbox():
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    SANDBOX.mkdir(parents=True)
    quarantine = SANDBOX / "quarantine"
    quarantine.mkdir()

    # Log réel contenant une tentative d'injection SQL
    log_file = SANDBOX / "access.log"
    log_file.write_text(
        '192.168.1.50 - - [27/Jul/2026:10:00:00] "GET /admin_panel?id=1%27%20OR%201=1-- HTTP/1.1" 200 4520\n'
    )


def build():
    setup_sandbox()

    # --- Hôte 1 : LogAnalyzer ---
    h_log = Host()
    h_log.sensors["sandbox.log_content"] = lambda: (
        (SANDBOX / "access.log").read_text() if (SANDBOX / "access.log").exists() else ""
    )
    # --- Hôte 2 : SecurityInvestigator ---
    h_investigator = Host()
    h_investigator.sensors["sandbox.file_count"] = lambda: len(list(SANDBOX.glob("*.log")))

    # --- Hôte 3 : RemediationAgent ---
    h_remed = Host()
    h_remed.sensors["sandbox.quarantined"] = lambda: Symbol(
        "yes" if len(list((SANDBOX / "quarantine").glob("*.log"))) > 0 else "no"
    )

    def isolate_target(target):
        target_str = str(target)
        src = SANDBOX / "access.log"
        dst = SANDBOX / "quarantine" / f"{target}_access.log"
        if src.exists():
            shutil.move(str(src), str(dst))
            return {"status": Symbol("success")}
        return {"status": Symbol("failed")}

    h_remed.tools["isolate_target"] = isolate_target

    def approver(request):
        ans = input(f"\n  🖐  [SÉCURITÉ] AUTORISER ACTION : {request.render()} ? (o/N) : ")
        return ans.strip().lower() == "o"

    h_remed.approver = approver

    hosts = {
        "LogAnalyzer": h_log,
        "SecurityInvestigator": h_investigator,
        "RemediationAgent": h_remed,
    }

    llm = GeminiLLM(
        api_key=os.environ.get("GEMINI_API_KEY"),
        model="gemini-3.1-flash-lite"
    )

    return hosts, llm


if __name__ == "__main__":
    program = parse_file("examples/llm_soc.agent")
    hosts, llm = build()
    society = Society(program.agents, hosts=hosts, llms={"LogAnalyzer": llm, "SecurityInvestigator": llm, "RemediationAgent": llm}, echo=True)

    print("🚀 Démarrage du Test Multi-LLM...")
    society.run(max_ticks=6)

    print("\n--- ÉTAT FINAL DU BAC À SABLE LINUX ---")
    print(f"Fichier access.log restant à la racine : {(SANDBOX / 'access.log').exists()}")
    print(f"Fichiers déplacés dans /quarantine : {[f.name for f in (SANDBOX / 'quarantine').glob('*')]}")
    print("\n--- MÉMOIRE PARTAGÉE (SHARED) ---")
    print(society.render_shared())