"""Une phase d'un cas, dans un processus neuf.

    python case.py FRAMEWORK SCENARIO VARIANT PHASE WORKDIR

Un processus par phase : la panne (`os._exit`) et la reprise se font pour de
vrai, sans état partagé en mémoire. La sortie standard est libre ; seul le
monde (`WORKDIR/effects.jsonl`) et le code de sortie comptent pour l'oracle.
"""
from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402

MODULES = {"agentl": "fw_agentl", "langgraph": "fw_langgraph",
           "pydanticai": "fw_pydanticai", "crewai": "fw_crewai"}


def main(argv: list) -> int:
    framework, scenario, variant, phase, workdir = argv
    module = importlib.import_module(MODULES[framework])
    world = common.World(workdir)
    try:
        if phase == "tamper":
            module.tamper(scenario, world)
        else:
            out = module.run(scenario, variant, phase, world)
            if out:
                print(out)
    except Exception as exc:  # noqa: BLE001 — une erreur est un résultat
        traceback.print_exc()
        world.note("phase_error", phase=phase,
                   error=f"{type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
