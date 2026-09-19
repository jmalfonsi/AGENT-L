"""Exécute un `.agent` compilé contre une tâche AutomationBench et le note.

Usage : run_task.py <domaine> <task> [--ticks N] [--echo]
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/AGENT-L")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import ab_bridge as ab  # noqa: E402
from agentl import Runtime, parse_file  # noqa: E402
from agentl.analyzer import Analyzer  # noqa: E402


def make_llm(model: str = ""):
    """Oracle réel : Gemini, ou Jev selon `AGENTL_ORACLE`. Le LLM ne voit
    qu'une projection en lecture seule de l'état et ne peut produire qu'un
    dict conforme au schéma `PRODUCE` — il ne choisit jamais d'agir."""
    import os

    sys.path.insert(0, "/home/ubuntu/AGENT-L/examples")
    from jev_llm import oracle_from_env

    if not os.environ.get("GEMINI_API_KEY"):
        env = Path("/home/ubuntu/HAL/.env")
        for line in env.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip().strip('"\'')
    repo_env = Path(__file__).resolve().parent.parent / ".env"
    if repo_env.exists() and not os.environ.get("TYPESAFE_API_KEY"):
        for line in repo_env.read_text().splitlines():
            if line.startswith(("TYPESAFE_API_KEY=", "TYPESAFE_AI_KEY=")):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value.strip().strip('"\''))
    # `AGENTL_ORACLE=hybrid` : Jev (TypeSafe) pour les champs clos, Gemini
    # pour le texte libre ; `jev` : Jev seul. Défaut : Gemini seul.
    return oracle_from_env(model)


def run(domain: str, name: str, ticks: int = 0, echo: bool = False,
        llm: bool = False, html: str = "", record: str = "") -> dict:
    task = ab.load_task(domain, name)
    info = task["info"]
    path = Path(__file__).resolve().parent / "tasks" / f"{name.replace('.', '_')}.agent"
    program = parse_file(str(path))
    agent = program.agents[0]

    # Budget de ticks : le programme sait combien de tours son cycle demande.
    # Un défaut arbitraire plus petit que son `LOOP … MAX` coupe le run au
    # milieu du lot et se lit comme un échec de l'agent — alors que c'est le
    # runner qui a rendu la main trop tôt.
    if not ticks:
        ticks = getattr(getattr(agent, "loop", None), "max_iter", 0) or 6

    errors = [d for d in Analyzer(agent).run() if d.severity == "error"]
    if errors:
        # Un programme comportant une erreur ne s'exécute pas : c'est la
        # règle du langage, on ne la contourne pas pour le banc.
        return {"compiled": False, "diagnostics": [d.code for d in errors],
                "partial_credit": 0.0, "task_completed": 0.0}

    world, initial = ab.build_world(info)
    # Norme de nommage AGENT-L : X.agent est servi par X.py, dans le même
    # répertoire. À défaut, l'hôte purement mécanique du pont.
    host_path = path.with_suffix(".py")
    if host_path.exists():
        import importlib.util

        spec = importlib.util.spec_from_file_location(host_path.stem, host_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        host = module.build(info, world)
    else:
        host = ab.make_host(info, world)
    oracle = make_llm() if llm else None

    # `--record` scelle les points de franchissement de frontière ; `--html`
    # rend le journal visuel. Les deux sont optionnels et n'altèrent en rien
    # l'exécution notée.
    journal = None
    if record:
        from agentl.replay import Journal, RecordingHost, RecordingLLM, sha256
        journal = Journal(meta={"agent": agent.name, "source": str(path),
                                "source_sha256": sha256(path.read_text()),
                                "ticks": ticks})
        host = RecordingHost(host, journal)
        oracle = RecordingLLM(oracle, journal) if oracle is not None else None

    runtime = Runtime(agent, host, oracle, echo=echo)
    runtime.run(max_ticks=ticks)
    if journal is not None:
        journal.seal(runtime.trace.render())
        journal.save(record)
    if html:
        from agentl import trace_html
        Path(html).write_text(trace_html.render(runtime, agent), encoding="utf-8")

    result = ab.score(info, world, initial)
    result["compiled"] = True
    result["tool_calls"] = len(host.call_log)
    result["by_tool"] = dict(collections.Counter(c["tool"] for c in host.call_log))
    result["blocked"] = runtime.metrics.get("blocked", 0)
    result["errors"] = [e.text for e in runtime.trace.events if e.kind == "ERROR"]
    return result


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ticks = 0
    if "--ticks" in sys.argv:
        ticks = int(sys.argv[sys.argv.index("--ticks") + 1])
        args = [a for a in args if a != str(ticks)]
    def opt(flag: str) -> str:
        return (sys.argv[sys.argv.index(flag) + 1]
                if flag in sys.argv else "")

    html, record = opt("--html"), opt("--record")
    args = [a for a in args if a not in (html, record) or not a]
    out = run(args[0], args[1], ticks=ticks, echo="--echo" in sys.argv,
              llm="--llm" in sys.argv, html=html, record=record)
    failed = [a for a in out.pop("assertions", []) if not a["passed"] and not a.get("excluded")]
    for key, value in out.items():
        print(f"{key}: {value}")
    if failed:
        print(f"\nassertions échouées ({len(failed)}) :")
        for a in failed[:12]:
            print(f"  ✗ {a['type']} {a['params']}")
