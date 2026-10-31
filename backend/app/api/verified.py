"""Verified answers: admins promote a good answer, with an owner and an expiry, so it's
found and cited ahead of raw documents (roadmap §6 #3). Indexing lives in
app/ingestion/verified.py; members can read the verified answers of collections they
can see."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import or_, select

from app.agent.citations import cited_refs
from app.core import audit
from app.core.deps import OrgContext, admin_ctx, org_context
from app.core.errors import bad_request, not_found
from app.ingestion import verified as svc
from app.models import (
    Collection,
    Conversation,
    Document,
    Membership,
    Message,
    User,
    VerifiedAnswer,
)
from app.retrieval.access import visible_collections
from app.schemas.common import (
    PersonOut,
    ReverifyIn,
    VerifiedAnswerCreate,
    VerifiedAnswerOut,
    VerifiedAnswerPatch,
    VerifiedStatus,
)

router = APIRouter(prefix="/orgs/{slug}", tags=["verified answers"])

EXPIRING_WITHIN = timedelta(days=14)


def _status(va: VerifiedAnswer, now: datetime) -> VerifiedStatus:
    if svc.is_expired(va, now):
        return "expired"
    if va.expires_at is not None and va.expires_at - now <= EXPIRING_WITHIN:
        return "expiring"
    return "active"


async def _out(ctx: OrgContext, rows: list[VerifiedAnswer]) -> list[VerifiedAnswerOut]:
    people_ids = {i for va in rows for i in (va.owner_id, va.verified_by_id) if i}
    people = {
        u.id: PersonOut(id=u.id, name=u.name, email=u.email, avatar_hue=u.avatar_hue)
        for u in (await ctx.db.execute(select(User).where(User.id.in_(people_ids)))).scalars()
    }
    names = dict(
        (
            await ctx.db.execute(
                select(Collection.id, Collection.name).where(
                    Collection.id.in_({va.collection_id for va in rows})
                )
            )
        ).all()
    )
    now = datetime.now(UTC)
    return [
        VerifiedAnswerOut(
            id=va.id,
            collection_id=va.collection_id,
            collection_name=names.get(va.collection_id, ""),
            document_id=va.document_id,
            message_id=va.message_id,
            question=va.question,
            answer=va.answer,
            sources=va.sources,
            owner=people.get(va.owner_id),
            verified_by=people.get(va.verified_by_id),
            verified_at=va.verified_at,
            expires_at=va.expires_at,
            status=_status(va, now),
            created_at=va.created_at,
            updated_at=va.updated_at,
        )
        for va in rows
    ]


async def _visible_ids(ctx: OrgContext) -> set[uuid.UUID]:
    return {c.id for c in await visible_collections(ctx.db, ctx.org_id, ctx.user.id, ctx.role)}


async def _get(ctx: OrgContext, answer_id: uuid.UUID) -> VerifiedAnswer:
    va = await ctx.db.get(VerifiedAnswer, answer_id)
    if va is None or va.org_id != ctx.org_id or va.collection_id not in await _visible_ids(ctx):
        raise not_found("Verified answer")
    return va


async def _collection(ctx: OrgContext, collection_id: uuid.UUID) -> Collection:
    col = await ctx.db.get(Collection, collection_id)
    if col is None or col.org_id != ctx.org_id:
        raise not_found("Collection")
    return col


async def _member(ctx: OrgContext, user_id: uuid.UUID) -> uuid.UUID:
    found = await ctx.db.scalar(
        select(Membership.user_id).where(
            Membership.org_id == ctx.org_id, Membership.user_id == user_id
        )
    )
    if found is None:
        raise bad_request("The owner must be a member of this organization", "invalid_owner")
    return user_id


async def _index(ctx: OrgContext, va: VerifiedAnswer, col: Collection) -> None:
    try:
        await svc.check_sources(ctx.db, col, va.sources)
    except svc.LeakError as exc:
        raise bad_request(str(exc), "sources_not_visible") from exc
    await svc.index(ctx.db, va, col)


@router.get("/verified-answers", response_model=list[VerifiedAnswerOut])
async def list_verified(
    ctx: OrgContext = Depends(org_context),
    status: Literal["all", "active", "expiring", "expired"] = "all",
    q: str | None = Query(None, max_length=200),
    collection_id: uuid.UUID | None = None,
):
    stmt = select(VerifiedAnswer).where(
        VerifiedAnswer.org_id == ctx.org_id,
        VerifiedAnswer.collection_id.in_(await _visible_ids(ctx) or {uuid.uuid4()}),
    )
    if collection_id:
        stmt = stmt.where(VerifiedAnswer.collection_id == collection_id)
    if q and q.strip():
        like = f"%{q.strip().replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(
            or_(VerifiedAnswer.question.ilike(like), VerifiedAnswer.answer.ilike(like))
        )
    rows = list((await ctx.db.execute(stmt.order_by(VerifiedAnswer.verified_at.desc()))).scalars())
    out = await _out(ctx, rows)
    return out if status == "all" else [v for v in out if v.status == status]


@router.post("/verified-answers", response_model=VerifiedAnswerOut, status_code=201)
async def create_verified(body: VerifiedAnswerCreate, ctx: OrgContext = Depends(admin_ctx)):
    question, answer, sources = body.question, body.answer, []
    if body.message_id:
        message = await ctx.db.get(Message, body.message_id)
        conv = await ctx.db.get(Conversation, message.conversation_id) if message else None
        # Only the asker's own answers: conversations are private to their owner.
        if (
            message is None
            or message.role != "assistant"
            or conv is None
            or conv.org_id != ctx.org_id
            or conv.user_id != ctx.user.id
        ):
            raise not_found("Message")
        if message.status != "complete":
            raise bad_request("Only complete answers can be verified", "answer_incomplete")
        if question is None:
            asked = await ctx.db.scalar(
                select(Message.content)
                .where(
                    Message.conversation_id == conv.id,
                    Message.role == "user",
                    Message.created_at <= message.created_at,
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
            question = asked
        answer = answer or message.content
        cited = set(cited_refs(message.content))
        sources = [s for s in message.sources if s.get("ref") in cited]
    if not question or not answer:
        raise bad_request("A question and an answer are required", "missing_answer")
    col = await _collection(ctx, body.collection_id)
    now = datetime.now(UTC)
    va = VerifiedAnswer(
        id=uuid.uuid4(),
        org_id=ctx.org_id,
        collection_id=col.id,
        message_id=body.message_id,
        question=question.strip()[:2000],
        answer=answer.strip(),
        sources=sources,
        owner_id=await _member(ctx, body.owner_id) if body.owner_id else ctx.user.id,
        verified_by_id=ctx.user.id,
        verified_at=now,
        expires_at=svc.expiry(body.expires_in_days, now),
    )
    await _index(ctx, va, col)
    ctx.db.add(va)
    audit.record_event(
        ctx,
        "verified_answer.created",
        "verified_answer",
        va.id,
        va.question,
        collection=col.name,
        owner=(await audit.emails(ctx.db, [va.owner_id]) or [None])[0],
        expires_at=va.expires_at,
        message_id=va.message_id,
    )
    await ctx.db.commit()
    return (await _out(ctx, [va]))[0]


def _fields(va: VerifiedAnswer) -> dict:
    return {
        "question": va.question,
        "answer": va.answer,
        "collection_id": va.collection_id,
        "owner_id": va.owner_id,
        "expires_at": va.expires_at,
    }


@router.get("/verified-answers/{answer_id}", response_model=VerifiedAnswerOut)
async def get_verified(answer_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    return (await _out(ctx, [await _get(ctx, answer_id)]))[0]


@router.patch("/verified-answers/{answer_id}", response_model=VerifiedAnswerOut)
async def update_verified(
    answer_id: uuid.UUID, body: VerifiedAnswerPatch, ctx: OrgContext = Depends(admin_ctx)
):
    """Edit the text, move it, change the owner or the expiry. Editing doesn't
    re-verify: use `reverify` to vouch for it again."""
    va = await _get(ctx, answer_id)
    before = _fields(va)
    if body.question is not None:
        va.question = body.question.strip()
    if body.answer is not None:
        va.answer = body.answer.strip()
    if body.owner_id is not None:
        va.owner_id = await _member(ctx, body.owner_id)
    if "expires_at" in body.model_fields_set:
        va.expires_at = body.expires_at
    col = await _collection(ctx, body.collection_id or va.collection_id)
    va.collection_id = col.id
    await _index(ctx, va, col)
    if diff := audit.changes(before, _fields(va)):
        if "answer" in diff:  # the text can be long: say it changed, not what it says
            diff["answer"] = {"changed": True}
        audit.record_event(
            ctx,
            "verified_answer.updated",
            "verified_answer",
            va.id,
            va.question,
            changes=diff,
            collection=col.name,
        )
    await ctx.db.commit()
    return (await _out(ctx, [va]))[0]


@router.post("/verified-answers/{answer_id}/reverify", response_model=VerifiedAnswerOut)
async def reverify(answer_id: uuid.UUID, body: ReverifyIn, ctx: OrgContext = Depends(admin_ctx)):
    """Vouch for the answer again: you become the verifier and the expiry restarts."""
    va = await _get(ctx, answer_id)
    va.verified_by_id = ctx.user.id
    va.verified_at = datetime.now(UTC)
    va.expires_at = svc.expiry(body.expires_in_days, va.verified_at)
    await _index(ctx, va, await _collection(ctx, va.collection_id))
    audit.record_event(
        ctx,
        "verified_answer.reverified",
        "verified_answer",
        va.id,
        va.question,
        expires_at=va.expires_at,
    )
    await ctx.db.commit()
    return (await _out(ctx, [va]))[0]


@router.delete("/verified-answers/{answer_id}", status_code=204)
async def delete_verified(answer_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    va = await _get(ctx, answer_id)
    doc = await ctx.db.get(Document, va.document_id)
    audit.record_event(ctx, "verified_answer.deleted", "verified_answer", va.id, va.question)
    await ctx.db.delete(doc if doc is not None else va)  # the answer goes with its document
    await ctx.db.commit()
    return Response(status_code=204)
