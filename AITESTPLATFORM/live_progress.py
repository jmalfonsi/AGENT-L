"""Événements d'exécution sûrs envoyés au serveur sur stderr.

Le stdout du runner reste réservé au résultat JSON final. Ces événements ne
contiennent ni réponse du modèle, ni observation d'outil, ni chaîne de pensée.
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any


LIVE_PREFIX = "AITEST_EVENT "


def emit_live_event(event_type: str, message: str, **details: Any) -> None:
    if os.environ.get("AGENT_BENCH_LIVE") != "1":
        return
    event = {
        "type": event_type,
        "message": message,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runId": os.environ.get("AGENT_BENCH_LIVE_RUN_ID"),
        "taskId": os.environ.get("AGENT_BENCH_LIVE_TASK_ID"),
        "frameworkId": os.environ.get("AGENT_BENCH_LIVE_FRAMEWORK_ID"),
        **details,
    }
    stream = sys.__stderr__ or sys.stderr
    stream.write(LIVE_PREFIX + json.dumps(event, ensure_ascii=False, default=str) + "\n")
    stream.flush()
