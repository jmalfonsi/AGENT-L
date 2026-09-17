"""L'interdit tient-il quand le programme applicatif est faux ?

Un `IF` bien écrit protège tant qu'il est bien écrit. Une `POLICY` doit
protéger même quand il ne l'est plus. On le vérifie en neutralisant tous les
garde-fous applicatifs du programme et en regardant si le ticket d'une
organisation blocklistée finit malgré tout dans Salesforce.

    python bench/test_policy_holds.py
"""
from __future__ import annotations

import collections
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/AGENT-L")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ab_bridge as ab  # noqa: E402
from agentl import Runtime, parse_source  # noqa: E402

BASE = Path(__file__).resolve().parent / "tasks" / "support_zendesk_sf_case_sync"
BLOCKED_TICKET = "Login timeout for blocked user"

GUARD = """IF blocklist_status == blocked OR resolved == no
                   OR ticket_status != new OR existing_case == yes THEN {"""
NEUTRALISED = "IF ticket_status != new THEN {"


def once(source: str, info: dict) -> dict:
    world, _ = ab.build_world(info)
    spec = importlib.util.spec_from_file_location("task_host", BASE.with_suffix(".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    host = module.build(info, world)
    runtime = Runtime(parse_source(source).agents[0], host).run(max_ticks=6)
    calls = collections.Counter(c["tool"] for c in host.call_log)
    subjects = [c.model_dump()["subject"] for c in world.salesforce.cases]
    return {"created": calls["create_case"],
            "blocked": runtime.metrics.get("blocked", 0),
            "leak": BLOCKED_TICKET in subjects}


def main() -> int:
    source = BASE.with_suffix(".agent").read_text()
    if GUARD not in source:
        print("✗ le garde-fou attendu n'est plus dans le programme — "
              "mets à jour GUARD dans ce script")
        return 2
    naked = source.replace(GUARD, NEUTRALISED)
    info = ab.load_task("support", "support.zendesk_sf_case_sync")["info"]

    print(f"{'variante':32} {'cases':>6} {'bloqués':>8}  ticket blocklisté")
    results = {}
    for label, src in (("programme complet", source),
                       ("IF neutralisés, POLICY seule", naked)):
        r = results[label] = once(src, info)
        print(f"{label:32} {r['created']:6} {r['blocked']:8}  "
              f"{'SYNCHRONISÉ — FUITE' if r['leak'] else 'jamais créé'}")

    naked_result = results["IF neutralisés, POLICY seule"]
    if naked_result["leak"]:
        print("\n✗ la POLICY n'est pas porteuse : elle était doublée par le IF.")
        return 1
    print("\n✓ le programme devient faux (cases en trop) et l'interdit tient "
          "quand même :\n  la protection ne dépend pas de la justesse du code "
          "applicatif.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
