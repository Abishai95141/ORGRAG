"""Postgres-backed job queue. Producers insert rows; workers claim with
FOR UPDATE SKIP LOCKED so any number of workers can run safely."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, Source

MAX_ATTEMPTS = 3


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


_CLAIM = text(
    """
    UPDATE jobs SET status = 'running', started_at = now(), attempts = attempts + 1
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


async def requeue_stale(db: AsyncSession, older_than: timedelta = timedelta(minutes=30)) -> int:
    """Jobs left 'running' by a crashed worker go back to the queue."""
    result = await db.execute(
        text("UPDATE jobs SET status = 'queued' WHERE status = 'running' AND started_at < :cutoff"),
        {"cutoff": datetime.now(UTC) - older_than},
    )
    await db.commit()
    return result.rowcount or 0
