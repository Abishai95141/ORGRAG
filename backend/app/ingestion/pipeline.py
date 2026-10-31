"""Source sync: connector → parse → hash check → chunk → embed → replace chunks,
then prune documents that no longer exist upstream.

Each document is committed on its own so a long sync shows live progress and a
failure halfway through keeps everything indexed so far.

Full syncs see every document, so anything they don't yield was deleted upstream.
Incremental syncs (connectors with `supports_incremental`) only fetch changes, so
they prune against the connector's cheap `list_ids()` listing when it has one.

Pruning deletes a document's votes, boost, hidden flag and graph facts, so it errs on
the side of keeping: items a connector reports as skipped (transient errors) count as
present, a listing the connector marks incomplete prunes nothing, and neither does a
sync where more than `MAX_PRUNE_ERROR_RATIO` of the items failed."""

import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.base import RawDocument
from app.connectors.registry import get_connector_class
from app.core.config import get_settings
from app.embeddings import get_embedder
from app.ingestion.chunker import chunk_markdown, embedding_text, estimate_tokens
from app.ingestion.dates import document_date
from app.ingestion.docling_parser import ParseError
from app.ingestion.parsers import UnsupportedFileType, parse_document
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
        if raw.updated_at and existing.source_updated_at != raw.updated_at:
            existing.source_updated_at = raw.updated_at
        elif existing.source_updated_at is None and not raw.updated_at:
            existing.source_updated_at = document_date(existing.content, existing.title)
        return "unchanged"

    try:
        parsed = await parse_document(raw.data, raw.mime)
    except (ParseError, UnsupportedFileType) as exc:
        log.warning("Couldn't parse %s: %s", raw.external_id, exc)
        return "failed"
    markdown, detected_title = parsed.markdown, parsed.title
    markdown = markdown.strip()
    if not markdown:
        return "empty"
    title = (detected_title or raw.title or raw.external_id).strip()[:500]

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
        outcome = "updated"
    doc.title = title
    doc.url = raw.url
    doc.mime = raw.mime
    doc.content = markdown
    doc.content_hash = content_hash
    doc.meta = {**(raw.metadata or {}), **({"parse": parsed.info} if parsed.info else {})}
    # The source system's own timestamp when there is one; otherwise the date the
    # document states about itself (uploads have no trustworthy modified time).
    doc.source_updated_at = raw.updated_at or document_date(markdown, title)
    doc.token_count = estimate_tokens(markdown)
    doc.updated_at = datetime.now(UTC)
    await db.flush()
    await write_chunks(db, doc)
    return outcome


async def write_chunks(db: AsyncSession, doc: Document) -> int:
    """(Re)build a document's chunks and embeddings from its stored Markdown. Graph
    mentions go with the old chunks (cascade); `graph_hash` is left to the caller."""
    settings = get_settings()
    drafts = chunk_markdown(
        doc.content,
        title=doc.title,
        target_words=settings.chunk_words,
        overlap_words=settings.chunk_overlap_words,
    )
    vectors = await get_embedder().embed_documents(
        [embedding_text(doc.title, d.heading, d.content) for d in drafts]
    )
    await db.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    doc.context_hash = None  # new chunks have no context yet
    db.add_all(
        Chunk(
            org_id=doc.org_id,
            document_id=doc.id,
            collection_id=doc.collection_id,
            ordinal=d.ordinal,
            heading=d.heading,
            content=d.content,
            token_count=estimate_tokens(d.content),
            page=d.page,
            embedding=vec,
        )
        for d, vec in zip(drafts, vectors, strict=True)
    )
    return len(drafts)


INCREMENTAL_OVERLAP = timedelta(minutes=30)
MAX_PRUNE_ERROR_RATIO = 0.1


async def sync_source(db: AsyncSession, source: Source, *, full: bool = True) -> dict[str, int]:
    """Run a sync for a source. Caller owns the status transitions' commit."""
    connector_cls = get_connector_class(source.kind)
    config = {**source.config, "_source_id": str(source.id)}
    connector = connector_cls(config, source.secrets or {})
    since = None
    if not full and connector_cls.supports_incremental and source.last_synced_at:
        # Overlap absorbs clock skew and edits made while the last sync ran.
        since = source.last_synced_at - INCREMENTAL_OVERLAP

    stats = {
        "created": 0, "updated": 0, "unchanged": 0, "empty": 0,
        "failed": 0, "skipped": 0, "removed": 0,
    }  # fmt: skip
    seen: set[str] = set()
    last_flush = 0.0

    async def progress(done: int, total: int | None, message: str | None) -> None:
        nonlocal last_flush
        source.progress = {"done": done, "total": total, "message": message}
        if time.monotonic() - last_flush > 1.0:
            last_flush = time.monotonic()
            await db.commit()

    async for raw in connector.iter_documents(progress, since=since):
        seen.add(raw.external_id)
        try:
            async with db.begin_nested():
                outcome = await index_document(db, source, raw)
        except Exception as exc:  # one bad file must not fail the whole sync
            log.warning("Failed to index %s: %s", raw.external_id, exc)
            stats["failed"] += 1
            continue
        stats[outcome] += 1
        await db.commit()

    stats["skipped"] = len(connector.skipped - seen)
    errors = stats["failed"] + stats["skipped"]
    attempted = len(seen) + stats["skipped"]
    if not connector.complete:
        log.warning("Source %s listing was incomplete; not removing anything", source.id)
        return stats
    if attempted and errors / attempted > MAX_PRUNE_ERROR_RATIO:
        log.warning(
            "Source %s: %d of %d items failed; not removing anything", source.id, errors, attempted
        )
        return stats

    if since is None:
        alive: set[str] | None = seen
    else:
        source.progress = {"done": 0, "total": None, "message": "Checking for deleted items"}
        await db.commit()
        alive = await connector.list_ids()
    if alive is not None:
        stats["removed"] = await prune_documents(db, source, alive | connector.skipped)
    return stats


async def prune_documents(db: AsyncSession, source: Source, alive: set[str]) -> int:
    """Delete this source's documents whose external id isn't in `alive`."""
    existing = (
        await db.execute(
            select(Document.id, Document.external_id).where(Document.source_id == source.id)
        )
    ).all()
    stale = [doc_id for doc_id, external_id in existing if external_id not in alive]
    if stale and not alive:
        # An empty listing from a remote source is far more likely an access problem
        # (revoked share, expired token) than everything being deleted. Keep the index.
        log.warning(
            "Source %s listed no documents; skipping removal of %d documents", source.id, len(stale)
        )
        return 0
    if stale:
        await db.execute(delete(Document).where(Document.id.in_(stale)))
        await db.commit()
    return len(stale)
