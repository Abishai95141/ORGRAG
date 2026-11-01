"""Re-chunk documents indexed by an older chunker: before the chunk size cap (audit C2),
before a CSV's rows shared passages (D82), or while long table rows were cut short (D83).

Chunks longer than the cap were embedded only up to ~512 tokens, so their tails are
unsearchable. A CSV indexed one chunk per row costs an embedding (and any context or
graph call) per row. Table text cut from a passage wasn't indexed at all. This rebuilds
just those documents from their stored Markdown (no connector fetch). Their
knowledge-graph facts go with the old chunks, so they're marked for re-extraction (which
costs model calls in orgs with the graph on).

    python -m app.ingestion.rechunk            # report only
    python -m app.ingestion.rechunk --apply    # rebuild affected documents
"""

import argparse
import asyncio
import re

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.ingestion.chunker import MAX_CHUNK_CHARS, chunk_markdown
from app.ingestion.pipeline import write_chunks
from app.models import Chunk, Document


async def oversized_documents(db) -> list[Document]:
    ids = select(Chunk.document_id).where(func.length(Chunk.content) > MAX_CHUNK_CHARS)
    return list((await db.execute(select(Document).where(Document.id.in_(ids)))).scalars())


_WORD = re.compile(r"\w+")


async def restructured_documents(db) -> list[Document]:
    """Documents the chunker now cuts differently: a CSV's rows a chunk each, or tables
    whose stored passages lack words the chunker now keeps. A table that only loses its
    padding isn't rebuilt (that would cost a graph re-extraction for nothing)."""
    settings = get_settings()
    rows = (
        select(Chunk.document_id)
        .where(Chunk.heading.regexp_match(r"(^| › )Row [0-9]+$"))
        .group_by(Chunk.document_id)
        .having(func.count() > 1)
    )
    tables = select(Chunk.document_id).where(Chunk.content.regexp_match(r"(^|\n)\|"))
    row_ids = set((await db.execute(rows)).scalars())
    out = []
    candidates = select(Document).where(Document.id.in_(rows) | Document.id.in_(tables))
    for doc in (await db.execute(candidates)).scalars():
        stored = (
            await db.execute(
                select(Chunk.heading, Chunk.content)
                .where(Chunk.document_id == doc.id)
                .order_by(Chunk.ordinal)
            )
        ).all()
        drafts = chunk_markdown(
            doc.content,
            title=doc.title,
            target_words=settings.chunk_words,
            overlap_words=settings.chunk_overlap_words,
        )
        repacked = doc.id in row_ids and [d.heading for d in drafts] != [h for h, _ in stored]
        words = {w for _, content in stored for w in _WORD.findall(content)}
        if repacked or any(set(_WORD.findall(d.content)) - words for d in drafts):
            out.append(doc)
    return out


async def main(apply: bool) -> None:
    async with SessionLocal() as db:
        oversized = await oversized_documents(db)
        older = [d for d in await restructured_documents(db) if d not in oversized]
        docs = oversized + older
        print(
            f"{len(docs)} document(s) to rebuild: {len(oversized)} with chunks over "
            f"{MAX_CHUNK_CHARS} characters, {len(older)} with a chunk per CSV row or table "
            "text left out"
        )
        for doc in docs:
            if not apply:
                print(f"  {doc.id}  {doc.title[:70]}")
                continue
            n = await write_chunks(db, doc)
            doc.graph_hash = None
            await db.commit()
            print(f"  rebuilt {doc.title[:60]!r}: {n} chunks")
        if docs and not apply:
            print("Run with --apply to rebuild them.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="rebuild (default: report only)")
    asyncio.run(main(parser.parse_args().apply))
