"""Job handlers."""

import logging
import uuid
from datetime import UTC, datetime

from app.connectors.base import ConnectorError
from app.ingestion.pipeline import sync_source
from app.models import Job, Source

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


HANDLERS = {"sync_source": handle_sync_source}
