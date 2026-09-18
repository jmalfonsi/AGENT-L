"""Lance le banc comparatif et écrit `results/results.json` + `results/RESULTS.md`.

    .venv/bin/python run.py                     # tout
    .venv/bin/python run.py --only agentl,langgraph --scenario injection
    .venv/bin/python run.py --repeat 3          # stabilité : verdicts identiques ?
    .venv/bin/python run.py --check             # CI : compare à expected.json

Chaque phase d'un cas tourne dans un processus neuf (`case.py`), dans un
répertoire de travail neuf. Le verdict vient de `common.judge`, qui ne lit que
le monde et les codes de sortie.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import common  # noqa: E402

FRAMEWORKS = ("agentl", "langgraph", "pydanticai", "crewai")
PACKAGES = {"langgraph": ["langgraph", "langgraph-checkpoint-sqlite",
                          "langchain-core"],
            "pydanticai": ["pydantic-ai-slim", "pydantic"],
            "crewai": ["crewai"]}
ENV = {"CREWAI_DISABLE_TELEMETRY": "true", "OTEL_SDK_DISABLED": "true",
       "CREWAI_TRACING_ENABLED": "false", "PYDANTIC_AI_NO_BANNER": "1",
       "PYTHONHASHSEED": "0",
       # `async` (le défaut de LangGraph) reproduit l'effet doublé observé.
       "BENCH_LANGGRAPH_DURABILITY":
           os.environ.get("BENCH_LANGGRAPH_DURABILITY", "sync")}
TIMEOUT = 180


def _unsupported(framework: str) -> Dict[str, str]:
    if framework != "crewai":
        return {}
    # Sans importer CrewAI dans l'orchestrateur (lent, et bruyant).
    return {"approval_binding": "pas d'état d'approbation persisté : "
                                "l'approbation a lieu dans le processus, au "
                                "moment de l'appel"}


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def environment() -> Dict[str, Any]:
    versions: Dict[str, str] = {}
    for packages in PACKAGES.values():
        for name in packages:
            try:
                versions[name] = metadata.version(name)
            except metadata.PackageNotFoundError:
                versions[name] = "absent"
    sys.path.insert(0, str(ROOT))
    try:
        from agentl import __version__ as agentl_version  # type: ignore
    except ImportError:
        agentl_version = "?"
    versions["agentl"] = agentl_version
    return {
        "date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "commit": _git("rev-parse", "HEAD") or "?",
        "dirty": bool(_git("status", "--porcelain")),
        "versions": versions,
        "env": ENV,
    }


def run_case(framework: str, scenario: str, variant: str,
             keep: Path | None) -> Dict[str, Any]:
    reason = _unsupported(framework).get(scenario)
    if reason:
        return {"framework": framework, "scenario": scenario,
                "variant": variant, "status": "n/a", "detail": reason}
    work = Path(tempfile.mkdtemp(prefix=f"{framework}-{scenario}-{variant}-"))
    exits: List[int] = []
    phases = common.SCENARIOS[scenario].phases_of(variant)
    started = time.perf_counter()
    logs: List[str] = []
    env = {**os.environ, **ENV}
    for phase in phases:
        try:
            done = subprocess.run(
                [sys.executable, str(HERE / "case.py"), framework, scenario,
                 variant, phase, str(work)],
                capture_output=True, text=True, timeout=TIMEOUT, env=env,
                cwd=HERE)
            exits.append(done.returncode)
            logs.append(f"--- {phase} (exit {done.returncode})\n"
                        f"{done.stdout[-4000:]}{done.stderr[-4000:]}")
        except subprocess.TimeoutExpired:
            exits.append(-9)
            logs.append(f"--- {phase} : délai dépassé ({TIMEOUT}s)")
    elapsed = time.perf_counter() - started
    world = common.World(work)
    verdict = common.judge(scenario, variant, world, exits)
    effects = [{"tool": e["tool"], "args": e["args"]} for e in world.effects()]
    if keep is not None:
        target = keep / f"{framework}-{scenario}-{variant}"
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(work, target)
        (target / "phases.log").write_text("\n".join(logs), "utf-8")
    shutil.rmtree(work, ignore_errors=True)
    return {"framework": framework, "scenario": scenario, "variant": variant,
            "status": "pass" if verdict.ok else "fail",
            "detail": verdict.detail, "phases": list(phases), "exits": exits,
            "effects": effects, "seconds": round(elapsed, 2)}


# ---------------------------------------------------------------- rapport
MARK = {"pass": "✅", "fail": "❌", "n/a": "—"}


def markdown(env: Dict[str, Any], results: List[Dict[str, Any]],
             frameworks: List[str], scenarios: List[str]) -> str:
    cell = {(r["framework"], r["scenario"], r["variant"]): r for r in results}
    out = ["# Banc comparatif — résultats", "",
           f"- date : {env['date']}",
           f"- commit AGENT-L : `{env['commit'][:12]}`"
           + (" (arbre modifié)" if env["dirty"] else ""),
           f"- Python {env['python']} — {env['platform']}",
           "- versions : " + ", ".join(f"{k} {v}" for k, v in
                                       sorted(env["versions"].items())),
           "", "Sûreté = variante `attack` ; utilité = variante `control` "
           "(le chemin légitime produit bien l'effet attendu).", "",
           "| scénario | " + " | ".join(frameworks) + " |",
           "|---|" + "---|" * len(frameworks)]
    for scenario in scenarios:
        row = []
        for fw in frameworks:
            a = cell.get((fw, scenario, "attack"))
            c = cell.get((fw, scenario, "control"))
            if a is None:
                row.append("")
                continue
            row.append(f"{MARK[a['status']]} sûreté · "
                       f"{MARK[c['status']] if c else '?'} utilité")
        out.append(f"| `{scenario}` | " + " | ".join(row) + " |")
    out += ["", "## Propriétés", ""]
    for scenario in scenarios:
        out.append(f"- `{scenario}` : {common.SCENARIOS[scenario].prop}.")
    out += ["", "## Détail", "",
            "| framework | scénario | variante | verdict | détail | s |",
            "|---|---|---|---|---|---|"]
    for r in results:
        out.append(f"| {r['framework']} | {r['scenario']} | {r['variant']} | "
                   f"{MARK[r['status']]} | {r['detail']} | "
                   f"{r.get('seconds', '')} |")
    return "\n".join(out) + "\n"


def signature(results: List[Dict[str, Any]]) -> Dict[str, str]:
    return {f"{r['framework']}/{r['scenario']}/{r['variant']}": r["status"]
            for r in results}


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", default=",".join(FRAMEWORKS))
    ap.add_argument("--scenario", default=",".join(common.SCENARIOS))
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--out", default=str(HERE / "results"))
    ap.add_argument("--keep", action="store_true",
                    help="conserver mondes, journaux et sorties de chaque cas")
    ap.add_argument("--check", action="store_true",
                    help="échouer si un verdict diffère de expected.json")
    args = ap.parse_args(argv)

    frameworks = [f for f in args.only.split(",") if f]
    scenarios = [s for s in args.scenario.split(",") if s]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    keep = out / "cases" if args.keep else None
    env = environment()

    runs: List[List[Dict[str, Any]]] = []
    for attempt in range(args.repeat):
        results = []
        for fw in frameworks:
            for scenario in scenarios:
                for variant in common.VARIANTS:
                    r = run_case(fw, scenario, variant, keep)
                    results.append(r)
                    print(f"[{attempt + 1}] {fw:<10} {scenario:<18} "
                          f"{variant:<8} {r['status']:<5} {r['detail']}",
                          flush=True)
        runs.append(results)

    stable = all(signature(r) == signature(runs[0]) for r in runs)
    results = runs[0]
    report = {"environment": env, "repeat": args.repeat, "stable": stable,
              "results": results}
    (out / "results.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", "utf-8")
    (out / "RESULTS.md").write_text(
        markdown(env, results, frameworks, scenarios), "utf-8")
    print(f"\n→ {out / 'RESULTS.md'}")

    status = 0
    if not stable:
        print("INSTABLE : les verdicts diffèrent d'une répétition à l'autre")
        status = 1
    if args.check:
        expected = json.loads((HERE / "expected.json").read_text("utf-8"))
        got = signature(results)
        drift = {k: (v, got.get(k)) for k, v in expected.items()
                 if k in got and got[k] != v}
        for key, (want, have) in sorted(drift.items()):
            print(f"DÉRIVE {key} : attendu {want}, obtenu {have}")
        status = status or (1 if drift else 0)
    return status


if __name__ == "__main__":
    sys.exit(main())
