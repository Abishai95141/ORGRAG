"""Anthropic client wiring shared by the agent engine and small helper calls."""

import json
from functools import lru_cache
from typing import Any

import anthropic

from app.core.config import get_settings

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMUnavailable(RuntimeError):
    pass


@lru_cache
def get_client() -> anthropic.AsyncAnthropic:
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3)


def request_extras() -> dict[str, Any]:
    """Refusal fallbacks: on a policy decline the API re-runs the request on a
    fallback model inside the same call. Disable with LLM_FALLBACKS=off."""
    if get_settings().llm_fallbacks == "off":
        return {}
    return {"betas": [FALLBACK_BETA], "fallbacks": "default"}


def describe_error(exc: Exception) -> tuple[str, str]:
    """Map SDK errors to (detail, code) — most specific first."""
    if isinstance(exc, LLMUnavailable):
        return str(exc), "llm_not_configured"
    if isinstance(exc, anthropic.AuthenticationError):
        return "The Anthropic API key was rejected. Check ANTHROPIC_API_KEY.", "llm_auth"
    if isinstance(exc, anthropic.PermissionDeniedError):
        return "The API key lacks permission for this model.", "llm_forbidden"
    if isinstance(exc, anthropic.NotFoundError):
        return "The configured model was not found. Check the org's model setting.", "llm_model"
    if isinstance(exc, anthropic.RateLimitError):
        return "The model is rate limited right now. Try again in a moment.", "llm_rate_limited"
    if isinstance(exc, anthropic.BadRequestError):
        return f"The model rejected the request: {exc.message}", "llm_bad_request"
    if isinstance(exc, anthropic.APIStatusError):
        return f"Model provider error ({exc.status_code}). Try again shortly.", "llm_error"
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not reach the model provider.", "llm_unreachable"
    return "Something went wrong while generating the answer.", "internal_error"


async def structured_call(
    *,
    model: str,
    system: str,
    prompt: str,
    schema: dict[str, Any],
    effort: str = "low",
    max_tokens: int = 4000,
) -> dict[str, Any]:
    """One-shot call constrained to a JSON schema (structured outputs)."""
    client = get_client()
    async with client.beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        **request_extras(),
    ) as stream:
        message = await stream.get_final_message()
    if message.stop_reason == "refusal":
        raise ValueError("refused")
    text = next((b.text for b in message.content if b.type == "text"), "")
    return json.loads(text)
