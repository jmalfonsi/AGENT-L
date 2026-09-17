from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import benchmark_runner as runner
from campaign_integrity import assess_execution, normalize_history_run, semantic_outcome
from model_telemetry import litellm_metadata


class CampaignIntegrityTests(unittest.TestCase):
    def test_all_runtimes_receive_only_declared_task_facade(self):
        task = runner._load_task("hr.offboarding_automation")
        world, _initial = runner.ab.build_world(task["info"])
        tools = runner._bound_tools(task, world, [])
        self.assertEqual(
            [tool.name for tool in tools],
            [
                "list_departures",
                "post_farewell",
                "notify_it",
                "notify_hr_director",
                "mark_status",
            ],
        )
        self.assertNotIn("google_sheets_get_many_rows", {tool.name for tool in tools})

    def test_shared_facade_reaches_the_real_automationbench_world(self):
        task = runner._load_task("hr.offboarding_automation")
        world, _initial = runner.ab.build_world(task["info"])
        events: list[dict] = []
        tools = {tool.name: tool for tool in runner._bound_tools(task, world, events)}
        result = json.loads(tools["list_departures"].invoke())
        self.assertEqual(result["departure_count"], 4)
        self.assertEqual(result["departures"][0]["employee"], "Greg Foster")
        self.assertEqual(events[0]["semanticStatus"], "success")

    def test_generic_baselines_do_not_receive_the_agent_source(self):
        instructions = runner._baseline_instructions()
        self.assertEqual(instructions, runner.SYSTEM_INSTRUCTIONS)
        self.assertNotIn("NEVER post_farewell", instructions)
        self.assertNotIn("Severance", instructions)
        self.assertNotIn("```agentl", instructions)

    def test_generic_tool_binding_does_not_read_an_agent_file(self):
        task = runner._load_task("hr.offboarding_automation")
        world, _initial = runner.ab.build_world(task["info"])
        with patch.object(Path, "read_text", side_effect=AssertionError("unexpected .agent read")):
            tools = runner._bound_tools(task, world, [])
        self.assertEqual(len(tools), 5)

    def test_semantic_tool_errors_are_not_reported_as_success(self):
        status, error = semantic_outcome({"error": "Spreadsheet not found"})
        self.assertEqual(status, "error")
        self.assertEqual(error, "Spreadsheet not found")
        status, error = semantic_outcome({"success": False, "message": "Rejected"})
        self.assertEqual(status, "error")
        self.assertEqual(error, "Rejected")

    def test_empty_output_at_token_cap_invalidates_a_run(self):
        assessment = assess_execution(
            error=None,
            final_answer="",
            tool_calls=[],
            token_usage={"completionTokens": 65534},
            transport_log=[{"finishReason": "MAX_TOKENS"}],
            max_output_tokens=65536,
        )
        self.assertEqual(assessment["status"], "invalid")
        self.assertFalse(assessment["valid"])
        self.assertEqual(assessment["invalidReason"], "output_token_limit")

    def test_partial_official_score_remains_valid_when_model_did_real_work(self):
        assessment = assess_execution(
            error=None,
            final_answer="Terminé partiellement",
            tool_calls=[{"tool": "mark_status", "ok": True}],
            token_usage={"completionTokens": 100},
            transport_log=[{"finishReason": "STOP"}],
            max_output_tokens=8192,
        )
        self.assertEqual(assessment["status"], "completed")
        self.assertTrue(assessment["valid"])
        self.assertIsNone(assessment["invalidReason"])

    def test_common_model_policy_is_explicit_and_bounded(self):
        self.assertEqual(runner.THINKING_LEVEL, "low")
        self.assertEqual(runner.MODEL_MAX_OUTPUT_TOKENS, 8192)

    def test_every_catalog_task_builds_exactly_its_declared_facade(self):
        for summary in runner.task_catalog():
            task = runner._load_task(summary["id"])
            world, _initial = runner.ab.build_world(task["info"])
            tools = runner._bound_tools(task, world, [])
            self.assertEqual([tool.name for tool in tools], summary["runtimeTools"])

    def test_all_generic_adapters_apply_the_common_model_policy(self):
        langgraph = runner.inspect.getsource(runner._run_langgraph)
        crewai = runner.inspect.getsource(runner._run_crewai)
        openai_agents = runner.inspect.getsource(runner._run_openai_agents)
        self.assertIn("thinking_level=THINKING_LEVEL", langgraph)
        self.assertIn("max_output_tokens=MODEL_MAX_OUTPUT_TOKENS", langgraph)
        self.assertIn("is_litellm=True", crewai)
        self.assertIn("reasoning_effort=THINKING_LEVEL", crewai)
        self.assertIn("max_tokens=MODEL_MAX_OUTPUT_TOKENS", openai_agents)
        self.assertIn("reasoning={\"effort\": THINKING_LEVEL}", openai_agents)

    def test_legacy_logs_are_corrected_without_rewriting_history(self):
        crew = normalize_history_run({
            "frameworkId": "crewai", "status": "completed", "error": None,
            "llmCallCount": 0, "finalAnswer": "Blocked",
            "tokenUsage": {"totalTokens": 100},
            "toolCalls": [{"ok": True, "observation": {"error": "not found"}}],
        })
        self.assertEqual(crew["protocolVersion"], "legacy-v1")
        self.assertIsNone(crew["llmCallCount"])
        self.assertFalse(crew["toolCalls"][0]["ok"])
        self.assertEqual(crew["toolCalls"][0]["semanticStatus"], "error")

    def test_legacy_empty_output_at_the_old_cap_is_reclassified(self):
        old = normalize_history_run({
            "frameworkId": "openai_agents", "status": "completed",
            "error": None, "finalAnswer": "", "toolCalls": [],
            "tokenUsage": {"completionTokens": 65534, "totalTokens": 68000},
        })
        self.assertEqual(old["status"], "invalid")
        self.assertEqual(old["invalidReason"], "output_token_limit")

    def test_model_telemetry_never_keeps_response_content(self):
        metadata = litellm_metadata({
            "id": "resp_1", "model": "gemini-3.1-flash-lite",
            "_hidden_params": {"custom_llm_provider": "gemini"},
            "choices": [{"finish_reason": "stop", "message": {"content": "secret output"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }, "crewai")
        self.assertEqual(metadata["finishReason"], "stop")
        self.assertEqual(metadata["totalTokens"], 30)
        self.assertNotIn("secret output", str(metadata))
        self.assertNotIn("message", metadata)


if __name__ == "__main__":
    unittest.main()
