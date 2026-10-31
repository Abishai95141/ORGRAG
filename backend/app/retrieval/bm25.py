"""BM25 keyword ranking with the pg_textsearch extension, when it's installed.

Keyword search reads the top chunks straight from the BM25 index in score order, so
its cost stays flat as matches grow (`ts_rank_cd` scores every matching row). The
`tsv` match is deliberately not used here: with it, Postgres filters through the GIN
index and sorts every match, never using the BM25 index. Without the extension or its
index, ranking falls back to `ts_rank_cd`, so a plain pgvector Postgres keeps working.

BM25 has no field weights, so the indexed text is heading + content + context. Its
term statistics span the whole index, across orgs: that affects ordering only,
never which chunks a search can see (tenant filtering stays in SQL).

    python -m app.retrieval.bm25   # create the index after installing the extension
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.config import get_settings

log = logging.getLogger(__name__)

INDEX = "ix_chunks_bm25"
# Queries must repeat this exact expression for the index to be used.
EXPRESSION = "(heading || ' ' || content || ' ' || context)"
# A chunk's BM25 score for :qtext (negative; lower is better), for a table alias.
SCORE = (
    "({alias}heading || ' ' || {alias}content || ' ' || {alias}context)"
    f" <@> to_bm25query(:qtext, '{INDEX}')"
)
CREATE_INDEX = (
    f"CREATE INDEX IF NOT EXISTS {INDEX} ON chunks USING bm25 ({EXPRESSION}) "
    "WITH (text_config='english')"
)

_available: bool | None = None


async def available(db: AsyncSession) -> bool:
    """Whether BM25 ranking is on: allowed by KEYWORD_RANKING and the index exists.
    Looked up once per process."""
    global _available
    setting = get_settings().keyword_ranking
    if setting == "ts_rank":
        return False
    if _available is None:
        found = await db.scalar(
            text(
                "SELECT 1 FROM pg_class c JOIN pg_am a ON a.oid = c.relam "
                "WHERE c.relname = :name AND a.amname = 'bm25'"
            ),
            {"name": INDEX},
        )
        _available = bool(found)
        if not _available and setting == "bm25":
            log.warning("KEYWORD_RANKING=bm25 but %s is missing; using ts_rank_cd", INDEX)
    return _available


def reset() -> None:
    """Forget the cached lookup (after creating or dropping the index)."""
    global _available
    _available = None


async def extension_ready(conn: AsyncConnection) -> bool:
    """pg_textsearch is installed on the server and preloaded (required to use it)."""
    installed = await conn.scalar(
        text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_textsearch'")
    )
    preload = await conn.scalar(text("SHOW shared_preload_libraries")) or ""
    return bool(installed) and "pg_textsearch" in preload


async def create_index(conn: AsyncConnection) -> bool:
    """Create the extension and index if the server supports them. Parallel builds are
    off: upstream issue #571 reports parallel builds that silently omit rows."""
    if not await extension_ready(conn):
        return False
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_textsearch"))
    await conn.execute(text("SET LOCAL max_parallel_maintenance_workers = 0"))
    await conn.execute(text(CREATE_INDEX))
    reset()
    return True


async def _main() -> None:
    from app.core.db import engine

    async with engine.begin() as conn:
        created = await create_index(conn)
    print(
        f"{INDEX} is ready"
        if created
        else "pg_textsearch isn't installed and preloaded on this server; nothing done"
    )


if __name__ == "__main__":
    asyncio.run(_main())
