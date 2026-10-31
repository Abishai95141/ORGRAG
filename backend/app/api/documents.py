"""Documents across an org: browse, read, curate (boost / hide) and vote.

Every route checks the caller can see the document's collection; documents in
collections they can't see behave as missing."""

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import OrgContext, admin_ctx, org_context
from app.core.errors import not_found
from app.models import Chunk, Conversation, Document, DocumentFeedback, Message, Source
from app.retrieval.access import visible_collections
from app.schemas.common import (
    MAX_OFFSET,
    DocumentDetail,
    DocumentFeedbackIn,
    DocumentFeedbackOut,
    DocumentPage,
    DocumentPatch,
    DocumentRow,
)

router = APIRouter(prefix="/orgs/{slug}", tags=["documents"])

SORTS = {
    "title": (Document.title.asc(),),
    "updated": (func.coalesce(Document.source_updated_at, Document.updated_at).desc(),),
    "feedback": ((Document.feedback_up - Document.feedback_down).desc(), Document.title.asc()),
    "boost": (Document.boost.desc(), Document.title.asc()),
}


def _row(doc: Document, chunks: int) -> DocumentRow:
    return DocumentRow(
        id=doc.id,
        source_id=doc.source_id,
        collection_id=doc.collection_id,
        title=doc.title,
        url=doc.url,
        mime=doc.mime,
        chunk_count=chunks,
        token_count=doc.token_count,
        boost=doc.boost,
        hidden=doc.hidden,
        feedback_up=doc.feedback_up,
        feedback_down=doc.feedback_down,
        source_updated_at=doc.source_updated_at,
        updated_at=doc.updated_at,
    )


