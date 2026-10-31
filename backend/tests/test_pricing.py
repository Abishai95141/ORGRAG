"""Per-answer cost from token usage."""

from types import SimpleNamespace

import pytest

from app.agent import engine
from app.llm import pricing


def test_haiku_cost_counts_every_token_kind():
    usage = {"input": 10_000, "output": 10_000, "cache_read": 10_000, "cache_write": 10_000}
    expected = 10_000 * (0.10 + 0.50 + 0.01 + 0.125) / 1e6
    assert pricing.cost("claude-haiku-5-5", usage) == pytest.approx(expected)


def test_long_prompts_use_the_higher_rate():
    # The threshold counts the whole prompt, cached or not.
    usage = {"input": 60_000, "output": 0, "cache_read": 60_000, "cache_write": 0}
    assert pricing.cost("claude-haiku-5-5", usage) == pytest.approx(
        (60_000 * 0.50 + 60_000 * 0.05) / 1e6
    )


def test_unpriced_models_cost_nothing_known():
    assert pricing.cost("nvidia/nemotron-3-ultra", {"input": 10, "output": 10}) is None
    assert pricing.cost("explabs:claude-haiku-5.5", {"input": 10}) is None
    assert pricing.cost("claude-haiku-5-5", None) is None


def test_run_usage_records_cache_writes_and_cost_per_call():
    final = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=2_000,
            output_tokens=400,
            cache_read_input_tokens=8_000,
            cache_creation_input_tokens=1_000,
        )
    )
    usage = engine._claude_usage(final)
    assert usage == {"input": 2_000, "output": 400, "cache_read": 8_000, "cache_write": 1_000}

    run = SimpleNamespace(usage={})
    engine.AgentRun._add_usage(run, "claude-haiku-5-5", usage)
    engine.AgentRun._add_usage(run, "claude-haiku-5-5", usage)
    totals = run.usage["claude-haiku-5-5"]
    assert totals["calls"] == 2 and totals["cache_write"] == 2_000
    assert totals["cost_usd"] == pytest.approx(2 * pricing.cost("claude-haiku-5-5", usage))

    engine.AgentRun._add_usage(run, "nvidia/nemotron-3-ultra", {"input": 5, "output": 5})
    assert "cost_usd" not in run.usage["nvidia/nemotron-3-ultra"]
