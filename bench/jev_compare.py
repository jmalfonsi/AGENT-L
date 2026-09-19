"""Compare les oracles d'AGENT-L sur les tâches AutomationBench à `REASON`.

Trois oracles, même `.agent`, même hôte, même barème officiel :

  gemini   GeminiLLM seul (référence du banc)
  hybrid   JevLLM : Jev pour les champs clos, Gemini pour le texte libre
  jev      JevLLM sans repli : ce que Jev ne sait pas faire reste absent

Usage : jev_compare.py [--modes gemini,hybrid,jev] [--tasks a.b,c.d]
                       [--repeat N] [--escalate P] [--out fichier.json]
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))
sys.path.insert(0, str(HERE))

TASKS = [
    "hr.comp_adjustment_batch",
    "hr.employee_request_routing",
    "hr.employee_transfer_approval_workflow",
    "hr.offboarding_automation",
    "marketing.social_mention_response",
    "operations.chatgpt_feedback_analysis",
    "sales.chatgpt_lead_classification_pipeline",
    "sales.feedback_routing",
    "support.reamaze_feedback_sentiment",
]


def load_env() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"\''))


class Timed:
    """Mesure le temps passé dans l'oracle, hors attente de quota Gemini."""

    def __init__(self, inner):
        self.inner = inner
        self.seconds = 0.0
        self.reasons = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __setattr__(self, name, value):
        if name in ("inner", "seconds", "reasons"):
            object.__setattr__(self, name, value)
        else:
            setattr(self.inner, name, value)

    def reason(self, task, context, produce):
        started = time.monotonic()
        try:
            return self.inner.reason(task, context, produce)
        finally:
            self.seconds += time.monotonic() - started
            self.reasons += 1

    def select_plan(self, context, candidates):
        started = time.monotonic()
        try:
            return self.inner.select_plan(context, candidates)
        finally:
            self.seconds += time.monotonic() - started


def make_oracle(mode: str, escalate: float):
    import gemini_llm
    from jev_llm import JevLLM

    model = os.environ.get("AGENTL_BENCH_MODEL", "gemini-3.1-flash-lite")
    if mode == "gemini":
        return gemini_llm.GeminiLLM(model=model)
    if mode == "hybrid":
        return JevLLM(fallback=gemini_llm.GeminiLLM(model=model),
                      escalate_below=escalate)
    if mode == "jev":
        return JevLLM(fallback=None, escalate_below=0.0)
    raise SystemExit(f"mode inconnu : {mode}")


def pace_seconds():
    """Temps d'attente de quota imposé par gemini_llm.pace, à soustraire."""
    import gemini_llm

    box = {"s": 0.0}
    original = gemini_llm.pace

    def timed_pace(rpm: int = 0):
        started = time.monotonic()
        original(rpm)
        box["s"] += time.monotonic() - started

    gemini_llm.pace = timed_pace
    return box


def run_one(task_id: str, mode: str, escalate: float, paced) -> dict:
    import run_task
    from jev_llm import usage_summary

    domain = task_id.split(".", 1)[0]
    oracle = Timed(make_oracle(mode, escalate))
    run_task.make_llm = lambda model="": oracle
    paced["s"] = 0.0
    started = time.monotonic()
    try:
        out = run_task.run(domain, task_id, llm=True)
    except Exception as exc:                          # noqa: BLE001
        out = {"partial_credit": 0.0, "task_completed": 0.0,
               "crash": f"{type(exc).__name__}: {exc}"}
    wall = time.monotonic() - started
    inner = oracle.inner
    usage = usage_summary(inner)
    if mode == "gemini":
        rows = inner.responses
        usage.update(gen_calls=len(rows), jev_calls=0,
                     gen_input_tokens=sum(r.get("promptTokens") or 0 for r in rows),
                     gen_output_tokens=sum(r.get("completionTokens") or 0 for r in rows))
    failed = [f"{a['type']} {a['params']}" for a in out.get("assertions", [])
              if not a["passed"] and not a.get("excluded")]
    reasons = []
    for call in getattr(inner, "calls", []):
        if call.get("kind") == "reason" and "routes" in call:
            reasons.append({k: call.get(k) for k in
                            ("task", "routes", "probability", "escalated", "jevError")})
    return {
        "task": task_id, "mode": mode,
        "partial_credit": out.get("partial_credit"),
        "task_completed": out.get("task_completed"),
        "tool_calls": out.get("tool_calls"), "blocked": out.get("blocked"),
        "errors": out.get("errors", [])[:5], "crash": out.get("crash"),
        "failed_assertions": failed[:8],
        "wall_seconds": round(wall, 2),
        "oracle_seconds": round(oracle.seconds - paced["s"], 2),
        "quota_wait_seconds": round(paced["s"], 2),
        "reason_calls": oracle.reasons,
        **usage,
        "jev_reasons": reasons,
    }


def main() -> None:
    def opt(flag: str, default: str) -> str:
        return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

    load_env()
    modes = opt("--modes", "gemini,hybrid,jev").split(",")
    tasks = opt("--tasks", ",".join(TASKS)).split(",")
    repeat = int(opt("--repeat", "1"))
    escalate = float(opt("--escalate", "0"))
    out_path = opt("--out", "")
    paced = pace_seconds()
    rows = []
    for rep in range(repeat):
        for task_id in tasks:
            for mode in modes:
                row = run_one(task_id, mode, escalate, paced)
                row["rep"] = rep
                rows.append(row)
                print(f"{task_id:48} {mode:7} pc={row['partial_credit']!s:6} "
                      f"ok={row['task_completed']!s:4} oracle={row['oracle_seconds']:6.1f}s "
                      f"gen={row['gen_calls']:3} jev={row['jev_calls']:3} "
                      f"{row.get('crash') or ''}", flush=True)
                if out_path:
                    Path(out_path).write_text(json.dumps(rows, indent=1, default=str))


if __name__ == "__main__":
    main()
