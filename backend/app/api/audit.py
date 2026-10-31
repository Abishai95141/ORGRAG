"""The org's audit log [admin] (roadmap §6 #4): every read of its data — also from
other orgs' workspaces, by members of both — and the sensitive changes made in it,
newest first with cursor pagination, plus a streamed CSV / JSONL export. Entries are
written by app/core/audit.py; an org only ever sees its own rows."""

import base64
import binascii
import csv
import io
import json
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Select, or_, select, tuple_

from app.core import audit
from app.core.db import SessionLocal
from app.core.deps import OrgContext, admin_ctx
from app.core.errors import bad_request
from app.models import AuditEvent, DataAccess
from app.schemas.common import (
    AccessedDocument,
    AccessEntryOut,
    AccessKind,
    AccessPage,
    AuditActor,
    AuditChannel,
    AuditEventOut,
    AuditEventPage,
    AuditLog,
    ExportFormat,
)

router = APIRouter(prefix="/orgs/{slug}/audit", tags=["audit"])

EXPORT_BATCH = 500  # rows fetched per round trip while streaming an export
# "member" (every member.* action) or one action, e.g. "member.role_changed".
ACTION_PATTERN = r"^[a-z_]{1,32}(\.[a-z_]{1,32})?$"
# Spreadsheets run cells starting with these as formulas (CSV injection).
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


@dataclass
class Window:
    since: datetime | None
    until: datetime | None
    actor_id: uuid.UUID | None
    q: str | None


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def window(
    since: datetime | None = Query(None, description="From this time (inclusive)"),
    until: datetime | None = Query(None, description="Up to this time (exclusive)"),
    actor_id: uuid.UUID | None = Query(None, description="A user id or an API key id"),
    q: str | None = Query(
        None, max_length=200, description="Substring of the query (access) or target (events)"
    ),
) -> Window:
    since, until = _utc(since), _utc(until)
    if since and until and since >= until:
        raise bad_request("`since` must be before `until`", "invalid_range")
    return Window(since, until, actor_id, q.strip() if q and q.strip() else None)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _filtered(stmt: Select, model: Any, w: Window, text_column: Any) -> Select:
    if w.since:
        stmt = stmt.where(model.created_at >= w.since)
    if w.until:
        stmt = stmt.where(model.created_at < w.until)
    if w.actor_id:
        stmt = stmt.where(or_(model.actor_user_id == w.actor_id, model.api_key_id == w.actor_id))
    if w.q:
        stmt = stmt.where(text_column.ilike(f"%{_escape(w.q)}%", escape="\\"))
    return stmt


def _access_stmt(
    ctx: OrgContext,
    w: Window,
    kind: AccessKind | None,
    channel: AuditChannel | None,
    document_id: uuid.UUID | None,
) -> Select:
    stmt = _filtered(
        select(DataAccess).where(DataAccess.org_id == ctx.org_id), DataAccess, w, DataAccess.query
    )
    if kind:
        stmt = stmt.where(DataAccess.kind == kind)
    if channel:
        stmt = stmt.where(DataAccess.channel == channel)
    if document_id:
        stmt = stmt.where(DataAccess.documents.contains([{"id": str(document_id)}]))
    return stmt


def _events_stmt(ctx: OrgContext, w: Window, action: str | None) -> Select:
    stmt = _filtered(
        select(AuditEvent).where(AuditEvent.org_id == ctx.org_id),
        AuditEvent,
        w,
        AuditEvent.target_label,
    )
    if action:
        stmt = stmt.where(
            AuditEvent.action == action
            if "." in action
            else AuditEvent.action.like(f"{_escape(action)}.%", escape="\\")
        )
    return stmt


