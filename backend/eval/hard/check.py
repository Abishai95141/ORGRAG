"""Prove the hard set's gold answers from the documents as they are stored.

    .venv/bin/python -m eval.hard.check [questions.jsonl ...]

Reads strata_eval_hard (no model, nothing written). For every question:

- each hop's quote is in its document, word for word;
- each gold document exists and the asker is allowed to read it;
- each fact has a phrasing in the gold documents, or is the result of a derivation;
- a derivation's result is recomputed: its items for a sum or count, the database for
  a count over documents or rows;
- what a question forbids is really hidden from its asker, and really exists;
- what makes a question unanswerable is true: its `absent` strings are in no document,
  and its `absent_from` strings aren't in the document that would hold the answer.

It also checks that the database still matches manifest.json, and reports how many steps
each category's answers take.
"""

import asyncio
import json
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import asyncpg

HERE = Path(__file__).resolve().parent
DB = os.environ.get("HARD_DB", "postgresql://postgres:postgres@localhost:55432/strata_eval_hard")
FILES = [HERE / "questions.jsonl", HERE / "questions_dev.jsonl"]
CATEGORIES = {
    "chain", "aggregate", "as_of", "superseded", "near_duplicate", "permissions",
    "unanswerable", "real_corpus",
}  # fmt: skip
REQUIRED = {"id", "category", "scope", "question", "gold_docs", "facts", "notes", "hops", "split"}
OPTIONAL = {"asker", "forbidden", "derivation", "absent", "absent_from"}


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


async def load() -> tuple[list[dict], dict[str, set[str]], dict]:
    conn = await asyncpg.connect(DB)
    rows = await conn.fetch(
        "SELECT d.title, d.content, d.url, d.external_id, d.content_hash, d.collection_id, "
        "c.visibility FROM documents d JOIN collections c ON c.id = d.collection_id"
    )
    grants = await conn.fetch(
        "SELECT u.email, a.collection_id FROM collection_access a JOIN users u ON u.id = a.user_id"
    )
    await conn.close()
    granted: dict[str, set[str]] = defaultdict(set)
    for g in grants:
        granted[g["email"].split("@")[0]].add(str(g["collection_id"]))
    docs = [
        {
            "title": r["title"],
            "text": squash(r["content"]),
            "lower": squash(r["content"]).lower(),
            "raw": r["content"],
            "url": r["url"] or "",
            "external_id": r["external_id"],
            "hash": r["content_hash"],
            "collection": str(r["collection_id"]),
            "open": r["visibility"] == "org",
        }
        for r in rows
    ]
    return docs, granted, {f"{d['title']}|{d['external_id']}": d["hash"][:16] for d in docs}


def manifest_problems(hashes: dict) -> list[str]:
    path = HERE / "manifest.json"
    if not path.exists():
        return ["manifest.json is missing: run eval/hard/build.py"]
    saved = json.loads(path.read_text())["documents"]
    changed = [k for k in saved.keys() & hashes.keys() if saved[k] != hashes[k]]
    missing = saved.keys() - hashes.keys()
    extra = hashes.keys() - saved.keys()
    out = []
    if changed or missing or extra:
        out.append(
            f"database differs from manifest: {len(changed)} changed, {len(missing)} missing, "
            f"{len(extra)} extra"
        )
    return out


