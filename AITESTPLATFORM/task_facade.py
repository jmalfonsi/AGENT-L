"""Façade métier partagée entre AGENT-L et les baselines de framework."""
from __future__ import annotations

import importlib.util
import inspect
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from campaign_integrity import semantic_outcome
from live_progress import emit_live_event
from task_tool_manifest import task_tool_names
from tool_signatures import declared_inputs


def task_source_paths(task_id: str, task_agent_root: Path) -> tuple[Path, Path]:
    stem = task_id.replace(".", "_")
    return task_agent_root / f"{stem}.agent", task_agent_root / f"{stem}.py"


def load_task_host(task: dict, world: Any, task_agent_root: Path) -> Any:
    _agent_path, host_path = task_source_paths(task["task"], task_agent_root)
    spec = importlib.util.spec_from_file_location(f"shared_task_host_{uuid.uuid4().hex}", host_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Hôte de tâche introuvable: {host_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(task["info"], world)


def baseline_instructions(system_instructions: str, briefing: str | None = None) -> str:
    """Consignes des baselines.

    Sans `briefing` (régime `prompt_only`), les trois frameworks ne reçoivent
    que les consignes communes : ils doivent trouver seuls la marche à suivre,
    conformément au protocole officiel AutomationBench.

    Avec `briefing` (régime `plan_parity`), on y ajoute la transcription du
    programme `.agent` de la tâche, produite par `agent_briefing`. Le texte est
    concaténé tel quel : rien n'est reformulé ici, sans quoi la parité de plan
    ne serait plus vérifiable.
    """
    if not briefing:
        return system_instructions
    return f"{system_instructions}\n\n{briefing}"


def build_task_facade(
    task: dict,
    world: Any,
    events: list[dict],
    *,
    task_agent_root: Path,
    args_model: Callable[[Callable[..., Any], Optional[dict]], Any],
    tool_schema: Callable[[Any], dict],
    json_safe: Callable[[Any], Any],
) -> list[dict]:
    host = load_task_host(task, world, task_agent_root)
    names = task_tool_names(task["task"])
    # Les hôtes de tâche n'annotent pas leurs paramètres : le typage vit dans
    # le bloc `INPUT` du `.agent`. Sans lui, le schéma transmis aux baselines
    # n'aurait aucun type et Gemini déclarerait tout en chaîne de caractères.
    signatures = declared_inputs(task["task"], task_agent_root)
    tools = []
    for name in names:
        if name not in host.tools:
            raise RuntimeError(f"L’hôte {task['task']} n’implémente pas l’outil métier {name}.")
        fn = host.tools[name]
        model = args_model(fn, signatures.get(name))

        def invoke(_fn=fn, _name=name, **kwargs):
            started = time.perf_counter()
            event = {
                "index": len(events) + 1,
                "tool": _name,
                "args": json_safe(kwargs),
                "transportStatus": "completed",
            }
            emit_live_event("tool_start", f"Outil {_name} appelé", tool=_name, index=event["index"])
            try:
                raw_output = _fn(**kwargs)
                try:
                    decoded_output = json.loads(raw_output) if isinstance(raw_output, str) else raw_output
                except json.JSONDecodeError:
                    decoded_output = raw_output
                observation = json_safe(decoded_output)
                semantic_status, semantic_error = semantic_outcome(observation)
                event["observation"] = observation
                event["semanticStatus"] = semantic_status
                event["ok"] = semantic_status == "success"
                if semantic_error:
                    event["error"] = semantic_error
                return json.dumps(observation, ensure_ascii=False)
            except Exception as exc:
                event["transportStatus"] = "exception"
                event["semanticStatus"] = "error"
                event["ok"] = False
                event["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                event["durationMs"] = round((time.perf_counter() - started) * 1000, 2)
                emit_live_event(
                    "tool_end",
                    f"Outil {_name} {'terminé' if event.get('ok') else 'en erreur'}",
                    tool=_name, index=event["index"],
                    status="success" if event.get("ok") else "error",
                    durationMs=event["durationMs"],
                )
                events.append(event)

        tools.append({
            "name": name,
            "description": (inspect.getdoc(fn) or f"Task tool {name}")[:1800],
            "schema": tool_schema(model),
            "args_model": model,
            "invoke": invoke,
        })
    return tools