# ── pagination ───────────────────────────────────────────────────────────────
def _cursor(row: DataAccess | AuditEvent) -> str:
    raw = f"{row.created_at.isoformat()}|{row.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _after(stmt: Select, model: Any, cursor: str | None) -> Select:
    """Rows older than the cursor's, newest first (keyset on created_at, id)."""
    if cursor:
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
            at, row_id = raw.split("|")
            key = (datetime.fromisoformat(at), uuid.UUID(row_id))
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            raise bad_request(
                "That cursor isn't valid; start again without it", "invalid_cursor"
            ) from exc
        stmt = stmt.where(tuple_(model.created_at, model.id) < key)
    return stmt.order_by(model.created_at.desc(), model.id.desc())


async def _page(ctx: OrgContext, stmt: Select, model: Any, cursor: str | None, limit: int):
    rows = list((await ctx.db.execute(_after(stmt, model, cursor).limit(limit + 1))).scalars())
    return rows[:limit], _cursor(rows[limit - 1]) if len(rows) > limit else None


# ── serialization ────────────────────────────────────────────────────────────
def _actor(row: DataAccess | AuditEvent) -> AuditActor:
    return AuditActor(
        type="api_key" if row.api_key_id else "user",
        user_id=row.actor_user_id,
        name=row.actor_name,
        email=row.actor_email,
        api_key_id=row.api_key_id,
        api_key_name=row.api_key_name,
    )


def access_out(row: DataAccess, org_id: uuid.UUID) -> AccessEntryOut:
    # Made from another org's workspace: that org stays unnamed and its ids withheld.
    cross = row.origin_org_id is not None and row.origin_org_id != org_id
    return AccessEntryOut(
        id=row.id,
        created_at=row.created_at,
        kind=row.kind,
        channel=row.channel,
        actor=_actor(row),
        query=row.query,
        cross_org=cross,
        message_id=None if cross else row.message_id,
        documents=[AccessedDocument(**d) for d in row.documents],
        document_count=row.document_count,
        cited_count=row.cited_count,
    )


def event_out(row: AuditEvent) -> AuditEventOut:
    return AuditEventOut(
        id=row.id,
        created_at=row.created_at,
        action=row.action,
        channel=row.channel,
        actor=_actor(row),
        target_type=row.target_type,
        target_id=row.target_id,
        target_label=row.target_label,
        details=row.details or {},
    )


# ── routes ───────────────────────────────────────────────────────────────────
@router.get("/access", response_model=AccessPage)
async def list_access(
    ctx: OrgContext = Depends(admin_ctx),
    w: Window = Depends(window),
    kind: AccessKind | None = None,
    channel: AuditChannel | None = None,
    document_id: uuid.UUID | None = Query(None, description="Entries that returned it"),
    cursor: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
):
    """Requests that returned this org's documents: chat answers (everything retrieved
    for them, cited or not), searches, documents opened in full and shared answers
    viewed — by anyone, from any org's workspace. Each entry lists only this org's
    documents."""
    stmt = _access_stmt(ctx, w, kind, channel, document_id)
    rows, next_cursor = await _page(ctx, stmt, DataAccess, cursor, limit)
    return AccessPage(
        items=[access_out(r, ctx.org_id) for r in rows], next_cursor=next_cursor, limit=limit
    )


@router.get("/events", response_model=AuditEventPage)
async def list_events(
    ctx: OrgContext = Depends(admin_ctx),
    w: Window = Depends(window),
    action: str | None = Query(
        None, pattern=ACTION_PATTERN, description='An action, or a group like "member"'
    ),
    cursor: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
):
    """Admin actions and other sensitive changes in this org."""
    rows, next_cursor = await _page(ctx, _events_stmt(ctx, w, action), AuditEvent, cursor, limit)
    return AuditEventPage(items=[event_out(r) for r in rows], next_cursor=next_cursor, limit=limit)


