from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import benchmark_runner as runner
from live_progress import LIVE_PREFIX, emit_live_event


class LiveProgressTests(unittest.TestCase):
    def setUp(self):
        self.previous_live = os.environ.get("AGENT_BENCH_LIVE")
        os.environ["AGENT_BENCH_LIVE"] = "1"

    def tearDown(self):
        if self.previous_live is None:
            os.environ.pop("AGENT_BENCH_LIVE", None)
        else:
            os.environ["AGENT_BENCH_LIVE"] = self.previous_live

    def test_event_uses_stderr_and_keeps_stdout_json_channel_clean(self):
        stream = io.StringIO()
        with patch("live_progress.sys.__stderr__", stream):
            emit_live_event("runtime_start", "Runtime démarré", safeValue=3)
        line = stream.getvalue().strip()
        self.assertTrue(line.startswith(LIVE_PREFIX))
        event = json.loads(line[len(LIVE_PREFIX):])
        self.assertEqual(event["type"], "runtime_start")
        self.assertEqual(event["safeValue"], 3)

    def test_tool_progress_has_names_and_status_but_no_observation(self):
        task = runner._load_task("hr.offboarding_automation")
        world, _initial = runner.ab.build_world(task["info"])
        stream = io.StringIO()
        with patch("live_progress.sys.__stderr__", stream):
            tools = {tool.name: tool for tool in runner._bound_tools(task, world, [])}
            tools["list_departures"].invoke()
        events = [
            json.loads(line[len(LIVE_PREFIX):])
            for line in stream.getvalue().splitlines()
            if line.startswith(LIVE_PREFIX)
        ]
        self.assertEqual([event["type"] for event in events], ["tool_start", "tool_end"])
        self.assertTrue(all(event["tool"] == "list_departures" for event in events))
        self.assertNotIn("Greg Foster", json.dumps(events))
        self.assertNotIn("observation", json.dumps(events))


if __name__ == "__main__":
    unittest.main()
