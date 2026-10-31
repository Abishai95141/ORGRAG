"""Structured records (roadmap §4.1): record schemas per collection, their extraction,
and exact queries over the records.

Admins find, review, edit and activate schemas; activating one extracts its records in
the worker, and every later sync of the collection keeps them current. Members see the
active schemas of collections they can read and can query their records (the same
query the agent's `query_records` tool runs)."""

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete, select

from app.core.deps import OrgContext, admin_ctx, org_context
from app.core.errors import bad_request, conflict, not_found
from app.models import Collection, Document, Job, Record, RecordRun, RecordSchema
from app.records import query as records_query
from app.records import suggest as records_suggest
from app.records.jobs import enqueue_extraction
from app.retrieval.access import ADMIN_ROLES, visible_collections
from app.schemas.records import (
    RecordDocument,
    RecordFailure,
    RecordGroup,
    RecordOut,
    RecordQueryIn,
    RecordQueryOut,
    RecordSchemaCreate,
    RecordSchemaDetail,
    RecordSchemaOut,
    RecordSchemaPatch,
)

router = APIRouter(prefix="/orgs/{slug}", tags=["records"])


async def _readable(ctx: OrgContext) -> dict[uuid.UUID, Collection]:
    cols = await visible_collections(ctx.db, ctx.org_id, ctx.user.id, ctx.role)
    return {c.id: c for c in cols}


async def _extracting(ctx: OrgContext) -> set[str]:
    """Ids of schemas with an extraction job queued or running."""
    rows = await ctx.db.scalars(
        select(Job.payload["schema_id"].astext).where(
            Job.org_id == ctx.org_id,
            Job.kind == "extract_records",
            Job.status.in_(("queued", "running")),
        )
    )
    return set(rows)


def _out(
    schema: RecordSchema,
    collections: dict[uuid.UUID, Collection],
    extracting: set[str] = frozenset(),
    model: type[RecordSchemaOut] = RecordSchemaOut,
    **extra,
) -> RecordSchemaOut:
    stats = {k: v for k, v in (schema.stats or {}).items() if k != "samples"}
    if schema.status == "suggested":
        stats["samples"] = (schema.stats or {}).get("samples", [])
    return model(
        id=schema.id,
        collection_id=schema.collection_id,
        collection_name=collections[schema.collection_id].name,
        name=schema.name,
        label=schema.label,
        description=schema.description,
        fields=schema.fields,
        key_field=schema.key_field,
        origin=schema.origin,
        status=schema.status,
        stats=stats,
        extracting=str(schema.id) in extracting,
        extracted_at=schema.extracted_at,
        created_at=schema.created_at,
        updated_at=schema.updated_at,
        **extra,
    )


async def _schema(ctx: OrgContext, schema_id: uuid.UUID) -> RecordSchema:
    schema = await ctx.db.get(RecordSchema, schema_id)
    if schema is None or schema.org_id != ctx.org_id:
        raise not_found("Record schema")
    return schema


@router.get("/record-schemas", response_model=list[RecordSchemaOut])
async def list_schemas(
    ctx: OrgContext = Depends(org_context), collection_id: uuid.UUID | None = None
):
    """Active schemas of collections you can read; admins also see suggested and
    archived ones."""
    collections = await _readable(ctx)
    stmt = select(RecordSchema).where(RecordSchema.collection_id.in_(collections.keys()))
    if ctx.role not in ADMIN_ROLES:
        stmt = stmt.where(RecordSchema.status == "active")
    if collection_id is not None:
        stmt = stmt.where(RecordSchema.collection_id == collection_id)
    rows = (await ctx.db.execute(stmt.order_by(RecordSchema.label))).scalars()
    extracting = await _extracting(ctx)
    return [_out(s, collections, extracting) for s in rows]


