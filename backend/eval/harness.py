"""Strata evaluation harness.

Loads the seed corpus plus eval/corpus into a dedicated database, then measures:

* retrieval — does hybrid search surface the gold documents? (recall@5/@10, MRR;
  deterministic, no LLM), and
* answers — does the agent's answer contain the required facts, cite the gold
  documents, and abstain on unanswerable questions? (deterministic substring check
  plus an LLM judge), and
* grounding — is every claim supported by what was retrieved, and do its citations
  support it? (faithfulness, citation precision / recall; see eval/grounding.py).

Judge replies are cached in eval/.data, keyed by judge model and prompt, so
re-scoring the same answers is free. `rejudge` re-scores a saved run, `compare`
tests differences with a paired bootstrap, and `label` / `kappa` measure how often
the judge agrees with a human.

Variants (rerank on/off, recency, and later the concept / graph layers) are org
settings, so each run states exactly what was switched on.

    .venv/bin/python -m eval.harness retrieval --variant rerank
    .venv/bin/python -m eval.harness answers --variant rerank --limit 10
    .venv/bin/python -m eval.harness answers --set smoke --chat-model claude-haiku-5-5
    .venv/bin/python -m eval.harness compare results/a.json results/b.json
    .venv/bin/python -m eval.harness rejudge results/a.json
    .venv/bin/python -m eval.harness label results/a.json --n 50
    .venv/bin/python -m eval.harness kappa labels/a.jsonl
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import uuid
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
LABELS = HERE / "labels"
DATA = HERE / ".data"
JUDGE_CACHE = DATA / "judge-cache.jsonl"

# What the index is built with. A configuration other than the default gets its own
# database, so a variant that re-indexes (another embedding model; later BM25 and
# contextual retrieval) never overwrites another's index.
DEFAULT_INDEX = {"embedding": "fastembed:BAAI/bge-small-en-v1.5"}
INDEX_CONFIG = {
    "embedding": f"{os.environ.get('EMBEDDING_PROVIDER', 'fastembed')}:"
    f"{os.environ.get('EMBEDDING_MODEL', 'BAAI/bge-small-en-v1.5')}",
}
INDEX_KEY = (
    ""
    if INDEX_CONFIG == DEFAULT_INDEX
    else hashlib.sha256(json.dumps(INDEX_CONFIG, sort_keys=True).encode()).hexdigest()[:8]
)
EVAL_DB = os.environ.get(
    "EVAL_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:55432/strata_eval"
    + (f"_{INDEX_KEY}" if INDEX_KEY else ""),
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
from app.retrieval.hybrid import RankingOptions, assemble_window, hybrid_search  # noqa: E402
from app.schemas.common import OrgSettings  # noqa: E402
from eval import grounding  # noqa: E402
from eval.stats import cohen_kappa, paired_bootstrap  # noqa: E402

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
    smoke: bool = False  # in the 15-question set run on every change


def load_questions(
    limit: int | None = None,
    ids: list[str] | None = None,
    path: Path = QUESTIONS,
    subset: str = "all",
) -> list[Question]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    qs = [Question(**{k: r[k] for k in Question.__dataclass_fields__ if k in r}) for r in rows]
    if subset == "smoke":
        qs = [q for q in qs if q.smoke]
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
    marker = DATA / (f"corpus-{INDEX_KEY}.hash" if INDEX_KEY else "corpus.hash")
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


class JudgeCache:
    """Judge replies keyed by (model, system prompt, prompt). Append-only JSONL, so
    concurrent runs at worst judge the same thing twice."""

    def __init__(self, path: Path = JUDGE_CACHE, enabled: bool = True):
        self.path, self.enabled = path, enabled
        self.entries: dict[str, str] = {}
        self.hits = self.misses = 0
        if enabled and path.exists():
            for line in path.read_text().splitlines():
                with contextlib.suppress(ValueError):
                    row = json.loads(line)
                    self.entries[row["key"]] = row["reply"]

    @staticmethod
    def key(system: str, prompt: str) -> str:
        return hashlib.sha256(f"{JUDGE_MODEL}\0{system}\0{prompt}".encode()).hexdigest()

    def get(self, key: str) -> str | None:
        reply = self.entries.get(key) if self.enabled else None
        if reply is None:
            self.misses += 1
        else:
            self.hits += 1
        return reply

    def put(self, key: str, reply: str) -> None:
        if not self.enabled:
            return
        self.entries[key] = reply
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps({"key": key, "reply": reply}) + "\n")


CACHE = JudgeCache()


async def judge_call(system: str, prompt: str, parse, max_tokens: int = 4096):
    """Ask the judge and parse its reply, retrying transient and parse failures. Only
    replies that parse are cached."""
    key = JudgeCache.key(system, prompt)
    cached = CACHE.get(key)
    if cached is not None:
        with contextlib.suppress(Exception):
            return parse(cached)
    for attempt in range(3):
        try:
            reply = await complete_text(
                model=JUDGE_MODEL, system=system, prompt=prompt, max_tokens=max_tokens
            )
            parsed = parse(reply)
            CACHE.put(key, reply)
            return parsed
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(2 + attempt * 3)
    raise RuntimeError("unreachable")


def judge_versions() -> dict[str, str]:
    """Pinned with every run, so results judged differently aren't compared blindly."""
    digest = lambda s: hashlib.sha256(s.encode()).hexdigest()[:10]  # noqa: E731
    return {
        "judge_model": JUDGE_MODEL,
        "facts_prompt": digest(JUDGE_SYSTEM),
        "grounding_prompt": f"{grounding.PROMPT_VERSION}:{digest(grounding.SYSTEM)}",
    }


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
    message_id: str | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)  # ref, title, chunk_id
    grounding: dict[str, Any] | None = None
    usage: dict[str, dict[str, Any]] = field(default_factory=dict)  # per model, as stored
    cost_usd: float | None = None  # None when no model in the run has a price


