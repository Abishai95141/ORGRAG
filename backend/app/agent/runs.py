"""Lifecycle of chat runs outside the request: stop, heartbeat and reaping (audit O3/F1).

A run is an asyncio task in the API process that streams into a message row. Closing
the browser stream doesn't stop it (so an answer survives a dropped connection);
`cancel` does. While it runs, the task bumps `messages.updated_at` every
`HEARTBEAT_SECONDS`. If the process dies, the heartbeat stops, and `reap_stale`
(run by the worker's scheduler) marks the message as interrupted, so nothing stays
"streaming" forever, whichever process owned it."""

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.core.db import SessionLocal
from app.models import Message

log = logging.getLogger("strata.runs")

HEARTBEAT_SECONDS = 20
STALE_AFTER = timedelta(seconds=90)
INTERRUPTED = "This answer was interrupted before it finished (the server restarted). Ask again."

_tasks: dict[uuid.UUID, asyncio.Task] = {}


def register(message_id: uuid.UUID, task: asyncio.Task) -> None:
    _tasks[message_id] = task
    task.add_done_callback(lambda _: _tasks.pop(message_id, None))


def cancel(message_id: uuid.UUID) -> bool:
    """Cancel the run streaming into `message_id` if this process owns it."""
    task = _tasks.get(message_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


async def wait(message_id: uuid.UUID, seconds: float) -> None:
    """Wait (bounded) for a cancelled run to save its partial answer."""
    task = _tasks.get(message_id)
    if task is not None:
        with contextlib.suppress(asyncio.CancelledError, TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), seconds)


async def heartbeat(message_id: uuid.UUID) -> None:
    """Runs alongside a chat run until cancelled. Uses its own short sessions so it
    never holds a connection between beats."""
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        with contextlib.suppress(Exception):  # a missed beat is fine; the next one retries
            async with SessionLocal() as db:
                await db.execute(
                    update(Message)
                    .where(Message.id == message_id, Message.status == "streaming")
                    .values(updated_at=datetime.now(UTC))
                )
                await db.commit()


async def reap_stale(db, now: datetime | None = None) -> int:
    """Mark streaming messages whose heartbeat stopped as interrupted. Returns how many."""
    cutoff = (now or datetime.now(UTC)) - STALE_AFTER
    result = await db.execute(
        update(Message)
        .where(Message.status == "streaming", Message.updated_at < cutoff)
        .values(status="error", content=INTERRUPTED, updated_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if result.rowcount:
        log.warning("Marked %d interrupted answer(s) as failed", result.rowcount)
    return result.rowcount or 0
