"""Cliquet de mutation : le nombre de règles de politique non défendues par un
scénario ne peut que baisser.

Exécuter : python3 tools/check_mutation.py [--write]

`agentl mutate` retire chaque règle qui relâche la politique et rejoue les
scénarios ; un mutant qui survit est une règle qu'aucun test ne défend. Les
exemples en portent une dette réelle (voir `docs/mutation-baseline.json`). Ce
contrôle ne l'efface pas : il l'empêche de grossir. Il échoue si

  * une règle non défendue apparaît qui n'était pas dans la référence (une règle
    nouvelle sans scénario, ou une règle qui cesse d'être défendue) ;
  * un exemple jugé devient non jugeable (ses scénarios sont rouges ou ont
    disparu).

Une règle désormais défendue n'échoue pas : le contrôle le dit et demande de
resserrer la référence (`--write`), pour que le progrès ne se perde pas.
Aucun hôte d'exemple n'est importé ni exécuté.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agentl.mutate import mutate_file          # noqa: E402

BASELINE = ROOT / "docs" / "mutation-baseline.json"
#: Exemples volontairement fautifs : ils échouent à `check`/`verify` par dessein.
NEGATIVE = {"broken", "unsafe", "gmail_butler_naif"}


def measure() -> dict:
    out = {}
    for path in sorted((ROOT / "examples").glob("*.agent")):
        if path.stem in NEGATIVE:
            continue
        report = mutate_file(str(path))
        unjudged = bool(report.unjudged) and not report.mutants
        out[path.name] = {
            "unjudged": [why for _agent, why in report.unjudged]
            if report.unjudged else [],
            # Identité sans numéro de ligne : déplacer une règle n'est pas
            # une régression.
            "survivors": sorted(f"{m.agent} · {m.kind} : {m.rule}"
                                for m in report.survivors),
            "mutants": len(report.mutants),
            "judged": not unjudged,
        }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="réécrit la référence avec l'état mesuré")
    args = parser.parse_args(argv)
    now = measure()
    if args.write:
        BASELINE.write_text(json.dumps(
            {"description": "Règles de politique qu'aucun SCENARIO ne défend "
                            "(mutants survivants d'`agentl mutate`), par exemple. "
                            "Ne peut que baisser : tools/check_mutation.py.",
             "examples": now}, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n", encoding="utf-8")
        total = sum(len(v["survivors"]) for v in now.values())
        print(f"référence écrite : {total} règle(s) non défendue(s) "
              f"sur {len(now)} exemples")
        return 0
    if not BASELINE.exists():
        print(f"✗ référence absente : {BASELINE} (--write)", file=sys.stderr)
        return 2
    base = json.loads(BASELINE.read_text(encoding="utf-8"))["examples"]
    failed = 0
    improved = 0
    for name, state in now.items():
        was = base.get(name)
        if was is None:
            print(f"✗ {name} : exemple absent de la référence (--write)")
            failed += 1
            continue
        if was["judged"] and not state["judged"]:
            print(f"✗ {name} : n'est plus jugeable — "
                  f"{'; '.join(state['unjudged'])}")
            failed += 1
        fresh = sorted(set(state["survivors"]) - set(was["survivors"]))
        for rule in fresh:
            print(f"✗ {name} : règle non défendue nouvelle — {rule}")
            failed += 1
        gone = sorted(set(was["survivors"]) - set(state["survivors"]))
        improved += len(gone)
    total = sum(len(v["survivors"]) for v in now.values())
    print(f"{total} règle(s) non défendue(s) sur {len(now)} exemples ; "
          f"{failed} régression(s)")
    if improved and not failed:
        print(f"↓ {improved} règle(s) désormais défendue(s) : resserrer la "
              f"référence avec `python3 tools/check_mutation.py --write`")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
