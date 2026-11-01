"""Re-chunk documents indexed by an older chunker: before the chunk size cap (audit C2),
or before a CSV's rows shared passages (D82).

Chunks longer than the cap were embedded only up to ~512 tokens, so their tails are
unsearchable. A CSV indexed one chunk per row costs an embedding (and any context or
graph call) per row. This rebuilds just those documents from their stored Markdown (no
connector fetch). Their knowledge-graph facts go with the old chunks, so they're
marked for re-extraction (which costs model calls in orgs with the graph on).

    python -m app.ingestion.rechunk            # report only
    python -m app.ingestion.rechunk --apply    # rebuild affected documents
"""

import argparse
import asyncio

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.ingestion.chunker import MAX_CHUNK_CHARS, chunk_markdown
from app.ingestion.pipeline import write_chunks
from app.models import Chunk, Document


async def oversized_documents(db) -> list[Document]:
    ids = select(Chunk.document_id).where(func.length(Chunk.content) > MAX_CHUNK_CHARS)
    return list((await db.execute(select(Document).where(Document.id.in_(ids)))).scalars())


async def row_chunked_documents(db) -> list[Document]:
    """Documents with rows in chunks of their own that the chunker now packs together."""
    settings = get_settings()
    ids = (
        select(Chunk.document_id)
        .where(Chunk.heading.regexp_match(r"(^| › )Row [0-9]+$"))
        .group_by(Chunk.document_id)
        .having(func.count() > 1)
    )
    out = []
    for doc in (await db.execute(select(Document).where(Document.id.in_(ids)))).scalars():
        stored = await db.execute(
            select(Chunk.heading).where(Chunk.document_id == doc.id).order_by(Chunk.ordinal)
        )
        drafts = chunk_markdown(
            doc.content,
            title=doc.title,
            target_words=settings.chunk_words,
            overlap_words=settings.chunk_overlap_words,
        )
        if [d.heading for d in drafts] != list(stored.scalars()):
            out.append(doc)
    return out


async def main(apply: bool) -> None:
    async with SessionLocal() as db:
        oversized = await oversized_documents(db)
        rows = [d for d in await row_chunked_documents(db) if d not in oversized]
        docs = oversized + rows
        print(
            f"{len(docs)} document(s) to rebuild: {len(oversized)} with chunks over "
            f"{MAX_CHUNK_CHARS} characters, {len(rows)} with a chunk per table row"
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
