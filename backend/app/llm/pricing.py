"""What a model call cost, from its token usage (USD, list prices, Oct 2026).

Only models with a published per-token price are listed; anything else (the free
NVIDIA tier, the gateway) costs None rather than a guess."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Price:
    """Dollars per million tokens. Cache reads and 5-minute cache writes are priced
    as multiples of input unless the model has its own rate."""

    input: float
    output: float
    cache_read: float
    cache_write: float
    long_prompt: "Price | None" = None  # rates when the prompt exceeds LONG_PROMPT_TOKENS


LONG_PROMPT_TOKENS = 100_000

PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(0.10, 0.50, 0.01, 0.125, long_prompt=Price(0.50, 2.50, 0.05, 0.625)),
    "claude-sonnet-5-5": Price(2.0, 10.0, 0.20, 2.50),
    "claude-opus-5-5": Price(4.0, 20.0, 0.20, 5.0),
}


def cost(model: str, usage: dict[str, int] | None) -> float | None:
    """Cost of one call's usage ({input, output, cache_read, cache_write}); None when
    the model has no known price."""
    price = PRICES.get(model)
    if price is None or not usage:
        return None
    read, write = usage.get("cache_read", 0), usage.get("cache_write", 0)
    prompt = usage.get("input", 0) + read + write
    if price.long_prompt and prompt > LONG_PROMPT_TOKENS:
        price = price.long_prompt
    return (
        usage.get("input", 0) * price.input
        + usage.get("output", 0) * price.output
        + read * price.cache_read
        + write * price.cache_write
    ) / 1_000_000
