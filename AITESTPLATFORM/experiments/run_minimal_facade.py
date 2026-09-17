"""Compare façade de production et façade minimale, hors historique.

Les runs produits ici n'entrent PAS dans `data/run-history.jsonl` : leur
protocole n'est pas celui de la plateforme, et les mélanger aux campagnes
fausserait toute moyenne. Le monde, les outils Zapier sous-jacents et le
barème officiel sont en revanche exactement les mêmes.

    python experiments/run_minimal_facade.py --frameworks langgraph,crewai
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import benchmark_runner as B  # noqa: E402
import minimal_facade as MF  # noqa: E402  (voisin de fichier)

TASK_ID = "hr.comp_adjustment_batch"


def minimal_bound_tools(task: dict, world, events: list[dict]) -> list:
    """Mêmes enveloppes d'événements que la façade de production."""
    import inspect

    handlers = MF.build_tools(world, events)
    tools = []
    for name, fn in handlers.items():
        model = B._args_model(fn, MF.DECLARED.get(name))

        def invoke(_fn=fn, _name=name, **kwargs):
            started = time.perf_counter()
            event = {"index": len(events) + 1, "tool": _name,
                     "args": B._json_safe(kwargs), "transportStatus": "completed"}
            try:
                observation = B._json_safe(_fn(**kwargs))
                event["observation"] = observation
                event["semanticStatus"] = "success"
                event["ok"] = True
                return json.dumps(observation, ensure_ascii=False)
            except Exception as exc:
                event["transportStatus"] = "exception"
                event["semanticStatus"] = "error"
                event["ok"] = False
                event["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                event["durationMs"] = round((time.perf_counter() - started) * 1000, 2)
                events.append(event)

        tools.append(B.BoundTool(
            name=name,
            description=(inspect.getdoc(fn) or f"Task tool {name}")[:1800],
            schema=B._tool_schema(model), args_model=model, invoke=invoke))
    return tools


def minimal_agent_l(task: dict, world, events: list[dict]) -> dict:
    """AGENT-L sur la façade minimale : autre programme, autre hôte.

    Le `.agent` de production se lie à des verbes métier qui n'existent plus
    ici. `hr_comp_minimal.agent` est son équivalent écrit contre les trois
    outils génériques : il calcule la hausse, juge l'autorité de la source par
    le domaine perçu, et fait rédiger ses e-mails par un REASON.
    """
    from agentl import Runtime, parse_file
    from agentl.analyzer import Analyzer
    from bench.run_task import make_llm

    import hr_comp_minimal as MH

    path = Path(__file__).resolve().parent / "hr_comp_minimal.agent"
    program = parse_file(str(path))
    agent = program.agents[0]
    errors = [d for d in Analyzer(agent).run() if d.severity == "error"]
    if errors:
        raise RuntimeError("Compilation AGENT-L: " + ", ".join(d.code for d in errors))

    host = MH.build_for(world)
    oracle = make_llm(B.MODEL)
    original_call = oracle._call

    def paced(*args, **kwargs):
        B._pace_llm()
        return original_call(*args, **kwargs)

    oracle._call = paced
    declared = getattr(getattr(agent, "loop", None), "max_iter", 0) or 0
    runtime = Runtime(agent, host, oracle, echo=False).run(
        max_ticks=max(declared, 12))
    for index, raw in enumerate(host.call_log, 1):
        item = B._json_safe(raw)
        item.setdefault("index", index)
        item.setdefault("ok", "error" not in item)
        events.append(item)
    failures = [e.text for e in runtime.trace.events if e.kind == "ERROR"]
    return {
        "finalAnswer": "Agent AGENT-L terminé." if not failures else "; ".join(failures),
        "tokenUsage": B._usage_empty(),
        "modelCalls": len(getattr(oracle, "calls", [])),
        "modelTelemetry": [{"framework": "agent_l", **item}
                           for item in getattr(oracle, "responses", [])],
        "runtimeMetrics": B._json_safe(runtime.metrics),
    }


def run_once(framework_id: str, minimal: bool) -> dict:
    task = B._load_task(TASK_ID)
    world, initial = B._ab().build_world(task["info"])
    events: list[dict] = []
    B.ACTIVE_BRIEFING = None
    original = B._bound_tools
    if minimal:
        B._bound_tools = minimal_bound_tools
    runner = B.RUNNERS[framework_id]
    if minimal and framework_id == "agent_l":
        runner = minimal_agent_l
    started = time.perf_counter()
    error = None
    payload: dict = {}
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            payload = runner(task, world, events)
    except Exception as exc:                                   # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    finally:
        B._bound_tools = original
    elapsed = round((time.perf_counter() - started) * 1000)

    official = B._ab().score(task["info"], world, initial)
    assertions = B._json_safe(official.get("assertions", []))
    evaluated = [a for a in assertions if not a.get("excluded", False)]
    usage = payload.get("tokenUsage") or B._usage_empty()
    if usage.get("totalTokens") is None:
        usage = B.aggregate_usage([*B.MODEL_TRANSPORT_LOG,
                                   *payload.get("modelTelemetry", [])])
    return {
        "framework": framework_id,
        "facade": "minimale" if minimal else "production",
        "error": error,
        "partialCredit": float(official.get("partial_credit", 0.0) or 0.0),
        "taskCompleted": float(official.get("task_completed", 0.0) or 0.0),
        "satisfied": sum(1 for a in evaluated if a.get("passed")),
        "evaluated": len(evaluated),
        "brokenNegatives": [a["type"] for a in assertions
                            if "_not_" in a["type"] and not a.get("passed")],
        "failed": [{"type": a["type"], "params": a.get("params")}
                   for a in evaluated if not a.get("passed")],
        "toolCalls": len(events),
        "exceptions": sum(1 for e in events if e.get("transportStatus") == "exception"),
        "llmCalls": len(B.MODEL_TRANSPORT_LOG) or payload.get("modelCalls"),
        "totalTokens": usage.get("totalTokens"),
        "elapsedMs": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frameworks", default="langgraph,crewai,openai_agents")
    parser.add_argument("--facades", default="minimale")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    results = []
    for facade in args.facades.split(","):
        for framework in args.frameworks.split(","):
            B.MODEL_TRANSPORT_LOG.clear()
            row = run_once(framework, minimal=(facade.strip() == "minimale"))
            results.append(row)
            print(f"{row['framework']:16} {row['facade']:11} "
                  f"credit={row['partialCredit']:.4f} "
                  f"verif={row['satisfied']}/{row['evaluated']} "
                  f"outils={row['toolCalls']:3} exc={row['exceptions']} "
                  f"llm={row['llmCalls']} tok={row['totalTokens']} "
                  f"{row['error'] or ''}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                  encoding="utf-8")


if __name__ == "__main__":
    main()
