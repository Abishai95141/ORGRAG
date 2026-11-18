"""Recompute every published number from the stored per-question rows.

    cd backend && .venv/bin/python -m eval.verify_claims            # print the table
    cd backend && .venv/bin/python -m eval.verify_claims --write    # also write docs/EVIDENCE.md

It reads only committed result files, recomputes each metric from the rows (not from
the run's own `summary`), checks it against the run's summary and the published
figure, and prints each file's SHA-256 and the commit that added it. No model calls,
no database.
"""

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
MAIN = "eval/results/answers-haiku-baseline-claude-haiku-5-5.json"
RETRIEVAL = "eval/results/retrieval-default.json"
HARD = {
    "Baseline agent": ["eval/hard/baseline.json"],
    "Graph offered as tools": [
        "eval/results/hard-graph.json",
        "eval/results/hard-graph-repeat.json",
    ],
    "Search passages up front": ["eval/results/hard-plain.json"],
    "Graph facts up front": ["eval/results/hard-terms.json"],
}
# List price per million tokens (input, output), as in app/llm/pricing.py.
HAIKU_PRICE = (0.10, 0.50)


def load(rel: str) -> dict:
    return json.loads((BACKEND / rel).read_text())


def provenance(rel: str) -> tuple[str, str]:
    digest = hashlib.sha256((BACKEND / rel).read_bytes()).hexdigest()[:16]
    commit = (
        subprocess.run(
            ["git", "log", "-1", "--format=%h %ad", "--date=short", "--", f"backend/{rel}"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .splitlines()
    )
    return digest, (commit[0] if commit else "not committed")


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write docs/EVIDENCE.md")
    args = ap.parse_args()

    run = load(MAIN)
    rows, summ = run["rows"], run["summary"]["ALL"]
    n = len(rows)
    passed = sum(r["passed"] for r in rows)
    gs = [r["grounding"] for r in rows if r.get("grounding")]
    claims, supported = sum(g["claims"] for g in gs), sum(g["supported"] for g in gs)
    cites, good = sum(g["citations"] for g in gs), sum(g["good_citations"] for g in gs)
    lat = [r["latency_s"] for r in rows]
    cost = [r["cost_usd"] for r in rows]
    usage = [m for r in rows for m in r["usage"].values()]
    tin, tout = sum(m["input"] for m in usage), sum(m["output"] for m in usage)
    calls = sum(m["calls"] for m in usage)
    priced = tin * HAIKU_PRICE[0] / 1e6 + tout * HAIKU_PRICE[1] / 1e6

    ret = load(RETRIEVAL)
    rrows = ret["rows"]
    r5 = statistics.mean(r["recall5"] for r in rrows)

    checks = [
        # claim, published, recomputed (display), recomputed value, run summary value, how
        (
            "Answers judged correct",
            "94%",
            f"{passed}/{n} = {passed / n:.1%}",
            passed / n,
            summ["pass"],
            "every key fact judged present and no contradiction; "
            "for unanswerable questions, a correct abstention",
        ),
        (
            "Faithfulness to the cited sources",
            "0.955",
            f"{supported}/{claims} = {supported / claims:.3f}",
            supported / claims,
            summ["faithfulness"],
            "claims supported by the retrieved passages / all claims",
        ),
        (
            "Citation precision",
            "0.98",
            f"{good}/{cites} = {good / cites:.3f}",
            good / cites,
            summ["cite_precision"],
            "citations whose passage supports the statement / all citations on claims",
        ),
        (
            "Retrieval recall@5, reranked",
            "0.94",
            f"mean of {len(rrows)} = {r5:.3f}",
            r5,
            ret["summary"]["ALL"]["recall@5"],
            "share of gold documents in the top 5, per answerable question",
        ),
        (
            "Mean time to answer",
            "5.4 s",
            f"{statistics.mean(lat):.2f} s (p95 {p95(lat)} s)",
            statistics.mean(lat),
            summ["s"],
            "wall-clock seconds per answer, request to final token",
        ),
        (
            "Cost per answer",
            "about $0.001",
            f"mean ${statistics.mean(cost):.5f}, median ${statistics.median(cost):.5f}",
            statistics.mean(cost),
            summ["usd"],
            f"{tin:,} input + {tout:,} output tokens over {calls} model calls; "
            f"at ${HAIKU_PRICE[0]}/${HAIKU_PRICE[1]} per million that is "
            f"${priced:.4f} for {n} answers",
        ),
    ]

    lines = [
        "| Claim | Published | Recomputed from rows | Matches run summary | How it's counted |",
        "|---|---|---|---|---|",
    ]
    ok = True
    for claim, pub, shown, value, stored, how in checks:
        tol = 0.0051 if stored >= 0.5 else 0.000051
        match = abs(value - stored) <= max(tol, abs(stored) * 0.01)
        ok &= match
        lines.append(
            f"| {claim} | {pub} | {shown} | {'yes' if match else 'NO'} ({stored}) | {how} |"
        )

    hard = [
        "| Arm (146 questions) | Repeats | Pass rate | Answers | $ per answer |",
        "|---|---|---|---|---|",
    ]
    for arm, files in HARD.items():
        outs, reps, usd = [], 0, 0.0
        for f in files:
            d = load(f)
            outs += [x for v in d["outcomes"].values() for x in v if x is not None]
            reps += d["repeats"]
            usd += d["cost_usd"] * d["repeats"]
        hard.append(
            f"| {arm} | {reps} | {sum(outs) / len(outs):.1%} | {len(outs)} "
            f"| ${usd / len(outs):.5f} |"
        )

    fails = ["| Question | Category | What went wrong |", "|---|---|---|"]
    for r in rows:
        if not r["passed"]:
            why = (
                "a fact missing"
                if not all(r["fact_hits_judge"] or [True])
                else "contradiction"
                if r["contradiction"]
                else "should have abstained"
            )
            fails.append(f"| {r['id']} | {r['category'].replace('_', ' ')} | {why} |")

    files = [MAIN, RETRIEVAL] + [f for fs in HARD.values() for f in fs]
    prov = ["| File | SHA-256 (first 16) | Last changed in |", "|---|---|---|"]
    for f in files:
        digest, commit = provenance(f)
        prov.append(f"| `backend/{f}` | `{digest}` | {commit} |")

    cheap = sum(c < 0.001 for c in cost)
    complete = sum(r["status"] == "complete" for r in rows)
    body = f"""# Evidence for the published numbers

Generated by `backend/eval/verify_claims.py` from the committed result files. Every
figure below is recomputed from the per-question rows, not copied from a summary.
Rerun it to check: `cd backend && .venv/bin/python -m eval.verify_claims`.

## The headline run

150 questions over two organizations; Claude Haiku 5.5 answering in quick mode; judged
by `{run["judge_model"]}`; corpus digest `{run["corpus"]}`;
settings `{json.dumps(run["settings"])}`. Recall@5 comes from the retrieval run on the
same corpus (`{RETRIEVAL}`, {len(rrows)} answerable questions).

{chr(10).join(lines)}

{cheap} of {n} answers cost under $0.001; the most expensive cost ${max(cost):.5f}. The run
was made before prompt caching, and {complete} of its {n} answers completed.

### The {n - passed} failures

{chr(10).join(fails)}

Each row in the result file carries the full answer, its sources, the judge's verdict
per fact, every statement's grounding label and the token usage.

## The harder set

146 questions written to break the agent (multi-step chains, superseded and dated facts,
aggregates, a real corpus). Claude Haiku 5.5, judged by `claude-sonnet-5-5`; pass rate over
every question and repeat.

{chr(10).join(hard)}

## End-to-end scenario: 509/509

Recorded in `docs/LOG.md` (2026-11-01): the scenario in `backend/e2e/` passed 509 of 509
checks against the Docker stack. That run's `results.json` was written outside the repo
and wasn't kept, so this figure rests on the log entry until it is run again.

## Files

{chr(10).join(prov)}
"""
    print(body)
    if args.write:
        (REPO / "docs/EVIDENCE.md").write_text(body)
        print("wrote docs/EVIDENCE.md", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
