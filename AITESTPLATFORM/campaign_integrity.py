"""Règles de validité et de preuve communes aux campagnes AITESTPLATFORM."""
from __future__ import annotations

from typing import Any, Optional


def semantic_outcome(observation: Any) -> tuple[str, Optional[str]]:
    """Distingue une invocation transport réussie d'une opération réussie."""
    if not isinstance(observation, dict):
        return "success", None
    if observation.get("error"):
        return "error", str(observation["error"])
    if observation.get("success") is False:
        message = observation.get("message") or observation.get("detail") or "L’outil a refusé l’opération."
        return "error", str(message)
    return "success", None


def answer_is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return all(answer_is_empty(item) for item in value)
    if isinstance(value, dict):
        textual = [value[key] for key in ("text", "content", "output_text") if key in value]
        return all(answer_is_empty(item) for item in textual) if textual else not value
    return False


def _finish_reasons(transport_log: list[dict]) -> set[str]:
    reasons = set()
    for event in transport_log:
        reason = event.get("finishReason")
        if reason:
            reasons.add(str(reason).upper())
        for item in event.get("finishReasons", []) or []:
            reasons.add(str(item).upper())
    return reasons


def assess_execution(
    *,
    error: Optional[str],
    final_answer: Any,
    tool_calls: list[dict],
    token_usage: dict,
    transport_log: list[dict],
    max_output_tokens: int,
) -> dict:
    """Classe l'intégrité technique indépendamment du score AutomationBench."""
    if error:
        return {"valid": False, "status": "failed", "invalidReason": "framework_error"}

    if not tool_calls and answer_is_empty(final_answer):
        completion = token_usage.get("completionTokens")
        near_cap = isinstance(completion, (int, float)) and completion >= max_output_tokens - 2
        capped = bool(_finish_reasons(transport_log) & {"MAX_TOKENS", "LENGTH"})
        reason = "output_token_limit" if near_cap or capped else "empty_model_output"
        return {"valid": False, "status": "invalid", "invalidReason": reason}

    return {"valid": True, "status": "completed", "invalidReason": None}


def normalize_history_run(run: dict) -> dict:
    normalized = dict(run)
    calls = []
    for original in run.get("toolCalls", []) or []:
        call = dict(original)
        if "observation" in call:
            status, semantic_error = semantic_outcome(call["observation"])
            call["semanticStatus"] = status
            call["ok"] = status == "success"
            if semantic_error:
                call["error"] = semantic_error
        calls.append(call)
    normalized["toolCalls"] = calls
    # Les runs antérieurs au régime « parité de plan » n'ont pas ce champ ; ils
    # ont tous été exécutés sans transmission du plan aux baselines.
    if not normalized.get("regime"):
        normalized["regime"] = "prompt_only"
    if normalized.get("briefingChars") is None:
        normalized["briefingChars"] = 0

    if run.get("protocolVersion"):
        return normalized

    normalized["protocolVersion"] = "legacy-v1"
    normalized["toolSurface"] = "legacy_mixed_surface"
    normalized.setdefault("modelTelemetry", [])
    if (run.get("frameworkId") == "crewai" and run.get("llmCallCount") == 0
            and (run.get("tokenUsage") or {}).get("totalTokens")):
        normalized["llmCallCount"] = None
    legacy_cap = 4096 if run.get("frameworkId") == "agent_l" else 65536
    assessment = assess_execution(
        error=run.get("error"), final_answer=run.get("finalAnswer"),
        tool_calls=calls, token_usage=run.get("tokenUsage") or {},
        transport_log=[], max_output_tokens=legacy_cap,
    )
    normalized.update(assessment)
    return normalized
