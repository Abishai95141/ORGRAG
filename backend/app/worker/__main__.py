"""Worker process: `python -m app.worker`.

Runs N job loops plus a scheduler that re-queues sources whose
sync_interval_minutes has elapsed and closes out chat answers whose run died.
Scheduled syncs are incremental; a full sync (which also removes documents
deleted upstream) runs at least once a day."""

import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.agent.runs import reap_stale
from app.core import telemetry
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.ingestion.verified import expire_due
from app.models import Source
from app.worker.jobs import HANDLERS
from app.worker.queue import (
    claim_next,
    defer,
    enqueue_sync,
    exclusive,
    finish,
    heartbeat,
    requeue_stale,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("strata.worker")

FULL_SYNC_EVERY = timedelta(hours=24)


async def job_loop(n: int, stop: asyncio.Event) -> None:
    poll = get_settings().worker_poll_seconds
    while not stop.is_set():
        async with SessionLocal() as db:
            job = await claim_next(db)
            if job is None:
                pass
            else:
                await run_job(n, db, job)
                continue
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll)


async def run_job(n: int, db, job) -> None:
    handler = HANDLERS.get(job.kind)
    async with exclusive(job) as acquired:
        if not acquired:
            log.info("[loop %d] %s %s waits: its source is busy", n, job.kind, job.id)
            await defer(db, job)
            return
        log.info("[loop %d] running %s %s (attempt %d)", n, job.kind, job.id, job.attempts)
        beat = asyncio.create_task(heartbeat(job.id))
        attrs = {
            "langfuse.trace.name": f"job {job.kind}",
            "strata.job.id": str(job.id),
            "strata.job.attempt": job.attempts,
            **telemetry.org_attrs(job.org_id),
        }
        with telemetry.span(f"job {job.kind}", root=True, **attrs) as span:
            try:
                if handler is None:
                    raise RuntimeError(f"No handler for job kind {job.kind}")
                await handler(db, job)
                await finish(db, job)
            except Exception as exc:
                log.exception("Job %s failed", job.id)
                telemetry.set_attrs(span, **{"strata.job.error": str(exc)[:500]})
                await db.rollback()
                await finish(db, await db.get(type(job), job.id), error=str(exc)[:2000])
            finally:
                beat.cancel()


async def scheduler(stop: asyncio.Event) -> None:
    while not stop.is_set():
        async with SessionLocal() as db:
            await requeue_stale(db)
            await reap_stale(db)  # answers whose API process died mid-run
            await expire_due(db)  # verified answers past their expiry leave search
            now = datetime.now(UTC)
            due = (
                (
                    await db.execute(
                        select(Source).where(
                            Source.sync_interval_minutes.is_not(None),
                            Source.status.in_(("ready", "error", "idle")),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for source in due:
                last = source.last_synced_at or source.created_at
                if now - last >= timedelta(minutes=source.sync_interval_minutes):
                    full = (
                        source.last_full_synced_at is None
                        or now - source.last_full_synced_at >= FULL_SYNC_EVERY
                    )
                    log.info(
                        "Scheduling %s sync for %s", "full" if full else "incremental", source.name
                    )
                    await enqueue_sync(db, source, full=full)
            await db.commit()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=60)


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    concurrency = get_settings().worker_concurrency
    telemetry.setup("strata-worker")
    log.info("Worker started with %d loop(s)", concurrency)
    try:
        await asyncio.gather(scheduler(stop), *(job_loop(i, stop) for i in range(concurrency)))
    finally:
        telemetry.shutdown()
    log.info("Worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
