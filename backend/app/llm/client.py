"""LLM wiring shared by the agent engine and small helper calls: the Anthropic
client, provider-neutral structured calls, and error mapping for both providers."""

import json
from functools import lru_cache
from typing import Any

import anthropic
import httpx

from app.core.config import get_settings
from app.llm import nvidia
from app.llm.models import LLMUnavailable, resolve_model

FALLBACK_BETA = "server-side-fallback-2026-07-01"

__all__ = [
    "LLMUnavailable",
    "complete_text",
    "describe_error",
    "get_client",
    "request_extras",
    "structured_call",
]


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
    """Map provider errors to (detail, code) — most specific first."""
    if isinstance(exc, LLMUnavailable):
        return str(exc), "llm_not_configured"
    if isinstance(exc, nvidia.NvidiaError):
        if exc.status in (401, 403):
            return "The NVIDIA API key was rejected. Check NVIDIA_API_KEY.", "llm_auth"
        if exc.status == 404:
            return (
                "The configured NVIDIA model isn't available for this key. Pick another "
                "model in Settings.",
                "llm_model",
            )
        if exc.status == 429:
            return "The model is rate limited right now. Try again in a moment.", "llm_rate_limited"
        if exc.status in (400, 422):
            return f"The model rejected the request: {exc.message}", "llm_bad_request"
        return f"Model provider error ({exc.status}). Try again shortly.", "llm_error"
    if type(exc) is TimeoutError:  # the whole run hit chat_run_timeout_seconds
        minutes = get_settings().chat_run_timeout_seconds // 60
        return f"The answer took longer than {minutes} minutes and was stopped.", "run_timeout"
    if isinstance(exc, httpx.TimeoutException):
        return "The model provider took too long to respond. Try again.", "llm_timeout"
    if isinstance(exc, httpx.TransportError):
        return "Could not reach the model provider.", "llm_unreachable"
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
    think: bool = True,
) -> dict[str, Any]:
    """One-shot call that returns a JSON object matching `schema`. Claude enforces the
    schema with structured outputs; NVIDIA models are asked for JSON and parsed."""
    provider, model = resolve_model(model)
    if provider in nvidia.OPENAI_COMPATIBLE:
        text = await nvidia.complete(
            provider=provider,
            model=model,
            system=(
                f"{system}\n\nReply with only a JSON object matching this JSON schema, "
                f"with no other text:\n{json.dumps(schema)}"
            ),
            prompt=prompt,
            max_tokens=max_tokens,
        )
        return nvidia.extract_json(text)

    client = get_client()
    async with client.beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        # Haiku 5.5 accepts thinking off (at effort high or below); Sonnet/Opus 5.5 don't.
        thinking={"type": "disabled" if not think and "haiku" in model else "adaptive"},
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        **request_extras(),
    ) as stream:
        message = await stream.get_final_message()
    if message.stop_reason == "refusal":
        raise ValueError("refused")
    text = next((b.text for b in message.content if b.type == "text"), "")
    return json.loads(text)


async def complete_text(
    *,
    model: str | None,
    system: str,
    prompt: str,
    max_tokens: int = 4096,
    effort: str | None = None,
) -> str:
    """One plain-text completion on whichever provider the model resolves to. `effort`
    applies to Claude only (it bounds adaptive thinking; the API default is high)."""
    provider, model = resolve_model(model)
    if provider in nvidia.OPENAI_COMPATIBLE:
        return await nvidia.complete(
            provider=provider,
            model=model,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=0.0,
        )
    message = await get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
        **({"output_config": {"effort": effort}} if effort else {}),
    )
    return next((b.text for b in message.content if b.type == "text"), "")
