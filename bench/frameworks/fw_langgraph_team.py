"""LangGraph — l'équipe : un graphe superviseur, deux sous-graphes (1.16).

Même architecture que celle que la documentation LangGraph recommande pour un
superviseur : chaque spécialiste est un **sous-graphe** compilé (une boucle
ReAct avec sa revue humaine par `interrupt()`) ajouté comme nœud du graphe
parent, qui partage la clé `messages`. Mécanismes, et rien d'autre :

* revue humaine : un nœud `review` avant `ToolNode`, dans le sous-graphe ;
* persistance : `SqliteSaver` sur le graphe parent, hérité par les
  sous-graphes ; reprise par `graph.invoke(None, config)` ;
* idempotence : l'identifiant d'appel d'outil comme clé du service.

Ce que LangGraph ne propose pas est aussi ce que le banc mesure : le
superviseur n'a aucun moyen intégré de **plafonner** les outils d'un
sous-graphe ni de suivre la provenance de ce qu'un premier spécialiste a
rapporté. Un développeur peut écrire cette garde lui-même ; le banc mesure ce
que le framework donne sans ce code.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Annotated, Any, Dict, List

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command, interrupt

import common
from fw_langgraph import CONFIG, DURABILITY, _drive


def _agent_graph(scenario: str, variant: str, role: str, tools: List[Any],
                 sensitive: set):
    """Un spécialiste : boucle ReAct + revue humaine, compilée en sous-graphe."""

    def agent(state: MessagesState) -> Dict[str, Any]:
        seen = [(m.name, m.content) for m in state["messages"]
                if isinstance(m, ToolMessage)]
        move = common.next_team_move(scenario, variant, role, seen)
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
    return graph.compile()


def build(scenario: str, variant: str, world: common.World):
    sensitive = set(common.SCENARIOS[scenario].sensitive)
    ticket = common.team_ticket(scenario, variant)

    @tool
    def fetch_ticket() -> str:
        """Lit le ticket de support en attente."""
        return ticket

    @tool
    def wipe_host(host: str) -> str:
        """Efface les fichiers temporaires d'un hôte."""
        return json.dumps(world.apply("wipe_host", {"host": host}))

    @tool
    def transfer(amount: float, to: str,
                 tool_call_id: Annotated[str, InjectedToolCallId]) -> str:
        """Effectue un virement."""
        return json.dumps(world.apply("transfer",
                                      {"amount": amount, "to": to},
                                      key=tool_call_id))

    scout = _agent_graph(scenario, variant, "scout", [fetch_ticket], sensitive)
    responder = _agent_graph(scenario, variant, "responder",
                             [wipe_host, transfer], sensitive)
    parent = StateGraph(MessagesState)
    parent.add_node("scout", scout)
    parent.add_node("responder", responder)
    parent.add_edge(START, "scout")
    parent.add_edge("scout", "responder")
    parent.add_edge("responder", END)
    saver = SqliteSaver(sqlite3.connect(str(world.dir / "lg.sqlite"),
                                        check_same_thread=False))
    return parent.compile(checkpointer=saver)


def run(scenario: str, variant: str, phase: str, world: common.World) -> str:
    graph = build(scenario, variant, world)
    if phase == "resume":
        result = graph.invoke(None, CONFIG, durability=DURABILITY)
        _drive(graph, scenario, variant, world, result)
        return ""
    world.crash_armed = phase == "crash"
    task = {"messages": [HumanMessage(content="traite la demande")]}
    result = graph.invoke(task, CONFIG, durability=DURABILITY)
    _drive(graph, scenario, variant, world, result)
    return ""
