import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from app.agent.engine import ChatRequest, message_payload, run_chat
from app.agent.prompts import TITLE_SYSTEM
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.deps import OrgContext, org_context
from app.core.errors import ApiError, bad_request, not_found
from app.llm.client import structured_call
from app.models import Agent, ChatAttachment, Conversation, Membership, Message, Organization
from app.schemas.common import (
    ConversationCreate,
    ConversationDetail,
    ConversationOut,
    ConversationPatch,
    FeedbackIn,
    MessageCreate,
    MessageOut,
    OrgSettings,
)

# Statuses for errors a non-streaming request reports (see the SSE `error` event).
ERROR_STATUS = {
    "llm_not_configured": 503,
    "llm_auth": 502,
    "llm_forbidden": 502,
    "llm_model": 502,
    "llm_rate_limited": 429,
    "llm_bad_request": 502,
    "llm_timeout": 504,
    "llm_unreachable": 502,
    "llm_error": 502,
}

log = logging.getLogger("strata.chat")
router = APIRouter(prefix="/orgs/{slug}", tags=["chat"])

DEFAULT_TITLE = "New conversation"
PING_SECONDS = 15
_background: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


async def _scope_ids(ctx: OrgContext, slugs: list[str] | None) -> list[uuid.UUID]:
    """Requested org slugs → ids the caller belongs to; the current org always first."""
    ids = [ctx.org_id]
    if slugs:
        rows = await ctx.db.execute(
            select(Organization.id)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == ctx.user.id, Organization.slug.in_(slugs))
        )
        ids += [i for i in rows.scalars() if i != ctx.org_id]
    return ids


