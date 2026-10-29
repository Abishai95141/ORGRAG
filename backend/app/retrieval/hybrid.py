"""Hybrid retrieval: pgvector cosine + Postgres full-text, fused with Reciprocal Rank
Fusion, then expanded with neighbouring chunks into readable sections."""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import get_embedder

RRF_K = 60
CANDIDATES_PER_LIST = 40
MAX_CHUNKS_PER_DOCUMENT = 2


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


_VECTOR_SQL = text(
    """
    SELECT c.id FROM chunks c
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
    SELECT c.id FROM chunks c, q
    WHERE c.collection_id IN :cids AND c.tsv @@ q.query
    ORDER BY ts_rank_cd(c.tsv, q.query, 32) DESC
    LIMIT :lim
    """
).bindparams(bindparam("cids", expanding=True))

_DETAIL_SQL = text(
    """
    SELECT c.id, c.document_id, c.ordinal, c.org_id, c.collection_id, c.heading, c.content,
           d.title, d.url, d.source_id, s.kind
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    JOIN sources s ON s.id = d.source_id
    WHERE c.id IN :ids
    """
).bindparams(bindparam("ids", expanding=True))


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


async def hybrid_search(
    db: AsyncSession,
    queries: list[str],
    collection_ids: list[uuid.UUID],
    limit: int = 8,
    per_doc: int = MAX_CHUNKS_PER_DOCUMENT,
) -> list[Hit]:
    queries = [q.strip() for q in queries if q and q.strip()][:6]
    if not queries or not collection_ids:
        return []
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
    top_ids = sorted(fused, key=fused.__getitem__, reverse=True)[: limit * 4]
    rows = (await db.execute(_DETAIL_SQL, {"ids": top_ids})).all()
    best = max(fused.values())
    by_id = {
        r.id: Hit(
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
            score=round(fused[r.id] / best, 4),
        )
        for r in rows
    }
    ordered = [by_id[i] for i in top_ids if i in by_id]
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
