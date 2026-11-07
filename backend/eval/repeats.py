"""Several repeats of a configuration, and whether two configurations really differ.

A single run is one sample: the same configuration moves by several questions from run to
run, so `harness compare` of two runs can't see a small change. This reads repeat sets
(`harness answers --repeats N`, or eval/hard/baseline.json) and needs no database.

    python -m eval.repeats summary eval/results/answers-x-r*.json
    python -m eval.repeats compare --a eval/hard/baseline.json --b eval/results/answers-y-r*.json
    python -m eval.repeats freeze eval/results/answers-x-r*.json --out eval/hard/baseline.json
    python -m eval.repeats power eval/hard/baseline.json

`compare` prints a verdict per category: "better" or "worse" when the difference is past
the noise floor and its bootstrap interval is clear of zero, "noise" when it's inside the
floor, "unclear" otherwise. docs/EVALUATION.md says why, and how big a difference that is.
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

from eval.stats import compare_repeats, detection_rates, noise_floor, rates, split_half

NO_ANSWER = "I wasn't able to put together an answer"
SAME = ("corpus", "judge_model", "facts_prompt", "questions")


def load(paths: list[str]) -> dict:
    """A repeat set: each question's outcomes, its category, and what the runs shared."""
    if len(paths) == 1 and json.loads(Path(paths[0]).read_text()).get("kind") == "repeats":
        return json.loads(Path(paths[0]).read_text())
    runs = [json.loads(Path(p).read_text()) for p in paths]
    problems = []
    for key in SAME:
        values = {r.get(key) for r in runs}
        if len(values) > 1:
            problems.append(f"the repeats differ in {key}: {sorted(map(str, values))}")
    outcomes: dict[str, list[int]] = {}
    categories: dict[str, str] = {}
    errors = no_answer = 0
    for run in runs:
        for row in run["rows"]:
            outcomes.setdefault(row["id"], []).append(int(row["passed"]))
            categories[row["id"]] = row["category"]
            errors += bool(row.get("error")) or row["status"] != "complete"
            no_answer += row["answer"].startswith(NO_ANSWER)
    sizes = {len(v) for v in outcomes.values()}
    if len(sizes) > 1:
        problems.append("not every question was answered in every repeat")
    return {
        "kind": "repeats",
        "name": re.sub(r"-r\d+(-rejudged)?$", "", runs[0]["variant"]),
        "repeats": len(runs),
        "settings": {k: v for k, v in runs[0].get("settings", {}).items() if k != "records"},
        **{key: runs[0].get(key) for key in SAME},
        "outcomes": outcomes,
        "categories": categories,
        "errors": errors,
        "no_answer": no_answer,
        "rerank_failures": sum(r.get("rerank_failures", 0) for r in runs),
        "cost_usd": round(
            sum(row.get("cost_usd") or 0 for r in runs for row in r["rows"]) / len(runs), 4
        ),
        "problems": problems,
    }


def groups(run: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"ALL": sorted(run["outcomes"])}
    for qid in out["ALL"]:
        out.setdefault(run["categories"][qid], []).append(qid)
    return dict(sorted(out.items()))


def means_by_repeat(run: dict, ids: list[str]) -> list[float]:
    k = run["repeats"]
    return [sum(run["outcomes"][q][i] for q in ids) / len(ids) for i in range(k)]