def substring_hits(answer: str, facts: list[dict[str, Any]]) -> list[bool]:
    norm = re.sub(r"\s+", " ", answer.lower().replace(" ", " ").replace("‑", "-"))
    return [any(p.lower() in norm for p in f.get("any_of", [])) for f in facts]


async def judge(q: Question, answer: str) -> dict[str, Any]:
    facts = "\n".join(f"{i}. {f['fact']}" for i, f in enumerate(q.facts)) or "(none — the "
    facts += "" if q.facts else "knowledge base does not contain the answer)"
    prompt = f"Question: {q.question}\n\nRequired facts:\n{facts}\n\nAnswer:\n{answer}"

    def parse(text: str) -> dict[str, Any]:
        verdict = nvidia.extract_json(text)
        hits = [bool(x) for x in verdict.get("facts", [])][: len(q.facts)]
        hits += [False] * (len(q.facts) - len(hits))
        return {
            "facts": hits,
            "abstained": bool(verdict.get("abstained")),
            "contradiction": bool(verdict.get("contradiction")),
        }

    return await judge_call(JUDGE_SYSTEM, prompt, parse, max_tokens=2048)


DOC_PASSAGE_CHARS = 12_000


async def passages_for(sources: list[dict[str, Any]]) -> dict[int, str]:
    """The text behind each source. A ref stands for a whole document (the agent may
    have read several parts of it), so the judge gets the document; a very long one
    is cut to the hit-centred window the search tools show, plus its opening."""
    out: dict[int, str] = {}
    async with SessionLocal() as db:
        for s in sources:
            if not s.get("chunk_id"):
                out[s["ref"]] = s.get("snippet") or s["title"]
                continue
            doc = (
                await db.execute(
                    text(
                        "SELECT d.content, c.ordinal, c.document_id FROM chunks c "
                        "JOIN documents d ON d.id = c.document_id WHERE c.id = :id"
                    ),
                    {"id": s["chunk_id"]},
                )
            ).first()
            if doc is None:  # re-ingested since the answer
                out[s["ref"]] = s.get("snippet") or s["title"]
            elif len(doc.content) <= DOC_PASSAGE_CHARS:
                out[s["ref"]] = f"{s['title']}\n{doc.content}"
            else:
                parts = dict(
                    (
                        await db.execute(
                            text(
                                "SELECT ordinal, content FROM chunks WHERE document_id = :d "
                                "AND ordinal BETWEEN :o - 1 AND :o + 1"
                            ),
                            {"d": doc.document_id, "o": doc.ordinal},
                        )
                    ).all()
                )
                window = assemble_window(
                    parts.get(doc.ordinal - 1), parts[doc.ordinal], parts.get(doc.ordinal + 1)
                )
                head = doc.content[: DOC_PASSAGE_CHARS - len(window)]
                out[s["ref"]] = f"{s['title']}\n{head}\n…\n{window}"
    return out


