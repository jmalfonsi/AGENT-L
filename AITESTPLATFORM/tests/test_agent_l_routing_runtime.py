from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTL_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(AGENTL_ROOT))

import benchmark_runner as runner
from agentl import Runtime, parse_file
from agentl.llm import LLM
from task_facade import load_task_host


class RoutingFixtureLLM(LLM):
    categories = {
        "Question about dental coverage": ("benefits", "Alice Park"),
        "Address change": ("address_change", "Bob Chen"),
        "Missing overtime pay - URGENT": ("payroll", "Carol Diaz"),
        "Need access to Confluence": ("it_access", "Marcus Lindgren"),
        "Update my direct deposit information": ("security", "Sarah Nakamura"),
        "PTO request - April 21-25": ("leave", "Eve Liu"),
        "Payroll Routing Change - Effective Immediately": ("policy_notice", "HR Director"),
        "Legal Team CC Policy - Payroll Routing": ("policy_notice", "HR Director"),
        "Request Handling Scope": ("policy_notice", "HR Director"),
    }

    def __init__(self):
        self.calls = []

    def reason(self, task, context, produce):
        self.calls.append({"task": task, "focus": context.get("focus", {})})
        if "ensemble des demandes" in task:
            return {"payroll_route_override": "payroll-escalations@company.example.com"}
        subject = context["focus"]["r.subject"]
        category, requester = self.categories[subject]
        return {"category": category, "requester_name": requester}

    def select_plan(self, context, candidates):
        return candidates[0] if candidates else None


class AgentLRoutingRuntimeTests(unittest.TestCase):
    def test_ten_reason_calls_keep_the_official_score_at_one(self):
        task = runner._load_task("hr.employee_request_routing")
        world, initial = runner.ab.build_world(task["info"])
        host = load_task_host(task, world, runner.TASK_AGENT_ROOT)
        agent_path = runner.TASK_AGENT_ROOT / "hr_employee_request_routing.agent"
        agent = parse_file(str(agent_path)).agents[0]
        llm = RoutingFixtureLLM()

        runtime = Runtime(agent, host, llm, echo=False).run(max_ticks=12)
        score = runner.ab.score(task["info"], world, initial)

        self.assertEqual(len(llm.calls), 10)
        self.assertEqual(runtime.metrics["llm_calls"], 10)
        self.assertEqual(score["partial_credit"], 1.0)
        self.assertEqual(score["task_completed"], 1.0)


if __name__ == "__main__":
    unittest.main()
