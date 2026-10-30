"""Hybrid retrieval: pgvector cosine + Postgres full-text, fused with Reciprocal Rank
Fusion, reranked by a cross-encoder, adjusted for recency, admin boosts and reader
feedback, then expanded with neighbouring chunks into readable sections.

Hidden documents are filtered in SQL, so they never take a candidate slot."""

import asyncio
import logging
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import get_embedder
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

# Recency bias: score × ((1 - w) + w · 0.5^(age / half-life)). "low" barely nudges
# ties; "high" lets a fresh document beat a slightly more relevant stale one.
RECENCY_WEIGHT = {"off": 0.0, "low": 0.1, "medium": 0.2, "high": 0.35}
RECENCY_HALF_LIFE_DAYS = 180
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
    score: float
    updated_at: datetime | None = None


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

_DETAIL_SQL = text(
    """
    SELECT c.id, c.document_id, c.ordinal, c.org_id, c.collection_id, c.heading, c.content,
           d.title, d.url, d.source_id, s.kind, d.boost, d.feedback_up, d.feedback_down,
           coalesce(d.source_updated_at, d.updated_at) AS doc_time
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE c.id IN :ids
    """
).bindparams(bindparam("ids", expanding=True))


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


async def _rerank_scores(queries: list[str], rows) -> dict[uuid.UUID, float] | None:
    """Best cross-encoder probability per chunk across the queries; None if reranking
    is unavailable or fails (callers fall back to fusion scores)."""
    reranker = get_reranker()
    if reranker is None or not rows:
        return None
    passages = [
        f"{r.title}\n{r.heading}\n{r.content}"[:RERANK_PASSAGE_CHARS]
        if r.heading
        else f"{r.title}\n{r.content}"[:RERANK_PASSAGE_CHARS]
        for r in rows
    ]
    try:
        per_query = await asyncio.gather(*(reranker.score(q, passages) for q in queries))
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
    queries = [q.strip() for q in queries if q and q.strip()][:6]
    if not queries or not collection_ids:
        return []
    ranking = ranking or RankingOptions()
    embedder = get_embedder()
    # Iterative HNSW scans keep recall high when the collection filter is selective.
    await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    await db.execute(text("SET LOCAL hnsw.ef_search = 100"))

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
                    _KEYWORD_SQL, {"cids": collection_ids, "qtext": q, "lim": CANDIDATES_PER_LIST}
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
    if base is None:
        best = max(fused.values())
        base = {r.id: fused[r.id] / best for r in rows}
    now = datetime.now(UTC)
    scores = {
        r.id: adjusted_score(
            base[r.id],
            age_days=(now - r.doc_time).total_seconds() / 86400 if r.doc_time else 0.0,
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
        )
        for r in sorted(rows, key=lambda r: scores[r.id], reverse=True)
    ]
    return diversify(ordered, limit, per_doc)


async def neighbour_text(db: AsyncSession, hit: Hit, radius: int = 1) -> str:
    """The hit chunk joined with its neighbours — a readable 'section'."""
    rows = (
        (
            await db.execute(
                text(
                    "SELECT content FROM chunks WHERE document_id = :doc "
                    "AND ordinal BETWEEN :lo AND :hi ORDER BY ordinal"
                ),
                {"doc": hit.document_id, "lo": hit.ordinal - radius, "hi": hit.ordinal + radius},
            )
        )
        .scalars()
        .all()
    )
    return "\n\n".join(rows) if rows else hit.content


def snippet(content: str, max_chars: int = 280) -> str:
    flat = " ".join(content.split())
    if len(flat) <= max_chars:
        return flat
    cut = flat[:max_chars].rsplit(" ", 1)[0]
    return cut.rstrip(",.;:") + "…"
