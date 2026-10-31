"""Model catalogue and provider resolution.

An org stores a model id. Claude ids ("claude-…") run on Anthropic; "vendor/model"
ids run on NVIDIA. When the stored model's provider has no key but another provider
does, the run falls back to that provider's default instead of failing."""

from dataclasses import dataclass

from app.core.config import DEFAULT_CLAUDE_MODEL, DEFAULT_NVIDIA_MODEL, get_settings


class LLMUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    provider: str
    hint: str


# Chosen by probing the NVIDIA catalogue for tool calling, citation discipline and
# latency on an agentic RAG task (Oct 2026). Nemotron 3 Ultra led on all three.
CATALOGUE: tuple[ModelInfo, ...] = (
    ModelInfo(
        DEFAULT_CLAUDE_MODEL,
        "Claude Opus 5.5",
        "anthropic",
        "Strongest reasoning for research",
    ),
    ModelInfo(
        "claude-sonnet-5-5",
        "Claude Sonnet 5.5",
        "anthropic",
        "Faster and cheaper for everyday questions",
    ),
    ModelInfo(
        "claude-haiku-5-5",
        "Claude Haiku 5.5",
        "anthropic",
        "Lowest latency and cost",
    ),
    ModelInfo(
        DEFAULT_NVIDIA_MODEL,
        "Nemotron 3 Ultra",
        "nvidia",
        "Best NVIDIA model for agentic research",
    ),
    ModelInfo(
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "Nemotron 3.5 Lightning",
        "nvidia",
        "Fast, accurate tool use for quick questions",
    ),
    ModelInfo(
        "explabs:claude-haiku-5.5",
        "Claude Haiku 5.5 (Experiential Labs)",
        "explabs",
        "Measured best value: fast, cheap, strong tool use",
    ),
    ModelInfo(
        "explabs:gpt-5.4-nano",
        "GPT-5.4 nano (Experiential Labs)",
        "explabs",
        "Cheapest; best graph extraction in our tests",
    ),
    ModelInfo(
        "minimaxai/minimax-m3.1-flash",
        "MiniMax M3.1 Flash",
        "nvidia",
        "Alternative open model with good citations",
    ),
)


def provider_for(model: str) -> str:
    if model.startswith("explabs:"):
        return "explabs"
    return "anthropic" if model.startswith("claude") else "nvidia"


def resolve_model(model: str | None) -> tuple[str, str]:
    """(provider, model) to actually call for an org's configured model."""
    settings = get_settings()
    available = settings.llm_providers
    if not available:
        raise LLMUnavailable("No LLM API key is configured")
    model = model or settings.default_model
    provider = provider_for(model)
    if provider in available:
        return provider, model.removeprefix("explabs:")
    # Prefer the deployment's configured default (LLM_MODEL) when it can run.
    default = settings.default_model
    if default != model and provider_for(default) in available:
        return provider_for(default), default.removeprefix("explabs:")
    fallback = next((p for p in available if p != "explabs"), None)
    if fallback is None:
        raise LLMUnavailable(f"No API key for {model}; set ANTHROPIC_API_KEY or NVIDIA_API_KEY")
    return fallback, DEFAULT_CLAUDE_MODEL if fallback == "anthropic" else DEFAULT_NVIDIA_MODEL


def catalogue() -> list[dict]:
    available = set(get_settings().llm_providers)
    return [
        {
            "id": m.id,
            "label": m.label,
            "provider": m.provider,
            "hint": m.hint,
            "available": m.provider in available,
        }
        for m in CATALOGUE
    ]
