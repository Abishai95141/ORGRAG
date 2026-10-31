"""Answer permalinks and collection access requests (roadmap §6 #7).

A link is a capability to *ask*, not to read: every viewer's permissions are checked
when they open it. They get the answer only if they can read every source it cites,
because the answer text is made of those sources. Otherwise they see the question and
which collections they'd need, and can ask an admin for access."""

import secrets
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.citations import cited_refs
from app.core import audit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import OrgContext, admin_ctx, org_context, session_user
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.models import (
    AccessRequest,
    Collection,
    CollectionAccess,
    Conversation,
    Document,
    Membership,
    Message,
    Organization,
    SharedAnswer,
    User,
)
from app.retrieval.access import visible_collections
from app.schemas.common import (
    AccessRequestIn,
    AccessRequestOut,
    MissingAccess,
    PersonOut,
    SharedAnswerOut,
    SharedOrg,
    ShareOut,
)

router = APIRouter(tags=["sharing"])


# ── creating and revoking links ──────────────────────────────────────────────
async def _own_answer(ctx: OrgContext, message_id: uuid.UUID) -> Message:
    message = await ctx.db.get(Message, message_id)
    conv = await ctx.db.get(Conversation, message.conversation_id) if message else None
    if (
        message is None
        or message.role != "assistant"
        or conv is None
        or conv.org_id != ctx.org_id
        or conv.user_id != ctx.user.id
    ):
        raise not_found("Message")
    return message


async def active_share(db: AsyncSession, message_id: uuid.UUID) -> SharedAnswer | None:
    return (
        await db.execute(
            select(SharedAnswer).where(
                SharedAnswer.message_id == message_id, SharedAnswer.revoked_at.is_(None)
            )
        )
    ).scalar_one_or_none()


def _share_out(share: SharedAnswer) -> ShareOut:
    url = f"{get_settings().public_url.rstrip('/')}/a/{share.token}"
    return ShareOut(token=share.token, url=url, created_at=share.created_at)


