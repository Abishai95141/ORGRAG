"""The audit log (roadmap §6 #4): who read each org's data, and the sensitive changes
made in it. Read through app/api/audit.py; see docs/ARCHITECTURE.md#audit-log.

`audit_access` gets one row per request per org whose documents the request returned —
an answer's sources (everything retrieved and shown to the model, cited or not), search
results, a document opened in full, a shared answer viewed — and each row lists only
that org's documents. So when a member of orgs A and B asks a question from B that
reads A's documents, A's admins see who asked what and which of A's documents it
reached, and nothing of what it read in B. `audit_events` records admin actions,
added to the same session as the change so both commit (or roll back) together.

Rows are snapshots — actor name and email, API key name, document titles — written at
the time, so they survive revoking the key, leaving the org or deleting the
conversation, and are read and exported without joins."""

import contextlib
import contextvars
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.citations import cited_refs
from app.core.config import get_settings
from app.core.deps import OrgContext
from app.core.security import API_KEY_PREFIX
from app.models import AuditEvent, Collection, DataAccess, Document, Message, User

if TYPE_CHECKING:
    from app.agent.engine import ChatRequest

QUERY_CHARS = 2000
LABEL_CHARS = 512
PURGE_BATCH = 5000
# Defence in depth: keys that name a credential are dropped from `details` whatever
# the caller passes (callers never pass secret values in the first place).
_SECRET_KEYS = (
    "token",
    "secret",
    "password",
    "credential",
    "service_account",
    "key_hash",
    "api_key",
)
REDACTED = "[redacted]"


@dataclass(frozen=True)
class Actor:
    """Who a request acts as: a signed-in user, or an API key acting as its creator."""

    user_id: uuid.UUID | None
    name: str
    email: str
    channel: str = "app"  # app (signed in) | api (API key) | mcp (API key, MCP server)
    api_key_id: uuid.UUID | None = None
    api_key_name: str | None = None

    @classmethod
    def of(cls, ctx: OrgContext) -> "Actor":
        key = ctx.api_key
        return cls(
            user_id=ctx.user.id,
            name=ctx.user.name,
            email=ctx.user.email,
            channel="mcp" if ctx.via_mcp else "api" if key is not None else "app",
            api_key_id=key.id if key is not None else None,
            api_key_name=key.name if key is not None else None,
        )

    @classmethod
    def of_user(cls, user: User) -> "Actor":
        return cls(user_id=user.id, name=user.name, email=user.email)

    def columns(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "actor_user_id": self.user_id,
            "actor_name": self.name[:120],
            "actor_email": self.email[:320],
            "api_key_id": self.api_key_id,
            "api_key_name": self.api_key_name[:120] if self.api_key_name else None,
        }


# ── data access ──────────────────────────────────────────────────────────────
# The actor of the chat answer being written: set by the route that starts the run
# (app.api.chat.begin_answer) and inherited by the run's task, so the engine records
# the access without knowing about API keys or the MCP server.
_answer_actor: contextvars.ContextVar[Actor | None] = contextvars.ContextVar(
    "answer_actor", default=None
)


@contextlib.contextmanager
def acting_as(actor: Actor) -> Iterator[None]:
    """Tasks created inside the block attribute the answers they write to `actor`."""
    token = _answer_actor.set(actor)
    try:
        yield
    finally:
        _answer_actor.reset(token)


def record_reads(
    db: AsyncSession,
    actor: Actor,
    kind: str,
    sources: Iterable[dict[str, Any]],
    *,
    query: str | None = None,
    origin_org_id: uuid.UUID | None = None,
    message_id: uuid.UUID | None = None,
    cited: set[int] | frozenset[int] = frozenset(),
) -> int:
    """Add one access row per org among `sources` (SourceRef dicts), each listing only
    that org's documents; web pages and attached files aren't org data. Returns how
    many orgs were touched. `cited` holds the refs the answer cites."""
    by_org: dict[str, dict[str, dict[str, Any]]] = {}
    for s in sources:
        if (s.get("origin") or "knowledge") != "knowledge":
            continue
        if not s.get("org_id") or not s.get("document_id"):
            continue
        docs = by_org.setdefault(str(s["org_id"]), {})
        doc = docs.setdefault(
            str(s["document_id"]),
            {
                "id": str(s["document_id"]),
                "title": (s.get("title") or "")[:LABEL_CHARS],
                "collection": s.get("collection_name"),
                "cited": False,
            },
        )
        doc["cited"] = doc["cited"] or s.get("ref") in cited
    for org_id, docs in by_org.items():
        items = list(docs.values())
        db.add(
            DataAccess(
                org_id=uuid.UUID(org_id),
                kind=kind,
                query=query[:QUERY_CHARS] if query else None,
                origin_org_id=origin_org_id,
                message_id=message_id,
                documents=items,
                document_count=len(items),
                cited_count=sum(1 for d in items if d["cited"]),
                **actor.columns(),
            )
        )
    return len(by_org)