async def grade_grounding(q: Question, answer: str, sources: list[dict]) -> dict[str, Any]:
    passages = await passages_for(sources)

    async def complete(system: str, prompt: str) -> dict[str, Any]:
        return await judge_call(system, prompt, grounding.parse_json, max_tokens=8192)

    items = grounding.statements(answer)
    if not items:
        return grounding.Grounding().as_dict()
    verdict = await complete(
        grounding.SYSTEM, grounding.build_prompt(q.question, answer, items, passages)
    )
    return grounding.score(items, verdict, set(passages)).as_dict()


async def score_answer(
    q: Question, result: "AnswerResult", with_grounding: bool = True, with_judge: bool = True
) -> "AnswerResult":
    """Fill in the judged fields of a result from its answer and sources. Without the
    judge only the deterministic fields are filled (and nothing passes) until `rejudge`."""
    answer, status = result.answer, result.status
    by_ref = {s["ref"]: s for s in result.sources}
    result.cited_titles = [by_ref[r]["title"] for r in cited_refs(answer) if r in by_ref]
    gold = set(q.gold_docs)
    result.gold_cited = len(gold & set(result.cited_titles)) / len(gold) if gold else 0.0
    result.fact_hits_substring = substring_hits(answer, q.facts)
    if status == "complete" and with_judge:
        verdict = await judge(q, answer)
        result.fact_hits_judge = verdict["facts"]
        result.abstained = verdict["abstained"]
        result.contradiction = verdict["contradiction"]
        if with_grounding:
            result.grounding = await grade_grounding(q, answer, result.sources)
    if not with_judge:
        result.passed = False  # unknown until `rejudge`
    elif q.category == "unanswerable":
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


async def answer_one(
    q: Question, user, orgs, mode: str, with_grounding: bool = True, with_judge: bool = True
) -> AnswerResult:
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
        usage = msg.usage or {}
    result = AnswerResult(q.id, q.category, answer, status, latency_s=round(latency, 1))
    result.error = error
    result.usage = usage
    costs = [u["cost_usd"] for u in usage.values() if u.get("cost_usd") is not None]
    result.cost_usd = round(sum(costs), 6) if costs else None
    result.message_id = str(msg_id)
    result.sources = [_source_row(s) for s in sources]
    return await score_answer(q, result, with_grounding, with_judge)


def _source_row(s: dict[str, Any]) -> dict[str, Any]:
    keep = ("ref", "title", "chunk_id", "document_id", "origin", "snippet")
    return {k: s.get(k) for k in keep}


async def run_answers(
    questions: list[Question],
    mode: str,
    concurrency: int,
    with_grounding: bool = True,
    with_judge: bool = True,
) -> list[AnswerResult]:
    user, orgs = await _context()
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def one(q: Question) -> AnswerResult:
        nonlocal done
        async with sem:
            try:
                r = await answer_one(q, user, orgs, mode, with_grounding, with_judge)
            except Exception as exc:  # a crashed question is a failed question, not a crash
                r = AnswerResult(q.id, q.category, "", "error", error=repr(exc)[:300])
            done += 1
            verdict = ("PASS" if r.passed else "fail") if with_judge else r.status
            print(f"  [{done}/{len(questions)}] {q.id} {verdict}", flush=True)
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
            **grounding.aggregate(v),
            "errors": sum(r["status"] != "complete" for r in v),
            "s": round(sum(r["latency_s"] for r in v) / len(v), 1),
            "p95_s": _p95([r["latency_s"] for r in v]),
            **_cost(v),
        }
        for c, v in sorted(cats.items())
    }