@router.get("/record-schemas/{schema_id}", response_model=RecordSchemaDetail)
async def get_schema(schema_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    """One schema, with the documents whose last extraction failed. Members see active
    schemas of collections they can read."""
    schema = await _schema(ctx, schema_id)
    collections = await _readable(ctx)
    if schema.collection_id not in collections or (
        ctx.role not in ADMIN_ROLES and schema.status != "active"
    ):
        raise not_found("Record schema")
    failed = (
        await ctx.db.execute(
            select(RecordRun.document_id, Document.title, RecordRun.error, RecordRun.extracted_at)
            .join(Document, Document.id == RecordRun.document_id)
            .where(RecordRun.schema_id == schema.id, RecordRun.error.is_not(None))
            .order_by(Document.title)
        )
    ).all()
    return _out(
        schema,
        collections,
        await _extracting(ctx),
        RecordSchemaDetail,
        failures=[
            RecordFailure(document_id=d, title=t, error=e, extracted_at=at)
            for d, t, e, at in failed
        ],
    )


@router.post(
    "/collections/{collection_id}/record-schemas/suggest", response_model=list[RecordSchemaOut]
)
async def suggest_schemas(collection_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    """Find record types in a collection's documents (tables, field lines). Saved as
    `suggested`; schemas you already activated or archived are returned unchanged."""
    collection = await ctx.db.get(Collection, collection_id)
    if collection is None or collection.org_id != ctx.org_id:
        raise not_found("Collection")
    schemas = await records_suggest.suggest(ctx.db, collection, ctx.user.id)
    await ctx.db.commit()
    extracting = await _extracting(ctx)
    return [_out(s, {collection.id: collection}, extracting) for s in schemas]


@router.post("/record-schemas", response_model=RecordSchemaOut, status_code=201)
async def create_schema(body: RecordSchemaCreate, ctx: OrgContext = Depends(admin_ctx)):
    """A schema you define, extracted from prose with LangExtract once activated."""
    collection = await ctx.db.get(Collection, body.collection_id)
    if collection is None or collection.org_id != ctx.org_id:
        raise not_found("Collection")
    taken = await ctx.db.scalar(
        select(RecordSchema.id).where(
            RecordSchema.collection_id == collection.id, RecordSchema.name == body.name
        )
    )
    if taken:
        raise conflict(f"This collection already has a '{body.name}' schema", "name_taken")
    schema = RecordSchema(
        org_id=ctx.org_id,
        collection_id=collection.id,
        name=body.name,
        label=body.label,
        description=body.description,
        fields=[f.model_dump() for f in body.fields],
        key_field=body.key_field,
        origin="llm",
        status="suggested",
        stats={},
        created_by_id=ctx.user.id,
    )
    ctx.db.add(schema)
    await ctx.db.commit()
    return _out(schema, {collection.id: collection})


@router.patch("/record-schemas/{schema_id}", response_model=RecordSchemaOut)
async def update_schema(
    schema_id: uuid.UUID, body: RecordSchemaPatch, ctx: OrgContext = Depends(admin_ctx)
):
    """Edit a schema or change its status. Activating, or changing the fields of an
    active schema, (re-)extracts its records."""
    schema = await _schema(ctx, schema_id)
    reextract = False
    if body.label is not None:
        schema.label = body.label
    if body.description is not None:
        schema.description = body.description
    fields = [f.model_dump() for f in body.fields] if body.fields is not None else schema.fields
    key_field = body.key_field or schema.key_field
    names = [f["name"] for f in fields]
    if len(set(names)) != len(names):
        raise bad_request("Field names must be unique", "invalid_fields")
    if key_field not in names:
        raise bad_request(f"key_field '{key_field}' isn't one of the fields", "invalid_fields")
    if fields != schema.fields or key_field != schema.key_field:
        schema.fields, schema.key_field = fields, key_field
        reextract = True
    if body.status is not None and body.status != schema.status:
        reextract = reextract or body.status == "active"
        schema.status = body.status
        if body.status != "active":  # records of an inactive schema aren't queryable anyway
            await ctx.db.execute(delete(Record).where(Record.schema_id == schema.id))
            await ctx.db.execute(delete(RecordRun).where(RecordRun.schema_id == schema.id))
    if reextract and schema.status == "active":
        await ctx.db.execute(delete(RecordRun).where(RecordRun.schema_id == schema.id))
        await enqueue_extraction(ctx.db, schema, force=True)
    await ctx.db.commit()
    collection = await ctx.db.get(Collection, schema.collection_id)
    return _out(schema, {collection.id: collection}, await _extracting(ctx))


@router.delete("/record-schemas/{schema_id}", status_code=204)
async def delete_schema(schema_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    schema = await _schema(ctx, schema_id)
    await ctx.db.delete(schema)  # records and runs cascade
    await ctx.db.commit()
    return Response(status_code=204)


@router.post("/record-schemas/{schema_id}/extract", status_code=202)
async def extract_schema(
    schema_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx), force: bool = Query(False)
):
    """Queue extraction now (only changed documents, unless `force`)."""
    schema = await _schema(ctx, schema_id)
    if schema.status != "active":
        raise bad_request("Activate the schema before extracting", "schema_inactive")
    await enqueue_extraction(ctx.db, schema, force=force)
    await ctx.db.commit()
    return {"queued": True}


@router.post("/records/query", response_model=RecordQueryOut)
async def query_records(body: RecordQueryIn, ctx: OrgContext = Depends(org_context)):
    """Exact filters, counts or groupings over the active schemas named `schema` in
    collections you can read."""
    collections = await _readable(ctx)
    schemas = [
        s
        for s in await records_query.schemas_in_scope(ctx.db, list(collections))
        if s.name == body.schema_name
    ]
    if not schemas:
        raise not_found(f"Record type '{body.schema_name}'")
    try:
        result = await records_query.run(
            ctx.db,
            schemas,
            list(collections),
            filters=[f.model_dump() for f in body.filters],
            mode=body.mode,
            group_by=body.group_by,
            sort=body.sort,
            descending=body.descending,
            limit=body.limit,
            latest_only=body.latest_only,
        )
    except records_query.QueryError as exc:
        raise bad_request(str(exc), "invalid_query") from exc
    return RecordQueryOut(
        total=result.total,
        schemas=[str(s.id) for s in schemas],
        rows=[
            RecordOut(
                id=r.record_id,
                schema_id=r.schema_id,
                key=r.key,
                values=r.values,
                provenance=r.provenance,
                document=RecordDocument(id=r.document_id, title=r.title, updated_at=r.updated_at),
            )
            for r in result.rows
        ],
        groups=[RecordGroup(value=v, count=n) for v, n in result.groups],
    )


@router.get("/records/{record_id}", response_model=RecordOut)
async def get_record(record_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    record = await ctx.db.get(Record, record_id)
    collections = await _readable(ctx)
    if record is None or record.org_id != ctx.org_id or record.collection_id not in collections:
        raise not_found("Record")
    doc = await ctx.db.get(Document, record.document_id)
    if doc is None or doc.hidden:
        raise not_found("Record")
    return RecordOut(
        id=record.id,
        schema_id=record.schema_id,
        key=record.key,
        values=record.values,
        provenance=record.provenance,
        document=RecordDocument(id=doc.id, title=doc.title, updated_at=doc.source_updated_at),
    )
