"""Extraction de métadonnées modèle sans contenu ni chaîne de pensée."""
from __future__ import annotations

from typing import Any


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _usage(usage: Any) -> dict:
    details = _value(usage, "completion_tokens_details", {}) or {}
    return {
        "promptTokens": _value(usage, "prompt_tokens"),
        "completionTokens": _value(usage, "completion_tokens"),
        "totalTokens": _value(usage, "total_tokens"),
        "reasoningTokens": _value(details, "reasoning_tokens"),
    }


def litellm_metadata(response: Any, framework: str) -> dict:
    choices = _value(response, "choices", []) or []
    reasons = [
        _value(choice, "finish_reason")
        for choice in choices
        if _value(choice, "finish_reason") is not None
    ]
    hidden = _value(response, "_hidden_params", {}) or {}
    return {
        "framework": framework,
        "provider": _value(hidden, "custom_llm_provider", "litellm/gemini"),
        "model": _value(response, "model"),
        "responseId": _value(response, "id"),
        "finishReason": reasons[0] if reasons else None,
        "finishReasons": reasons,
        **_usage(_value(response, "usage", {}) or {}),
        "status": "completed",
    }


def langchain_metadata(response: Any, framework: str = "langgraph") -> dict:
    reasons: list[str] = []
    prompt = completion = total = reasoning = 0
    usage_found = False
    for group in _value(response, "generations", []) or []:
        for generation in group:
            message = _value(generation, "message")
            metadata = _value(message, "response_metadata", {}) or {}
            reason = metadata.get("finish_reason") or metadata.get("finishReason")
            if reason is not None:
                reasons.append(str(reason))
            usage = _value(message, "usage_metadata", {}) or {}
            if usage:
                usage_found = True
                prompt += int(usage.get("input_tokens", 0) or 0)
                completion += int(usage.get("output_tokens", 0) or 0)
                total += int(usage.get("total_tokens", 0) or 0)
                details = usage.get("output_token_details", {}) or {}
                reasoning += int(details.get("reasoning", 0) or 0)
    llm_output = _value(response, "llm_output", {}) or {}
    return {
        "framework": framework,
        "provider": "langchain-google-genai",
        "model": llm_output.get("model_name") or llm_output.get("model"),
        "responseId": llm_output.get("response_id"),
        "finishReason": reasons[0] if reasons else None,
        "finishReasons": reasons,
        "promptTokens": prompt if usage_found else None,
        "completionTokens": completion if usage_found else None,
        "totalTokens": total if usage_found else None,
        "reasoningTokens": reasoning if usage_found else None,
        "status": "completed",
    }


def failed_transport(framework: str, error: Exception) -> dict:
    return {
        "framework": framework,
        "provider": "gemini",
        "status": "error",
        "errorType": type(error).__name__,
    }


def aggregate_usage(events: list[dict]) -> dict:
    fields = {
        "promptTokens": [event.get("promptTokens") for event in events],
        "completionTokens": [event.get("completionTokens") for event in events],
        "totalTokens": [event.get("totalTokens") for event in events],
    }
    return {
        name: sum(int(value or 0) for value in values)
        if any(value is not None for value in values) else None
        for name, values in fields.items()
    }