def _cost(rows: list[dict]) -> dict[str, float]:
    """Mean dollars per answer, when the run's model has a price (older results and
    unpriced models have no cost column)."""
    known = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    return {"usd": round(sum(known) / len(known), 5)} if known else {}


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))] if ordered else 0.0


def print_table(summary: dict[str, dict[str, Any]]) -> None:
    if not summary:
        print("(no rows)")
        return
    cols = list(next(iter(summary.values())))
    width = max(8, *(len(c) + 2 for c in cols))
    print(f"{'category':<18}" + "".join(f"{c:>{width}}" for c in cols))
    for cat, vals in summary.items():
        print(f"{cat:<18}" + "".join(f"{str(vals.get(c, '—')):>{width}}" for c in cols))


def save(kind: str, variant: str, rows: list, summary: dict, extra: dict) -> Path:
    RESULTS.mkdir(exist_ok=True)
    # Model ids like "nvidia/nemotron-…" would otherwise become directories.
    variant = re.sub(r"[^A-Za-z0-9._-]+", "_", variant)
    path = RESULTS / f"{kind}-{variant}.json"
    if kind == "answers":
        extra = {**judge_versions(), **extra}
    path.write_text(
        json.dumps(
            {"kind": kind, "variant": variant, "corpus": corpus_hash(), "index": INDEX_CONFIG,
             **extra,
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
    if len(runs) == 2 and metric == "pass":
        paired_report(*runs)


def paired_report(a: dict, b: dict) -> None:
    """Paired bootstrap of pass rate (overall and per category) on the questions both
    runs answered: is the difference bigger than resampling noise?"""
    for run in (a, b):
        versions = {k: run.get(k) for k in ("judge_model", "facts_prompt")}
        if versions != {k: a.get(k) for k in versions}:
            print("! the runs were judged with different judges or prompts; rejudge one")
    rows_a = {r["id"]: r for r in a["rows"]}
    rows_b = {r["id"]: r for r in b["rows"]}
    shared = sorted(rows_a.keys() & rows_b.keys())
    groups: dict[str, list[str]] = {"ALL": shared}
    for qid in shared:
        groups.setdefault(rows_a[qid]["category"], []).append(qid)
    print(f"\nPaired bootstrap, {b['variant']} minus {a['variant']} (pass rate, 95% CI):")
    for cat, ids in sorted(groups.items()):
        res = paired_bootstrap(
            [float(rows_a[i]["passed"]) for i in ids], [float(rows_b[i]["passed"]) for i in ids]
        )
        changed = sum(rows_a[i]["passed"] != rows_b[i]["passed"] for i in ids)
        verdict = "better" if res["lo"] > 0 else "worse" if res["hi"] < 0 else "no clear change"
        print(
            f"  {cat:<16} n={res['n']:<4} {res['diff']:+.3f} [{res['lo']:+.3f}, {res['hi']:+.3f}]"
            f"  {changed} changed  → {verdict}"
        )


# ── re-scoring and judge reliability ─────────────────────────────────────────
async def rejudge(run: dict, source_name: str, with_grounding: bool) -> Path:
    """Re-score a saved answers run with the current judges, without re-answering
    (free when the verdicts are cached). Rows from runs before sources were saved are
    looked up by message id; older rows get facts only."""
    questions = {q.id: q for q in load_questions()}
    fields = AnswerResult.__dataclass_fields__
    sem = asyncio.Semaphore(3)

    async def one(row: dict) -> dict:
        q = questions.get(row["id"])
        if q is None:
            return row
        result = AnswerResult(**{k: v for k, v in row.items() if k in fields})
        if not result.sources and result.message_id:
            async with SessionLocal() as db:
                msg = await db.get(Message, uuid.UUID(result.message_id))
                result.sources = [_source_row(s) for s in (msg.sources if msg else [])]
        async with sem:
            await score_answer(q, result, with_grounding and bool(result.sources))
        return result.__dict__

    rows = list(await asyncio.gather(*(one(r) for r in run["rows"])))
    summary = summarize_answers(rows)
    print_table(summary)
    extra = {k: v for k, v in run.items() if k not in ("kind", "variant", "summary", "rows")}
    extra.update(judge_versions(), rejudged_from=source_name, judged=True)
    return save("answers", f"{run['variant']}-rejudged", rows, summary, extra)


def _balanced(pool: list[dict], n: int, rng: random.Random) -> list[dict]:
    """Up to n items, alternating judge-yes and judge-no so κ isn't dominated by the
    common verdict."""
    yes = [x for x in pool if x["judge"]]
    no = [x for x in pool if not x["judge"]]
    rng.shuffle(yes)
    rng.shuffle(no)
    out = []
    while len(out) < n and (yes or no):
        for side in (no, yes):
            if side and len(out) < n:
                out.append(side.pop())
    return out


async def label_items(run: dict, n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    questions = {q.id: q for q in load_questions()}
    facts, claims = [], []
    for row in run["rows"]:
        q = questions.get(row["id"])
        if q is None or row["status"] != "complete":
            continue
        if q.category == "unanswerable":
            facts.append(
                {"key": f"{q.id}:abstain", "kind": "abstain", "qid": q.id,
                 "question": q.question, "answer": row["answer"],
                 "ask": "Does the answer say the information isn't available "
                        "(without making one up)?",
                 "judge": row["abstained"] and not row["contradiction"]}
            )  # fmt: skip
        for i, f in enumerate(q.facts):
            if i < len(row["fact_hits_judge"]):
                facts.append(
                    {"key": f"{q.id}:fact{i}", "kind": "fact", "qid": q.id,
                     "question": q.question, "answer": row["answer"],
                     "ask": f"Does the answer clearly state: {f['fact']}?",
                     "judge": row["fact_hits_judge"][i]}
                )  # fmt: skip
        for j, lab in enumerate((row.get("grounding") or {}).get("labels", [])):
            if lab["claim"] and lab["refs"]:
                claims.append(
                    {"key": f"{q.id}:claim{j}", "kind": "claim", "qid": q.id,
                     "statement": lab["text"], "refs": lab["refs"],
                     "sources": row["sources"],
                     "ask": "Do the cited passages, together, fully support this statement?",
                     "judge": lab["cited_support"]}
                )  # fmt: skip
    n_claims = min(len(claims), n * 2 // 5)
    picked = _balanced(facts, n - n_claims, rng) + _balanced(claims, n_claims, rng)
    rng.shuffle(picked)
    for item in picked:
        if item["kind"] == "claim":
            texts = await passages_for([s for s in item.pop("sources") if s["ref"] in item["refs"]])
            item["passages"] = {str(k): v for k, v in texts.items()}
    return picked


def label(path: Path, n: int, seed: int) -> Path:
    """Walk through n judge verdicts without showing them, record the human's call,
    then report agreement. Resumable: labelled items are skipped."""
    run = json.loads(path.read_text())
    out = LABELS / f"{path.stem}.jsonl"
    done = {}
    if out.exists():
        done = {json.loads(line)["key"] for line in out.read_text().splitlines() if line.strip()}
    items = [i for i in asyncio.run(label_items(run, n, seed)) if i["key"] not in done]
    LABELS.mkdir(exist_ok=True)
    print(f"{len(done)} labelled already, {len(items)} to go. y = yes, n = no, s = skip, q = quit")
    for k, item in enumerate(items, 1):
        print("\n" + "─" * 78 + f"\n[{k}/{len(items)}] {item['qid']} · {item['kind']}")
        if item["kind"] == "claim":
            for ref, text_ in item["passages"].items():
                print(f"\n[{ref}] {text_}")
            print(f"\nStatement: {item['statement']}  (cites {item['refs']})")
        else:
            print(f"Question: {item['question']}\n\nAnswer:\n{item['answer']}")
        answer = ""
        while answer not in ("y", "n", "s", "q"):
            answer = input(f"\n{item['ask']} [y/n/s/q] ").strip().lower()[:1]
        if answer == "q":
            break
        if answer == "s":
            continue
        record = {k_: v for k_, v in item.items() if k_ not in ("passages", "answer")}
        with out.open("a") as f:
            f.write(json.dumps({**record, "human": answer == "y"}) + "\n")
    print()
    kappa(out)
    return out


def kappa(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    groups: dict[str, list[dict]] = {"ALL": rows}
    for r in rows:
        groups.setdefault(r["kind"], []).append(r)
    print(f"Judge vs human ({path.name}); target κ ≥ 0.7")
    for name, rs in sorted(groups.items()):
        agree = sum(r["judge"] == r["human"] for r in rs) / len(rs)
        k = cohen_kappa([r["judge"] for r in rs], [r["human"] for r in rs])
        print(f"  {name:<8} n={len(rs):<4} agreement {agree:.0%}  κ {k}")


async def main_async(args) -> None:
    await ingest(force=args.reingest)
    questions = load_questions(
        args.limit, args.ids.split(",") if args.ids else None, Path(args.questions), args.set
    )
    overrides = await apply_variant(args.variant, args.chat_model)
    if args.cmd == "retrieval":
        rows = [r.__dict__ for r in await run_retrieval(questions)]
        summary = summarize_retrieval(rows)
        print_table(summary)
        print("saved", save("retrieval", args.variant, rows, summary, {"settings": overrides}))
    else:
        answered = await run_answers(
            questions, args.mode, args.concurrency, not args.no_grounding, not args.no_judge
        )
        rows = [r.__dict__ for r in answered]
        summary = summarize_answers(rows)
        print_table(summary)
        print(f"judge cache: {CACHE.hits} hits, {CACHE.misses} misses")
        name = (
            (f"{args.name}" if args.name else f"{args.variant}-{args.mode}")
            + (f"-{args.chat_model.split(':')[-1]}" if args.chat_model else "")
            + ("-smoke" if args.set == "smoke" else "")
        )
        extra = {"settings": overrides, "judged": not args.no_judge}
        print("saved", save("answers", name, rows, summary, extra))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "cmd",
        choices=[
            "retrieval", "answers", "compare", "ingest", "build-graph",
            "rejudge", "label", "kappa",
        ],
    )  # fmt: skip
    p.add_argument("paths", nargs="*")
    p.add_argument("--variant", default="rerank", choices=sorted(VARIANTS))
    p.add_argument("--mode", default="quick", choices=["quick", "deep"])
    p.add_argument("--limit", type=int)
    p.add_argument("--ids")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--reingest", action="store_true")
    p.add_argument("--questions", default=str(QUESTIONS))
    p.add_argument("--set", default="all", choices=["all", "smoke"], help="question subset")
    p.add_argument("--name", help="answers: result name (default: <variant>-<mode>)")
    p.add_argument("--no-grounding", action="store_true", help="skip faithfulness/citations")
    p.add_argument("--no-cache", action="store_true", help="don't reuse cached verdicts")
    p.add_argument(
        "--no-judge",
        action="store_true",
        help="answers: answer only; score later with `rejudge` (for a model you can use now)",
    )
    p.add_argument("--n", type=int, default=50, help="label: items to label")
    p.add_argument("--seed", type=int, default=0, help="label: sampling seed")
    p.add_argument("--model", help="build-graph: extraction model (default: the org's)")
    p.add_argument(
        "--chat-model", help="answers: the org model to chat with, e.g. claude-haiku-5-5"
    )
    args = p.parse_args()
    if args.no_cache:
        CACHE.enabled = False
    if args.cmd == "compare":
        compare(args.paths)
        return
    if args.cmd == "kappa":
        for path in args.paths:
            kappa(Path(path))
        return
    ensure_database()
    if args.cmd == "rejudge":
        for path in args.paths:
            run = json.loads(Path(path).read_text())
            saved = asyncio.run(rejudge(run, Path(path).name, not args.no_grounding))
            print("saved", saved)
        return
    if args.cmd == "label":
        label(Path(args.paths[0]), args.n, args.seed)
        return
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