def _like(q: str) -> str:
    """Substring pattern with the caller's % and _ taken literally."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def document_page(
    db: AsyncSession, where: list, *, q: str | None, limit: int, offset: int, sort: str = "title"
) -> DocumentPage:
    if q:
        like = _like(q)
        where = [
            *where,
            or_(Document.title.ilike(like, escape="\\"), Document.url.ilike(like, escape="\\")),
        ]
    total = (
        await db.execute(select(func.count()).select_from(Document).where(*where))
    ).scalar_one()
    docs = (
        (
            await db.execute(
                select(Document)
                .where(*where)
                .order_by(*SORTS[sort], Document.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    # Chunk counts for this page only (not an aggregate over every chunk in the table).
    counts: dict[uuid.UUID, int] = {}
    if docs:
        rows = await db.execute(
            select(Chunk.document_id, func.count())
            .where(Chunk.document_id.in_([d.id for d in docs]))
            .group_by(Chunk.document_id)
        )
        counts = dict(rows.all())
    return DocumentPage(
        items=[_row(d, counts.get(d.id, 0)) for d in docs], total=total, limit=limit, offset=offset
    )


async def _visible_ids(ctx: OrgContext) -> set[uuid.UUID]:
    return {c.id for c in await visible_collections(ctx.db, ctx.org_id, ctx.user.id, ctx.role)}


async def _get_document(ctx: OrgContext, document_id: uuid.UUID) -> Document:
    doc = await ctx.db.get(Document, document_id)
    if doc is None or doc.org_id != ctx.org_id or doc.collection_id not in await _visible_ids(ctx):
        raise not_found("Document")
    return doc


async def _chunk_count(db: AsyncSession, doc: Document) -> int:
    return (await db.execute(select(func.count()).where(Chunk.document_id == doc.id))).scalar_one()


async def _my_vote(ctx: OrgContext, doc: Document) -> str | None:
    return (
        await ctx.db.execute(
            select(DocumentFeedback.vote).where(
                DocumentFeedback.document_id == doc.id, DocumentFeedback.user_id == ctx.user.id
            )
        )
    ).scalar_one_or_none()


@router.get("/documents", response_model=DocumentPage)
async def list_documents(
    ctx: OrgContext = Depends(org_context),
    q: str | None = Query(None, max_length=200),
    collection_id: uuid.UUID | None = None,
    source_id: uuid.UUID | None = None,
    hidden: bool | None = Query(None, description="Filter on the hidden flag"),
    sort: Literal["title", "updated", "feedback", "boost"] = "title",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
):
    visible = await _visible_ids(ctx)
    where = [Document.org_id == ctx.org_id, Document.collection_id.in_(visible or {uuid.uuid4()})]
    if collection_id:
        where.append(Document.collection_id == collection_id)
    if source_id:
        where.append(Document.source_id == source_id)
    if hidden is not None:
        where.append(Document.hidden.is_(hidden))
    return await document_page(ctx.db, where, q=q, limit=limit, offset=offset, sort=sort)


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    doc = await _get_document(ctx, document_id)
    return DocumentDetail(
        **_row(doc, await _chunk_count(ctx.db, doc)).model_dump(),
        content=doc.content,
        my_vote=await _my_vote(ctx, doc),
    )


@router.patch("/documents/{document_id}", response_model=DocumentDetail)
async def curate_document(
    document_id: uuid.UUID, body: DocumentPatch, ctx: OrgContext = Depends(admin_ctx)
):
    """Admin curation: `boost` (-3..3) scales the document's ranking; `hidden` removes
    it from search and answers without deleting it."""
    doc = await _get_document(ctx, document_id)
    if body.boost is not None:
        doc.boost = body.boost
    if body.hidden is not None:
        doc.hidden = body.hidden
    await ctx.db.commit()
    return DocumentDetail(
        **_row(doc, await _chunk_count(ctx.db, doc)).model_dump(),
        content=doc.content,
        my_vote=await _my_vote(ctx, doc),
    )


@router.post("/documents/{document_id}/feedback", response_model=DocumentFeedbackOut)
async def document_feedback(
    document_id: uuid.UUID, body: DocumentFeedbackIn, ctx: OrgContext = Depends(org_context)
):
    """Vote a source up or down (null clears). One vote per person per document; the
    totals feed into ranking."""
    doc = await _get_document(ctx, document_id)
    if body.message_id is not None:
        # Only an answer in one of the caller's own conversations in this org.
        owner = (
            await ctx.db.execute(
                select(Conversation.id)
                .join(Message, Message.conversation_id == Conversation.id)
                .where(
                    Message.id == body.message_id,
                    Conversation.org_id == ctx.org_id,
                    Conversation.user_id == ctx.user.id,
                )
            )
        ).first()
        if owner is None:
            raise not_found("Message")
    # Serialize votes per document: concurrent votes (even by the same person) would
    # otherwise race on the unique (document, user) row and on the recounted totals.
    await ctx.db.execute(select(Document.id).where(Document.id == doc.id).with_for_update())
    existing = (
        await ctx.db.execute(
            select(DocumentFeedback).where(
                DocumentFeedback.document_id == doc.id, DocumentFeedback.user_id == ctx.user.id
            )
        )
    ).scalar_one_or_none()
    if existing is not None and body.vote is not None:
        existing.vote = body.vote
        existing.message_id = body.message_id or existing.message_id
    elif existing is not None:
        await ctx.db.delete(existing)
    elif body.vote is not None:
        ctx.db.add(
            DocumentFeedback(
                org_id=ctx.org_id,
                document_id=doc.id,
                user_id=ctx.user.id,
                message_id=body.message_id,
                vote=body.vote,
            )
        )
    await ctx.db.flush()
    counts = dict(
        (
            await ctx.db.execute(
                select(DocumentFeedback.vote, func.count())
                .where(DocumentFeedback.document_id == doc.id)
                .group_by(DocumentFeedback.vote)
            )
        ).all()
    )
    doc.feedback_up = counts.get("up", 0)
    doc.feedback_down = counts.get("down", 0)
    await ctx.db.commit()
    return DocumentFeedbackOut(
        document_id=doc.id,
        vote=body.vote,
        feedback_up=doc.feedback_up,
        feedback_down=doc.feedback_down,
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    doc = await _get_document(ctx, document_id)
    source = await ctx.db.get(Source, doc.source_id)
    await ctx.db.delete(doc)
    await ctx.db.commit()
    if source and source.kind == "upload":
        folder = (Path(get_settings().file_storage_dir) / str(source.id)).resolve()
        path = (folder / doc.external_id).resolve()
        if path.parent == folder:  # never outside the source's own folder
            path.unlink(missing_ok=True)
    return Response(status_code=204)
