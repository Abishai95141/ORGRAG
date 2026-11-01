import asyncio
import re
import shutil
import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import func, select

from app.api.documents import WEAK_FILTER, document_page
from app.api.serializers import WEAKLY_READ, sources_out
from app.connectors.base import ConnectorError
from app.connectors.registry import CONNECTORS, get_connector_class
from app.connectors.upload import storage_dir
from app.core import audit
from app.core.config import get_settings
from app.core.deps import OrgContext, admin_ctx, current_user, member_ctx, org_context
from app.core.errors import ApiError, bad_request, not_found
from app.ingestion.parsers import UnsupportedFileType, mime_for
from app.ingestion.verified import VERIFIED_KIND
from app.models import Collection, Document, Source
from app.retrieval.access import visible_collections
from app.schemas.common import (
    MAX_OFFSET,
    ConnectorCheckIn,
    ConnectorCheckOut,
    ConnectorInfo,
    DocumentPage,
    SourceCreate,
    SourceKind,
    SourceOut,
    SourcePatch,
    UploadResult,
)
from app.worker.queue import enqueue_sync

router = APIRouter(tags=["sources"])
CHECK_SECONDS = 45  # a connector check answers the form, so it can't take long


@router.get("/connectors", response_model=list[ConnectorInfo], dependencies=[Depends(current_user)])
async def connector_catalogue():
    """Source kinds and their config JSON Schemas. Fields marked `"secret": true` are
    write-only."""
    return [
        ConnectorInfo(
            kind=c.kind,
            name=c.display_name,
            description=c.description,
            config_schema=c.config_schema,
            checkable=c.can_check,
        )
        for c in CONNECTORS.values()
    ]


@router.post("/orgs/{slug}/connectors/{kind}/check", response_model=ConnectorCheckOut)
async def check_connector(
    kind: SourceKind, body: ConnectorCheckIn, ctx: OrgContext = Depends(admin_ctx)
):
    """What the given credentials can reach (Drive folders and shared drives, Slack
    channels), so the form can offer them as a list instead of asking for ids. Nothing
    is saved."""
    connector_cls = get_connector_class(kind)
    if not connector_cls.can_check:
        raise bad_request(f"{connector_cls.display_name} has nothing to check", "check_unsupported")
    config, secrets = _validated_config(kind, body.config)
    if body.source_id is not None:
        source = await _get_source(ctx, body.source_id)
        if source.kind != kind:
            raise not_found("Source")
        secrets = {**(source.secrets or {}), **secrets}
    try:
        result = await asyncio.wait_for(connector_cls(config, secrets).check(), CHECK_SECONDS)
    except ConnectorError as exc:
        raise bad_request(str(exc), "connector_error") from exc
    except (httpx.HTTPError, TimeoutError) as exc:
        raise ApiError(
            502, f"Couldn't reach {connector_cls.display_name}; try again", "connector_unreachable"
        ) from exc
    return ConnectorCheckOut(**result)


async def _visible_collection_ids(ctx: OrgContext) -> set[uuid.UUID]:
    return {c.id for c in await visible_collections(ctx.db, ctx.org_id, ctx.user.id, ctx.role)}


async def _get_source(ctx: OrgContext, source_id: uuid.UUID) -> Source:
    source = await ctx.db.get(Source, source_id)
    if (
        source is None
        or source.org_id != ctx.org_id
        or source.kind == VERIFIED_KIND
        or source.collection_id not in await _visible_collection_ids(ctx)
    ):
        raise not_found("Source")
    return source


async def _one(ctx: OrgContext, source: Source) -> SourceOut:
    return (await sources_out(ctx.db, [source]))[0]


def _validated_config(kind: str, config: dict) -> tuple[dict, dict]:
    """Split secret fields (per the connector's schema) out of the config, then
    validate the rest. Secrets are stored apart and never returned."""
    try:
        connector = get_connector_class(kind)
    except ValueError as exc:
        raise bad_request(str(exc), "unknown_connector") from exc
    secrets = {}
    config = dict(config)
    for name in connector.secret_fields():
        value = config.pop(name, None)
        config.pop(f"{name}_set", None)
        if value not in (None, ""):
            secrets[name] = value if isinstance(value, str) else str(value)
    try:
        return connector.validate_config(config), secrets
    except (ValueError, TypeError) as exc:
        raise bad_request(str(exc), "invalid_config") from exc


@router.get("/orgs/{slug}/sources", response_model=list[SourceOut])
async def list_sources(ctx: OrgContext = Depends(org_context)):
    visible = await _visible_collection_ids(ctx)
    rows = await ctx.db.execute(
        select(Source)
        .where(
            Source.org_id == ctx.org_id,
            Source.collection_id.in_(visible or {uuid.uuid4()}),
            # Verified answers are managed under Verified answers, not as a source.
            Source.kind != VERIFIED_KIND,
        )
        .order_by(Source.created_at.desc())
    )
    return await sources_out(ctx.db, list(rows.scalars()))


@router.post("/orgs/{slug}/sources", response_model=SourceOut, status_code=201)
async def create_source(body: SourceCreate, ctx: OrgContext = Depends(admin_ctx)):
    col = await ctx.db.get(Collection, body.collection_id)
    if col is None or col.org_id != ctx.org_id:
        raise not_found("Collection")
    config, secrets = _validated_config(body.kind, body.config)
    source = Source(
        org_id=ctx.org_id,
        collection_id=col.id,
        kind=body.kind,
        name=body.name.strip(),
        config=config,
        secrets=secrets,
        status="idle",
        sync_interval_minutes=body.sync_interval_minutes,
    )
    ctx.db.add(source)
    await ctx.db.flush()
    if body.kind != "upload":
        await enqueue_sync(ctx.db, source)
    # The config is what clients see anyway; secret fields only by name.
    audit.record_event(
        ctx,
        "source.created",
        "source",
        source.id,
        source.name,
        kind=source.kind,
        collection=col.name,
        config=config,
        write_only_fields=sorted(secrets),
        sync_interval_minutes=source.sync_interval_minutes,
    )
    await ctx.db.commit()
    return await _one(ctx, source)


