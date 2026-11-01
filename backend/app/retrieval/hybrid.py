"""Hybrid retrieval: pgvector cosine + Postgres full-text, fused with Reciprocal Rank
Fusion, reranked by a cross-encoder, adjusted for recency, admin boosts and reader
feedback, then expanded with neighbouring chunks into readable sections.

Hidden documents are filtered in SQL, so they never take a candidate slot."""

import asyncio
import logging
import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import telemetry
from app.embeddings import get_embedder
from app.retrieval import bm25
from app.retrieval.rerank import get_reranker, sigmoid

log = logging.getLogger("strata.retrieval")

RRF_K = 60
CANDIDATES_PER_LIST = 40
RERANK_CANDIDATES = 40
MAX_CHUNKS_PER_DOCUMENT = 2
# At most this many chunks of one document enter reranking, so one long, well-titled
# document can't crowd everything else out of the candidate set (as in Onyx).
MAX_CANDIDATES_PER_DOCUMENT = 3
RERANK_PASSAGE_CHARS = 2000
# A list-like passage (a CSV's rows, a table) is shown to the reranker as at most this
# many of its rows: those sharing the query's rarest words (0: the whole passage).
FOCUS_ROWS = 4
MIN_LIST_ROWS = 8  # fewer rows than this is read whole

# Recency bias: score × ((1 - w) + w · 0.5^(age / half-life)). "low" barely nudges
# ties; "high" lets a fresh document beat a slightly more relevant stale one.
RECENCY_WEIGHT = {"off": 0.0, "low": 0.1, "medium": 0.2, "high": 0.35}
RECENCY_HALF_LIFE_DAYS = 180
# Documents with no known date are treated as this old (as Onyx does), rather than
# as new as the moment they were indexed.
UNDATED_AGE_DAYS = 90
BOOST_STEP = 0.15  # admin boost -3..3 → ×0.55..×1.45
FEEDBACK_WEIGHT = 0.25  # net reader votes → at most ×0.75..×1.25


@dataclass
class RankingOptions:
    rerank: bool = True
    recency_bias: str = "low"


@dataclass
class Hit:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    org_id: uuid.UUID
    collection_id: uuid.UUID
    source_id: uuid.UUID
    source_kind: str
    title: str
    url: str | None
    heading: str
    content: str
    score: float  # relative: the best hit of the search is 1.0
    updated_at: datetime | None = None
    page: int | None = None  # where the passage starts, in paginated formats
    # The reranker's probability that the passage answers the query (absolute, before
    # boosts and recency); None when reranking didn't run.
    relevance: float | None = None


def rrf_fuse(rankings: list[list[uuid.UUID]], k: int = RRF_K) -> dict[uuid.UUID, float]:
    """Reciprocal Rank Fusion: score(d) = Σ 1 / (k + rank_i(d)), rank starting at 1."""
    scores: dict[uuid.UUID, float] = defaultdict(float)
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] += 1.0 / (k + rank)
    return dict(scores)


def diversify(ordered: list[Hit], limit: int, per_doc: int = MAX_CHUNKS_PER_DOCUMENT) -> list[Hit]:
    counts: dict[uuid.UUID, int] = defaultdict(int)
    out: list[Hit] = []
    for hit in ordered:
        if counts[hit.document_id] >= per_doc:
            continue
        counts[hit.document_id] += 1
        out.append(hit)
        if len(out) >= limit:
            break
    return out


def adjusted_score(
    base: float,
    *,
    age_days: float,
    recency_bias: str,
    boost: int,
    feedback_up: int,
    feedback_down: int,
) -> float:
    """Relevance × recency × admin boost × reader feedback."""
    weight = RECENCY_WEIGHT.get(recency_bias, 0.0)
    recency = (1 - weight) + weight * 0.5 ** (max(age_days, 0.0) / RECENCY_HALF_LIFE_DAYS)
    boost_factor = 1 + BOOST_STEP * max(-3, min(3, boost))
    feedback = 1 + FEEDBACK_WEIGHT * math.tanh((feedback_up - feedback_down) / 4)
    return base * recency * boost_factor * feedback


_VECTOR_SQL = text(
    """
    SELECT c.id FROM chunks c
    JOIN documents d ON d.id = c.document_id AND NOT d.hidden
    WHERE c.collection_id IN :cids
    ORDER BY c.embedding <=> CAST(:qvec AS vector)
    LIMIT :lim
    """
).bindparams(bindparam("cids", expanding=True))