def derive(q: dict, docs: list[dict], by_title: dict) -> list[str]:
    d = q["derivation"]
    op, result = d["op"], d["result"]
    if op == "before":  # "not yet": what's reported comes after the date asked about
        titled = any(d["reported"] in h["doc"] for h in q["hops"])
        return [] if d["asked"] < d["reported"] and titled else [f"{q['id']}: dates don't prove it"]
    if op == "add_months":
        when = datetime.strptime(d["from"], "%d %B %Y").date()
        total = when.month - 1 + d["months"]
        moved = when.replace(year=when.year + total // 12, month=total % 12 + 1)
        got = f"{moved.day} {moved:%B %Y}"
        in_gold = any(d["from"] in by_title[t][0]["text"] for t in q["gold_docs"])
        return [] if got == result and in_gold else [f"{q['id']}: {got}, gold says {result}"]
    if op == "sum":
        got = sum(i["value"] for i in d["items"])
    elif op == "count":
        got = len(d["items"])
    elif op == "count_docs":
        pattern = re.compile(d["pattern"])
        got = sum(
            bool(pattern.search(x["raw"] if d["field"] == "content" else x[d["field"]]))
            for x in docs
        )
    elif op == "count_matches":
        got = len(re.findall(d["pattern"], by_title[d["doc"]][0]["raw"]))
    else:
        return [f"{q['id']}: unknown derivation {op!r}"]
    return [] if got == result else [f"{q['id']}: derivation gives {got}, gold says {result}"]


def visible(doc: dict, asker: str | None, granted: dict) -> bool:
    return asker is None or doc["open"] or doc["collection"] in granted.get(asker, set())


def check(rows: list[dict], docs: list[dict], granted: dict) -> list[str]:
    errors: list[str] = []
    by_title: dict[str, list[dict]] = defaultdict(list)
    for d in docs:
        by_title[d["title"]].append(d)
    ids = Counter(q.get("id") for q in rows)
    errors += [f"duplicate id {i!r}" for i, n in ids.items() if n > 1]
    for q in rows:
        qid = q.get("id", "?")
        if missing := REQUIRED - q.keys():
            errors.append(f"{qid}: missing {sorted(missing)}")
            continue
        if unknown := q.keys() - REQUIRED - OPTIONAL:
            errors.append(f"{qid}: unknown fields {sorted(unknown)}")
        if q["category"] not in CATEGORIES:
            errors.append(f"{qid}: unknown category {q['category']!r}")
        if q["split"] not in ("dev", "test"):
            errors.append(f"{qid}: unknown split")
        asker = q.get("asker")
        if (
            asker
            and asker not in granted
            and q["category"] == "permissions"
            and not q.get("forbidden")
        ):
            errors.append(f"{qid}: asker {asker!r} has no grants")
        gold_text = []
        for title in q["gold_docs"]:
            found = by_title.get(title)
            if not found:
                errors.append(f"{qid}: no document titled {title!r}")
                continue
            if not any(visible(d, asker, granted) for d in found):
                errors.append(f"{qid}: {asker} can't read {title!r}")
            gold_text += [d["lower"] for d in found]
        for hop in q["hops"]:
            found = by_title.get(hop["doc"], [])
            if not any(squash(hop["quote"]) in d["text"] for d in found):
                errors.append(f"{qid}: {hop['doc']!r} doesn't say {hop['quote']!r}")
        for fact in q["facts"]:
            forms = fact["any_of"]
            if not forms:
                errors.append(f"{qid}: fact {fact['fact']!r} has no phrasings")
            derived = q.get("derivation", {})
            proved = (
                any(p.lower() in t for p in forms for t in gold_text)
                or str(derived.get("result")) in forms
                or fact["fact"] == derived.get("proves")
            )
            if not proved:
                errors.append(f"{qid}: {fact['fact']!r} isn't in the gold documents or derived")
        if "derivation" in q:
            errors += derive(q, docs, by_title)
        forbidden = q.get("forbidden", [])
        for text in forbidden:
            shown = [
                d["title"]
                for d in docs
                if visible(d, asker, granted) and text.lower() in d["lower"]
            ]
            if shown:
                errors.append(f"{qid}: {asker} can read {text!r} in {shown[0]!r}: not a leak")
        if forbidden and not any(t.lower() in d["lower"] for t in forbidden for d in docs):
            errors.append(f"{qid}: nothing it forbids is in any document")
        for text in q.get("absent", []):
            if hits := [d["title"] for d in docs if text.lower() in d["lower"]]:
                errors.append(f"{qid}: {text!r} should be in no document, but is in {hits[0]!r}")
        for title, text in q.get("absent_from", {}).items():
            if not by_title.get(title):
                errors.append(f"{qid}: no document titled {title!r}")
            elif any(text.lower() in d["lower"] for d in by_title[title]):
                errors.append(f"{qid}: {title!r} does say {text!r}")
        if not q["facts"] and q["category"] not in ("unanswerable", "permissions"):
            errors.append(f"{qid}: no facts")
        if q["category"] == "unanswerable" and not (q.get("absent") or q.get("absent_from")):
            errors.append(f"{qid}: nothing proves this is unanswerable")
    return errors


async def main() -> int:
    paths = [Path(a) for a in sys.argv[1:]] or FILES
    rows = [json.loads(x) for p in paths for x in p.read_text().splitlines() if x.strip()]
    docs, granted, hashes = await load()
    errors = manifest_problems(hashes)
    errors += check(rows, docs, granted)
    print(f"{len(docs)} documents, {len(rows)} questions")
    steps: dict[str, list[int]] = defaultdict(list)
    spans: dict[str, list[int]] = defaultdict(list)
    for q in rows:
        steps[q["category"]].append(len(q["hops"]))
        spans[q["category"]].append(len(q["gold_docs"]))
    for category in sorted(steps):
        print(
            f"  {category:<16} {len(steps[category]):>3} questions, median "
            f"{statistics.median(steps[category]):g} evidence quotes (max {max(steps[category])}), "
            f"median {statistics.median(spans[category]):g} documents"
        )
    if errors:
        print(f"\nFAILED with {len(errors)} problem(s):")
        for e in errors[:60]:
            print(f"  - {e}")
        return 1
    print("\nOK: every gold fact is proved from the stored documents.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
