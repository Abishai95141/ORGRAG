"""Sanity checks for the retrieval/answer evaluation dataset.

Loads every Markdown document in app/seed_data and eval/corpus, extracts the
document title (first `# ` heading, as ingestion does) and verifies that
eval/questions.jsonl is internally consistent with that corpus:

- every line parses as JSON and has the required fields;
- ids are unique and categories / scopes are valid;
- every gold_doc title exists exactly, and belongs to an org in the question's scope;
- every fact has at least one any_of phrasing, and at least one phrasing of each
  fact appears (case-insensitively) in at least one of the question's gold docs;
- unanswerable questions have no gold docs and no facts;
- document titles are unique and word counts of the eval corpus are in range.

Run: cd backend && .venv/bin/python eval/check_dataset.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
CORPUS_DIRS = [BACKEND / "app" / "seed_data", BACKEND / "eval" / "corpus"]
QUESTIONS = BACKEND / "eval" / "questions.jsonl"

CATEGORIES = {
    "single_fact",
    "cross_org_vocab",
    "multi_hop",
    "list_aggregate",
    "superseded",
    "alias",
    "unanswerable",
}
ORGS = {"northwind", "halcyon"}
REQUIRED = {"id", "category", "scope", "question", "gold_docs", "facts", "notes"}
EVAL_WORDS = (150, 600)
ALT_ONLY: list[str] = []  # any_of phrasings absent from gold docs (answer-side alternatives)


def title_of(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return None


def load_corpus() -> tuple[dict[str, dict], list[str]]:
    docs: dict[str, dict] = {}
    errors: list[str] = []
    for root in CORPUS_DIRS:
        for path in sorted(root.rglob("*.md")):
            rel = path.relative_to(root)
            org, collection = rel.parts[0], rel.parts[1]
            text = path.read_text(encoding="utf-8")
            title = title_of(text)
            if title is None:
                errors.append(f"{path}: no '# ' title")
                continue
            if title in docs:
                errors.append(f"duplicate title {title!r}: {docs[title]['path']} and {path}")
            words = len(text.split())
            if root.name == "corpus" and not EVAL_WORDS[0] <= words <= EVAL_WORDS[1]:
                errors.append(f"{path}: {words} words, outside {EVAL_WORDS}")
            docs[title] = {
                "path": path,
                "org": org,
                "collection": collection,
                "text": text.lower(),
                "eval": root.name == "corpus",
                "words": words,
            }
    return docs, errors


def check_questions(docs: dict[str, dict]) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    questions: list[dict] = []
    for n, line in enumerate(QUESTIONS.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            q = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {n}: invalid JSON ({e})")
            continue
        questions.append(q)
        qid = q.get("id", f"line {n}")
        missing = REQUIRED - q.keys()
        if missing:
            errors.append(f"{qid}: missing fields {sorted(missing)}")
            continue
        if q["category"] not in CATEGORIES:
            errors.append(f"{qid}: invalid category {q['category']!r}")
        scope = q["scope"]
        if (
            not isinstance(scope, list)
            or not scope
            or not set(scope) <= ORGS
            or len(set(scope)) != len(scope)
        ):
            errors.append(f"{qid}: invalid scope {scope!r}")
            scope = list(ORGS)
        if q["category"] == "cross_org_vocab" and len(scope) != 2:
            errors.append(f"{qid}: cross_org_vocab questions must span both orgs")

        if q["category"] == "unanswerable":
            if q["gold_docs"] or q["facts"]:
                errors.append(f"{qid}: unanswerable questions need empty gold_docs and facts")
            continue
        if not q["gold_docs"]:
            errors.append(f"{qid}: no gold_docs")
        if not q["facts"]:
            errors.append(f"{qid}: no facts")

        gold_texts = []
        for title in q["gold_docs"]:
            doc = docs.get(title)
            if doc is None:
                errors.append(f"{qid}: gold doc not found: {title!r}")
                continue
            if doc["org"] not in scope:
                errors.append(
                    f"{qid}: gold doc {title!r} is in {doc['org']}, outside scope {scope}"
                )
            gold_texts.append(doc["text"])
        if len(set(q["gold_docs"])) != len(q["gold_docs"]):
            errors.append(f"{qid}: duplicate gold_docs")

        for i, fact in enumerate(q["facts"]):
            phrasings = fact.get("any_of") or []
            if not fact.get("fact") or not phrasings:
                errors.append(f"{qid} fact {i}: needs 'fact' and a non-empty 'any_of'")
                continue
            if not any(p.lower() in t for p in phrasings for t in gold_texts):
                errors.append(
                    f"{qid} fact {i} ({fact['fact']!r}): no any_of phrasing found in gold docs"
                )
            for p in phrasings:
                if not p.strip():
                    errors.append(f"{qid} fact {i}: empty any_of phrasing")
                elif not any(p.lower() in t for t in gold_texts):
                    # Allowed: alternative wordings an answer may use ("fifteen percent").
                    ALT_ONLY.append(f"{qid}: {p!r}")

    ids = Counter(q.get("id") for q in questions)
    for qid, count in ids.items():
        if count > 1:
            errors.append(f"duplicate id {qid!r} ({count}x)")
    return questions, errors


def main() -> int:
    docs, errors = load_corpus()
    questions, q_errors = check_questions(docs)
    errors += q_errors

    by_collection = Counter(
        (d["org"], d["collection"], "eval" if d["eval"] else "seed") for d in docs.values()
    )
    print(f"Corpus: {len(docs)} documents")
    for (org, coll, src), count in sorted(by_collection.items()):
        print(f"  {org}/{coll} [{src}]: {count}")
    print(f"Questions: {len(questions)}")
    for cat, count in sorted(Counter(q.get("category") for q in questions).items()):
        print(f"  {cat}: {count}")
    used = {t for q in questions for t in q.get("gold_docs", [])}
    unused = sorted(t for t in docs if t not in used)
    print(f"Documents never used as gold: {len(unused)}")
    for t in unused:
        print(f"  - {t}")
    print(f"Alternative any_of phrasings not in gold text (ok, informational): {len(ALT_ONLY)}")
    eval_words = sorted(d["words"] for d in docs.values() if d["eval"])
    if eval_words:
        print(f"Eval corpus word counts: min {eval_words[0]}, max {eval_words[-1]}")

    if errors:
        print(f"\nFAILED with {len(errors)} problem(s):")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nOK: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
