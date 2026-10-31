"""Files attached to chat. Uploading parses the file to Markdown straight away (so
problems surface before the message is sent); sending a message with
`attachment_ids` binds the files to that conversation."""

import re
import uuid

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import OrgContext, org_context
from app.core.errors import ApiError, bad_request, not_found
from app.ingestion.chunker import estimate_tokens
from app.ingestion.parsers import UnsupportedFileType, mime_for, parse_document
from app.models import ChatAttachment, Conversation
from app.schemas.common import AttachmentDetail, AttachmentOut

router = APIRouter(prefix="/orgs/{slug}", tags=["attachments"])

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _display_name(filename: str | None) -> str:
    """The bare file name (no client path, either slash style), without control chars."""
    name = _CONTROL.sub("", (filename or "").replace("\\", "/").rsplit("/", 1)[-1]).strip()
    return name[:255] or "file"


async def get_attachment(ctx: OrgContext, attachment_id: uuid.UUID) -> ChatAttachment:
    attachment = await ctx.db.get(ChatAttachment, attachment_id)
    if attachment is None or attachment.org_id != ctx.org_id or attachment.user_id != ctx.user.id:
        raise not_found("Attachment")
    return attachment


@router.post("/attachments", response_model=AttachmentOut, status_code=201)
async def upload_attachment(
    file: UploadFile = File(...),
    conversation_id: uuid.UUID | None = Form(None),
    ctx: OrgContext = Depends(org_context),
):
    """Upload a file for chat. It is parsed to text now (so an unreadable file fails
    here, not mid-answer) and bound to a conversation when a message references it."""
    settings = get_settings()
    filename = _display_name(file.filename)
    try:
        mime = mime_for(filename)
    except UnsupportedFileType as exc:
        raise bad_request(str(exc), "unsupported_file") from exc
    limit = settings.max_upload_mb * 1024 * 1024
    if (file.size or 0) > limit:  # checked before reading it into memory
        raise ApiError(
            413, f"{filename} is larger than {settings.max_upload_mb} MB", "file_too_large"
        )
    data = await file.read()
    if conversation_id is not None:
        conv = await ctx.db.get(Conversation, conversation_id)
        if conv is None or conv.org_id != ctx.org_id or conv.user_id != ctx.user.id:
            raise not_found("Conversation")
    try:
        markdown = (await parse_document(data, mime)).markdown
    except Exception as exc:
        raise bad_request(f"Couldn't read {filename}: {exc}", "unreadable_file") from exc
    markdown = markdown.strip()
    if not markdown:
        raise bad_request(f"{filename} has no readable text", "empty_file")
    markdown = markdown[: settings.max_attachment_chars]
    attachment = ChatAttachment(
        org_id=ctx.org_id,
        user_id=ctx.user.id,
        conversation_id=conversation_id,
        filename=filename,
        mime=mime,
        size_bytes=len(data),
        content=markdown,
        token_count=estimate_tokens(markdown),
    )
    ctx.db.add(attachment)
    await ctx.db.commit()
    return attachment


@router.get("/attachments/{attachment_id}", response_model=AttachmentDetail)
async def read_attachment(attachment_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    return await get_attachment(ctx, attachment_id)


@router.delete("/attachments/{attachment_id}", status_code=204)
async def delete_attachment(attachment_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    await ctx.db.delete(await get_attachment(ctx, attachment_id))
    await ctx.db.commit()
    return Response(status_code=204)


@router.get("/conversations/{conversation_id}/attachments", response_model=list[AttachmentOut])
async def conversation_attachments(
    conversation_id: uuid.UUID, ctx: OrgContext = Depends(org_context)
):
    conv = await ctx.db.get(Conversation, conversation_id)
    if conv is None or conv.org_id != ctx.org_id or conv.user_id != ctx.user.id:
        raise not_found("Conversation")
    rows = await ctx.db.execute(
        select(ChatAttachment)
        .where(ChatAttachment.conversation_id == conv.id)
        .order_by(ChatAttachment.created_at)
    )
    return list(rows.scalars())
