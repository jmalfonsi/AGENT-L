"""Correspondance framework → skill d'écriture, partagée par la fabrique.

Ce module ne contient que des constantes, sans dépendance : la fabrique et
l'agent de codage le lisent tous les deux sans se référencer l'un l'autre.

Le contenu d'un skill n'est jamais recopié dans un prompt. Claude Code le résout
lui-même par son nom, de sorte que la méthode appliquée est toujours celle du
disque, pas une copie figée ici qui dériverait en silence.
"""
from __future__ import annotations

FRAMEWORK_SKILLS: dict[str, str] = {
    "agent_l": "agentl-author",
    "langgraph": "mastering-langgraph",
    "crewai": "design-agent",
    "openai_agents": "openai-agents-author",
}

FRAMEWORK_LABELS: dict[str, str] = {
    "agent_l": "AGENT-L",
    "langgraph": "LangGraph",
    "crewai": "CrewAI",
    "openai_agents": "OpenAI Agents SDK",
}

SKILL_NAMES: tuple[str, ...] = tuple(FRAMEWORK_SKILLS.values())
