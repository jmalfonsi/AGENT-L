"""Jev en ombre de Gemini : mêmes REASON, mêmes contextes, réponses comparées.

Gemini pilote l'exécution (le run suit donc exactement le chemin de
référence) ; Jev reçoit en parallèle chaque REASON à l'identique et sa réponse
est seulement enregistrée. Sur des entrées identiques, on mesure, champ par
champ, l'accord des deux oracles et la probabilité que Jev accordait à sa
réponse selon qu'il est d'accord ou non — c'est ce qui dit si sa confiance
peut servir de garde.

Usage : jev_shadow.py [--tasks a.b,c.d] [--out fichier.json]
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "examples"))

from jev_compare import TASKS, load_env  # noqa: E402

POOL = ThreadPoolExecutor(max_workers=2)


class ShadowLLM:
    def __init__(self, primary, shadow, sink, task_id):
        self.primary, self.shadow, self.sink, self.task_id = primary, shadow, sink, task_id
        self.last_reason_missing = None

    def reason(self, task, context, produce):
        future = POOL.submit(self.shadow.reason, task, context, produce)
        self.primary.last_reason_missing = None
        out = self.primary.reason(task, context, produce)
        self.last_reason_missing = self.primary.last_reason_missing
        try:
            jev_out, error = future.result(), None
        except Exception as exc:                      # noqa: BLE001
            jev_out, error = {}, f"{type(exc).__name__}: {exc}"
        call = self.shadow.calls[-1] if self.shadow.calls else {}
        self.sink.append({
            "task_id": self.task_id, "task": task, "produce": produce,
            "context": context, "gemini": out, "jev": jev_out,
            "gemini_missing": self.last_reason_missing,
            "routes": call.get("routes"), "probability": call.get("probability"),
            "answers": call.get("answers"), "error": error,
        })
        return out

    def select_plan(self, context, candidates):
        return self.primary.select_plan(context, candidates)


def main() -> None:
    def opt(flag: str, default: str) -> str:
        return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default

    load_env()
    import gemini_llm
    import run_task
    from jev_llm import JevLLM

    tasks = opt("--tasks", ",".join(TASKS)).split(",")
    out_path = Path(opt("--out", "jev_shadow.json"))
    model = os.environ.get("AGENTL_BENCH_MODEL", "gemini-3.1-flash-lite")
    sink: list = []
    scores = {}
    for task_id in tasks:
        oracle = ShadowLLM(gemini_llm.GeminiLLM(model=model),
                           JevLLM(fallback=None), sink, task_id)
        run_task.make_llm = lambda model="", o=oracle: o
        res = run_task.run(task_id.split(".", 1)[0], task_id, llm=True)
        scores[task_id] = res.get("partial_credit")
        print(f"{task_id:48} pc={scores[task_id]} reasons="
              f"{sum(1 for r in sink if r['task_id'] == task_id)}", flush=True)
        out_path.write_text(json.dumps({"scores": scores, "reasons": sink},
                                       indent=1, default=str))


if __name__ == "__main__":
    main()
