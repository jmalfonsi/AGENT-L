"""Régressions du canal ``stream-json`` utilisé par la fabrique d'agents."""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

import codegen_backend


class _FailedStreamProcess:
    """Sous-processus déterministe : erreur API dans stdout, stderr vide."""

    def __init__(self, lines: list[dict]):
        self.stdin = io.StringIO()
        self.stdout = iter(json.dumps(line) + "\n" for line in lines)
        self.stderr = iter(())
        self._returncode = 1

    def wait(self, timeout=None):
        return self._returncode

    def poll(self):
        return self._returncode

    def kill(self):
        self._returncode = -9


def test_stream_restitue_le_quota_et_sa_reinitialisation(tmp_path):
    process = _FailedStreamProcess([
        {
            "type": "rate_limit_event",
            "rate_limit_info": {
                "status": "rejected",
                "resetsAt": 1786354200,
                "rateLimitType": "five_hour",
            },
        },
        {
            "type": "result",
            "is_error": True,
            "api_error_status": 429,
            "result": "You've hit your session limit · resets 9:30am (UTC)",
        },
    ])

    with patch.object(codegen_backend.subprocess, "Popen", return_value=process):
        with pytest.raises(codegen_backend.CodegenError) as raised:
            codegen_backend._stream(
                ["-p"], cwd=tmp_path, timeout=5, stdin="prompt", phase="author"
            )

    message = str(raised.value)
    assert "HTTP 429" in message
    assert "2026-08-10T09:30:00Z" in message
    assert "You've hit your session limit" in message
