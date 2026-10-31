"""Statistics for comparing runs and checking the judge (roadmap §1).

* `paired_bootstrap`: is run B better than run A on the same questions, or is the
  difference noise? Resamples questions (not answers), so both runs are compared on
  the same draw each time.
* `cohen_kappa`: agreement between the LLM judge and a human, corrected for chance.
"""

import random
from collections.abc import Sequence


def paired_bootstrap(
    a: Sequence[float], b: Sequence[float], *, rounds: int = 10_000, seed: int = 0
) -> dict[str, float]:
    """Mean of b - a with a 95% interval, and the share of resamples where b ≤ a
    (a one-sided p-value for "b is better")."""
    if len(a) != len(b) or not a:
        raise ValueError("paired samples must be non-empty and the same length")
    rng = random.Random(seed)
    diffs = [y - x for x, y in zip(a, b, strict=True)]
    n = len(diffs)
    means = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(rounds))
    return {
        "n": n,
        "diff": round(sum(diffs) / n, 4),
        "lo": round(means[int(0.025 * rounds)], 4),
        "hi": round(means[int(0.975 * rounds) - 1], 4),
        "p_not_better": round(sum(m <= 0 for m in means) / rounds, 4),
    }


def cohen_kappa(a: Sequence[bool], b: Sequence[bool]) -> float | None:
    """κ for two raters' yes/no labels on the same items; None when undefined
    (no items, or both raters gave one constant label)."""
    if len(a) != len(b) or not a:
        return None
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    if expected == 1:
        return None
    return round((observed - expected) / (1 - expected), 3)
