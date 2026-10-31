"""Document requests (roadmap §6 #2): a member asks the org's admins for a document the
knowledge base lacks, from an answer that couldn't answer or from a knowledge gap.
Admins fulfil it with the document that answers it (one the requester can read) or
dismiss it, in the request/decide pattern of access requests (app/api/sharing.py).
Requesters follow their own requests; once fulfilled they see the document."""

import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.sharing import _own_answer
from app.core.deps import OrgContext, admin_ctx, org_context
from app.core.errors import bad_request, conflict, not_found
from app.gaps.signals import text_key
from app.models import Collection, Document, DocumentRequest, Membership, Message, User
from app.retrieval.access import visible_collections
from app.schemas.common import (
    MAX_OFFSET,
    DocRequestDismiss,
    DocRequestFulfill,
    DocRequestIn,
    DocRequestOut,
    DocRequestPage,
    PersonOut,
    RequestDocument,
)

router = APIRouter(prefix="/orgs/{slug}", tags=["document requests"])

StatusFilter = Literal["pending", "fulfilled", "dismissed", "all"]


def _key(title: str) -> str:
    return (text_key(title) or title.casefold())[:300]


async def _asked_before(ctx: OrgContext, message: Message) -> str | None:
    """The question an answer answers: the user message just before it."""
    return await ctx.db.scalar(
        select(Message.content)
        .where(
            Message.conversation_id == message.conversation_id,
            Message.role == "user",
            Message.created_at <= message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )


async def _out(ctx: OrgContext, rows: list[DocumentRequest]) -> list[DocRequestOut]:
    db = ctx.db
    people = {
        u.id: PersonOut(id=u.id, name=u.name, email=u.email, avatar_hue=u.avatar_hue)
        for u in (
            await db.execute(
                select(User).where(
                    User.id.in_({i for r in rows for i in (r.user_id, r.decided_by_id) if i})
                )
            )
        ).scalars()
    }
    docs = {
        d.id: d
        for d in (
            await db.execute(
                select(Document).where(
                    Document.id.in_({r.document_id for r in rows if r.document_id})
                )
            )
        ).scalars()
    }
    names = dict(
        (
            await db.execute(
                select(Collection.id, Collection.name).where(
                    Collection.id.in_({d.collection_id for d in docs.values()})
                )
            )
        ).all()
    )
    readable = (
        {c.id for c in await visible_collections(db, ctx.org_id, ctx.user.id, ctx.role)}
        if docs
        else set()
    )
    mine = [r.message_id for r in rows if r.user_id == ctx.user.id and r.message_id]
    conversations = dict(
        (
            await db.execute(
                select(Message.id, Message.conversation_id).where(Message.id.in_(mine))
            )
        ).all()
    )
    out = []
    for r in rows:
        if r.user_id not in people:
            continue
        doc = docs.get(r.document_id) if r.document_id else None
        document = None
        if doc is not None and doc.org_id == ctx.org_id and doc.collection_id in readable:
            document = RequestDocument(
                id=doc.id,
                title=doc.title,
                collection_id=doc.collection_id,
                collection_name=names.get(doc.collection_id, ""),
                source_id=doc.source_id,
                url=doc.url,
            )
        out.append(
            DocRequestOut(
                id=r.id,
                title=r.title,
                note=r.note,
                question=r.question,
                message_id=r.message_id,
                conversation_id=conversations.get(r.message_id) if r.message_id else None,
                status=r.status,
                requester=people[r.user_id],
                document=document,
                resolution=r.resolution,
                decided_by=people.get(r.decided_by_id) if r.decided_by_id else None,
                created_at=r.created_at,
                decided_at=r.decided_at,
            )
        )
    return out


async def _page(ctx: OrgContext, where: list, limit: int, offset: int) -> DocRequestPage:
    total = (
        await ctx.db.execute(select(func.count(DocumentRequest.id)).where(*where))
    ).scalar_one()
    rows = list(
        (
            await ctx.db.execute(
                select(DocumentRequest)
                .where(*where)
                .order_by(DocumentRequest.created_at.desc(), DocumentRequest.id)
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
    )
    return DocRequestPage(items=await _out(ctx, rows), total=total, limit=limit, offset=offset)


async def _open_request(ctx: OrgContext, key: str) -> DocumentRequest | None:
    return (
        await ctx.db.execute(
            select(DocumentRequest).where(
                DocumentRequest.org_id == ctx.org_id,
                DocumentRequest.user_id == ctx.user.id,
                DocumentRequest.title_key == key,
                DocumentRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()


@router.post(
    "/doc-requests",
    response_model=DocRequestOut,
    status_code=201,
    responses={200: {"model": DocRequestOut, "description": "You already asked for this"}},
)
async def request_document(
    body: DocRequestIn, response: Response, ctx: OrgContext = Depends(org_context)
):
    """Ask the org's admins for a document. Asking again for the same thing while your
    request is open returns that request (200)."""
    question = (body.question or "").strip() or None
    if body.message_id is not None:
        message = await _own_answer(ctx, body.message_id)
        question = (await _asked_before(ctx, message) or "").strip() or question
    key = _key(body.title)
    existing = await _open_request(ctx, key)
    if existing is None:
        req = DocumentRequest(
            org_id=ctx.org_id,
            user_id=ctx.user.id,
            title=body.title,
            title_key=key,
            note=(body.note or "").strip() or None,
            question=question[:2000] if question else None,
            message_id=body.message_id,
            status="pending",
        )
        ctx.db.add(req)
        try:
            await ctx.db.commit()
        except IntegrityError:  # the same request sent twice at once: one wins
            await ctx.db.rollback()
            existing = await _open_request(ctx, key)
            if existing is None:
                raise
        else:
            return (await _out(ctx, [req]))[0]
    response.status_code = 200
    return (await _out(ctx, [existing]))[0]


@router.get("/doc-requests", response_model=DocRequestPage)
async def list_doc_requests(
    ctx: OrgContext = Depends(admin_ctx),
    status: StatusFilter = "pending",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
):
    """Everyone's requests in this org, newest first."""
    where = [DocumentRequest.org_id == ctx.org_id]
    if status != "all":
        where.append(DocumentRequest.status == status)
    return await _page(ctx, where, limit, offset)


@router.get("/doc-requests/mine", response_model=DocRequestPage)
async def my_doc_requests(
    ctx: OrgContext = Depends(org_context),
    status: StatusFilter = "all",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
):
    """Your own requests in this org and what became of them, newest first."""
    where = [DocumentRequest.org_id == ctx.org_id, DocumentRequest.user_id == ctx.user.id]
    if status != "all":
        where.append(DocumentRequest.status == status)
    return await _page(ctx, where, limit, offset)


async def _pending(ctx: OrgContext, request_id: uuid.UUID) -> DocumentRequest:
    # Locked: two admins deciding at once can't both win.
    req = (
        await ctx.db.execute(
            select(DocumentRequest)
            .where(DocumentRequest.id == request_id, DocumentRequest.org_id == ctx.org_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if req is None:
        raise not_found("Document request")
    if req.status != "pending":
        raise conflict(f"This request was already {req.status}", "already_decided")
    return req


def _decide(ctx: OrgContext, req: DocumentRequest, status: str, note: str | None) -> None:
    req.status = status
    req.resolution = (note or "").strip() or None
    req.decided_by_id = ctx.user.id
    req.decided_at = datetime.now(UTC)


@router.post("/doc-requests/{request_id}/fulfill", response_model=DocRequestOut)
async def fulfill_doc_request(
    request_id: uuid.UUID, body: DocRequestFulfill, ctx: OrgContext = Depends(admin_ctx)
):
    """Answer the request with a document in this org that the requester can read."""
    req = await _pending(ctx, request_id)
    doc = await ctx.db.get(Document, body.document_id)
    if doc is None or doc.org_id != ctx.org_id:
        raise not_found("Document")
    if doc.hidden:
        raise bad_request(
            "That document is hidden from search, so it can't answer anyone", "document_hidden"
        )
    role = await ctx.db.scalar(
        select(Membership.role).where(
            Membership.org_id == ctx.org_id, Membership.user_id == req.user_id
        )
    )
    if role is None:
        raise conflict("They're no longer a member of this organization", "not_a_member")
    readable = {c.id for c in await visible_collections(ctx.db, ctx.org_id, req.user_id, role)}
    if doc.collection_id not in readable:
        raise bad_request(
            "The requester can't read that document's collection: give them access first, "
            "or choose another document",
            "requester_cannot_read",
        )
    req.document_id = doc.id
    _decide(ctx, req, "fulfilled", body.note)
    await ctx.db.commit()
    return (await _out(ctx, [req]))[0]


@router.post("/doc-requests/{request_id}/dismiss", response_model=DocRequestOut)
async def dismiss_doc_request(
    request_id: uuid.UUID,
    body: DocRequestDismiss | None = None,
    ctx: OrgContext = Depends(admin_ctx),
):
    """Close the request without a document; the optional note tells the requester why."""
    req = await _pending(ctx, request_id)
    _decide(ctx, req, "dismissed", body.note if body else None)
    await ctx.db.commit()
    return (await _out(ctx, [req]))[0]
