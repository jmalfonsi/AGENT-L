"""Chaîne hors ligne des exemples, avec exemptions de suites vides nommées.

Exécuter : python3 tools/check_examples.py
Aucun hôte d'exemple n'est importé ni exécuté.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentl.cli import main as agentl_main

NEGATIVE_EXAMPLES = {"broken", "unsafe", "gmail_butler_naif"}
# Dette historique explicite : toute autre suite vide doit faire échouer TEST.
EMPTY_EXAMPLES = {
    "K8S_HEALER", "complex_soc", "forensic", "llm_soc",
    "maintenance", "soc_risk", "soc_team",
}


def main() -> int:
    failed = []
    count = 0
    for path in sorted((ROOT / "examples").glob("*.agent")):
        if path.stem in NEGATIVE_EXAMPLES:
            continue
        for command in ("check", "verify", "test"):
            args = [command, str(path)]
            if command == "test" and path.stem in EMPTY_EXAMPLES:
                args.append("--allow-empty")
            print(f"== {command} {path.name}", flush=True)
            count += 1
            if agentl_main(args) != 0:
                failed.append(f"{command} {path.name}")
    if not count:
        print("Aucun exemple trouvé.", file=sys.stderr)
        return 1
    print(f"{count} contrôles ; {len(failed)} échec(s)")
    for failure in failed:
        print(f"  {failure}")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
