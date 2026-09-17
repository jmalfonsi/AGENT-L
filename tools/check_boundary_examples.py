"""Fail on any new Boundary diagnostic, including inside an already red host.

The baseline records review debt, never waives findings or changes Boundary's
exit status. Updating it is an explicit source change, not a CLI option.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentl.boundary import check_pair

BASELINE = ROOT / "docs" / "boundary-example-debt.json"


def inventory(root: Path = ROOT) -> dict[str, list[str]]:
    results = {}
    for agent in sorted((root / "examples").glob("*.agent")):
        if not agent.with_suffix(".py").is_file():
            continue
        report = check_pair(agent)
        diagnostics = []
        for finding in report.blocking:
            source = (finding.path or report.path).resolve().relative_to(root.resolve())
            diagnostics.append(f"{source}:{finding.line} {finding.code} {finding.message}")
        results[agent.name] = sorted(diagnostics)
    return results


def regressions(current: dict, baseline: dict) -> dict:
    return {name: list((Counter(findings) - Counter(baseline.get(name, []))).elements())
            for name, findings in current.items()
            if Counter(findings) - Counter(baseline.get(name, []))}


def main() -> int:
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["diagnostics"]
    current = inventory()
    added = regressions(current, baseline)
    for name, findings in current.items():
        if findings:
            print(f"{name}: {len(findings)} diagnostic(s) bloquant(s), dette de revue explicite")
    for name, findings in added.items():
        for finding in findings:
            print(f"RÉGRESSION {name}: {finding}")
    removed = sum(len(Counter(findings) - Counter(current.get(name, [])))
                  for name, findings in baseline.items())
    if removed:
        print(f"{removed} diagnostic(s) résolu(s) : retirer les entrées correspondantes du suivi.")
    print("Aucun nouveau diagnostic." if not added else "Nouveaux diagnostics à traiter.")
    return int(bool(added))


if __name__ == "__main__":
    raise SystemExit(main())