@router.post("/orgs/{slug}/messages/{message_id}/share", response_model=ShareOut)
async def share_answer(message_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    """A link to one of your answers (the same link if it's already shared)."""
    message = await _own_answer(ctx, message_id)
    if message.status != "complete":
        raise bad_request("Only complete answers can be shared", "answer_incomplete")
    share = await active_share(ctx.db, message.id)
    if share is None:
        share = SharedAnswer(
            token=secrets.token_urlsafe(16),
            org_id=ctx.org_id,
            message_id=message.id,
            created_by_id=ctx.user.id,
        )
        ctx.db.add(share)
        await ctx.db.flush()
        await _share_event(ctx, "share.created", share, message)
        await ctx.db.commit()
    return _share_out(share)


async def _question(db: AsyncSession, message: Message) -> str | None:
    """The user message an answer replies to."""
    return await db.scalar(
        select(Message.content)
        .where(
            Message.conversation_id == message.conversation_id,
            Message.role == "user",
            Message.created_at <= message.created_at,
        )
        .order_by(Message.created_at.desc())
        .limit(1)
    )


async def _share_event(ctx: OrgContext, action: str, share: SharedAnswer, message: Message) -> None:
    """Labelled with the question; never the token, which is the link itself."""
    question = await _question(ctx.db, message)
    audit.record_event(ctx, action, "shared_answer", share.id, question, message_id=message.id)


@router.delete("/orgs/{slug}/messages/{message_id}/share", status_code=204)
async def unshare_answer(message_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    """Revoke the link: it stops working for everyone."""
    message = await _own_answer(ctx, message_id)
    share = await active_share(ctx.db, message.id)
    if share is not None:
        share.revoked_at = datetime.now(UTC)
        await _share_event(ctx, "share.revoked", share, message)
        await ctx.db.commit()
    return Response(status_code=204)


# ── opening a link ───────────────────────────────────────────────────────────
async def _missing(
    db: AsyncSession, viewer: User, author_id: uuid.UUID, sources: list[dict]
) -> list[MissingAccess]:
    """What the viewer can't read among the cited sources (empty: they can read all)."""
    roles = dict(
        (
            await db.execute(
                select(Membership.org_id, Membership.role).where(Membership.user_id == viewer.id)
            )
        ).all()
    )
    visible: dict[uuid.UUID, set[uuid.UUID]] = {}
    pending = set(
        (
            await db.execute(
                select(AccessRequest.collection_id).where(
                    AccessRequest.user_id == viewer.id, AccessRequest.status == "pending"
                )
            )
        ).scalars()
    )
    out: dict[tuple, MissingAccess] = {}
    for s in sources:
        origin = s.get("origin") or "knowledge"
        if origin == "web":
            continue
        if origin == "attachment":
            if viewer.id != author_id:
                out[("attachment",)] = MissingAccess(
                    kind="attachment", org_name=s.get("title") or "an attached file"
                )
            continue
        doc = await db.get(Document, uuid.UUID(str(s["document_id"])))
        if doc is None:
            continue  # deleted since: nothing left to protect
        org = await db.get(Organization, doc.org_id)
        role = roles.get(doc.org_id)
        if role is None:
            out[("org", doc.org_id)] = MissingAccess(kind="org", org_name=org.name)
            continue
        if doc.org_id not in visible:
            cols = await visible_collections(db, doc.org_id, viewer.id, role)
            visible[doc.org_id] = {c.id for c in cols}
        collection = await db.get(Collection, doc.collection_id)
        if doc.hidden:
            out[("hidden", doc.id)] = MissingAccess(
                kind="hidden", org_name=org.name, org_slug=org.slug,
                collection_name=collection.name if collection else None,
            )  # fmt: skip
        elif doc.collection_id not in visible[doc.org_id]:
            out[("collection", doc.collection_id)] = MissingAccess(
                kind="collection",
                org_name=org.name,
                org_slug=org.slug,
                collection_id=doc.collection_id,
                collection_name=collection.name if collection else None,
                requested=doc.collection_id in pending,
            )
    return list(out.values())


@router.get("/shared/{token}", response_model=SharedAnswerOut)
async def open_shared(
    token: str, user: User = Depends(session_user), db: AsyncSession = Depends(get_db)
):
    share = (
        await db.execute(select(SharedAnswer).where(SharedAnswer.token == token))
    ).scalar_one_or_none()
    message = await db.get(Message, share.message_id) if share else None
    if share is None or share.revoked_at is not None or message is None:
        raise ApiError(404, "This link was turned off, or never existed", "share_not_found")
    org = await db.get(Organization, share.org_id)
    base = SharedAnswerOut(
        token=token,
        access="none",
        org=SharedOrg(name=org.name, slug=org.slug, hue=org.hue),
        shared_at=share.created_at,
    )
    member = await db.scalar(
        select(Membership.role).where(Membership.org_id == org.id, Membership.user_id == user.id)
    )
    if member is None:
        base.missing = [MissingAccess(kind="org", org_name=org.name)]
        return base

    conv = await db.get(Conversation, message.conversation_id)
    author = await db.get(User, conv.user_id) if conv else None
    question = await _question(db, message)
    cited = set(cited_refs(message.content))
    sources = [s for s in message.sources if s.get("ref") in cited]
    base.author_name = author.name if author else None
    base.question = question
    base.answered_at = message.created_at
    base.missing = await _missing(db, user, conv.user_id if conv else uuid.uuid4(), sources)
    if base.missing:
        base.access = "restricted"
        return base
    base.access = "full"
    base.answer = message.content
    base.sources = message.sources  # the full list, so [n] markers resolve
    # A read of every org whose documents the viewer now sees through the answer.
    audit.record_reads(
        db,
        audit.Actor.of_user(user),
        "shared",
        message.sources,
        query=question,
        origin_org_id=share.org_id,
        message_id=message.id,
        cited=cited,
    )
    await db.commit()
    return base


# ── access requests ──────────────────────────────────────────────────────────
async def _request_out(db: AsyncSession, rows: list[AccessRequest]) -> list[AccessRequestOut]:
    users = {
        u.id: u
        for u in (
            await db.execute(select(User).where(User.id.in_({r.user_id for r in rows})))
        ).scalars()
    }
    names = dict(
        (
            await db.execute(
                select(Collection.id, Collection.name).where(
                    Collection.id.in_({r.collection_id for r in rows})
                )
            )
        ).all()
    )
    return [
        AccessRequestOut(
            id=r.id,
            collection_id=r.collection_id,
            collection_name=names.get(r.collection_id, ""),
            user=PersonOut(
                id=users[r.user_id].id,
                name=users[r.user_id].name,
                email=users[r.user_id].email,
                avatar_hue=users[r.user_id].avatar_hue,
            ),
            note=r.note,
            status=r.status,
            created_at=r.created_at,
            decided_at=r.decided_at,
        )
        for r in rows
        if r.user_id in users
    ]


@router.post(
    "/orgs/{slug}/collections/{collection_id}/access-requests",
    response_model=AccessRequestOut,
    status_code=201,
)
async def request_access(
    collection_id: uuid.UUID, body: AccessRequestIn, ctx: OrgContext = Depends(org_context)
):
    """Ask the org's admins for read access to a collection (once: a pending request
    is returned as is)."""
    col = await ctx.db.get(Collection, collection_id)
    if col is None or col.org_id != ctx.org_id:
        raise not_found("Collection")
    visible = await visible_collections(ctx.db, ctx.org_id, ctx.user.id, ctx.role)
    if col.id in {c.id for c in visible}:
        raise conflict("You can already read this collection", "already_has_access")
    existing = (
        await ctx.db.execute(
            select(AccessRequest).where(
                AccessRequest.collection_id == col.id,
                AccessRequest.user_id == ctx.user.id,
                AccessRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = AccessRequest(
            org_id=ctx.org_id,
            collection_id=col.id,
            user_id=ctx.user.id,
            note=(body.note or "").strip() or None,
            status="pending",
        )
        ctx.db.add(existing)
        await ctx.db.commit()
    return (await _request_out(ctx.db, [existing]))[0]


@router.get("/orgs/{slug}/access-requests", response_model=list[AccessRequestOut])
async def list_access_requests(ctx: OrgContext = Depends(admin_ctx), status: str = "pending"):
    stmt = select(AccessRequest).where(AccessRequest.org_id == ctx.org_id)
    if status != "all":
        stmt = stmt.where(AccessRequest.status == status)
    rows = list((await ctx.db.execute(stmt.order_by(AccessRequest.created_at.desc()))).scalars())
    return await _request_out(ctx.db, rows)


async def _pending(ctx: OrgContext, request_id: uuid.UUID) -> AccessRequest:
    req = await ctx.db.get(AccessRequest, request_id)
    if req is None or req.org_id != ctx.org_id:
        raise not_found("Access request")
    if req.status != "pending":
        raise conflict(f"This request was already {req.status}", "already_decided")
    return req


async def _decision_event(ctx: OrgContext, action: str, req: AccessRequest) -> None:
    requester = await ctx.db.get(User, req.user_id)
    collection = await ctx.db.get(Collection, req.collection_id)
    audit.record_event(
        ctx,
        action,
        "access_request",
        req.id,
        requester.email if requester else None,
        collection=collection.name if collection else None,
        collection_id=req.collection_id,
        note=req.note,
    )


@router.post("/orgs/{slug}/access-requests/{request_id}/approve", response_model=AccessRequestOut)
async def approve_access(request_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    """Grant the requester read access to the collection."""
    req = await _pending(ctx, request_id)
    still_member = await ctx.db.scalar(
        select(Membership.user_id).where(
            Membership.org_id == ctx.org_id, Membership.user_id == req.user_id
        )
    )
    if still_member is None:
        raise conflict("They're no longer a member of this organization", "not_a_member")
    granted = await ctx.db.get(CollectionAccess, (req.collection_id, req.user_id))
    if granted is None:
        ctx.db.add(CollectionAccess(collection_id=req.collection_id, user_id=req.user_id))
    req.status, req.decided_by_id, req.decided_at = "approved", ctx.user.id, datetime.now(UTC)
    await _decision_event(ctx, "access_request.approved", req)
    await ctx.db.commit()
    return (await _request_out(ctx.db, [req]))[0]


@router.post("/orgs/{slug}/access-requests/{request_id}/dismiss", response_model=AccessRequestOut)
async def dismiss_access(request_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    req = await _pending(ctx, request_id)
    req.status, req.decided_by_id, req.decided_at = "dismissed", ctx.user.id, datetime.now(UTC)
    await _decision_event(ctx, "access_request.dismissed", req)
    await ctx.db.commit()
    return (await _request_out(ctx.db, [req]))[0]
