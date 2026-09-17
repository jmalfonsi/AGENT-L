"""Construction des bundles de sources affichés par AITESTPLATFORM."""
from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


def build_framework_code(
    framework_id: str,
    task_id: Optional[str],
    *,
    runners: Mapping[str, Callable[..., dict]],
    metadata: Mapping[str, dict],
    task_agent_root: Path,
    load_task: Callable[[str], dict],
    bound_tools: Callable[..., Any],
    system_instructions: str,
) -> dict:
    """Retourne uniquement les sources réellement appelées par le runner."""
    if framework_id not in runners:
        raise ValueError(f"Framework inconnu: {framework_id}")

    meta = metadata[framework_id]
    adapter = runners[framework_id]
    adapter_source = {
        "name": f"benchmark_runner.py::{adapter.__name__}",
        "label": f"Adaptateur exécuté — {meta['name']}",
        "language": "python",
        "content": inspect.getsource(adapter),
    }

    if framework_id == "agent_l":
        if not task_id:
            raise ValueError("Une tâche est requise pour afficher le programme AGENT-L spécifique.")
        load_task(task_id)
        source_stem = task_id.replace(".", "_")
        agent_path = task_agent_root / f"{source_stem}.agent"
        host_path = task_agent_root / f"{source_stem}.py"
        if not agent_path.is_file() or not host_path.is_file():
            raise FileNotFoundError(f"Sources AGENT-L absentes pour {task_id}.")
        return {
            "id": framework_id,
            "name": meta["name"],
            "taskId": task_id,
            "scope": "task_specific",
            "description": (
                "Sources exactes du programme compilé, de son hôte AutomationBench "
                "et de l’adaptateur qui les exécute."
            ),
            "files": [
                {
                    "name": agent_path.name,
                    "label": "Programme AGENT-L spécifique à la tâche",
                    "language": "agentl",
                    "content": agent_path.read_text(encoding="utf-8"),
                },
                {
                    "name": host_path.name,
                    "label": "Hôte Python contrôlé spécifique à la tâche",
                    "language": "python",
                    "content": host_path.read_text(encoding="utf-8"),
                },
                adapter_source,
            ],
        }

    if task_id:
        load_task(task_id)

    return {
        "id": framework_id,
        "name": meta["name"],
        "taskId": task_id,
        "scope": "shared_adapter",
        "description": (
            "Adaptateur natif générique réellement exécuté pour toutes les tâches, "
            "avec la façade métier et le prompt système commun. Le programme .agent "
            "n’est ni lu par l’adaptateur ni transmis au modèle."
        ),
        "files": [
            adapter_source,
            {
                "name": "benchmark_runner.py::_bound_tools",
                "label": "Liaison réelle aux outils AutomationBench",
                "language": "python",
                "content": inspect.getsource(bound_tools),
            },
            {
                "name": "SYSTEM_INSTRUCTIONS.txt",
                "label": "Prompt système commun aux baselines",
                "language": "text",
                "content": system_instructions,
            },
        ],
    }
