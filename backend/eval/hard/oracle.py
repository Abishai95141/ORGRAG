"""What a question scores when the finding is already done: hand a model the question's
gold documents and nothing else.

    .venv/bin/python -m eval.hard.oracle --model claude-haiku-5-5 --judge claude-sonnet-5-5

Two uses. A question that fails here, with every document it needs in front of the model,
is suspect: ambiguous, or its gold is wrong. And the gap between this and the full system
splits a run's failures into reading (still wrong with the right documents) and finding
(right documents never reached). Unanswerable and no-access questions have nothing to
read, so they're skipped.
"""

import argparse
import asyncio
import json
import os
from pathlib import Path

os.environ["EVAL_CORPUS"] = "hard"

from eval import harness  # noqa: E402, I001

from sqlalchemy import text  # noqa: E402

from app.llm.client import complete_text  # noqa: E402

SYSTEM = (
    "Answer the question from the documents below, and only from them. Each document's "
    "title may carry its date. Be direct and state the facts asked for. If they disagree, "
    "work out which is current."
)
WINDOW = 3000  # characters kept around each quote of a document too long to send whole
LONG_DOC = 20000


async def gold_text(db, q: harness.Question, hops: list[dict]) -> str:
    parts = []
    for title in q.gold_docs:
        row = await db.execute(text("SELECT content FROM documents WHERE title = :t"), {"t": title})
        content = row.scalars().first() or ""
        if len(content) > LONG_DOC:
            cuts = []
            for h in hops:
                at = content.find(h["quote"]) if h["doc"] == title else -1
                if at >= 0:
                    cuts.append(content[max(0, at - WINDOW) : at + WINDOW])
            content = "\n…\n".join(cuts) or content[:LONG_DOC]
        parts.append(f"<document title={json.dumps(title)}>\n{content}\n</document>")
    return "\n\n".join(parts)


async def run(args, rows: dict[str, dict]) -> list[dict]:
    path = Path(args.questions or harness.QUESTIONS)
    whole_corpus = ("count_docs", "count_matches")  # a count over everything can't be handed over
    questions = [
        q
        for q in harness.load_questions(path=path)
        if q.gold_docs and rows[q.id].get("derivation", {}).get("op") not in whole_corpus
    ]
    sem = asyncio.Semaphore(args.concurrency)
    out = []

    async def one(q: harness.Question) -> None:
        async with sem:
            async with harness.SessionLocal() as db:
                docs = await gold_text(db, q, rows[q.id]["hops"])
            answer = await complete_text(
                model=args.model,
                system=SYSTEM,
                prompt=f"{docs}\n\nQuestion: {q.question}",
                max_tokens=4000,
            )
            try:
                verdict = await harness.judge(q, answer)
            except Exception as exc:  # one unjudged question shouldn't lose the rest
                print(f"  {q.id} not judged: {exc!r}"[:160], flush=True)
                return
            passed = all(verdict["facts"]) and not verdict["contradiction"]
            out.append(
                {"id": q.id, "category": q.category, "passed": passed, "answer": answer, **verdict}
            )
            print(f"  {q.id} {'PASS' if passed else 'fail'}", flush=True)

    await asyncio.gather(*(one(q) for q in questions))
    out.sort(key=lambda r: r["id"])
    cats: dict[str, list[bool]] = {}
    for r in out:
        cats.setdefault(r["category"], []).append(r["passed"])
        cats.setdefault("ALL", []).append(r["passed"])
    for c, v in sorted(cats.items()):
        print(f"{c:<16} {sum(v)}/{len(v)} = {sum(v) / len(v):.3f}")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="claude-haiku-5-5")
    p.add_argument("--judge", default="claude-sonnet-5-5")
    p.add_argument("--questions")
    p.add_argument("--concurrency", type=int, default=6)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    harness.use_judge(args.judge, "all")
    lines = Path(args.questions or harness.QUESTIONS).read_text().splitlines()
    rows = {json.loads(x)["id"]: json.loads(x) for x in lines if x.strip()}
    out = asyncio.run(run(args, rows))
    Path(args.out).write_text(json.dumps({"model": args.model, "rows": out}, indent=1))
    print("saved", args.out)


if __name__ == "__main__":
    main()