@router.get("/orgs/{slug}/sources/{source_id}", response_model=SourceOut)
async def get_source(source_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    return await _one(ctx, await _get_source(ctx, source_id))


@router.patch("/orgs/{slug}/sources/{source_id}", response_model=SourceOut)
async def update_source(
    source_id: uuid.UUID, body: SourcePatch, ctx: OrgContext = Depends(admin_ctx)
):
    source = await _get_source(ctx, source_id)
    before = _fields(source)
    secrets: dict = {}
    if body.name is not None:
        source.name = body.name.strip()
    if body.config is not None:
        config, secrets = _validated_config(source.kind, body.config)
        source.config = config
        if secrets:
            source.secrets = {**(source.secrets or {}), **secrets}
    if "sync_interval_minutes" in body.model_fields_set:
        source.sync_interval_minutes = body.sync_interval_minutes
    diff = audit.changes(before, _fields(source))
    if diff or secrets:
        audit.record_event(
            ctx,
            "source.updated",
            "source",
            source.id,
            source.name,
            changes=diff,
            write_only_fields=sorted(secrets),
        )
    await ctx.db.commit()
    return await _one(ctx, source)


def _fields(source: Source) -> dict:
    """What an update can change, for the audit log (secrets are never part of it)."""
    return {
        "name": source.name,
        "sync_interval_minutes": source.sync_interval_minutes,
        **{f"config.{k}": v for k, v in (source.config or {}).items()},
    }


@router.delete("/orgs/{slug}/sources/{source_id}", status_code=204)
async def delete_source(source_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    source = await _get_source(ctx, source_id)
    documents = await ctx.db.scalar(
        select(func.count(Document.id)).where(Document.source_id == source.id)
    )
    audit.record_event(
        ctx,
        "source.deleted",
        "source",
        source.id,
        source.name,
        kind=source.kind,
        documents=documents or 0,
    )
    await ctx.db.delete(source)
    await ctx.db.commit()
    # Files go only once the rows are gone, so a failed delete leaves a working source.
    if source.kind == "upload":
        shutil.rmtree(Path(get_settings().file_storage_dir) / str(source.id), ignore_errors=True)
    return Response(status_code=204)


@router.post("/orgs/{slug}/sources/{source_id}/sync", response_model=SourceOut)
async def sync_source(source_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    """Queue a full sync (also prunes documents gone upstream). Returns at once with
    status `queued`; poll the source for progress."""
    source = await _get_source(ctx, source_id)
    await enqueue_sync(ctx.db, source)
    audit.record_event(ctx, "source.synced", "source", source.id, source.name, full=True)
    await ctx.db.commit()
    return await _one(ctx, source)


_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def _safe_name(filename: str) -> str:
    name = _UNSAFE.sub("_", Path(filename).name).strip(" .")
    return name[:180] or f"file-{uuid.uuid4().hex[:8]}"


@router.post("/orgs/{slug}/sources/{source_id}/files", response_model=UploadResult)
async def upload_files(
    source_id: uuid.UUID,
    files: list[UploadFile] = File(...),
    ctx: OrgContext = Depends(member_ctx),
):
    """Add files to an upload source and queue a sync. All files are checked (type,
    size, count) before any is stored, so a rejected batch stores nothing. A file with
    the same name replaces the previous one."""
    source = await _get_source(ctx, source_id)
    if source.kind != "upload":
        raise bad_request("Files can only be added to upload sources", "not_upload_source")
    settings = get_settings()
    if len(files) > settings.max_upload_files:
        raise bad_request(
            f"Upload at most {settings.max_upload_files} files per request", "too_many_files"
        )
    limit = settings.max_upload_mb * 1024 * 1024
    named = []
    for upload in files:
        name = _safe_name(upload.filename or "")
        try:
            mime_for(name)
        except UnsupportedFileType as exc:
            raise bad_request(str(exc), "unsupported_file") from exc
        if (upload.size or 0) > limit:
            raise ApiError(413, f"{name} is larger than {limit // 1_048_576} MB", "file_too_large")
        named.append((name, upload))
    folder = storage_dir(str(source.id))
    for name, upload in named:
        (folder / name).write_bytes(await upload.read())
    await enqueue_sync(ctx.db, source)
    audit.record_event(
        ctx, "source.files_uploaded", "source", source.id, source.name, files=[n for n, _ in named]
    )
    await ctx.db.commit()
    return UploadResult(accepted=len(named), source=await _one(ctx, source))


# ── documents ────────────────────────────────────────────────────────────────
@router.get("/orgs/{slug}/sources/{source_id}/documents", response_model=DocumentPage)
async def list_documents(
    source_id: uuid.UUID,
    ctx: OrgContext = Depends(org_context),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=MAX_OFFSET),
    q: str | None = Query(None, max_length=200),
    weak: bool | None = Query(None, description=WEAK_FILTER),
):
    source = await _get_source(ctx, source_id)
    where = [Document.source_id == source.id]
    if weak is not None:
        where.append(WEAKLY_READ if weak else ~WEAKLY_READ)
    return await document_page(ctx.db, where, q=q, limit=limit, offset=offset)
