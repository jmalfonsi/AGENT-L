from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTL_ROOT = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(AGENTL_ROOT))

import benchmark_runner as runner
from agentl import parse_file
from agentl.nodes import ForEachStmt, IfStmt, LoopStmt, ReasonStmt
from task_facade import load_task_host


def reason_budget(statements, foreach_items: int) -> int:
    total = 0
    for statement in statements:
        if isinstance(statement, ReasonStmt):
            total += 1
        elif isinstance(statement, ForEachStmt):
            total += foreach_items * reason_budget(statement.body, foreach_items)
        elif isinstance(statement, IfStmt):
            total += max(
                reason_budget(statement.then, foreach_items),
                reason_budget(statement.otherwise, foreach_items),
            )
        elif isinstance(statement, LoopStmt):
            total += statement.max_iter * reason_budget(statement.body, foreach_items)
    return total


class AgentLEfficiencyTests(unittest.TestCase):
    def test_employee_request_routing_has_a_ten_call_structural_budget(self):
        task = runner._load_task("hr.employee_request_routing")
        world, _initial = runner.ab.build_world(task["info"])
        host = load_task_host(task, world, runner.TASK_AGENT_ROOT)
        request_count = json.loads(json.dumps(host.tools["list_requests"]()))["request_count"]

        path = runner.TASK_AGENT_ROOT / "hr_employee_request_routing.agent"
        agent = parse_file(str(path)).agents[0]
        budget = sum(
            reason_budget(step.body, request_count)
            for plan in agent.plans
            for step in plan.steps
        )

        self.assertEqual(request_count, 9)
        self.assertEqual(
            budget,
            10,
            "La détection globale de politique doit coûter 1 REASON, puis la classification 1 REASON par message.",
        )


if __name__ == "__main__":
    unittest.main()