def summary(run: dict) -> None:
    k = run["repeats"]
    print(f"{run['name']}: {k} repeats of {len(run['outcomes'])} questions")
    for p in run["problems"]:
        print(f"  ! {p}")
    print(f"{'category':<16}{'n':>5}{'mean':>8}{'sd':>7}{'min':>7}{'max':>7}{'floor':>8}")
    for cat, ids in groups(run).items():
        means = means_by_repeat(run, ids)
        sd = statistics.stdev(means) if k > 1 else 0.0
        floor = noise_floor({q: run["outcomes"][q] for q in ids})
        print(
            f"{cat:<16}{len(ids):>5}{statistics.mean(means):>8.3f}{sd:>7.3f}"
            f"{min(means):>7.3f}{max(means):>7.3f}{floor:>8.3f}"
        )
    r = rates(run["outcomes"])
    always = sum(v == 1 for v in r.values())
    never = sum(v == 0 for v in r.values())
    print(f"always pass {always}, never pass {never}, flip {len(r) - always - never}")
    print(f"A/A, one half of the repeats against the other: {split_half(run['outcomes'])}")
    print(
        f"not judged or errored: {run['errors']}; no answer at all: {run['no_answer']}; "
        f"rerank failures: {run['rerank_failures']}; ${run['cost_usd']} per repeat"
    )


def compare(a: dict, b: dict) -> None:
    for key in ("corpus", "judge_model", "facts_prompt"):
        if a.get(key) != b.get(key):
            print(f"! the sets differ in {key}: {a.get(key)} and {b.get(key)}")
    print(f"{b['name']} ({b['repeats']} repeats) minus {a['name']} ({a['repeats']} repeats)")
    print(
        f"{'category':<16}{'n':>5}{'a':>7}{'b':>7}{'diff':>8}  {'95% CI':<18}{'floor':>7}  verdict"
    )
    for cat, ids in groups(a).items():
        ids = [q for q in ids if q in b["outcomes"]]
        if not ids:
            continue
        x = {q: a["outcomes"][q] for q in ids}
        y = {q: b["outcomes"][q] for q in ids}
        res = compare_repeats(x, y)
        ra, rb = rates(x), rates(y)
        print(
            f"{cat:<16}{res['n']:>5}{statistics.mean(ra.values()):>7.3f}"
            f"{statistics.mean(rb.values()):>7.3f}{res['diff']:>+8.3f}  "
            f"[{res['lo']:+.3f}, {res['hi']:+.3f}]{'':<2}{res['floor']:>7.3f}  {res['verdict']}"
        )
    ra, rb = rates(a["outcomes"]), rates(b["outcomes"])
    moved = sorted(
        ((rb[q] - ra[q], q) for q in ra.keys() & rb.keys() if abs(rb[q] - ra[q]) >= 0.5),
        reverse=True,
    )
    print(f"questions that moved by half or more: {[f'{q} {d:+.2f}' for d, q in moved]}")


def power(run: dict, repeats: int) -> None:
    """How big a gain this set can see, and how often the rule cries wolf."""
    gains = (0, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10)
    rate = detection_rates(run["outcomes"], gains, repeats=repeats)
    n, k = len(run["outcomes"]), run["repeats"]
    floor = noise_floor(run["outcomes"], {q: v[:repeats] for q, v in run["outcomes"].items()})
    print(f"{repeats} new repeats against {k} baseline repeats, {n} questions; floor ±{floor:.3f}")
    print(f"same system again: called different {rate[0]:.1%} of the time")
    for gain in gains[1:]:
        print(f"true gain {gain:+.0%} ({gain * n:.0f} questions): called better {rate[gain]:.0%}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["summary", "compare", "freeze", "power"])
    p.add_argument("paths", nargs="*")
    p.add_argument("--a", nargs="+", help="compare: the base repeats, or a frozen baseline")
    p.add_argument("--b", nargs="+", help="compare: the repeats to test")
    p.add_argument("--out", help="freeze: where to write the compact repeat set")
    p.add_argument("--repeats", type=int, default=3, help="power: repeats of the new run")
    args = p.parse_args()
    if args.cmd == "compare":
        if not args.a or not args.b:
            sys.exit("compare needs --a and --b")
        compare(load(args.a), load(args.b))
    elif args.cmd == "summary":
        summary(load(args.paths))
    elif args.cmd == "power":
        power(load(args.paths), args.repeats)
    else:
        Path(args.out).write_text(json.dumps(load(args.paths), indent=0))
        print("saved", args.out)


if __name__ == "__main__":
    main()
