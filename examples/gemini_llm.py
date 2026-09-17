"""Adaptateur LLM réel pour Google Gemini.

Même contrat que `AnthropicLLM` : le modèle est un *oracle*. Il ne reçoit
jamais la main sur le runtime — il ne fait que produire un dict typé
(`REASON ... PRODUCE`) ou choisir un plan dans une liste fermée. Toute sortie
hors format est ramenée au schéma par le runtime (`_coerce`).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl.llm import LLM, _coerce, _parse_json, call_with_retry, missing_from

# --------------------------------------------------------------- débit d'API
# Le quota du modèle se compte en requêtes par minute. Le dépassement rend un
# HTTP 429 : un `REASON` échoue, retombe sur son schéma par défaut, et l'agent
# n'a plus d'oracle — une panne d'infrastructure déguisée en indécision.
# On espace donc les appels à la source, plutôt que de rattraper après coup.
RPM = int(os.environ.get("AGENTL_RPM", "10"))
_PACE_LOCK = threading.Lock()
_last_call = [0.0]


def pace(rpm: int = 0) -> None:
    """Bloque le temps qu'il faut pour ne pas dépasser `rpm` requêtes/minute."""
    limit = rpm or RPM
    if limit <= 0:
        return
    interval = 60.0 / limit
    with _PACE_LOCK:
        wait = _last_call[0] + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


class GeminiLLM(LLM):  # pragma: no cover - nécessite le réseau
    def __init__(self, model: str = "gemini-3.1-flash-lite",
                 api_key: Optional[str] = None):
        self.model = model
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.calls: List[Dict[str, Any]] = []
        self.responses: List[Dict[str, Any]] = []
        self.last_reason_missing: Optional[List[str]] = None
        #: Rejeux effectués, tous appels confondus — une reprise réussie reste
        #: un incident d'infrastructure, et doit se compter.
        self.retries: List[Dict[str, Any]] = []

    def _note_retry(self, attempt: int, delay: float, exc: BaseException) -> None:
        self.retries.append({"attempt": attempt, "delaySeconds": round(delay, 2),
                             "error": f"{type(exc).__name__}: {str(exc)[:120]}"})

    def _call(self, system: str, prompt: str) -> str:
        pace()
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent?key={self.api_key}")
        body = json.dumps({
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": int(os.environ.get("AGENTL_MAX_OUTPUT_TOKENS", "8192")),
                "thinkingConfig": {
                    "thinkingLevel": os.environ.get("AGENTL_THINKING_LEVEL", "low")
                },
            },
        }).encode()
        req = urllib.request.Request(
            url, data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        candidate = (data.get("candidates") or [{}])[0]
        usage = data.get("usageMetadata") or {}
        self.responses.append({
            "provider": "google-genai-rest",
            "model": self.model,
            "responseId": data.get("responseId"),
            "finishReason": candidate.get("finishReason"),
            "promptTokens": usage.get("promptTokenCount"),
            "completionTokens": (usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0),
            "reasoningTokens": usage.get("thoughtsTokenCount"),
            "totalTokens": usage.get("totalTokenCount"),
            "status": "completed",
        })
        return "".join(
            p.get("text", "")
            for p in data["candidates"][0]["content"]["parts"])

    def reason(self, task, context, produce):
        self.calls.append({"kind": "reason", "task": task})
        system = ("Tu es le module de raisonnement d'un runtime agentique "
                  "AGENT-L. Tu ne peux exécuter aucune action. Réponds "
                  "UNIQUEMENT par un objet JSON respectant exactement le "
                  "schéma demandé, sans texte ni balises Markdown.")
        prompt = (f"Tâche : {task}\n\n"
                  f"Schéma attendu (clé: type) : {json.dumps(produce)}\n\n"
                  f"Contexte :\n{json.dumps(context, default=str, indent=2)}")
        self.last_reason_missing = list(produce)
        raw = call_with_retry(lambda: self._call(system, prompt),
                              on_retry=self._note_retry)
        self.calls[-1]["raw"] = raw
        parsed = _parse_json(raw)
        self.last_reason_missing = missing_from(parsed, produce)
        return _coerce(parsed, produce)

    def select_plan(self, context, candidates):
        self.calls.append({"kind": "select_plan", "candidates": list(candidates)})
        if not candidates:
            return None
        system = ('Tu sélectionnes un plan pour un runtime AGENT-L. Réponds '
                  'uniquement par {"plan": "<nom>"} en choisissant dans la '
                  'liste fournie.')
        prompt = (f"Plans disponibles : {candidates}\n"
                  f"Contexte :\n{json.dumps(context, default=str, indent=2)}")
        raw = call_with_retry(lambda: self._call(system, prompt),
                              on_retry=self._note_retry)
        choice = _parse_json(raw).get("plan")
        return choice if choice in candidates else None
