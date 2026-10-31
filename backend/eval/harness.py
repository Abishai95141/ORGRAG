"""Strata evaluation harness.

Loads the seed corpus plus eval/corpus into a dedicated database, then measures:

* retrieval — does hybrid search surface the gold documents? (recall@5/@10, MRR;
  deterministic, no LLM), and
* answers — does the agent's answer contain the required facts, cite the gold
  documents, and abstain on unanswerable questions? (deterministic substring check
  plus an LLM judge).

Variants (rerank on/off, recency, and later the concept / graph layers) are org
settings, so each run states exactly what was switched on.

    .venv/bin/python -m eval.harness retrieval --variant rerank
    .venv/bin/python -m eval.harness answers --variant rerank --limit 10
    .venv/bin/python -m eval.harness compare results/a.json results/b.json
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent
SEED_DIR = BACKEND / "app" / "seed_data"
EVAL_CORPUS = HERE / "corpus"
# A 60-record vendor register for a third org, from eval/scale/generate.py.
SCALE_CORPUS = HERE / "corpus_scale"
QUESTIONS = HERE / "questions.jsonl"
RESULTS = HERE / "results"
DATA = HERE / ".data"

EVAL_DB = os.environ.get(
    "EVAL_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:55432/strata_eval"
)
# Settings are read at import time, so point the app at the eval database first.
os.environ["DATABASE_URL"] = EVAL_DB
os.environ["FILE_STORAGE_DIR"] = str(DATA / "files")
os.environ.setdefault("EMBEDDING_PROVIDER", "fastembed")
sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select, text  # noqa: E402

from app.agent import engine  # noqa: E402
from app.agent.citations import cited_refs  # noqa: E402
from app.connectors.upload import storage_dir  # noqa: E402
from app.core.db import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.ingestion.pipeline import sync_source  # noqa: E402
from app.llm import nvidia  # noqa: E402
from app.llm.client import complete_text  # noqa: E402
from app.models import (  # noqa: E402
    Collection,
    Conversation,
    Membership,
    Message,
    Organization,
    Source,
    User,
)
from app.retrieval.access import resolve_scope  # noqa: E402
from app.retrieval.hybrid import RankingOptions, hybrid_search  # noqa: E402
from app.schemas.common import OrgSettings  # noqa: E402

EVAL_EMAIL = "eval@strata.dev"
ORG_INFO = {
    "northwind": ("Northwind Labs", "Real-time data pipelines for product and growth teams."),
    "halcyon": ("Halcyon Health", "A network of primary-care and telehealth clinics."),
    "meridian": ("Meridian Care", "A regional clinic group (scale test: vendor register)."),
}

# Named configurations. Each maps to org settings applied before a run.
VARIANTS: dict[str, dict[str, Any]] = {
    "fusion": {"rerank": False, "recency_bias": "off"},
    "rerank": {"rerank": True, "recency_bias": "off"},
    # What a new org gets today.
    "default": {"rerank": True, "recency_bias": "low"},
    "rerank_recency": {"rerank": True, "recency_bias": "medium"},
    # Phase 2: knowledge-graph tools on top of the default (needs `build-graph` first).
    "graph": {"rerank": True, "recency_bias": "low", "knowledge_graph": True},
}


# ── dataset ──────────────────────────────────────────────────────────────────
@dataclass
class Question:
    id: str
    category: str
    scope: list[str]
    question: str
    gold_docs: list[str]
    facts: list[dict[str, Any]]
    notes: str = ""


def load_questions(
    limit: int | None = None, ids: list[str] | None = None, path: Path = QUESTIONS
) -> list[Question]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    qs = [Question(**{k: r.get(k, "") for k in Question.__dataclass_fields__}) for r in rows]
    if ids:
        qs = [q for q in qs if q.id in ids]
    return qs[:limit] if limit else qs


def corpus_files() -> list[tuple[str, str, Path]]:
    """(org slug, collection folder, path) for the seed corpus plus eval/corpus."""
    out = []
    for root in (SEED_DIR, EVAL_CORPUS, SCALE_CORPUS):
        if root.exists():
            for path in sorted(root.glob("*/*/*.md")):
                out.append((path.parts[-3], path.parts[-2], path))
    return out


def corpus_hash() -> str:
    h = hashlib.sha256()
    for org, col, path in corpus_files():
        h.update(f"{org}/{col}/{path.name}".encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


# ── database & ingestion ─────────────────────────────────────────────────────
def ensure_database() -> None:
    import asyncpg

    base, name = EVAL_DB.rsplit("/", 1)

    async def create() -> None:
        conn = await asyncpg.connect(base.replace("+asyncpg", "") + "/postgres")
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{name}"')
        await conn.close()

    asyncio.run(create())
    env = {**os.environ, "ALEMBIC_DATABASE_URL": EVAL_DB, "DATABASE_URL": EVAL_DB}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND,
        env=env,
        check=True,
        capture_output=True,
    )


async def ingest(force: bool = False) -> None:
    """(Re)build the eval orgs when the corpus changed. One upload source per collection."""
    digest = corpus_hash()
    marker = DATA / "corpus.hash"
    async with SessionLocal() as db:
        have = (await db.execute(select(Organization.id))).first()
    if not force and have and marker.exists() and marker.read_text() == digest:
        return
    print(f"Ingesting corpus {digest}…", flush=True)
    async with SessionLocal() as db:
        await db.execute(text("TRUNCATE organizations, users CASCADE"))
        await db.commit()
        user = User(email=EVAL_EMAIL, name="Eval Runner", password_hash=hash_password("eval"))
        db.add(user)
        await db.flush()
        by_org: dict[str, list[tuple[str, Path]]] = {}
        for org_slug, col, path in corpus_files():
            by_org.setdefault(org_slug, []).append((col, path))
        for org_slug, files in by_org.items():
            name, desc = ORG_INFO.get(org_slug, (org_slug.title(), None))
            org = Organization(
                slug=org_slug, name=name, description=desc, settings=OrgSettings().model_dump()
            )
            db.add(org)
            await db.flush()
            db.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
            for col_name in sorted({c for c, _ in files}):
                col = Collection(org_id=org.id, name=col_name.replace("-", " ").title())
                db.add(col)
                await db.flush()
                source = Source(
                    org_id=org.id, collection_id=col.id, kind="upload", name=col.name, config={}
                )
                db.add(source)
                await db.flush()
                folder = storage_dir(str(source.id))
                for c, path in files:
                    if c == col_name:
                        shutil.copy(path, folder / path.name)
                await db.commit()
                stats = await sync_source(db, source)
                source.status = "ready"
                await db.commit()
                print(f"  {org_slug}/{col_name}: {stats}", flush=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(digest)


JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL", "nvidia/nemotron-3-ultra-550b-a55b")


async def apply_variant(variant: str, chat_model: str | None = None) -> dict[str, Any]:
    overrides = dict(VARIANTS[variant])
    if chat_model:
        overrides["model"] = chat_model
    async with SessionLocal() as db:
        for org in (await db.execute(select(Organization))).scalars():
            org.settings = OrgSettings(**{**(org.settings or {}), **overrides}).model_dump()
        await db.commit()
    return overrides


async def _context():
    async with SessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == EVAL_EMAIL))).scalar_one()
        orgs = {o.slug: o for o in (await db.execute(select(Organization))).scalars()}
    return user, orgs


# ── retrieval ────────────────────────────────────────────────────────────────
@dataclass
class RetrievalResult:
    id: str
    category: str
    ranked: list[str]
    recall5: float
    recall10: float
    mrr: float
    latency_ms: int


async def retrieval_one(q: Question, user, orgs, k: int = 10) -> RetrievalResult:
    primary = orgs[q.scope[0]]
    settings = OrgSettings(**(primary.settings or {}))
    started = time.perf_counter()
    async with SessionLocal() as db:
        scope = await resolve_scope(db, user_id=user.id, primary_org=primary, org_slugs=q.scope)
        hits = await hybrid_search(
            db,
            [q.question],
            scope.collection_ids,
            limit=k,
            per_doc=1,
            ranking=RankingOptions(rerank=settings.rerank, recency_bias=settings.recency_bias),
        )
    latency = int((time.perf_counter() - started) * 1000)
    ranked = [h.title for h in hits]
    gold = set(q.gold_docs)
    found = lambda n: len(gold & set(ranked[:n])) / len(gold)  # noqa: E731
    first = next((i for i, t in enumerate(ranked, 1) if t in gold), None)
    return RetrievalResult(
        q.id, q.category, ranked, found(5), found(10), 1 / first if first else 0.0, latency
    )


async def run_retrieval(questions: list[Question]) -> list[RetrievalResult]:
    user, orgs = await _context()
    out = []
    for q in questions:
        if not q.gold_docs:
            continue
        out.append(await retrieval_one(q, user, orgs))
    return out


# ── answers ──────────────────────────────────────────────────────────────────
JUDGE_SYSTEM = """You grade answers from a company knowledge assistant. You get a \
question, the facts a correct answer must state, and the assistant's answer. For each \
fact decide whether the answer clearly states it (paraphrase is fine; contradicting or \
hedging it is not). Also decide whether the answer says the information could not be \
found / is not in the knowledge base. Reply with only JSON:
{"facts": [true/false for each fact, in order], "abstained": true/false, \
"contradiction": true/false (the answer asserts something that contradicts a fact)}"""


@dataclass
class AnswerResult:
    id: str
    category: str
    answer: str
    status: str
    fact_hits_substring: list[bool] = field(default_factory=list)
    fact_hits_judge: list[bool] = field(default_factory=list)
    abstained: bool = False
    contradiction: bool = False
    cited_titles: list[str] = field(default_factory=list)
    gold_cited: float = 0.0
    passed: bool = False
    latency_s: float = 0.0
    error: str | None = None


def substring_hits(answer: str, facts: list[dict[str, Any]]) -> list[bool]:
    norm = re.sub(r"\s+", " ", answer.lower().replace(" ", " ").replace("‑", "-"))
    return [any(p.lower() in norm for p in f.get("any_of", [])) for f in facts]


async def judge(q: Question, answer: str) -> dict[str, Any]:
    facts = "\n".join(f"{i}. {f['fact']}" for i, f in enumerate(q.facts)) or "(none — the "
    facts += "" if q.facts else "knowledge base does not contain the answer)"
    prompt = f"Question: {q.question}\n\nRequired facts:\n{facts}\n\nAnswer:\n{answer}"
    for attempt in range(3):
        try:
            text = await complete_text(
                model=JUDGE_MODEL, system=JUDGE_SYSTEM, prompt=prompt, max_tokens=2048
            )
            verdict = nvidia.extract_json(text)
            hits = [bool(x) for x in verdict.get("facts", [])][: len(q.facts)]
            hits += [False] * (len(q.facts) - len(hits))
            return {
                "facts": hits,
                "abstained": bool(verdict.get("abstained")),
                "contradiction": bool(verdict.get("contradiction")),
            }
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(2 + attempt * 3)
    raise RuntimeError("unreachable")


async def answer_one(q: Question, user, orgs, mode: str) -> AnswerResult:
    primary = orgs[q.scope[0]]
    scope_ids = [orgs[s].id for s in q.scope]
    async with SessionLocal() as db:
        conv = Conversation(
            org_id=primary.id, user_id=user.id, title=q.id, scope_org_ids=scope_ids, mode=mode
        )
        db.add(conv)
        await db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content=q.question))
        reserved = Message(conversation_id=conv.id, role="assistant", status="streaming")
        db.add(reserved)
        await db.commit()
        conv_id, msg_id = conv.id, reserved.id
    queue: asyncio.Queue = asyncio.Queue()
    req = engine.ChatRequest(
        conversation_id=conv_id,
        assistant_message_id=msg_id,
        user_id=user.id,
        org_id=primary.id,
        question=q.question,
        mode=mode,
        scope_org_ids=scope_ids,
        collection_ids=None,
    )
    started = time.perf_counter()
    await engine.run_chat(req, queue)
    latency = time.perf_counter() - started
    error = None
    while (item := queue.get_nowait()) is not None:
        if item[0] == "error":
            error = item[1].get("detail")
    async with SessionLocal() as db:
        msg = await db.get(Message, msg_id)
        answer, status, sources = msg.content, msg.status, msg.sources
    by_ref = {s["ref"]: s for s in sources}
    cited = [by_ref[r]["title"] for r in cited_refs(answer) if r in by_ref]
    result = AnswerResult(q.id, q.category, answer, status, latency_s=round(latency, 1))
    result.error = error
    result.cited_titles = cited
    gold = set(q.gold_docs)
    result.gold_cited = len(gold & set(cited)) / len(gold) if gold else 0.0
    result.fact_hits_substring = substring_hits(answer, q.facts)
    if status == "complete":
        verdict = await judge(q, answer)
        result.fact_hits_judge = verdict["facts"]
        result.abstained = verdict["abstained"]
        result.contradiction = verdict["contradiction"]
    if q.category == "unanswerable":
        # The answer must say it couldn't find this, without asserting a made-up answer.
        result.passed = status == "complete" and result.abstained and not result.contradiction
    else:
        result.passed = (
            status == "complete"
            and bool(q.facts)
            and all(result.fact_hits_judge)
            and not result.contradiction
        )
    return result


async def run_answers(questions: list[Question], mode: str, concurrency: int) -> list[AnswerResult]:
    user, orgs = await _context()
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def one(q: Question) -> AnswerResult:
        nonlocal done
        async with sem:
            try:
                r = await answer_one(q, user, orgs, mode)
            except Exception as exc:  # a crashed question is a failed question, not a crash
                r = AnswerResult(q.id, q.category, "", "error", error=repr(exc)[:300])
            done += 1
            print(
                f"  [{done}/{len(questions)}] {q.id} {'PASS' if r.passed else 'fail'}", flush=True
            )
            return r

    return list(await asyncio.gather(*(one(q) for q in questions)))


# ── knowledge graph ──────────────────────────────────────────────────────────
async def build_graph(model: str | None, concurrency: int) -> dict[str, Any]:
    """Extract the graph for every document whose content changed since its last
    extraction; reports time and model calls so indexing cost is part of the evidence."""
    from app.graph import extract
    from app.models import Chunk, Document

    calls = 0
    original = extract.complete_text

    async def counted(**kwargs):
        nonlocal calls
        calls += 1
        return await original(**kwargs)

    extract.complete_text = counted
    started = time.perf_counter()
    async with SessionLocal() as db:
        docs = list(
            (
                await db.execute(
                    select(Document.id).where(
                        (Document.graph_hash.is_(None))
                        | (Document.graph_hash != Document.content_hash)
                    )
                )
            ).scalars()
        )
        chunks = await db.scalar(
            select(func.count()).select_from(Chunk).where(Chunk.document_id.in_(docs))
        ) if docs else 0  # fmt: skip
    print(f"Extracting {len(docs)} documents ({chunks} chunks) with {model or 'org model'}…")
    sem = asyncio.Semaphore(concurrency)
    failed = []

    async def one(doc_id) -> None:
        async with sem, SessionLocal() as db:
            doc = await db.get(Document, doc_id)
            try:
                await extract.extract_document(db, doc, model)
                await db.commit()
            except Exception as exc:
                failed.append((doc.title, repr(exc)[:160]))

    await asyncio.gather(*(one(d) for d in docs))
    async with SessionLocal() as db:
        for org in (await db.execute(select(Organization))).scalars():
            await extract.prune_orphans(db, org.id)
        await db.commit()
    extract.complete_text = original
    report = {
        "documents": len(docs),
        "chunks": chunks,
        "model_calls": calls,
        "seconds": round(time.perf_counter() - started, 1),
        "failed": failed,
    }
    print(report)
    return report


# ── reporting ────────────────────────────────────────────────────────────────
def summarize_retrieval(rows: list[dict]) -> dict[str, dict[str, float]]:
    cats: dict[str, list[dict]] = {}
    for r in rows:
        cats.setdefault(r["category"], []).append(r)
        cats.setdefault("ALL", []).append(r)
    return {
        c: {
            "n": len(v),
            "recall@5": round(sum(r["recall5"] for r in v) / len(v), 3),
            "recall@10": round(sum(r["recall10"] for r in v) / len(v), 3),
            "mrr": round(sum(r["mrr"] for r in v) / len(v), 3),
            "ms": round(sum(r["latency_ms"] for r in v) / len(v)),
        }
        for c, v in sorted(cats.items())
    }


def summarize_answers(rows: list[dict]) -> dict[str, dict[str, float]]:
    cats: dict[str, list[dict]] = {}
    for r in rows:
        cats.setdefault(r["category"], []).append(r)
        cats.setdefault("ALL", []).append(r)

    def frac(v, key):
        hits = [h for r in v for h in r[key]]
        return round(sum(hits) / len(hits), 3) if hits else None

    return {
        c: {
            "n": len(v),
            "pass": round(sum(r["passed"] for r in v) / len(v), 3),
            "facts_judge": frac(v, "fact_hits_judge"),
            "facts_substring": frac(v, "fact_hits_substring"),
            "gold_cited": round(sum(r["gold_cited"] for r in v) / len(v), 3),
            "errors": sum(r["status"] != "complete" for r in v),
            "s": round(sum(r["latency_s"] for r in v) / len(v), 1),
        }
        for c, v in sorted(cats.items())
    }


def print_table(summary: dict[str, dict[str, Any]]) -> None:
    if not summary:
        print("(no rows)")
        return
    cols = list(next(iter(summary.values())))
    print(f"{'category':<18}" + "".join(f"{c:>16}" for c in cols))
    for cat, vals in summary.items():
        print(f"{cat:<18}" + "".join(f"{str(vals[c]):>16}" for c in cols))


def save(kind: str, variant: str, rows: list, summary: dict, extra: dict) -> Path:
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{kind}-{variant}.json"
    path.write_text(
        json.dumps(
            {"kind": kind, "variant": variant, "corpus": corpus_hash(), **extra,
             "summary": summary, "rows": rows},
            indent=1,
        )
    )  # fmt: skip
    return path


def compare(paths: list[str]) -> None:
    runs = [json.loads(Path(p).read_text()) for p in paths]
    metric = "recall@10" if runs[0]["kind"] == "retrieval" else "pass"
    cats = sorted({c for r in runs for c in r["summary"]})
    print(f"{'category':<18}" + "".join(f"{r['variant'][:16]:>18}" for r in runs))
    for c in cats:
        vals = [r["summary"].get(c, {}).get(metric) for r in runs]
        print(f"{c:<18}" + "".join(f"{str(v):>18}" for v in vals))
    print(f"(metric: {metric})")


async def main_async(args) -> None:
    await ingest(force=args.reingest)
    questions = load_questions(
        args.limit, args.ids.split(",") if args.ids else None, Path(args.questions)
    )
    overrides = await apply_variant(args.variant, args.chat_model)
    if args.cmd == "retrieval":
        rows = [r.__dict__ for r in await run_retrieval(questions)]
        summary = summarize_retrieval(rows)
        print_table(summary)
        print("saved", save("retrieval", args.variant, rows, summary, {"settings": overrides}))
    else:
        rows = [r.__dict__ for r in await run_answers(questions, args.mode, args.concurrency)]
        summary = summarize_answers(rows)
        print_table(summary)
        name = f"{args.variant}-{args.mode}" + (
            f"-{args.chat_model.split(':')[-1]}" if args.chat_model else ""
        )
        print("saved", save("answers", name, rows, summary, {"settings": overrides}))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["retrieval", "answers", "compare", "ingest", "build-graph"])
    p.add_argument("paths", nargs="*")
    p.add_argument("--variant", default="rerank", choices=sorted(VARIANTS))
    p.add_argument("--mode", default="quick", choices=["quick", "deep"])
    p.add_argument("--limit", type=int)
    p.add_argument("--ids")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--reingest", action="store_true")
    p.add_argument("--questions", default=str(QUESTIONS))
    p.add_argument("--model", help="build-graph: extraction model (default: the org's)")
    p.add_argument(
        "--chat-model", help="answers: the org model to chat with, e.g. explabs:claude-haiku-5.5"
    )
    args = p.parse_args()
    if args.cmd == "compare":
        compare(args.paths)
        return
    ensure_database()
    if args.cmd == "ingest":
        asyncio.run(ingest(force=True))
        return
    if args.cmd == "build-graph":

        async def run() -> None:
            await ingest()
            report = await build_graph(args.model, args.concurrency)
            RESULTS.mkdir(exist_ok=True)
            (RESULTS / "graph-build.json").write_text(json.dumps(report, indent=1))

        asyncio.run(run())
        return
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