# ── export ───────────────────────────────────────────────────────────────────
def _cell(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return "" if value is None else value


def _common_cells(item: AccessEntryOut | AuditEventOut) -> list[Any]:
    a = item.actor
    return [
        item.created_at.isoformat(),
        item.channel,
        a.type,
        a.name,
        a.email,
        a.user_id,
        a.api_key_name,
        a.api_key_id,
    ]


COMMON_COLUMNS = [
    "time", "channel", "actor_type", "actor_name", "actor_email", "actor_user_id",
    "api_key_name", "api_key_id",
]  # fmt: skip
ACCESS_COLUMNS = [
    *COMMON_COLUMNS, "kind", "cross_org", "query", "document_count", "cited_count",
    "documents", "document_ids",
]  # fmt: skip
EVENT_COLUMNS = [
    *COMMON_COLUMNS, "action", "target_type", "target_id", "target_label", "details",
]  # fmt: skip


def _access_cells(item: AccessEntryOut) -> list[Any]:
    titles = "; ".join(f"{d.title}{' (cited)' if d.cited else ''}" for d in item.documents)
    return [
        *_common_cells(item),
        item.kind,
        item.cross_org,
        item.query,
        item.document_count,
        item.cited_count,
        titles,
        " ".join(str(d.id) for d in item.documents),
    ]


def _event_cells(item: AuditEventOut) -> list[Any]:
    return [
        *_common_cells(item),
        item.action,
        item.target_type,
        item.target_id,
        item.target_label,
        json.dumps(item.details, ensure_ascii=False, sort_keys=True),
    ]


async def _stream(
    stmt: Select,
    to_item: Callable[[Any], BaseModel],
    fmt: ExportFormat,
    columns: list[str],
    cells: Callable[[Any], list[Any]],
) -> AsyncIterator[str]:
    """Rows in batches through a server-side cursor, so an export of any size is never
    held in memory. Runs in its own session: the request's is closed by then."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if fmt == "csv":
        writer.writerow(columns)
        yield buffer.getvalue()
    async with SessionLocal() as db:
        result = await db.stream_scalars(stmt.execution_options(yield_per=EXPORT_BATCH))
        async for rows in result.partitions():
            buffer.seek(0)
            buffer.truncate()
            for row in rows:
                item = to_item(row)
                if fmt == "jsonl":
                    buffer.write(item.model_dump_json() + "\n")
                else:
                    writer.writerow([_cell(c) for c in cells(item)])
            yield buffer.getvalue()


@router.get(
    "/export",
    responses={
        200: {
            "description": "The log as CSV or JSON Lines, streamed",
            "content": {"text/csv": {}, "application/x-ndjson": {}},
        }
    },
)
async def export(
    ctx: OrgContext = Depends(admin_ctx),
    w: Window = Depends(window),
    log: AuditLog = Query("access", description="Which log: data access or admin actions"),
    format: ExportFormat = "csv",
    kind: AccessKind | None = None,
    channel: AuditChannel | None = None,
    document_id: uuid.UUID | None = None,
    action: str | None = Query(None, pattern=ACTION_PATTERN),
):
    """Download a log, oldest first, with the same filters as the list routes. The
    export is streamed, and recorded as an `audit.exported` event before it starts."""
    if log == "access":
        stmt = _access_stmt(ctx, w, kind, channel, document_id)
        model, columns, cells = DataAccess, ACCESS_COLUMNS, _access_cells
        org_id = ctx.org_id

        def to_item(row: Any) -> BaseModel:
            return access_out(row, org_id)
    else:
        stmt = _events_stmt(ctx, w, action)
        model, columns, cells, to_item = AuditEvent, EVENT_COLUMNS, _event_cells, event_out
    stmt = stmt.order_by(model.created_at, model.id)

    filters = {
        "since": w.since,
        "until": w.until,
        "actor_id": w.actor_id,
        "q": w.q,
        **(
            {"kind": kind, "channel": channel, "document_id": document_id}
            if log == "access"
            else {"action": action}
        ),
    }
    audit.record_event(
        ctx,
        "audit.exported",
        "audit_log",
        None,
        "Data access" if log == "access" else "Admin actions",
        log=log,
        format=format,
        filters={k: v for k, v in filters.items() if v is not None},
    )
    await ctx.db.commit()

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"strata-{ctx.org.slug}-audit-{log}-{stamp}.{format}"
    return StreamingResponse(
        _stream(stmt, to_item, format, columns, cells),
        media_type="text/csv; charset=utf-8" if format == "csv" else "application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
