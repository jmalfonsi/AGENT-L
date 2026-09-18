"""AGENT-L — langage agentique déclaratif.

    from agentl import parse_file, Analyzer, Host, Runtime, MockLLM

    program = parse_file("examples/soc_analyst.agent")
    agent = program.agents[0]
    Analyzer(agent).run()          # bonne formation
    verify(agent)                  # sûreté : huit théorèmes par agent
    Runtime(agent, host, MockLLM()).run(max_ticks=4)

Multi-agent :

    Society(program.agents, hosts, llms).run(max_ticks=6)
"""
from .analyzer import Analyzer, Diagnostic, check_program
from .bayes import Inference, infer, reachable_range
from .core import AgentLError, Belief, ParseError, Symbol, UNDEFINED
from .host import Host
from .llm import AnthropicLLM, LLM, MockLLM
from .nodes import Agent, Program
from .parser import parse_file, parse_source
from .planner import Planner, PlanningResult
from .society import Society
from .solver import entails, satisfiable
from .verifier import Report, Theorem, Verifier, verify
from .policy import ActionRequest, PolicyEngine
from .replay import (Journal, RecordingHost, RecordingLLM, ReplayDivergence,
                     ReplayError, ReplayHost, ReplayLLM, verify_trace)
from .runtime import Runtime, Trace
from .state import Evaluator, State

__version__ = "1.9.0"

__all__ = [
    "Agent", "AgentLError", "Analyzer", "AnthropicLLM", "ActionRequest",
    "Belief", "Diagnostic", "Evaluator", "Host", "LLM", "MockLLM",
    "Inference", "ParseError", "Planner", "PlanningResult", "PolicyEngine",
    "check_program", "reachable_range",
    "Program", "Report", "Runtime", "Society", "State", "Symbol", "Theorem",
    "Trace", "UNDEFINED", "Verifier", "entails", "infer", "parse_file",
    "parse_source", "satisfiable", "verify", "__version__",
    # rejeu (§26)
    "Journal", "RecordingHost", "RecordingLLM", "ReplayDivergence",
    "ReplayError", "ReplayHost", "ReplayLLM", "verify_trace",
]
