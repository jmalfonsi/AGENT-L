from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import benchmark_runner as runner
from task_facade import build_task_facade
from task_tool_manifest import TASK_TOOL_MANIFEST


class BenchmarkRunnerContractTests(unittest.TestCase):
    def test_every_catalog_task_has_a_usable_tool_facade(self):
        """Un couple `.agent`/`.py` déposé dans bench/tasks entre au catalogue
        de lui-même : sans entrée de manifeste, la plateforme casse au
        démarrage. Ce test échoue à la place, en nommant la tâche."""
        catalog = {task["id"] for task in runner.task_catalog()}
        self.assertEqual(catalog, set(TASK_TOOL_MANIFEST),
                         "manifeste et catalogue divergent")
        for task_id in sorted(catalog):
            task = runner._load_task(task_id)
            world, _initial = runner.ab.build_world(task["info"])
            tools = build_task_facade(
                task, world, [], task_agent_root=runner.TASK_AGENT_ROOT,
                args_model=runner._args_model, tool_schema=runner._tool_schema,
                json_safe=runner._json_safe)
            self.assertEqual([tool["name"] for tool in tools],
                             list(TASK_TOOL_MANIFEST[task_id]), task_id)

    def test_catalog_contains_official_numbers_and_only_ready_tasks(self):
        tasks = runner.task_catalog()
        # Le catalogue est piloté par la présence du couple `.agent`/`.py` :
        # son cardinal se lit dans le manifeste, jamais dans une constante que
        # le prochain agent porté ferait mentir.
        self.assertEqual(len(tasks), len(TASK_TOOL_MANIFEST))
        self.assertTrue(all(task["source"] == "AutomationBench public" for task in tasks))
        self.assertTrue(all(task["agentLReady"] for task in tasks))
        self.assertTrue(all(isinstance(task["number"], int) and task["number"] > 0 for task in tasks))
        self.assertTrue(all("expectedState" not in task for task in tasks))

    def test_task_detail_is_the_original_automationbench_object(self):
        detail = runner.task_detail("support.reamaze_feedback_sentiment")
        self.assertEqual(detail["number"], 1531)
        self.assertEqual(detail["original"]["example_id"], 1531)
        self.assertEqual(set(detail["original"]), {"example_id", "task", "prompt", "answer", "info"})
        self.assertEqual(len(detail["toolDefinitions"]), 8)
        self.assertEqual(len(detail["assertions"]), 32)
        self.assertIn("reamaze", detail["initialState"])

    def test_all_native_frameworks_are_installed_on_one_model(self):
        rows = runner.framework_status()
        self.assertEqual(
            {row["id"] for row in rows},
            {"agent_l", "langgraph", "crewai", "openai_agents"},
        )
        self.assertTrue(all(row["model"] == "gemini-3.1-flash-lite" for row in rows))
        # L'installation est un fait de la machine, pas du code : la CI
        # n'installe pas les frameworks concurrents dans cet environnement.
        missing = sorted(row["id"] for row in rows if not row["installed"])
        if missing:
            self.skipTest(f"frameworks non installés ici : {', '.join(missing)}")

    def test_bound_tool_executes_official_world_and_records_fact(self):
        task = runner._load_task("support.reamaze_feedback_sentiment")
        world, _initial = runner.ab.build_world(task["info"])
        events: list[dict] = []
        tools = {tool.name: tool for tool in runner._bound_tools(task, world, events)}
        result = tools["reamaze_get_conversations"].invoke()
        self.assertIsInstance(result, str)
        self.assertEqual(events[0]["tool"], "reamaze_get_conversations")
        self.assertTrue(events[0]["ok"])
        self.assertIn("observation", events[0])

    def test_agent_l_tool_evidence_keeps_full_args_and_observation(self):
        host = SimpleNamespace(
            tools={"queue_response": lambda reply_to, response_draft: {"queued": "yes"}},
            call_log=[],
        )
        events: list[dict] = []
        runner._instrument_agent_l_tools(host, events)

        output = host.tools["queue_response"](
            reply_to="Director at Enterprise Co",
            response_draft="We can support your CRM requirements.",
        )

        self.assertEqual(output, {"queued": "yes"})
        self.assertEqual(events[0]["args"]["response_draft"], "We can support your CRM requirements.")
        self.assertEqual(events[0]["observation"], {"queued": "yes"})
        self.assertEqual(events[0]["semanticStatus"], "success")
        self.assertTrue(events[0]["ok"])

    def test_official_rubric_is_callable_on_fresh_world(self):
        task = runner._load_task("support.reamaze_feedback_sentiment")
        world, initial = runner.ab.build_world(task["info"])
        score = runner.ab.score(task["info"], world, initial)
        self.assertIn("partial_credit", score)
        self.assertIn("task_completed", score)
        self.assertIsInstance(score["assertions"], list)

    def test_tool_schema_preserves_structured_parameters(self):
        task = runner._load_task("support.reamaze_feedback_sentiment")
        world, _initial = runner.ab.build_world(task["info"])
        tools = {tool.name: tool for tool in runner._bound_tools(task, world, [])}
        schema = tools["google_sheets_add_row"].schema
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertIn("key1", schema["properties"])
        self.assertIn("value1", schema["properties"])

    def test_llm_attempt_counter_counts_actual_transport_attempts(self):
        previous_rpm = os.environ.get("AGENT_BENCH_RPM")
        try:
            os.environ["AGENT_BENCH_RPM"] = "0"
            runner.MODEL_ATTEMPTS = 0
            runner._pace_llm()
            runner._pace_llm()
            self.assertEqual(runner.MODEL_ATTEMPTS, 2)
        finally:
            if previous_rpm is None:
                os.environ.pop("AGENT_BENCH_RPM", None)
            else:
                os.environ["AGENT_BENCH_RPM"] = previous_rpm

    def test_generic_framework_code_exposes_executed_adapter(self):
        code = runner.framework_code("langgraph")
        self.assertEqual(code["scope"], "shared_adapter")
        self.assertIsNone(code["taskId"])
        self.assertEqual(
            [source["name"] for source in code["files"]],
            [
                "benchmark_runner.py::_run_langgraph",
                "benchmark_runner.py::_bound_tools",
                "SYSTEM_INSTRUCTIONS.txt",
            ],
        )
        task_code = runner.framework_code("langgraph", "hr.offboarding_automation")
        self.assertFalse(any(source["name"].endswith(".agent") for source in task_code["files"]))
        self.assertIn("create_react_agent", code["files"][0]["content"])
        self.assertIn("AutomationBench", code["files"][2]["content"])

    def test_agent_l_code_is_specific_to_selected_task(self):
        task_id = "support.reamaze_feedback_sentiment"
        code = runner.framework_code("agent_l", task_id)
        self.assertEqual(code["scope"], "task_specific")
        self.assertEqual(code["taskId"], task_id)
        self.assertEqual(code["files"][0]["name"], "support_reamaze_feedback_sentiment.agent")
        self.assertEqual(code["files"][1]["name"], "support_reamaze_feedback_sentiment.py")
        self.assertIn("agent ", code["files"][0]["content"])
        self.assertIn("def build", code["files"][1]["content"])

    def test_history_is_persistent_newest_first_and_paginated(self):
        previous_path = runner.HISTORY_PATH
        try:
            with tempfile.TemporaryDirectory() as directory:
                runner.HISTORY_PATH = Path(directory) / "runs.jsonl"
                runner._append_history({"id": "run_old", "llmCallCount": 1})
                runner._append_history({"id": "run_new", "llmCallCount": 3})
                first = runner.history_page(limit=1, offset=0)
                second = runner.history_page(limit=1, offset=1)
                self.assertEqual(first["total"], 2)
                self.assertEqual(first["runs"][0]["id"], "run_new")
                self.assertEqual(second["runs"][0]["id"], "run_old")
        finally:
            runner.HISTORY_PATH = previous_path


if __name__ == "__main__":
    unittest.main()
