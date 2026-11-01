"""Job lease (audit I2): long jobs aren't re-queued while alive, and jobs for one
source never run at the same time."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_api_integration import _org, _signup

pytestmark = pytest.mark.integration


async def _jobs(app, n: int, *, same_source: bool = True):
    from app.core.db import SessionLocal
    from app.models import Job, Organization

    admin = await _signup(app, "q")
    slug = (await _org(admin, "Queue Co"))["slug"]
    async with SessionLocal() as db:
        from sqlalchemy import select

        org = (await db.execute(select(Organization).where(Organization.slug == slug))).scalar_one()
        source = str(uuid.uuid4())
        jobs = [
            Job(
                org_id=org.id,
                kind="extract_graph",
                payload={"source_id": source if same_source else str(uuid.uuid4())},
            )
            for _ in range(n)
        ]
        db.add_all(jobs)
        await db.commit()
        return [j.id for j in jobs]


async def test_only_jobs_with_a_stopped_heartbeat_are_requeued(app):
    from app.core.db import SessionLocal
    from app.models import Job
    from app.worker.queue import requeue_stale

    alive_id, dead_id = await _jobs(app, 2, same_source=False)
    now = datetime.now(UTC)
    async with SessionLocal() as db:
        alive, dead = await db.get(Job, alive_id), await db.get(Job, dead_id)
        for job in (alive, dead):
            job.status, job.started_at = "running", now - timedelta(hours=2)
        alive.heartbeat_at = now - timedelta(seconds=20)  # a 2-hour sync that's still working
        dead.heartbeat_at = now - timedelta(minutes=10)
        await db.commit()
        assert await requeue_stale(db) >= 1
        await db.refresh(alive)
        await db.refresh(dead)
    assert alive.status == "running" and dead.status == "queued"


async def test_jobs_for_one_source_never_overlap(app):
    from app.core.db import SessionLocal
    from app.models import Job
    from app.worker.queue import exclusive

    a_id, b_id = await _jobs(app, 2)
    (c_id,) = await _jobs(app, 1, same_source=False)
    async with SessionLocal() as db:
        a, b, c = [await db.get(Job, i) for i in (a_id, b_id, c_id)]
        async with exclusive(a) as got_a, exclusive(b) as got_b, exclusive(c) as got_c:
            assert got_a and not got_b and got_c
        async with exclusive(b) as got_b:
            assert got_b  # released when the first job finished


async def test_busy_source_defers_without_spending_an_attempt(app, monkeypatch):
    from app.core.db import SessionLocal
    from app.models import Job
    from app.worker import __main__ as worker
    from app.worker.queue import claim_next, exclusive

    ran = []

    async def handler(db, job):
        ran.append(job.id)

    monkeypatch.setitem(worker.HANDLERS, "extract_graph", handler)
    a_id, b_id = await _jobs(app, 2)
    async with SessionLocal() as db:
        holder = await db.get(Job, a_id)
        async with exclusive(holder):
            # Another worker claims the second job while the first holds the source.
            job = await claim_next(db)
            while job is not None and job.id != b_id:
                job = await claim_next(db)
            assert job is not None
            await worker.run_job(0, db, job)
        await db.refresh(job)
    assert ran == [] and job.status == "queued" and job.attempts == 0
    assert job.run_after > datetime.now(UTC)


async def test_running_jobs_send_heartbeats(app, monkeypatch):
    from app.core.db import SessionLocal
    from app.models import Job
    from app.worker import queue

    (job_id,) = await _jobs(app, 1)
    old = datetime.now(UTC) - timedelta(minutes=5)
    async with SessionLocal() as db:
        job = await db.get(Job, job_id)
        job.status, job.heartbeat_at = "running", old
        await db.commit()
    monkeypatch.setattr(queue, "HEARTBEAT_SECONDS", 0.05)
    beat = asyncio.create_task(queue.heartbeat(job_id))
    await asyncio.sleep(0.3)
    beat.cancel()
    async with SessionLocal() as db:
        assert (await db.get(Job, job_id)).heartbeat_at > old


async def test_a_job_that_breaks_its_own_error_handling_never_stops_the_worker(app, monkeypatch):
    """Recording a failed job can fail too (a broken session): the failure is recorded on
    a fresh session, and the loop goes on to the next job either way."""
    from app.core.db import SessionLocal
    from app.models import Job
    from app.worker import __main__ as worker

    ran = []

    async def handler(db, job):
        ran.append(job.id)
        raise RuntimeError("boom")

    monkeypatch.setitem(worker.HANDLERS, "extract_graph", handler)
    real_finish, broken = worker.finish, []

    async def finish(db, job, error=None):
        if error and not broken:  # the first attempt to record the failure breaks
            broken.append(job.id)
            raise RuntimeError("session broken")
        return await real_finish(db, job, error=error)

    monkeypatch.setattr(worker, "finish", finish)
    first, second = await _jobs(app, 2, same_source=False)
    stop = asyncio.Event()
    loop = asyncio.create_task(worker.job_loop(0, stop))
    for _ in range(200):
        if len(ran) == 2:
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(loop, 10)
    assert sorted(ran) == sorted([first, second])  # the loop went on after the first
    async with SessionLocal() as db:
        for job_id in (first, second):
            job = await db.get(Job, job_id)
            assert job.error == "boom" and job.status == "queued"  # recorded, to retry
