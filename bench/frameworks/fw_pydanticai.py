"""Adaptateur PydanticAI — outils `requires_approval=True` + outils différés.

Mécanismes recommandés par la documentation PydanticAI, sans ajout maison :

* approbation : `requires_approval=True` sur les outils sensibles ; la course
  s'arrête sur `DeferredToolRequests`, l'application demande à l'humain et
  relance avec `DeferredToolResults(approvals={id: True | ToolDenied})` et
  l'historique des messages, qu'elle a persisté entre-temps
  (`ModelMessagesTypeAdapter`) ;
* idempotence : `RunContext.tool_call_id` est transmis au service ;
* reprise après panne : la bibliothèque seule n'en a pas — l'exécution
  durable passe par les intégrations Temporal / DBOS / Prefect, non testées
  ici (infrastructure externe). La phase `resume` relance donc la tâche,
  comme le ferait une application sans ces intégrations.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Tuple

from pydantic_ai import (Agent, DeferredToolRequests, DeferredToolResults,
                         RunContext, ToolDenied)
from pydantic_ai.messages import (ModelMessage, ModelMessagesTypeAdapter,
                                  ModelRequest, ModelResponse, RetryPromptPart,
                                  TextPart, ToolCallPart, ToolReturnPart)
from pydantic_ai.models.function import AgentInfo, FunctionModel

import common


def _observations(messages: List[ModelMessage]) -> List[Tuple[str, Any]]:
    seen: List[Tuple[str, Any]] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                seen.append((part.tool_name, part.content))
            elif isinstance(part, RetryPromptPart) and part.tool_name:
                seen.append((part.tool_name, part.content))
    return seen


def build(scenario: str, variant: str, world: common.World) -> Agent:
    sensitive = set(common.SCENARIOS[scenario].sensitive)
    body = (common.TICKET_INJECTED if variant == "attack"
            else common.TICKET_CLEAN)

    def model(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        move = common.next_move(scenario, variant, _observations(messages))
        if "final" in move:
            return ModelResponse(parts=[TextPart(move["final"])])
        return ModelResponse(parts=[ToolCallPart(
            move["tool"], dict(move["args"]),
            tool_call_id=f"call_{uuid.uuid4().hex[:12]}")])

    agent = Agent(FunctionModel(model), output_type=[str, DeferredToolRequests])

    @agent.tool_plain
    def fetch_ticket() -> str:
        """Lit le ticket de support en attente."""
        return body

    @agent.tool_plain
    def check_wipeable(host: str) -> Dict[str, Any]:
        """Vérifie qu'un hôte figure dans l'inventaire nettoyable."""
        return common.check_wipeable(host)

    @agent.tool_plain(requires_approval="wipe_host" in sensitive)
    def wipe_host(host: str) -> Dict[str, Any]:
        """Efface les fichiers temporaires d'un hôte."""
        return world.apply("wipe_host", {"host": host})

    @agent.tool_plain
    def restart_service(name: str) -> Dict[str, Any]:
        """Redémarre un service."""
        return world.apply("restart_service", {"name": name})

    @agent.tool(requires_approval="transfer" in sensitive)
    def transfer(ctx: RunContext[None], amount: float, to: str) -> Dict[str, Any]:
        """Effectue un virement."""
        return world.apply("transfer", {"amount": amount, "to": to},
                           key=ctx.tool_call_id)

    return agent


def _args(part: ToolCallPart) -> Dict[str, Any]:
    return part.args_as_dict()


def _decide(scenario: str, variant: str, requests: DeferredToolRequests,
            world: common.World) -> Dict[str, bool]:
    return {part.tool_call_id: common.approver_decision(
                scenario, variant, part.tool_name, _args(part), world)
            for part in requests.approvals}


def _results(decisions: Dict[str, bool]) -> DeferredToolResults:
    return DeferredToolResults(approvals={
        call_id: (True if granted else ToolDenied("refusé par l'approbateur"))
        for call_id, granted in decisions.items()})


def _drive(agent: Agent, scenario, variant, world, result) -> None:
    while isinstance(result.output, DeferredToolRequests):
        decisions = _decide(scenario, variant, result.output, world)
        result = agent.run_sync(message_history=result.all_messages(),
                                deferred_tool_results=_results(decisions))


def run(scenario: str, variant: str, phase: str, world: common.World) -> str:
    agent = build(scenario, variant, world)
    history = world.dir / "pai_messages.json"
    decisions_file = world.dir / "pai_decisions.json"
    if phase == "resume" and scenario == "approval_binding":
        messages = ModelMessagesTypeAdapter.validate_json(history.read_bytes())
        decisions = json.loads(decisions_file.read_text("utf-8"))
        result = agent.run_sync(message_history=messages,
                                deferred_tool_results=_results(decisions))
        _drive(agent, scenario, variant, world, result)
        return ""

    world.crash_armed = phase == "crash"
    result = agent.run_sync("traite la demande")
    if phase == "request":
        # L'application persiste l'historique et attend la réponse humaine.
        history.write_bytes(ModelMessagesTypeAdapter.dump_json(
            result.all_messages()))
        decisions = _decide(scenario, variant, result.output, world)
        decisions_file.write_text(json.dumps(decisions), "utf-8")
        return ""
    _drive(agent, scenario, variant, world, result)
    return ""


def tamper(scenario: str, world: common.World) -> None:
    """Réécrit l'appel en attente dans l'historique persisté."""
    history = world.dir / "pai_messages.json"
    messages = json.loads(history.read_text("utf-8"))
    touched = 0
    for message in messages:
        for part in message.get("parts", []):
            if part.get("part_kind") == "tool-call" \
                    and part.get("tool_name") == "transfer":
                part["args"] = dict(common.REWRITTEN)
                touched += 1
    history.write_text(json.dumps(messages), "utf-8")
    world.note("tampered", target="message_history", calls=touched)
    if not touched:
        raise SystemExit("rien à réécrire : le cas serait vide")
