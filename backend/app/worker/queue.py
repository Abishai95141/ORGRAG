"""Postgres-backed job queue. Producers insert rows; workers claim with
FOR UPDATE SKIP LOCKED so any number of workers can run safely.

A running job bumps `heartbeat_at` every `HEARTBEAT_SECONDS`; only jobs whose
heartbeat stopped (a dead worker) are re-queued, so a long sync is never run twice.
Jobs for the same source also take a Postgres advisory lock on a dedicated
connection, so a sync and a graph extraction (or two syncs) never overlap; the lock
goes away with the connection if the worker dies."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, engine
from app.models import Job, Source

MAX_ATTEMPTS = 3
HEARTBEAT_SECONDS = 30
STALE_AFTER = timedelta(minutes=3)
LOCKED_RETRY = timedelta(seconds=30)


async def enqueue_sync(db: AsyncSession, source: Source, *, full: bool = True) -> None:
    """Queue a sync unless one is already waiting (a waiting incremental sync is upgraded
    when a full one is requested). Caller commits."""
    pending = (
        await db.execute(
            select(Job).where(
                Job.kind == "sync_source",
                Job.status == "queued",
                Job.payload["source_id"].astext == str(source.id),
            )
        )
    ).scalar_one_or_none()
    source.status = "queued"
    source.error = None
    source.progress = None
    payload = {"source_id": str(source.id), "full": full}
    if pending is None:
        db.add(Job(org_id=source.org_id, kind="sync_source", payload=payload))
    elif full and not pending.payload.get("full", True):
        pending.payload = payload


async def enqueue_graph(db: AsyncSession, source: Source) -> None:
    """Queue knowledge-graph extraction for a source unless one is already waiting."""
    pending = (
        await db.execute(
            select(Job.id).where(
                Job.kind == "extract_graph",
                Job.status == "queued",
                Job.payload["source_id"].astext == str(source.id),
            )
        )
    ).first()
    if pending is None:
        db.add(
            Job(org_id=source.org_id, kind="extract_graph", payload={"source_id": str(source.id)})
        )


async def enqueue_contextualize(db: AsyncSession, source: Source) -> None:
    """Queue Contextual Retrieval for a source unless one is already waiting."""
    pending = (
        await db.execute(
            select(Job.id).where(
                Job.kind == "contextualize",
                Job.status == "queued",
                Job.payload["source_id"].astext == str(source.id),
            )
        )
    ).first()
    if pending is None:
        db.add(
            Job(org_id=source.org_id, kind="contextualize", payload={"source_id": str(source.id)})
        )


_CLAIM = text(
    """
    UPDATE jobs SET status = 'running', started_at = now(), heartbeat_at = now(),
                    attempts = attempts + 1
    WHERE id = (
        SELECT id FROM jobs
        WHERE status = 'queued' AND run_after <= now()
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id
    """
)


async def claim_next(db: AsyncSession) -> Job | None:
    job_id = (await db.execute(_CLAIM)).scalar_one_or_none()
    await db.commit()
    return await db.get(Job, job_id) if job_id else None


async def finish(db: AsyncSession, job: Job, error: str | None = None) -> None:
    job.finished_at = datetime.now(UTC)
    if error is None:
        job.status = "done"
        job.error = None
    elif job.attempts < MAX_ATTEMPTS:
        job.status = "queued"
        job.error = error
        job.run_after = datetime.now(UTC) + timedelta(seconds=30 * job.attempts)
    else:
        job.status = "failed"
        job.error = error
    await db.commit()


async def requeue_stale(db: AsyncSession, stale_after: timedelta = STALE_AFTER) -> int:
    """Jobs whose worker died (no heartbeat for `stale_after`) go back to the queue."""
    result = await db.execute(
        text(
            "UPDATE jobs SET status = 'queued' WHERE status = 'running' "
            "AND coalesce(heartbeat_at, started_at) < :cutoff"
        ),
        {"cutoff": datetime.now(UTC) - stale_after},
    )
    await db.commit()
    return result.rowcount or 0


async def heartbeat(job_id: uuid.UUID) -> None:
    """Runs alongside a job until cancelled, in its own short sessions."""
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        with contextlib.suppress(Exception):  # a missed beat is fine; the next one retries
            async with SessionLocal() as db:
                await db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "running")
                    .values(heartbeat_at=datetime.now(UTC))
                )
                await db.commit()


def _lock_key(job: Job) -> str | None:
    source_id = (job.payload or {}).get("source_id")
    return f"source:{source_id}" if source_id else None


@contextlib.asynccontextmanager
async def exclusive(job: Job) -> AsyncIterator[bool]:
    """Hold the job's per-source lock for the duration; yields False if another job
    for the same source holds it."""
    key = _lock_key(job)
    if key is None:
        yield True
        return
    async with engine.connect() as conn:
        got = (
            await conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": key})
        ).scalar()
        await conn.commit()
        try:
            yield bool(got)
        finally:
            if got:
                await conn.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": key})
                await conn.commit()


async def defer(db: AsyncSession, job: Job) -> None:
    """Put a job back without spending an attempt (its source is busy)."""
    job.status = "queued"
    job.attempts = max(0, job.attempts - 1)
    job.run_after = datetime.now(UTC) + LOCKED_RETRY
    await db.commit()
