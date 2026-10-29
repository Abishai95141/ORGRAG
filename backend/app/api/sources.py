import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import func, or_, select

from app.api.serializers import sources_out
from app.connectors.registry import CONNECTORS, get_connector_class
from app.connectors.upload import storage_dir
from app.core.config import get_settings
from app.core.deps import OrgContext, admin_ctx, member_ctx, org_context
from app.core.errors import ApiError, bad_request, not_found
from app.ingestion.parsers import UnsupportedFileType, mime_for
from app.models import Chunk, Collection, Document, Source
from app.retrieval.access import visible_collections
from app.schemas.common import (
    ConnectorInfo,
    DocumentDetail,
    DocumentPage,
    DocumentRow,
    SourceCreate,
    SourceOut,
    SourcePatch,
    UploadResult,
)
from app.worker.queue import enqueue_sync

router = APIRouter(tags=["sources"])


@router.get("/connectors", response_model=list[ConnectorInfo])
async def connector_catalogue():
    return [
        ConnectorInfo(
            kind=c.kind,
            name=c.display_name,
            description=c.description,
            config_schema=c.config_schema,
        )
        for c in CONNECTORS.values()
    ]


async def _visible_collection_ids(ctx: OrgContext) -> set[uuid.UUID]:
    return {c.id for c in await visible_collections(ctx.db, ctx.org_id, ctx.user.id, ctx.role)}


async def _get_source(ctx: OrgContext, source_id: uuid.UUID) -> Source:
    source = await ctx.db.get(Source, source_id)
    if (
        source is None
        or source.org_id != ctx.org_id
        or source.collection_id not in await _visible_collection_ids(ctx)
    ):
        raise not_found("Source")
    return source


async def _one(ctx: OrgContext, source: Source) -> SourceOut:
    return (await sources_out(ctx.db, [source]))[0]


def _validated_config(kind: str, config: dict) -> tuple[dict, dict]:
    secrets = {}
    config = dict(config)
    if token := config.pop("token", None):
        secrets["token"] = str(token)
    config.pop("token_set", None)
    try:
        return get_connector_class(kind).validate_config(config), secrets
    except (ValueError, TypeError) as exc:
        raise bad_request(str(exc), "invalid_config") from exc


@router.get("/orgs/{slug}/sources", response_model=list[SourceOut])
async def list_sources(ctx: OrgContext = Depends(org_context)):
    visible = await _visible_collection_ids(ctx)
    rows = await ctx.db.execute(
        select(Source)
        .where(Source.org_id == ctx.org_id, Source.collection_id.in_(visible or {uuid.uuid4()}))
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
    if body.name is not None:
        source.name = body.name.strip()
    if body.config is not None:
        config, secrets = _validated_config(source.kind, body.config)
        source.config = config
        if secrets:
            source.secrets = {**(source.secrets or {}), **secrets}
    if "sync_interval_minutes" in body.model_fields_set:
        source.sync_interval_minutes = body.sync_interval_minutes
    await ctx.db.commit()
    return await _one(ctx, source)


@router.delete("/orgs/{slug}/sources/{source_id}", status_code=204)
async def delete_source(source_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    source = await _get_source(ctx, source_id)
    if source.kind == "upload":
        shutil.rmtree(Path(get_settings().file_storage_dir) / str(source.id), ignore_errors=True)
    await ctx.db.delete(source)
    await ctx.db.commit()
    return Response(status_code=204)


@router.post("/orgs/{slug}/sources/{source_id}/sync", response_model=SourceOut)
async def sync_source(source_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    source = await _get_source(ctx, source_id)
    await enqueue_sync(ctx.db, source)
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
    source = await _get_source(ctx, source_id)
    if source.kind != "upload":
        raise bad_request("Files can only be added to upload sources", "not_upload_source")
    limit = get_settings().max_upload_mb * 1024 * 1024
    folder = storage_dir(str(source.id))
    accepted = 0
    for upload in files:
        name = _safe_name(upload.filename or "")
        try:
            mime_for(name)
        except UnsupportedFileType as exc:
            raise bad_request(str(exc), "unsupported_file") from exc
        data = await upload.read()
        if len(data) > limit:
            raise ApiError(413, f"{name} is larger than {limit // 1_048_576} MB", "file_too_large")
        (folder / name).write_bytes(data)
        accepted += 1
    await enqueue_sync(ctx.db, source)
    await ctx.db.commit()
    return UploadResult(accepted=accepted, source=await _one(ctx, source))


# ── documents ────────────────────────────────────────────────────────────────
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
        updated_at=doc.updated_at,
    )


@router.get("/orgs/{slug}/sources/{source_id}/documents", response_model=DocumentPage)
async def list_documents(
    source_id: uuid.UUID,
    ctx: OrgContext = Depends(org_context),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None, max_length=200),
):
    source = await _get_source(ctx, source_id)
    where = [Document.source_id == source.id]
    if q:
        like = f"%{q}%"
        where.append(or_(Document.title.ilike(like), Document.url.ilike(like)))
    total = (await ctx.db.execute(select(func.count()).where(*where))).scalar_one()
    chunk_counts = (
        select(Chunk.document_id, func.count().label("n")).group_by(Chunk.document_id).subquery()
    )
    rows = await ctx.db.execute(
        select(Document, func.coalesce(chunk_counts.c.n, 0))
        .outerjoin(chunk_counts, chunk_counts.c.document_id == Document.id)
        .where(*where)
        .order_by(Document.title)
        .limit(limit)
        .offset(offset)
    )
    return DocumentPage(items=[_row(d, n) for d, n in rows.all()], total=total)


async def _get_document(ctx: OrgContext, document_id: uuid.UUID) -> Document:
    doc = await ctx.db.get(Document, document_id)
    if (
        doc is None
        or doc.org_id != ctx.org_id
        or doc.collection_id not in await _visible_collection_ids(ctx)
    ):
        raise not_found("Document")
    return doc


@router.get("/orgs/{slug}/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    doc = await _get_document(ctx, document_id)
    chunks = (
        await ctx.db.execute(select(func.count()).where(Chunk.document_id == doc.id))
    ).scalar_one()
    return DocumentDetail(**_row(doc, chunks).model_dump(), content=doc.content)


@router.delete("/orgs/{slug}/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    doc = await _get_document(ctx, document_id)
    source = await ctx.db.get(Source, doc.source_id)
    if source and source.kind == "upload":
        (Path(get_settings().file_storage_dir) / str(source.id) / doc.external_id).unlink(
            missing_ok=True
        )
    await ctx.db.delete(doc)
    await ctx.db.commit()
    return Response(status_code=204)
