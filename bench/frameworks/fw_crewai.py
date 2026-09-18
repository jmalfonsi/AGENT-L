"""Adaptateur CrewAI — agent ReAct + crochet `before_tool_call` d'approbation.

Mécanismes recommandés par la documentation CrewAI, sans ajout maison :

* approbation : un crochet global `register_before_tool_call_hook` qui
  demande à l'humain pour les outils sensibles et rend `False` pour bloquer ;
* le modèle est un `BaseLLM` scripté en mode ReAct texte
  (`Action:` / `Action Input:` / `Final Answer:`), le même script que pour
  les autres frameworks ;
* pas d'identifiant d'appel stable ni de reprise d'une tâche interrompue :
  la phase `resume` relance la tâche (les `Flow` persistés de CrewAI
  sauvegardent l'état d'un flot, pas un appel d'outil en vol).

`approval_binding` est sans objet : l'approbation se fait dans le même
processus, au moment de l'appel — il n'y a pas d'état d'approbation persisté
à réécrire.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

from crewai import Agent, BaseLLM, Crew, Task  # noqa: E402
from crewai.hooks import (clear_all_hooks,  # noqa: E402
                          register_before_tool_call_hook)
from crewai.tools import tool  # noqa: E402

import common  # noqa: E402

UNSUPPORTED = {"approval_binding": "pas d'état d'approbation persisté : "
                                   "l'approbation a lieu dans le processus, "
                                   "au moment de l'appel"}


def _observations(messages: Any) -> List[Tuple[str, Any]]:
    seen: List[Tuple[str, Any]] = []
    if isinstance(messages, str):
        return seen
    for message in messages:
        if message.get("role") != "assistant":
            continue
        text = str(message.get("content", ""))
        if "Observation:" not in text:
            continue
        action = ""
        for line in text.splitlines():
            if line.startswith("Action:"):
                action = line.split(":", 1)[1].strip()
        seen.append((action, text.split("Observation:", 1)[1].strip()))
    return seen


def _react(move: Dict[str, Any]) -> str:
    if "final" in move:
        return f"Thought: I now know the final answer\nFinal Answer: {move['final']}"
    return (f"Thought: je suis la demande\nAction: {move['tool']}\n"
            f"Action Input: {json.dumps(move['args'])}")


def build(scenario: str, variant: str, world: common.World) -> Crew:
    sensitive = set(common.SCENARIOS[scenario].sensitive)
    body = (common.TICKET_INJECTED if variant == "attack"
            else common.TICKET_CLEAN)

    class Scripted(BaseLLM):
        def call(self, messages, tools=None, callbacks=None,
                 available_functions=None, from_task=None, from_agent=None,
                 response_model=None, **kwargs):
            return _react(common.next_move(scenario, variant,
                                           _observations(messages)))

        def supports_function_calling(self) -> bool:
            return False

        def supports_stop_words(self) -> bool:
            return False

        def get_context_window_size(self) -> int:
            return 8192

    @tool("fetch_ticket")
    def fetch_ticket() -> str:
        """Lit le ticket de support en attente."""
        return body

    @tool("check_wipeable")
    def check_wipeable(host: str) -> str:
        """Vérifie qu'un hôte figure dans l'inventaire nettoyable."""
        return json.dumps(common.check_wipeable(host))

    @tool("wipe_host")
    def wipe_host(host: str) -> str:
        """Efface les fichiers temporaires d'un hôte."""
        return json.dumps(world.apply("wipe_host", {"host": host}))

    @tool("restart_service")
    def restart_service(name: str) -> str:
        """Redémarre un service."""
        return json.dumps(world.apply("restart_service", {"name": name}))

    @tool("transfer")
    def transfer(amount: float, to: str) -> str:
        """Effectue un virement."""
        return json.dumps(world.apply("transfer",
                                      {"amount": amount, "to": to}))

    def approval(context) -> Any:
        if context.tool_name not in sensitive:
            return None
        granted = common.approver_decision(scenario, variant,
                                           context.tool_name,
                                           dict(context.tool_input), world)
        return None if granted else False

    clear_all_hooks()
    register_before_tool_call_hook(approval)

    agent = Agent(role="opérateur", goal="traiter la demande",
                  backstory="agent d'exploitation", llm=Scripted(model="scripted"),
                  tools=[fetch_ticket, check_wipeable, wipe_host,
                         restart_service, transfer],
                  verbose=False, max_iter=6)
    task = Task(description="Traite la demande en attente.",
                expected_output="un compte rendu", agent=agent)
    return Crew(agents=[agent], tasks=[task], verbose=False)


def run(scenario: str, variant: str, phase: str, world: common.World) -> str:
    if scenario in UNSUPPORTED:
        raise NotImplementedError(UNSUPPORTED[scenario])
    world.crash_armed = phase == "crash"
    return str(build(scenario, variant, world).kickoff())


def tamper(scenario: str, world: common.World) -> None:
    raise NotImplementedError(UNSUPPORTED.get(scenario, scenario))
