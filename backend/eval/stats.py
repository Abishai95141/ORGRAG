"""Statistics for comparing runs and checking the judge (roadmap §1).

* `paired_bootstrap`: is run B better than run A on the same questions, or is the
  difference noise? Resamples questions (not answers), so both runs are compared on
  the same draw each time.
* `noise_floor`, `split_half`, `verdict`: the same question when each side has several
  repeats (eval/repeats.py).
* `cohen_kappa`: agreement between the LLM judge and a human, corrected for chance.
"""

import math
import random
from collections.abc import Mapping, Sequence
from itertools import combinations

import numpy as np


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


Repeats = Mapping[str, Sequence[float]]  # question id → its outcome in each repeat


def rates(runs: Repeats) -> dict[str, float]:
    return {q: sum(v) / len(v) for q, v in runs.items()}


def repeat_variance(runs: Repeats) -> float:
    """Sum over questions of the variance of one repeat's outcome: how much a question
    flips from run to run. A question that always passes or always fails adds nothing."""
    total = 0.0
    for v in runs.values():
        k = len(v)
        if k > 1:
            mean = sum(v) / k
            total += sum((x - mean) ** 2 for x in v) / (k - 1)
    return total


def noise_floor(a: Repeats, b: Repeats | None = None, k: int | None = None) -> float:
    """Half-width of the band a difference between two configurations stays inside 95% of
    the time when they're the same system: 1.96 times the standard error of the difference
    in pass rate that comes from run-to-run randomness alone.

    With `b`, for these two sets of repeats. Without it, for two repeat sets of `k` drawn
    from `a` (default: as many repeats as `a` has)."""
    n = len(a)
    if not n:
        return 0.0
    k_a = max(len(v) for v in a.values())
    if b is None:
        var = 2 * repeat_variance(a) / (k or k_a)
    else:
        var = repeat_variance(a) / k_a + repeat_variance(b) / max(len(v) for v in b.values())
    return round(1.96 * math.sqrt(var) / n, 4)


def split_half(runs: Repeats) -> dict[str, float]:
    """An A/A test: every way of splitting one configuration's repeats into two halves,
    and how far apart the halves' pass rates came out. It checks `noise_floor`, which
    assumes repeats are independent, against what actually happened."""
    ids = list(runs)
    k = min(len(runs[q]) for q in ids)
    size = k // 2
    if size < 1:
        return {"splits": 0}
    diffs = []
    for left in combinations(range(k), size):
        right = [i for i in range(k) if i not in left][:size]
        if 0 not in left:
            continue  # each split once, not also its mirror image
        one = sum(runs[q][i] for q in ids for i in left) / (size * len(ids))
        two = sum(runs[q][i] for q in ids for i in right) / (size * len(ids))
        diffs.append(abs(two - one))
    diffs.sort()
    return {
        "splits": len(diffs),
        "repeats_per_half": size,
        "mean": round(sum(diffs) / len(diffs), 4),
        "max": round(diffs[-1], 4),
    }


def compare_repeats(
    a: Repeats, b: Repeats, *, rounds: int = 10_000, seed: int = 0
) -> dict[str, float | str]:
    """Is configuration B different from A, given several repeats of each?

    Each question's pass rate over its repeats is one paired observation, so repeats of one
    question aren't counted as independent evidence: the bootstrap resamples whole
    questions. `verdict` needs both tests, because they answer different things:

    * past the noise floor: the difference is more than these questions flipping at random;
    * bootstrap interval clear of zero: it isn't down to a handful of questions, so it
      should carry over to other questions of this kind.
    """
    shared = sorted(a.keys() & b.keys())
    if not shared:
        raise ValueError("no question in common")
    ra, rb = rates(a), rates(b)
    boot = paired_bootstrap(
        [ra[q] for q in shared], [rb[q] for q in shared], rounds=rounds, seed=seed
    )
    floor = noise_floor({q: a[q] for q in shared}, {q: b[q] for q in shared})
    return {**boot, "floor": floor, "verdict": verdict(boot["diff"], boot["lo"], boot["hi"], floor)}


def detection_rates(
    runs: Repeats, gains: Sequence[float], *, repeats: int = 3, trials: int = 300, seed: int = 0
) -> dict[float, float]:
    """How often `compare_repeats` calls a change "better", by the size of the true gain.

    Each trial makes up a new configuration from these repeats: every question keeps its
    pass rate, except that enough questions that sometimes fail become ones that always pass
    to lift the set by `gain`. It's then drawn `repeats` times and compared with `runs`. A
    gain of 0 is the same system again, so its rate (with "worse" counted too) is the rule's
    false-alarm rate."""
    rng = np.random.default_rng(seed)
    ids = sorted(runs)
    n = len(ids)
    p = np.array([sum(runs[q]) / len(runs[q]) for q in ids])
    base = {q: list(runs[q]) for q in ids}
    out: dict[float, float] = {}
    for gain in gains:
        hits = 0
        for _ in range(trials):
            rate = p.copy()
            need = gain * n
            for i in rng.permutation(np.flatnonzero(p < 1)):
                if need <= 0:
                    break
                need -= 1 - rate[i]
                rate[i] = 1.0
            drawn = rng.random((n, repeats)) < rate[:, None]
            other = {q: drawn[i].astype(float).tolist() for i, q in enumerate(ids)}
            call = compare_repeats(base, other, rounds=1000, seed=int(rng.integers(1 << 31)))
            hits += call["verdict"] == "better" or (gain == 0 and call["verdict"] == "worse")
        out[gain] = round(hits / trials, 3)
    return out


def verdict(diff: float, lo: float, hi: float, floor: float) -> str:
    if abs(diff) <= floor:
        return "noise"
    if lo > 0:
        return "better"
    if hi < 0:
        return "worse"
    return "unclear"  # past the noise, but it rests on too few questions to generalise


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
