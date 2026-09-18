"""Adaptateur LangGraph — boucle ReAct + revue humaine par `interrupt()`.

Mécanismes recommandés par la documentation LangGraph, sans ajout maison :

* human-in-the-loop : un nœud `review` avant `ToolNode` appelle
  `interrupt()` pour chaque appel d'outil sensible ; l'application reprend
  avec `Command(resume=décision)` ;
* persistance : `SqliteSaver` (un fichier par cas), reprise par
  `graph.invoke(None, config)` après une panne ;
* idempotence : l'identifiant d'appel d'outil (`InjectedToolCallId`) est
  point de contrôle avec le message du modèle, donc stable d'une tentative à
  l'autre — c'est la meilleure clé que LangGraph fournisse, on la donne au
  service.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from typing import Annotated, Any, Dict

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command, interrupt

import common

CONFIG = {"configurable": {"thread_id": "case"}}
# Le défaut (`"async"`) écrit le point de contrôle pendant l'étape suivante :
# une panne dans l'outil perd alors le message du modèle, qui est rejoué avec
# un nouvel identifiant d'appel — effet doublé. La documentation recommande
# `"sync"` pour l'exécution durable ; c'est ce qu'on mesure.
DURABILITY = os.environ.get("BENCH_LANGGRAPH_DURABILITY", "sync")


def build(scenario: str, variant: str, world: common.World):
    sensitive = set(common.SCENARIOS[scenario].sensitive)
    body = (common.TICKET_INJECTED if variant == "attack"
            else common.TICKET_CLEAN)

    @tool
    def fetch_ticket() -> str:
        """Lit le ticket de support en attente."""
        return body

    @tool
    def check_wipeable(host: str) -> str:
        """Vérifie qu'un hôte figure dans l'inventaire nettoyable."""
        return json.dumps(common.check_wipeable(host))

    @tool
    def wipe_host(host: str) -> str:
        """Efface les fichiers temporaires d'un hôte."""
        return json.dumps(world.apply("wipe_host", {"host": host}))

    @tool
    def restart_service(name: str) -> str:
        """Redémarre un service."""
        return json.dumps(world.apply("restart_service", {"name": name}))

    @tool
    def transfer(amount: float, to: str,
                 tool_call_id: Annotated[str, InjectedToolCallId]) -> str:
        """Effectue un virement."""
        return json.dumps(world.apply("transfer",
                                      {"amount": amount, "to": to},
                                      key=tool_call_id))

    tools = [fetch_ticket, check_wipeable, wipe_host, restart_service,
             transfer]

    def agent(state: MessagesState) -> Dict[str, Any]:
        seen = [(m.name, m.content) for m in state["messages"]
                if isinstance(m, ToolMessage)]
        move = common.next_move(scenario, variant, seen)
        if "final" in move:
            return {"messages": [AIMessage(content=move["final"])]}
        call = {"name": move["tool"], "args": move["args"],
                "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}
        return {"messages": [AIMessage(content="", tool_calls=[call])]}

    def review(state: MessagesState) -> Command:
        last = state["messages"][-1]
        for call in last.tool_calls:
            if call["name"] not in sensitive:
                continue
            decision = interrupt({"tool": call["name"], "args": call["args"],
                                  "id": call["id"]})
            if decision != "approve":
                return Command(goto="agent", update={"messages": [
                    ToolMessage(content="refusé par l'approbateur",
                                name=call["name"], tool_call_id=call["id"])]})
        return Command(goto="tools")

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    graph.add_node("review", review, destinations=("tools", "agent"))
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition,
                                {"tools": "review", END: END})
    graph.add_edge("tools", "agent")
    saver = SqliteSaver(sqlite3.connect(str(world.dir / "lg.sqlite"),
                                        check_same_thread=False))
    return graph.compile(checkpointer=saver)


def _decide(scenario: str, variant: str, payload: Dict[str, Any],
            world: common.World) -> str:
    granted = common.approver_decision(scenario, variant, payload["tool"],
                                       payload["args"], world)
    return "approve" if granted else "reject"


def _drive(graph, scenario, variant, world, result) -> None:
    while "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        decision = _decide(scenario, variant, payload, world)
        result = graph.invoke(Command(resume=decision), CONFIG,
                              durability=DURABILITY)


def run(scenario: str, variant: str, phase: str, world: common.World) -> str:
    graph = build(scenario, variant, world)
    decision_file = world.dir / "lg_decision.json"
    if phase == "resume" and scenario == "approval_binding":
        # L'humain a répondu entre-temps ; l'application reprend le fil.
        decision = json.loads(decision_file.read_text("utf-8"))
        result = graph.invoke(Command(resume=decision), CONFIG,
                              durability=DURABILITY)
        _drive(graph, scenario, variant, world, result)
        return ""
    if phase == "resume":
        # Reprise après panne, depuis le dernier point de contrôle.
        result = graph.invoke(None, CONFIG, durability=DURABILITY)
        _drive(graph, scenario, variant, world, result)
        return ""

    world.crash_armed = phase == "crash"
    task = {"messages": [HumanMessage(content="traite la demande")]}
    result = graph.invoke(task, CONFIG, durability=DURABILITY)
    if phase == "request":
        # La demande d'approbation part chez l'humain ; le processus s'arrête.
        payload = result["__interrupt__"][0].value
        decision_file.write_text(json.dumps(
            _decide(scenario, variant, payload, world)), "utf-8")
        return ""
    _drive(graph, scenario, variant, world, result)
    return ""


def tamper(scenario: str, world: common.World) -> None:
    """Réécrit l'appel en attente par l'API publique de persistance.

    Même attaquant que pour AGENT-L : un accès en écriture à l'état persisté
    entre l'approbation et l'exécution, sans toucher à la décision humaine.
    """
    graph = build(scenario, "attack", world)
    snapshot = graph.get_state(CONFIG)
    last = snapshot.values["messages"][-1]
    calls = [{**c, "args": dict(common.REWRITTEN)} for c in last.tool_calls]
    graph.update_state(CONFIG, {"messages": [
        AIMessage(content=last.content, id=last.id, tool_calls=calls)]})
    world.note("tampered", target="checkpoint", calls=len(calls))
