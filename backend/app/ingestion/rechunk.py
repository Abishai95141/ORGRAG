"""Re-chunk documents indexed before the chunk size cap (audit C2).

Chunks longer than the cap were embedded only up to ~512 tokens, so their tails are
unsearchable. This rebuilds just those documents from their stored Markdown (no
connector fetch). Their knowledge-graph facts go with the old chunks, so they're
marked for re-extraction (which costs model calls in orgs with the graph on).

    python -m app.ingestion.rechunk            # report only
    python -m app.ingestion.rechunk --apply    # rebuild affected documents
"""

import argparse
import asyncio

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.ingestion.chunker import MAX_CHUNK_CHARS
from app.ingestion.pipeline import write_chunks
from app.models import Chunk, Document


async def oversized_documents(db) -> list[Document]:
    ids = select(Chunk.document_id).where(func.length(Chunk.content) > MAX_CHUNK_CHARS)
    return list((await db.execute(select(Document).where(Document.id.in_(ids)))).scalars())


async def main(apply: bool) -> None:
    async with SessionLocal() as db:
        docs = await oversized_documents(db)
        print(f"{len(docs)} document(s) have chunks over {MAX_CHUNK_CHARS} characters")
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
