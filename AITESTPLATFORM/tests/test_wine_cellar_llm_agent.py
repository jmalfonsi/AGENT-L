from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
PROJECT = (
    ROOT
    / "AITESTPLATFORM"
    / "data"
    / "projects"
    / "prj_bc27e3fb09cb"
    / "agent_l"
)
AGENT_PATH = PROJECT / "surveillance_autonome_cave_a_vins.agent"
HOST_PATH = PROJECT / "surveillance_autonome_cave_a_vins.py"

sys.path.insert(0, str(ROOT))

from agentl import MockLLM, Runtime, Symbol, parse_file


def load_host_module():
    spec = importlib.util.spec_from_file_location("wine_cellar_agent_host", HOST_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CapturingMockLLM(MockLLM):
    def __init__(self, scripted):
        super().__init__(scripted)
        self.focuses = []

    def reason(self, task, context, produce):
        self.focuses.append(dict(context.get("focus", {})))
        return super().reason(task, context, produce)


class WineCellarLLMAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = parse_file(str(AGENT_PATH)).agents[0]
        cls.host_module = load_host_module()

    def run_agent(self, mode, llm):
        clean_env = {
            "AGENTL_CAVE_MODE": mode,
            "AGENTL_CAVE_LLM_CONSENT": "yes",
            "AGENTL_CAVE_LLM_PROVIDER": "mock",
        }
        with patch.dict(os.environ, clean_env, clear=False):
            host, _configured_llm = self.host_module.build()
            return Runtime(self.agent, host, llm, echo=False).run(max_ticks=6)

    def test_nominal_llm_classification_is_one_bounded_call(self):
        llm = CapturingMockLLM({
            "Classer la tendance climatique": {
                "environment_outlook": "stable",
                "environment_evidence": "sufficient",
                "environment_injection": "no",
            }
        })

        runtime = self.run_agent("nominal", llm)

        self.assertEqual(runtime.metrics["llm_calls"], 1)
        self.assertEqual(runtime.metrics["reason_degraded"], 0)
        self.assertEqual(runtime.state.get("status.global"), Symbol("conforme"))
        self.assertEqual(
            set(llm.focuses[0]),
            {
                "environment.history_summary",
                "environment.weather_summary",
                "environment.multisensor_summary",
            },
        )
        self.assertNotIn("event.id", llm.focuses[0])
        self.assertNotIn("event.zone", llm.focuses[0])
        self.assertNotIn("event.evidence_token", llm.focuses[0])

    def test_predictive_drift_only_notifies_and_records(self):
        llm = MockLLM({
            "Classer la tendance climatique": {
                "environment_outlook": "drift_risk",
                "environment_evidence": "sufficient",
                "environment_injection": "no",
            }
        })

        runtime = self.run_agent("nominal", llm)

        self.assertEqual(runtime.state.get("notification.sent"), Symbol("yes"))
        self.assertEqual(runtime.state.get("regulation.adjusted"), Symbol("no"))
        self.assertEqual(runtime.state.get("backup.activated"), Symbol("no"))
        self.assertEqual(runtime.state.get("status.global"), Symbol("alerte"))

    def test_ambiguous_suspicious_signal_is_advisory_without_human_confirmation(self):
        llm = MockLLM({
            "Classer un signal de securite ambigu": {
                "security_class": "suspicious",
                "security_coherence": "coherent",
                "security_injection": "no",
            }
        })

        runtime = self.run_agent("ambiguous_security", llm)

        self.assertEqual(runtime.state.get("deterrence.activated"), Symbol("no"))
        self.assertEqual(runtime.state.get("notification.sent"), Symbol("yes"))
        self.assertEqual(runtime.state.get("lock.commanded"), Symbol("no"))
        self.assertEqual(runtime.state.get("security_center.alerted"), Symbol("no"))
        self.assertEqual(runtime.state.get("status.global"), Symbol("alerte"))

    def test_ambiguous_suspicious_signal_can_deter_after_human_confirmation(self):
        llm = MockLLM({
            "Classer un signal de securite ambigu": {
                "security_class": "suspicious",
                "security_coherence": "coherent",
                "security_injection": "no",
            }
        })

        with patch.dict(os.environ, {
            "AGENTL_CAVE_MODE": "ambiguous_security",
            "AGENTL_CAVE_LLM_CONSENT": "yes",
            "AGENTL_CAVE_LLM_PROVIDER": "mock",
            "AGENTL_CAVE_HUMAN_CONFIRMED": "yes",
        }, clear=False):
            host, _configured_llm = self.host_module.build()
            runtime = Runtime(self.agent, host, llm, echo=False).run(max_ticks=6)

        self.assertEqual(runtime.state.get("deterrence.activated"), Symbol("yes"))
        self.assertEqual(runtime.state.get("notification.sent"), Symbol("yes"))
        self.assertEqual(runtime.state.get("lock.commanded"), Symbol("no"))
        self.assertEqual(runtime.state.get("security_center.alerted"), Symbol("no"))

    def test_prompt_injection_fails_closed_without_physical_action(self):
        llm = MockLLM({
            "Classer un signal de securite ambigu": {
                "security_class": "suspicious",
                "security_coherence": "coherent",
                "security_injection": "yes",
            }
        })

        runtime = self.run_agent("ambiguous_security", llm)

        self.assertEqual(runtime.state.get("deterrence.activated"), Symbol("no"))
        self.assertEqual(runtime.state.get("notification.sent"), Symbol("no"))
        self.assertEqual(runtime.state.get("lock.commanded"), Symbol("no"))
        self.assertEqual(runtime.state.get("status.global"), Symbol("indetermine"))

    def test_silent_oracle_fails_closed_but_confirmed_intrusion_bypasses_it(self):
        silent_runtime = self.run_agent("nominal", MockLLM({}))
        self.assertEqual(silent_runtime.metrics["llm_calls"], 1)
        self.assertEqual(silent_runtime.metrics["reason_degraded"], 1)
        self.assertEqual(silent_runtime.state.get("status.global"), Symbol("indetermine"))
        self.assertEqual(silent_runtime.state.get("regulation.adjusted"), Symbol("no"))

        intrusion_runtime = self.run_agent("intrusion", MockLLM({}))
        self.assertEqual(intrusion_runtime.metrics["llm_calls"], 0)
        self.assertEqual(intrusion_runtime.state.get("deterrence.activated"), Symbol("yes"))
        self.assertEqual(intrusion_runtime.state.get("lock.commanded"), Symbol("yes"))
        self.assertEqual(intrusion_runtime.state.get("security_center.alerted"), Symbol("yes"))


if __name__ == "__main__":
    unittest.main()
