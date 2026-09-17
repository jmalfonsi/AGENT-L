"""Boucle compiler → analyser → exécuter → réparer, sur une tâche du banc.

Le retour donné au compilateur est **la trace du runtime**, jamais le
résultat des assertions : on répare ce que l'agent a vu se passer, pas ce
que le correcteur attendait. Sans cette règle on n'écrirait pas un agent, on
optimiserait un score.

Chaque tentative repart d'un monde neuf : une réparation ne peut pas hériter
des écritures de la tentative précédente.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/AGENT-L")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ab_bridge as ab  # noqa: E402
import compile_agent as comp  # noqa: E402
from agentl import Runtime, parse_source  # noqa: E402
from agentl.analyzer import Analyzer  # noqa: E402

TASKS = Path(__file__).resolve().parent / "tasks"
# Les programmes générés ne touchent jamais aux programmes écrits à
# la main : ils vivent dans leur propre répertoire.
GENERATED = TASKS / "generated"


def execute(source: str, info: dict, ticks: int = 6,
            task_host: str = "") -> dict:
    world, initial = ab.build_world(info)
    if task_host:
        import importlib.util

        path = TASKS / f"{task_host.replace('.', '_')}.py"
        spec = importlib.util.spec_from_file_location("task_host", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        host = module.build(info, world)
    else:
        host = ab.make_host(info, world)
    agent = parse_source(source).agents[0]
    runtime = Runtime(agent, host).run(max_ticks=ticks)
    result = ab.score(info, world, initial)
    result["tool_calls"] = len(host.call_log)
    result["by_tool"] = dict(collections.Counter(c["tool"] for c in host.call_log))
    result["trace_errors"] = [
        f"{e.text} — {e.detail}" if e.detail else e.text
        for e in runtime.trace.events if e.kind in ("ERROR", "BLOCKED")
    ]
    return result


def runtime_feedback(result: dict) -> str:
    """Ce que l'exécution a révélé, sans jamais nommer une assertion."""
    counts = collections.Counter(result["trace_errors"])
    lines = [f"{n}× {text}" for text, n in counts.most_common(12)]
    if not lines and result["tool_calls"] == 0:
        lines = ["aucun outil n'a été appelé : le plan ne s'est jamais "
                 "déclenché (vérifie la condition WHEN et les drapeaux BELIEF)"]
    return "\n".join(lines)


def host_contracts(name: str, info: dict) -> list:
    """Contrats des outils exposés par l'hôte de la tâche (X.py).

    Même source de vérité que pour les outils du banc : l'introspection.
    Ce mode répond à une question précise — le compilateur bute-t-il sur la
    tâche, ou sur la surface d'outils brute ?
    """
    import importlib.util
    import inspect

    path = TASKS / f"{name.replace('.', '_')}.py"
    spec = importlib.util.spec_from_file_location("task_host", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    world, _ = ab.build_world(info)
    host = module.build(info, world)
    out = []
    for tool_name, fn in host.tools.items():
        sig = inspect.signature(fn)
        params = [{"name": p, "type": getattr(q.annotation, "__name__", "String"),
                   "required": q.default is inspect.Parameter.empty}
                  for p, q in sig.parameters.items() if p != "world"]
        out.append({"name": tool_name, "doc": inspect.getdoc(fn) or "",
                    "params": params})
    return out


def run(domain: str, name: str, attempts: int = 3, ticks: int = 6,
        use_task_host: bool = False) -> dict:
    task = ab.load_task(domain, name)
    info = task["info"]
    trigger = ab.trigger_text(task)
    contracts = (host_contracts(name, info) if use_task_host
                 else ab.tool_contracts(info))
    keys = {} if use_task_host else ab.probe_result_keys(info)

    history = []
    source, feedback = "", ""
    best = None
    for attempt in range(1, attempts + 1):
        print(f"\n── tentative {attempt}/{attempts} ─────────────────────")
        source, journal = comp.compile_task(
            trigger, contracts, rounds=3, result_keys=keys,
            seed=source, runtime_feedback=feedback)
        errors = [d for d in Analyzer(parse_source(source).agents[0]).run()
                  if d.severity == "error"] if source else [1]
        if errors:
            print("  programme non exécutable après réparation statique")
            history.append({"attempt": attempt, "compiled": False})
            continue
        result = execute(source, info, ticks=ticks,
                         task_host=name if use_task_host else "")
        print(f"  appels={result['tool_calls']}  "
              f"partial={result['partial_credit']:.3f}  "
              f"complété={result['task_completed']:.0f}  "
              f"incidents={len(result['trace_errors'])}")
        history.append({"attempt": attempt, "compiled": True,
                        "partial_credit": result["partial_credit"],
                        "task_completed": result["task_completed"],
                        "tool_calls": result["tool_calls"],
                        "incidents": len(result["trace_errors"])})
        if best is None or result["partial_credit"] > best["partial_credit"]:
            best = result
            GENERATED.mkdir(parents=True, exist_ok=True)
            (GENERATED / f"{name.replace('.', '_')}.agent").write_text(source)
        if result["task_completed"] == 1.0:
            break
        feedback = runtime_feedback(result)

    return {"task": name, "history": history,
            "best_partial": best["partial_credit"] if best else 0.0,
            "best_completed": best["task_completed"] if best else 0.0,
            "trace_errors": best["trace_errors"][:10] if best else []}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = run(args[0], args[1],
              attempts=int(args[2]) if len(args) > 2 else 3,
              use_task_host="--task-host" in sys.argv)
    print("\n" + json.dumps(out, indent=2, ensure_ascii=False))
