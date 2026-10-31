"""Job handlers."""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import or_, select

from app.connectors.base import ConnectorError
from app.core.config import get_settings
from app.graph.extract import extract_document, prune_orphans
from app.ingestion.contextual import contextualize_source
from app.ingestion.pipeline import sync_source
from app.models import Document, Job, Organization, Source
from app.records.jobs import enqueue_for_collection, handle_extract_records
from app.schemas.common import OrgSettings
from app.slackbot.answer import handle_slack_answer
from app.worker.queue import enqueue_contextualize, enqueue_graph

log = logging.getLogger("strata.worker")


async def handle_sync_source(db, job: Job) -> None:
    source = await db.get(Source, uuid.UUID(job.payload["source_id"]))
    if source is None:
        return  # deleted while queued
    source.status = "syncing"
    source.error = None
    source.progress = {"done": 0, "total": None, "message": "Starting"}
    await db.commit()
    full = bool(job.payload.get("full", True))
    try:
        stats = await sync_source(db, source, full=full)
    except ConnectorError as exc:
        source.status = "error"
        source.error = str(exc)
        source.progress = None
        await db.commit()
        return  # a connector error is the user's to fix; retrying won't help
    except Exception as exc:
        source.status = "error"
        source.error = f"Sync failed: {exc}"
        source.progress = None
        await db.commit()
        raise
    source.status = "ready"
    source.last_synced_at = datetime.now(UTC)
    if full:
        source.last_full_synced_at = source.last_synced_at
    source.progress = None
    await db.commit()
    log.info("Synced source %s (%s): %s", source.name, source.id, stats)
    if await enqueue_for_collection(db, source.collection_id):  # records follow the documents
        await db.commit()
    settings = get_settings()
    if settings.contextual_retrieval and "anthropic" in settings.llm_providers:
        await enqueue_contextualize(db, source)
        await db.commit()
    org = await db.get(Organization, source.org_id)
    if OrgSettings(**(org.settings or {})).knowledge_graph and settings.llm_configured:
        await enqueue_graph(db, source)
        await db.commit()


async def handle_extract_graph(db, job: Job) -> None:
    """Extract the knowledge graph for every document of a source whose content changed
    since its last extraction. Each document commits on its own, so a rate-limited or
    crashed job resumes where it stopped when it's retried."""
    source = await db.get(Source, uuid.UUID(job.payload["source_id"]))
    if source is None:
        return
    org = await db.get(Organization, source.org_id)
    model = get_settings().graph_extraction_model or OrgSettings(**(org.settings or {})).model
    pending = (
        (
            await db.execute(
                select(Document)
                .where(
                    Document.source_id == source.id,
                    or_(
                        Document.graph_hash.is_(None), Document.graph_hash != Document.content_hash
                    ),
                )
                .order_by(Document.title)
            )
        )
        .scalars()
        .all()
    )
    failed = 0
    for doc in pending:
        try:
            stats = await extract_document(db, doc, model)
            await db.commit()
            log.info("Graph: %s → %s", doc.title, stats)
        except Exception as exc:
            await db.rollback()
            failed += 1
            log.warning("Graph extraction failed for %s: %s", doc.title, exc)
    await prune_orphans(db, source.org_id)
    await db.commit()
    if failed:
        # Retried by the queue (with backoff); finished documents aren't redone.
        raise RuntimeError(f"Graph extraction failed for {failed} of {len(pending)} documents")


async def handle_contextualize(db, job: Job) -> None:
    """Contextual Retrieval for a source's new or changed documents (each commits on
    its own, so a retried job picks up where it stopped)."""
    source = await db.get(Source, uuid.UUID(job.payload["source_id"]))
    if source is None:
        return
    stats = await contextualize_source(db, source.id)
    log.info("Contextualized %s: %s", source.name, stats)
    if stats["failed"]:
        raise RuntimeError(f"Contextualizing failed for {stats['failed']} documents")


HANDLERS = {
    "sync_source": handle_sync_source,
    "extract_graph": handle_extract_graph,
    "contextualize": handle_contextualize,
    "extract_records": handle_extract_records,
    "slack_answer": handle_slack_answer,
}
