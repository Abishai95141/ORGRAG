"""The `extract_records` job: extract one schema's records in the worker."""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, RecordSchema
from app.records.extract import extract_schema

log = logging.getLogger("strata.records")


async def enqueue_extraction(db: AsyncSession, schema: RecordSchema, *, force: bool = False):
    """Queue extraction for a schema unless one is already waiting. Caller commits."""
    pending = (
        await db.execute(
            select(Job).where(
                Job.kind == "extract_records",
                Job.status == "queued",
                Job.payload["schema_id"].astext == str(schema.id),
            )
        )
    ).scalar_one_or_none()
    if pending is None:
        db.add(
            Job(
                org_id=schema.org_id,
                kind="extract_records",
                payload={"schema_id": str(schema.id), "force": force},
            )
        )
    elif force and not pending.payload.get("force"):
        pending.payload = {**pending.payload, "force": True}


async def enqueue_for_collection(db: AsyncSession, collection_id: uuid.UUID) -> int:
    """After a sync: re-extract the collection's active schemas (unchanged documents are
    skipped, so this is cheap). Caller commits."""
    schemas = (
        await db.execute(
            select(RecordSchema).where(
                RecordSchema.collection_id == collection_id, RecordSchema.status == "active"
            )
        )
    ).scalars()
    n = 0
    for schema in schemas:
        await enqueue_extraction(db, schema)
        n += 1
    return n


async def handle_extract_records(db: AsyncSession, job: Job) -> None:
    schema = await db.get(RecordSchema, uuid.UUID(job.payload["schema_id"]))
    if schema is None or schema.status != "active":
        return  # deleted or deactivated while queued
    stats = await extract_schema(db, schema, force=bool(job.payload.get("force")))
    log.info("Records %s: %s", schema.name, stats)
    if stats.get("failed"):
        raise RuntimeError(f"{stats['failed']} documents failed for {schema.name}")