async def _conversation(ctx: OrgContext, conversation_id: uuid.UUID) -> Conversation:
    conv = await ctx.db.get(Conversation, conversation_id)
    if conv is None or conv.org_id != ctx.org_id or conv.user_id != ctx.user.id:
        raise not_found("Conversation")
    return conv


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    ctx: OrgContext = Depends(org_context),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    rows = await ctx.db.execute(
        select(Conversation)
        .where(Conversation.org_id == ctx.org_id, Conversation.user_id == ctx.user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows.scalars())


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(body: ConversationCreate, ctx: OrgContext = Depends(org_context)):
    mode = body.mode
    if body.agent_id:
        agent = await ctx.db.get(Agent, body.agent_id)
        if agent is None or agent.org_id != ctx.org_id:
            raise not_found("Agent")
        mode = mode or agent.mode
    conv = Conversation(
        org_id=ctx.org_id,
        user_id=ctx.user.id,
        agent_id=body.agent_id,
        title=DEFAULT_TITLE,
        scope_org_ids=await _scope_ids(ctx, body.scope_org_slugs),
        mode=mode or OrgSettings(**(ctx.org.settings or {})).default_mode,
    )
    ctx.db.add(conv)
    await ctx.db.commit()
    return conv


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    conv = await _conversation(ctx, conversation_id)
    rows = await ctx.db.execute(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
    )
    return ConversationDetail(
        conversation=ConversationOut.model_validate(conv),
        messages=[MessageOut.model_validate(m) for m in rows.scalars()],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: uuid.UUID, body: ConversationPatch, ctx: OrgContext = Depends(org_context)
):
    conv = await _conversation(ctx, conversation_id)
    if body.title is not None:
        conv.title = body.title.strip()
    if body.mode is not None:
        conv.mode = body.mode
    if body.scope_org_slugs is not None:
        conv.scope_org_ids = await _scope_ids(ctx, body.scope_org_slugs)
    await ctx.db.commit()
    return conv


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    await ctx.db.delete(await _conversation(ctx, conversation_id))
    await ctx.db.commit()
    return Response(status_code=204)


@router.post("/messages/{message_id}/feedback", status_code=204)
async def message_feedback(
    message_id: uuid.UUID, body: FeedbackIn, ctx: OrgContext = Depends(org_context)
):
    message = await ctx.db.get(Message, message_id)
    if message is None or message.role != "assistant":
        raise not_found("Message")
    await _conversation(ctx, message.conversation_id)
    message.feedback = body.feedback
    await ctx.db.commit()
    return Response(status_code=204)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _refine_title(conversation_id: uuid.UUID, question: str, model: str) -> None:
    try:
        result = await structured_call(
            model=model,
            system=TITLE_SYSTEM,
            prompt=f"Write a title for a conversation that starts with:\n\n{question[:2000]}",
            schema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            effort="low",
            max_tokens=2000,
        )
        title = result["title"].strip().strip('"').rstrip(".")[:80]
        if not title:
            return
        async with SessionLocal() as db:
            conv = await db.get(Conversation, conversation_id)
            if conv is not None:
                conv.title = title
                await db.commit()
    except Exception as exc:
        log.info("Title refinement skipped: %s", exc)


async def _bind_attachments(
    ctx: OrgContext, conv: Conversation, ids: list[uuid.UUID]
) -> list[ChatAttachment]:
    if not ids:
        return []
    rows = (
        (
            await ctx.db.execute(
                select(ChatAttachment).where(
                    ChatAttachment.id.in_(ids),
                    ChatAttachment.org_id == ctx.org_id,
                    ChatAttachment.user_id == ctx.user.id,
                )
            )
        )
        .scalars()
        .all()
    )
    found = {a.id: a for a in rows}
    for attachment_id in ids:
        attachment = found.get(attachment_id)
        if attachment is None:
            raise not_found("Attachment")
        if attachment.conversation_id not in (None, conv.id):
            raise bad_request(
                f"{attachment.filename} belongs to another conversation", "attachment_in_use"
            )
        attachment.conversation_id = conv.id
    return [found[i] for i in dict.fromkeys(ids)]


@router.post(
    "/conversations/{conversation_id}/messages",
    responses={
        200: {
            "description": "SSE stream (default), or `{message}` JSON with `stream=false`",
            "content": {"text/event-stream": {}, "application/json": {}},
        }
    },
)
async def send_message(
    conversation_id: uuid.UUID,
    body: MessageCreate,
    ctx: OrgContext = Depends(org_context),
    stream: bool = Query(
        True, description="false waits for the full answer and returns {message} as JSON"
    ),
):
    conv = await _conversation(ctx, conversation_id)
    if body.mode:
        conv.mode = body.mode
    if body.scope_org_slugs is not None:
        conv.scope_org_ids = await _scope_ids(ctx, body.scope_org_slugs)
    attachments = await _bind_attachments(ctx, conv, body.attachment_ids)

    question = body.content.strip()
    first_message = conv.title == DEFAULT_TITLE
    if first_message:
        conv.title = question if len(question) <= 60 else question[:57].rstrip() + "…"
    conv.updated_at = datetime.now(UTC)

    user_message = Message(
        conversation_id=conv.id,
        role="user",
        content=question,
        attachments=[
            {
                "id": str(a.id),
                "filename": a.filename,
                "mime": a.mime,
                "size_bytes": a.size_bytes,
                "token_count": a.token_count,
            }
            for a in attachments
        ],
    )
    ctx.db.add(user_message)
    await ctx.db.flush()
    assistant = Message(conversation_id=conv.id, role="assistant", content="", status="streaming")
    ctx.db.add(assistant)
    await ctx.db.commit()
    await ctx.db.refresh(user_message)

    settings = OrgSettings(**(ctx.org.settings or {}))
    if first_message and get_settings().llm_configured:
        _spawn(_refine_title(conv.id, question, settings.model))

    queue: asyncio.Queue = asyncio.Queue()
    req = ChatRequest(
        conversation_id=conv.id,
        assistant_message_id=assistant.id,
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        question=question,
        mode=conv.mode,
        scope_org_ids=list(conv.scope_org_ids or [ctx.org_id]),
        collection_ids=body.collection_ids,
        attachment_ids=[a.id for a in attachments],
        web_search=body.web_search,
    )
    # The run owns its own task and DB session: it finishes and persists even if
    # the client disconnects mid-stream.
    _spawn(run_chat(req, queue))
    start = {
        "user_message": message_payload(user_message),
        "assistant_message_id": str(assistant.id),
        "mode": conv.mode,
    }

    if not stream:
        while (item := await queue.get()) is not None:
            event, data = item
            if event == "done":
                return JSONResponse(data)
            if event == "error":
                raise ApiError(ERROR_STATUS.get(data["code"], 500), data["detail"], data["code"])
        raise ApiError(500, "The answer stream ended unexpectedly", "internal_error")

    async def events():
        yield _sse("start", start)
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if item is None:
                break
            yield _sse(*item)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            # no-transform stops proxies (including the Next.js rewrite, which gzips)
            # from buffering the stream until it ends.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
