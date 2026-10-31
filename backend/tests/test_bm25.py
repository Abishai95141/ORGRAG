"""BM25 keyword ranking (pg_textsearch) and its ts_rank_cd fallback."""

import os
import uuid

import pytest
from sqlalchemy import text

from tests.test_api_integration import _general, _ingest, _org, _signup

pytestmark = pytest.mark.integration


async def _bm25_ready() -> bool:
    from app.core.db import engine
    from app.retrieval import bm25

    async with engine.connect() as conn:
        ready = await bm25.extension_ready(conn)
    if not ready and os.environ.get("REQUIRE_BM25"):  # CI's pg_textsearch leg
        pytest.fail("REQUIRE_BM25 is set but pg_textsearch isn't installed and preloaded")
    return ready


async def _corpus(app):
    admin = await _signup(app, f"bm{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Ranking Co")
    cols = (await admin.get(f"/api/orgs/{org['slug']}/collections")).json()
    collection = _general(org, cols)
    filler = " ".join(["Teams plan quarterly work and review progress every month."] * 30)
    files = {
        # One focused mention of "heron" vs. a single passing mention in a long note.
        "heron.md": "# Project Heron\n\nHeron is the merger codename. Heron closes in March.",
        "notes.md": f"# Planning notes\n\n{filler} The heron sighting was unrelated.",
        "other.md": "# Parking\n\nVisitors park on level two.",
    }
    await _ingest(admin, org["slug"], collection, files)
    return uuid.UUID(collection)


async def _ids(sql, collection, query, limit=40):
    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        rows = await db.execute(sql, {"cids": [collection], "qtext": query, "lim": limit})
        return list(rows.scalars())


async def test_bm25_ranks_the_same_matches_as_the_fallback(app):
    if not await _bm25_ready():
        pytest.skip("pg_textsearch isn't installed and preloaded on this server")
    from app.retrieval import hybrid

    collection = await _corpus(app)
    fallback = await _ids(hybrid._KEYWORD_SQL, collection, "heron codename")
    ranked = await _ids(hybrid._BM25_SQL, collection, "heron codename")
    assert set(ranked) == set(fallback) and len(ranked) == 2  # same candidates
    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        titles = [
            await db.scalar(
                text("SELECT d.title FROM chunks c JOIN documents d ON d.id = c.document_id "
                     "WHERE c.id = :id"),
                {"id": cid},
            )
            for cid in ranked
        ]  # fmt: skip
    assert titles[0] == "Project Heron"


async def test_search_uses_bm25_when_the_index_exists(app, monkeypatch):
    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.retrieval import bm25

    ready = await _bm25_ready()
    async with SessionLocal() as db:
        bm25.reset()
        assert await bm25.available(db) is ready  # "auto"
        monkeypatch.setattr(get_settings(), "keyword_ranking", "ts_rank")
        assert await bm25.available(db) is False  # forced fallback
    bm25.reset()


async def test_hybrid_search_works_in_both_modes(app, monkeypatch):
    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.retrieval import bm25
    from app.retrieval.hybrid import RankingOptions, hybrid_search

    collection = await _corpus(app)
    seen = {}
    for mode in ("ts_rank", "auto"):
        monkeypatch.setattr(get_settings(), "keyword_ranking", mode)
        bm25.reset()
        async with SessionLocal() as db:
            hits = await hybrid_search(
                db, ["heron merger"], [collection], ranking=RankingOptions(rerank=False)
            )
        seen[mode] = hits[0].title
    bm25.reset()
    assert seen == {"ts_rank": "Project Heron", "auto": "Project Heron"}


async def test_the_score_expression_matches_the_index(app):
    """The query must repeat the index expression exactly, or Postgres can only score
    rows one by one instead of reading the top matches from the index. (With a very
    selective collection filter it may still rightly filter first and score the few
    rows left, so this checks the expression, not one plan.)"""
    if not await _bm25_ready():
        pytest.skip("pg_textsearch isn't installed and preloaded on this server")
    from app.core.db import SessionLocal
    from app.retrieval import bm25

    await _corpus(app)
    score = bm25.SCORE.format(alias="c.")
    explain = text(f"EXPLAIN SELECT c.id FROM chunks c ORDER BY {score} LIMIT 5")
    async with SessionLocal() as db:
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join((await db.execute(explain, {"qtext": "heron"})).scalars().all())
    assert f"Index Scan using {bm25.INDEX}" in plan, plan