_KEYWORD_SQL = text(
    """
    WITH q AS (
      SELECT to_tsquery('english', coalesce(string_agg(quote_literal(lexeme), ' | '), ''))
             AS query
      FROM unnest(to_tsvector('english', :qtext))
    )
    SELECT c.id FROM chunks c
    JOIN documents d ON d.id = c.document_id AND NOT d.hidden, q
    WHERE c.collection_id IN :cids AND c.tsv @@ q.query
    ORDER BY ts_rank_cd(c.tsv, q.query, 32) DESC
    LIMIT :lim
    """
).bindparams(bindparam("cids", expanding=True))

# Top chunks by BM25, read from the pg_textsearch index in score order (scores are
# negative: ascending), so the cost doesn't grow with the number of matches. Tenant
# filters are post-filters the index scan keeps satisfying until LIMIT is filled;
# `< 0` drops non-matching chunks on a plan that scores rows without the index. The
# index is named explicitly because prepared statements can't infer it.
_BM25_SQL = text(
    f"""
    SELECT c.id FROM chunks c
    JOIN documents d ON d.id = c.document_id AND NOT d.hidden
    WHERE c.collection_id IN :cids
      AND {bm25.SCORE.format(alias="c.")} < 0
    ORDER BY {bm25.SCORE.format(alias="c.")}
    LIMIT :lim
    """
).bindparams(bindparam("cids", expanding=True))

_DETAIL_SQL = text(
    """
    SELECT c.id, c.document_id, c.ordinal, c.org_id, c.collection_id, c.heading, c.content,
           c.page, d.title, d.url, d.source_id, s.kind, d.boost, d.feedback_up,
           d.feedback_down, d.source_updated_at AS doc_time
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE c.id IN :ids
    """
).bindparams(bindparam("ids", expanding=True))


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


async def _rerank_scores(queries: list[str], rows) -> dict[uuid.UUID, float] | None:
    with telemetry.span("rerank", **{"strata.queries": len(queries), "strata.pairs": len(rows)}):
        return await _rerank(queries, rows)


_TERM = re.compile(r"\w+")
_ROW_BLOCK = re.compile(r"^Row \d+\n")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}")
_STOPWORDS = frozenset(
    [
        "a", "an", "and", "any", "are", "as", "at", "be", "by", "can", "do", "does", "for",
        "from", "has", "have", "how", "i", "in", "is", "it", "its", "list", "of", "on", "or",
        "our", "that", "the", "their", "there", "this", "to", "was", "we", "were", "what",
        "when", "where", "which", "who", "why", "will", "with",
    ]
)  # fmt: skip


def _list_rows(content: str) -> tuple[list[str], list[str], str] | None:
    """A list-like passage as (lead, rows, separator): a CSV's rows packed into a passage,
    or a table's rows under its header. None for anything else."""
    blocks = content.split("\n\n")
    if len(blocks) >= MIN_LIST_ROWS and all(_ROW_BLOCK.match(b) for b in blocks):
        return [], blocks, "\n\n"
    lines = content.splitlines()
    if all(line.lstrip().startswith("|") for line in lines):
        head = 2 if len(lines) > 1 and _TABLE_RULE.match(lines[1].strip()) else 0
        if len(lines) - head >= MIN_LIST_ROWS:
            return lines[:head], lines[head:], "\n"
    return None


def focus(content: str, query: str, keep: int) -> str:
    """What the reranker reads of a passage. A cross-encoder judges a passage as a whole,
    so one matching row among twenty reads as an unrelated list (D84). A list-like
    passage is cut to its `keep` rows that best share the query's words, each word
    weighted by how few of the rows hold it; anything else, or a list where no row holds
    such a word, is read whole."""
    parsed = _list_rows(content) if keep else None
    if parsed is None:
        return content
    lead, rows, sep = parsed
    words = [set(_TERM.findall(row.lower())) for row in rows]
    weight = {}
    for term in set(_TERM.findall(query.lower())) - _STOPWORDS:
        holding = sum(term in w for w in words)
        if 0 < holding < len(rows):
            weight[term] = math.log(len(rows) / holding)
    scores = [sum(weight.get(t, 0.0) for t in w) for w in words]
    best = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: -scores[i])[:keep]
    if not best:
        return content
    return sep.join([*lead, *(rows[i] for i in sorted(best))])