def document_ref(doc: Document, collection: Collection | None) -> dict[str, Any]:
    """A document opened in full, in the SourceRef shape record_reads takes."""
    return {
        "origin": "knowledge",
        "org_id": doc.org_id,
        "document_id": doc.id,
        "title": doc.title,
        "collection_name": collection.name if collection else None,
    }


async def record_answer(db: AsyncSession, req: "ChatRequest", message: Message) -> None:
    """Called by the engine as it saves an answer (any status), in the same session:
    every org whose documents were retrieved for it. `message.sources` is the answer's
    citation registry, so this covers what the model saw, not only what it cited."""
    actor = _answer_actor.get()
    if actor is None:  # a run started outside a route (scripts)
        user = await db.get(User, req.user_id)
        if user is None:
            return
        actor = Actor.of_user(user)
    record_reads(
        db,
        actor,
        "chat",
        message.sources or [],
        query=req.question,
        origin_org_id=req.org_id,
        message_id=message.id,
        cited=set(cited_refs(message.content or "")),
    )


# ── admin actions ────────────────────────────────────────────────────────────
def _clean(value: Any) -> Any:
    """JSON-safe copy of `value` without credentials."""
    if isinstance(value, dict):
        return {
            str(k): _clean(v)
            for k, v in value.items()
            if not any(hint in str(k).lower() for hint in _SECRET_KEYS)
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [_clean(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, str) and API_KEY_PREFIX in value:
        return REDACTED
    return value


def add_event(
    db: AsyncSession,
    org_id: uuid.UUID,
    actor: Actor,
    action: str,
    target_type: str,
    target_id: Any = None,
    label: str | None = None,
    **details: Any,
) -> None:
    db.add(
        AuditEvent(
            org_id=org_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id)[:64] if target_id is not None else None,
            target_label=label[:LABEL_CHARS] if label else None,
            details=_clean(details),
            **actor.columns(),
        )
    )


def record_event(
    ctx: OrgContext,
    action: str,
    target_type: str,
    target_id: Any = None,
    label: str | None = None,
    **details: Any,
) -> None:
    """Add an admin-action entry to the request's session: it's committed (or rolled
    back) with the change it describes, so call it before the route commits."""
    add_event(ctx.db, ctx.org_id, Actor.of(ctx), action, target_type, target_id, label, **details)


def change(before: Any, after: Any) -> dict[str, Any]:
    return {"from": before, "to": after}


def changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`{field: {"from", "to"}}` for the fields that differ."""
    return {k: change(before.get(k), after.get(k)) for k in after if before.get(k) != after.get(k)}


async def emails(db: AsyncSession, user_ids: Iterable[uuid.UUID]) -> list[str]:
    """The users' emails, for event details. Doesn't flush, so a pending change that
    violates a constraint still fails at the route's commit (and its own 409)."""
    ids = set(user_ids)
    if not ids:
        return []
    with db.no_autoflush:
        rows = await db.execute(select(User.email).where(User.id.in_(ids)).order_by(User.email))
    return list(rows.scalars())


# ── retention ────────────────────────────────────────────────────────────────
async def purge_expired(db: AsyncSession) -> int:
    """Delete entries older than AUDIT_RETENTION_DAYS (0 keeps everything), in batches
    so no single statement holds locks for long. Returns how many were deleted."""
    days = get_settings().audit_retention_days
    if days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=days)
    removed = 0
    for model in (DataAccess, AuditEvent):
        while True:
            batch = select(model.id).where(model.created_at < cutoff).limit(PURGE_BATCH)
            result = await db.execute(
                delete(model)
                .where(model.id.in_(batch))
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            removed += result.rowcount or 0
            if (result.rowcount or 0) < PURGE_BATCH:
                break
    return removed
