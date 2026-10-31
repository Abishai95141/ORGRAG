"""The org's knowledge graph: build status, rebuild, and browsing entities.

Everything is read through the caller's visible collections (capped to an API key's
role), like search: entities known only from documents they can't see don't exist."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.api.scope import key_collection_ids
from app.core import audit
from app.core.config import get_settings
from app.core.deps import OrgContext, admin_ctx, org_context
from app.core.errors import bad_request, not_found
from app.graph import query as graph
from app.graph.extract import ENTITY_TYPES
from app.models import Chunk, Document, KGEntity, KGRelation, Source
from app.schemas.common import (
    EntityDetail,
    EntityLink,
    EntityOut,
    GraphStats,
    OrgSettings,
    Page,
)
from app.worker.queue import enqueue_graph

router = APIRouter(prefix="/orgs/{slug}/graph", tags=["knowledge graph"])


async def _stats(ctx: OrgContext) -> GraphStats:
    def count(stmt):
        return ctx.db.scalar(stmt)

    pending = Document.graph_hash.is_(None) | (Document.graph_hash != Document.content_hash)
    return GraphStats(
        enabled=OrgSettings(**(ctx.org.settings or {})).knowledge_graph,
        entities=await count(select(func.count()).where(KGEntity.org_id == ctx.org_id)) or 0,
        relations=await count(select(func.count()).where(KGRelation.org_id == ctx.org_id)) or 0,
        documents_extracted=await count(
            select(func.count()).where(Document.org_id == ctx.org_id, ~pending)
        )
        or 0,
        documents_pending=await count(
            select(func.count()).where(Document.org_id == ctx.org_id, pending)
        )
        or 0,
    )


@router.get("", response_model=GraphStats)
async def graph_stats(ctx: OrgContext = Depends(org_context)):
    """Whether the graph is on, its size, and how many documents still await extraction."""
    return await _stats(ctx)


@router.post("/rebuild", response_model=GraphStats, status_code=202)
async def rebuild_graph(ctx: OrgContext = Depends(admin_ctx)):
    """Queue extraction for every source's changed or never-extracted documents.
    Requires `settings.knowledge_graph` and a configured model."""
    if not OrgSettings(**(ctx.org.settings or {})).knowledge_graph:
        raise bad_request("Turn on the knowledge graph in settings first", "graph_disabled")
    if not get_settings().llm_configured:
        raise bad_request("A model API key is needed to extract the graph", "llm_not_configured")
    for source in (
        await ctx.db.execute(select(Source).where(Source.org_id == ctx.org_id))
    ).scalars():
        await enqueue_graph(ctx.db, source)
    audit.record_event(ctx, "graph.rebuilt", "organization", ctx.org_id, ctx.org.name)
    await ctx.db.commit()
    return await _stats(ctx)


def _entity(row: graph.EntityRow, doc_ids: list[uuid.UUID]) -> EntityOut:
    return EntityOut(
        id=row.id, name=row.name, type=row.type, description=row.description, document_ids=doc_ids
    )


async def _doc_ids(ctx: OrgContext, chunk_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if not chunk_ids:
        return []
    rows = await ctx.db.execute(select(Chunk.document_id).where(Chunk.id.in_(chunk_ids)).distinct())
    return list(rows.scalars())


@router.get("/entities", response_model=Page[EntityOut])
async def list_entities(
    ctx: OrgContext = Depends(org_context),
    type: str | None = Query(None, description=f"One of: {', '.join(ENTITY_TYPES)}"),
    q: str | None = Query(None, max_length=200, description="Text in the name or description"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=100_000),
):
    if type is not None and type not in ENTITY_TYPES:
        raise bad_request(f"type must be one of: {', '.join(ENTITY_TYPES)}", "invalid_type")
    cids = await key_collection_ids(ctx, None)
    rows, total = await graph.list_entities(
        ctx.db, cids, entity_type=type, contains=q, limit=offset + limit
    )
    rows = rows[offset:]
    return Page[EntityOut](
        items=[_entity(r, await _doc_ids(ctx, r.chunk_ids)) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/entities/{entity_id}", response_model=EntityDetail)
async def get_entity(entity_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    entity = await ctx.db.get(KGEntity, entity_id)
    if entity is None or entity.org_id != ctx.org_id:
        raise not_found("Entity")
    cids = await key_collection_ids(ctx, None)
    match = [r for r in await graph.find_entity(ctx.db, cids, entity.name) if r.id == entity.id]
    if not match:
        raise not_found("Entity")
    relations, total = await graph.relations_of(ctx.db, cids, entity.id)
    links = [
        EntityLink(
            entity=EntityOut(
                id=r.other_id, name=r.other_name, type=r.other_type, description="",
                document_ids=await _doc_ids(ctx, r.chunk_ids),
            ),
            keywords=r.keywords,
            description=r.description,
        )
        for r in relations
    ]  # fmt: skip
    base = _entity(match[0], await _doc_ids(ctx, match[0].chunk_ids))
    return EntityDetail(**base.model_dump(), relations=links, relation_count=total)