async def _rerank(queries: list[str], rows) -> dict[uuid.UUID, float] | None:
    """Best cross-encoder probability per chunk across the queries; None if reranking
    is unavailable or fails (callers fall back to fusion scores)."""
    reranker = get_reranker()
    if reranker is None or not rows:
        return None

    def passages(query: str) -> list[str]:
        out = []
        for r in rows:
            head = f"{r.title}\n{r.heading}" if r.heading else r.title
            out.append(f"{head}\n{focus(r.content, query, FOCUS_ROWS)}"[:RERANK_PASSAGE_CHARS])
        return out

    try:
        per_query = await asyncio.gather(*(reranker.score(q, passages(q)) for q in queries))
    except Exception as exc:  # reranking is an optimisation; search must still work
        log.warning("Reranking with %s failed, using fusion scores: %s", reranker.name, exc)
        return None
    return {r.id: max(sigmoid(scores[i]) for scores in per_query) for i, r in enumerate(rows)}


async def hybrid_search(
    db: AsyncSession,
    queries: list[str],
    collection_ids: list[uuid.UUID],
    limit: int = 8,
    per_doc: int = MAX_CHUNKS_PER_DOCUMENT,
    ranking: RankingOptions | None = None,
) -> list[Hit]:
    attrs = {
        "strata.queries": len(queries),
        "strata.collections": len(collection_ids),
        "strata.limit": limit,
        "strata.rerank": bool(ranking and ranking.rerank),
    }
    if telemetry.capture_content():
        attrs["langfuse.observation.input"] = "\n".join(queries)
    with telemetry.span("retrieve", "retriever", **attrs) as span:
        hits = await _hybrid_search(db, queries, collection_ids, limit, per_doc, ranking)
        telemetry.set_attrs(span, **{"strata.hits": len(hits)})
        return hits


async def _hybrid_search(
    db: AsyncSession,
    queries: list[str],
    collection_ids: list[uuid.UUID],
    limit: int,
    per_doc: int,
    ranking: RankingOptions | None,
) -> list[Hit]:
    queries = [q.strip() for q in queries if q and q.strip()][:6]
    if not queries or not collection_ids:
        return []
    ranking = ranking or RankingOptions()
    embedder = get_embedder()
    # Iterative HNSW scans keep recall high when the collection filter is selective.
    await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    await db.execute(text("SET LOCAL hnsw.ef_search = 100"))

    keyword_sql = _BM25_SQL if await bm25.available(db) else _KEYWORD_SQL
    rankings: list[list[uuid.UUID]] = []
    for q in queries:
        qvec = await embedder.embed_query(q)
        vec_ids = (
            (
                await db.execute(
                    _VECTOR_SQL,
                    {
                        "cids": collection_ids,
                        "qvec": _vec_literal(qvec),
                        "lim": CANDIDATES_PER_LIST,
                    },
                )
            )
            .scalars()
            .all()
        )
        kw_ids = (
            (
                await db.execute(
                    keyword_sql, {"cids": collection_ids, "qtext": q, "lim": CANDIDATES_PER_LIST}
                )
            )
            .scalars()
            .all()
        )
        rankings.extend([list(vec_ids), list(kw_ids)])

    fused = rrf_fuse(rankings)
    if not fused:
        return []
    want = max(limit * 4, RERANK_CANDIDATES if ranking.rerank else 0)
    pool = sorted(fused, key=fused.__getitem__, reverse=True)[: want * 3]
    pool_rows = {r.id: r for r in (await db.execute(_DETAIL_SQL, {"ids": pool})).all()}
    per_doc_count: dict[uuid.UUID, int] = defaultdict(int)
    rows = []
    for chunk_id in pool:
        r = pool_rows.get(chunk_id)
        if r is None or per_doc_count[r.document_id] >= MAX_CANDIDATES_PER_DOCUMENT:
            continue
        per_doc_count[r.document_id] += 1
        rows.append(r)
        if len(rows) >= want:
            break

    base = await _rerank_scores(queries, rows) if ranking.rerank else None
    reranked = base is not None
    if base is None:
        best = max(fused.values())
        base = {r.id: fused[r.id] / best for r in rows}
    now = datetime.now(UTC)
    scores = {
        r.id: adjusted_score(
            base[r.id],
            age_days=(
                (now - r.doc_time).total_seconds() / 86400 if r.doc_time else UNDATED_AGE_DAYS
            ),
            recency_bias=ranking.recency_bias,
            boost=r.boost,
            feedback_up=r.feedback_up,
            feedback_down=r.feedback_down,
        )
        for r in rows
    }
    top = max(scores.values(), default=1.0) or 1.0
    ordered = [
        Hit(
            chunk_id=r.id,
            document_id=r.document_id,
            ordinal=r.ordinal,
            org_id=r.org_id,
            collection_id=r.collection_id,
            source_id=r.source_id,
            source_kind=r.kind,
            title=r.title,
            url=r.url,
            heading=r.heading,
            content=r.content,
            score=round(scores[r.id] / top, 4),
            updated_at=r.doc_time,
            page=r.page,
            relevance=round(base[r.id], 4) if reranked else None,
        )
        for r in sorted(rows, key=lambda r: scores[r.id], reverse=True)
    ]
    return diversify(ordered, limit, per_doc)


