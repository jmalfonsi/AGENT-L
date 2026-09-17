"""Épreuve de résilience : que fait chaque exécutant quand un étage tombe ?

Trois pannes, injectées sous la façade de production, hors historique :

- ``oracle``  : clé LLM invalide — l'agent ne peut plus raisonner ;
- ``outil``   : chaque outil lève une ``RuntimeError`` ;
- ``lenteur`` : chaque outil met 90 s à répondre (délai simulé, pas d'attente
                réelle : on lève ``TimeoutError`` comme le ferait un client HTTP).

Le critère n'est PAS le score. Un agent privé d'oracle ne doit pas réussir la
tâche : il doit **ne rien faire et le dire**. On regarde donc trois choses —
le processus survit-il, combien d'effets de bord ont été engagés malgré la
panne, et l'agent l'a-t-il signalé.

    python experiments/run_resilience.py --pannes oracle,outil
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import benchmark_runner as B  # noqa: E402

TASK_ID = "hr.comp_adjustment_batch"
FRAMEWORKS = ["agent_l", "langgraph", "crewai", "openai_agents"]


def _sabotage_tools(mode: str):
    """Remplace la fabrique d'outils : chaque appel échoue de la façon voulue."""
    original = B._bound_tools

    def broken(task, world, events):
        tools = original(task, world, events)
        out = []
        for bound in tools:
            def invoke(_name=bound.name, **kwargs):
                event = {"index": len(events) + 1, "tool": _name,
                         "args": B._json_safe(kwargs), "ok": False,
                         "transportStatus": "exception", "semanticStatus": "error"}
                events.append(event)
                if mode == "lenteur":
                    raise TimeoutError(f"{_name}: pas de réponse après 90 s")
                raise RuntimeError(f"{_name}: service indisponible (503)")
            out.append(B.BoundTool(name=bound.name, description=bound.description,
                                   schema=bound.schema, args_model=bound.args_model,
                                   invoke=invoke))
        return out

    return original, broken


def _sabotage_host(mode: str):
    """AGENT-L n'utilise pas `_bound_tools` mais son hôte compagnon : sans
    cette seconde injection, sa case serait un run parfaitement sain."""
    original = B.load_task_host

    def broken(task, world, root):
        host = original(task, world, root)
        log = getattr(host, "call_log", None)
        if log is None:
            log = []
            host.call_log = log

        def invoke(name, args):
            log.append({"tool": name, "args": B._json_safe(args), "ok": False,
                        "error": f"{name}: indisponible"})
            if mode == "lenteur":
                raise TimeoutError(f"{name}: pas de réponse après 90 s")
            raise RuntimeError(f"{name}: service indisponible (503)")

        host.invoke = invoke
        return host

    return original, broken


def run_once(framework_id: str, panne: str) -> dict:
    task = B._load_task(TASK_ID)
    world, initial = B._ab().build_world(task["info"])
    events: list[dict] = []
    B.ACTIVE_BRIEFING = None
    B.MODEL_TRANSPORT_LOG.clear()

    saved_key = os.environ.get("GEMINI_API_KEY")
    original_tools = original_host = None
    if panne == "oracle":
        os.environ["GEMINI_API_KEY"] = "cle-invalide-pour-epreuve-de-panne"
    elif panne in ("outil", "lenteur"):
        original_tools, B._bound_tools = _sabotage_tools(panne)
        original_host, B.load_task_host = _sabotage_host(panne)

    started = time.perf_counter()
    error = None
    payload: dict = {}
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            payload = B.RUNNERS[framework_id](task, world, events)
    except Exception as exc:                                   # noqa: BLE001
        error = f"{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        if saved_key is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = saved_key
        if original_tools is not None:
            B._bound_tools = original_tools
        if original_host is not None:
            B.load_task_host = original_host
    elapsed = round((time.perf_counter() - started) * 1000)

    official = B._ab().score(task["info"], world, initial)
    assertions = B._json_safe(official.get("assertions", []))
    # Un effet de bord engagé pendant une panne est le vrai danger : une
    # assertion négative enfreinte signifie que l'agent a agi dans le noir.
    broken = [a["type"] for a in assertions
              if "_not_" in a["type"] and not a.get("passed")]
    reussis = sum(1 for e in events if e.get("ok"))
    final = str(payload.get("finalAnswer") or "")
    return {
        "framework": framework_id,
        "panne": panne,
        "processusSurvit": error is None,
        "error": error,
        "appelsOutil": len(events),
        "appelsReussis": reussis,
        "effetsDeBordDansLeNoir": broken,
        "creditObtenu": round(float(official.get("partial_credit", 0.0) or 0.0), 4),
        "aSignale": bool(final.strip()),
        "finalAnswer": final[:220],
        "elapsedMs": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pannes", default="oracle")
    parser.add_argument("--frameworks", default=",".join(FRAMEWORKS))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    results = []
    for panne in args.pannes.split(","):
        for framework in args.frameworks.split(","):
            row = run_once(framework, panne.strip())
            results.append(row)
            print("%-14s %-8s survit=%-5s outils=%-3d ok=%-3d effets_noir=%-2d "
                  "credit=%.2f signale=%s %s"
                  % (row["framework"], row["panne"], row["processusSurvit"],
                     row["appelsOutil"], row["appelsReussis"],
                     len(row["effetsDeBordDansLeNoir"]), row["creditObtenu"],
                     row["aSignale"], (row["error"] or "")[:70]), flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1),
                                  encoding="utf-8")


if __name__ == "__main__":
    main()
