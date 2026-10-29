"""Source sync: connector → parse → hash check → chunk → embed → replace chunks.

Each document is committed on its own so a long sync shows live progress and a
failure halfway through keeps everything indexed so far."""

import hashlib
import logging
import time
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import RawDocument
from app.connectors.registry import get_connector_class
from app.core.config import get_settings
from app.embeddings import get_embedder
from app.ingestion.chunker import chunk_markdown, embedding_text, estimate_tokens
from app.ingestion.parsers import parse
from app.models import Chunk, Document, Source

log = logging.getLogger("strata.ingestion")


async def index_document(db: AsyncSession, source: Source, raw: RawDocument) -> str:
    """Upsert one document and its chunks. Returns 'created' | 'updated' | 'unchanged' | 'empty'."""
    content_hash = hashlib.sha256(raw.data).hexdigest()
    existing = (
        await db.execute(
            select(Document).where(
                Document.source_id == source.id, Document.external_id == raw.external_id
            )
        )
    ).scalar_one_or_none()
    if existing and existing.content_hash == content_hash:
        return "unchanged"

    markdown, detected_title = parse(raw.data, raw.mime)
    markdown = markdown.strip()
    if not markdown:
        return "empty"
    title = (detected_title or raw.title or raw.external_id).strip()[:500]

    settings = get_settings()
    drafts = chunk_markdown(
        markdown,
        title=title,
        target_words=settings.chunk_words,
        overlap_words=settings.chunk_overlap_words,
    )
    vectors = await get_embedder().embed_documents(
        [embedding_text(title, d.heading, d.content) for d in drafts]
    )

    if existing is None:
        doc = Document(
            org_id=source.org_id,
            source_id=source.id,
            collection_id=source.collection_id,
            external_id=raw.external_id,
            title=title,
            content_hash=content_hash,
        )
        db.add(doc)
        outcome = "created"
    else:
        doc = existing
        await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        outcome = "updated"
    doc.title = title
    doc.url = raw.url
    doc.mime = raw.mime
    doc.content = markdown
    doc.content_hash = content_hash
    doc.meta = raw.metadata
    doc.token_count = estimate_tokens(markdown)
    doc.updated_at = datetime.now(UTC)
    await db.flush()

    db.add_all(
        Chunk(
            org_id=source.org_id,
            document_id=doc.id,
            collection_id=source.collection_id,
            ordinal=d.ordinal,
            heading=d.heading,
            content=d.content,
            token_count=estimate_tokens(d.content),
            embedding=vec,
        )
        for d, vec in zip(drafts, vectors, strict=True)
    )
    return outcome


async def sync_source(db: AsyncSession, source: Source) -> dict[str, int]:
    """Run a full sync for a source. Caller owns the status transitions' commit."""
    connector_cls = get_connector_class(source.kind)
    config = {**source.config, "_source_id": str(source.id)}
    connector = connector_cls(config, source.secrets or {})

    stats = {"created": 0, "updated": 0, "unchanged": 0, "empty": 0, "removed": 0}
    seen: set[str] = set()
    last_flush = 0.0

    async def progress(done: int, total: int | None, message: str | None) -> None:
        nonlocal last_flush
        source.progress = {"done": done, "total": total, "message": message}
        if time.monotonic() - last_flush > 1.0:
            last_flush = time.monotonic()
            await db.commit()

    async for raw in connector.iter_documents(progress):
        seen.add(raw.external_id)
        try:
            async with db.begin_nested():
                outcome = await index_document(db, source, raw)
        except Exception as exc:  # one bad file must not fail the whole sync
            log.warning("Failed to index %s: %s", raw.external_id, exc)
            continue
        stats[outcome] += 1
        await db.commit()

    # Anything the connector no longer yields was deleted upstream.
    stale = (
        (
            await db.execute(
                select(Document.id).where(
                    Document.source_id == source.id, Document.external_id.not_in(seen or {""})
                )
            )
        )
        .scalars()
        .all()
    )
    if stale:
        await db.execute(delete(Document).where(Document.id.in_(stale)))
        stats["removed"] = len(stale)
    return stats