async def hits_for_chunks(db: AsyncSession, chunk_ids: list[uuid.UUID]) -> list[Hit]:
    """Hits for known chunks (e.g. the evidence behind graph facts), in input order.
    Hidden documents are dropped, as in search."""
    if not chunk_ids:
        return []
    rows = {r.id: r for r in (await db.execute(_DETAIL_SQL, {"ids": list(chunk_ids)})).all()}
    hidden = set(
        (
            await db.execute(
                text("SELECT id FROM documents WHERE hidden AND id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": list({r.document_id for r in rows.values()}) or [uuid.uuid4()]},
            )
        ).scalars()
    )
    return [
        Hit(
            chunk_id=r.id,
            document_id=r.document_id,
            ordinal=r.ordinal,
            org_id=r.org_id,
            collection_id=r.collection_id,
            source_id=r.source_id,
            source_kind=r.kind,
            title=r.title,
            url=r.url,
            heading=r.heading,
            content=r.content,
            score=1.0,
            updated_at=r.doc_time,
            page=r.page,
        )
        for cid in chunk_ids
        if (r := rows.get(cid)) is not None and r.document_id not in hidden
    ]


WINDOW_CHARS = 3000  # per search hit shown to the model; a capped chunk is ≤ 2600
_WORD = re.compile(r"\S+")


def _overlap_words(left: str, right: str, limit: int = 120) -> int:
    """How many words at the end of `left` repeat at the start of `right` (the chunker
    carries ~50 words of overlap into each next chunk)."""
    a, b = left.split()[-limit:], right.split()[:limit]
    for k in range(min(len(a), len(b)), 0, -1):
        if a[-k:] == b[:k]:
            return k
    return 0


def _drop_leading_words(text: str, k: int) -> str:
    if k <= 0:
        return text
    for i, m in enumerate(_WORD.finditer(text), 1):
        if i == k:
            return text[m.end() :].lstrip()
    return ""


def _drop_trailing_words(text: str, k: int) -> str:
    if k <= 0:
        return text
    starts = [m.start() for m in _WORD.finditer(text)]
    return text[: starts[-k]].rstrip() if k <= len(starts) else ""


def _head(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(None, 1)[0] if max_chars > 0 else ""
    return f"{cut}…" if cut else ""


def _tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[-max_chars:].split(None, 1)[-1] if max_chars > 0 else ""
    return f"…{cut}" if cut else ""


def assemble_window(
    before: str | None, hit: str, after: str | None, max_chars: int = WINDOW_CHARS
) -> str:
    """The text shown for a search hit: the hit chunk complete, then the start of the
    next chunk and the end of the previous one as budget allows, without repeating the
    chunker's overlap (audit C1: the old window led with the previous chunk and was cut
    before the hit)."""
    hit = hit.strip()
    if len(hit) >= max_chars:
        return _head(hit, max_chars)
    budget = max_chars - len(hit)
    pre = _drop_trailing_words(before, _overlap_words(before, hit)) if before else ""
    post = _drop_leading_words(after, _overlap_words(hit, after)) if after else ""
    pre_budget = min(len(pre), budget // 3)
    post_part = _head(post, budget - pre_budget - 4)
    pre_part = _tail(pre, budget - len(post_part) - 4)
    return "\n\n".join(p for p in (pre_part, hit, post_part) if p)


async def hit_window(db: AsyncSession, hit: Hit, max_chars: int = WINDOW_CHARS) -> str:
    rows = dict(
        (
            await db.execute(
                text(
                    "SELECT ordinal, content FROM chunks WHERE document_id = :doc "
                    "AND ordinal IN (:lo, :hi)"
                ),
                {"doc": hit.document_id, "lo": hit.ordinal - 1, "hi": hit.ordinal + 1},
            )
        ).all()
    )
    return assemble_window(
        rows.get(hit.ordinal - 1), hit.content, rows.get(hit.ordinal + 1), max_chars
    )


def snippet(content: str, max_chars: int = 280) -> str:
    flat = " ".join(content.split())
    if len(flat) <= max_chars:
        return flat
    cut = flat[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"
