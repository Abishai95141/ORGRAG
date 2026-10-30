"""Row → response-model helpers that need aggregate counts."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.registry import get_connector_class
from app.llm.models import LLMUnavailable, resolve_model
from app.models import (
    Chunk,
    Collection,
    CollectionAccess,
    Document,
    Membership,
    Organization,
    Source,
)
from app.schemas.common import (
    CollectionOut,
    OrgOut,
    OrgSettings,
    OrgSummary,
    SourceOut,
    SourceProgress,
)


async def org_summaries(
    db: AsyncSession, user_id: uuid.UUID, org_ids: list[uuid.UUID] | None = None
) -> list[OrgSummary]:
    member_counts = (
        select(Membership.org_id, func.count().label("n")).group_by(Membership.org_id).subquery()
    )
    doc_counts = (
        select(Document.org_id, func.count().label("n")).group_by(Document.org_id).subquery()
    )
    stmt = (
        select(
            Organization,
            Membership.role,
            func.coalesce(member_counts.c.n, 0),
            func.coalesce(doc_counts.c.n, 0),
        )
        .join(Membership, Membership.org_id == Organization.id)
        .outerjoin(member_counts, member_counts.c.org_id == Organization.id)
        .outerjoin(doc_counts, doc_counts.c.org_id == Organization.id)
        .where(Membership.user_id == user_id)
        .order_by(Organization.name)
    )
    if org_ids is not None:
        stmt = stmt.where(Organization.id.in_(org_ids))
    return [
        OrgSummary(
            id=org.id,
            slug=org.slug,
            name=org.name,
            hue=org.hue,
            role=role,
            member_count=members,
            document_count=docs,
        )
        for org, role, members, docs in (await db.execute(stmt)).all()
    ]


async def org_out(db: AsyncSession, org: Organization, user_id: uuid.UUID) -> OrgOut:
    summary = (await org_summaries(db, user_id, [org.id]))[0]
    settings = OrgSettings(**(org.settings or {}))
    try:
        effective_model = resolve_model(settings.model)[1]
    except LLMUnavailable:
        effective_model = None
    return OrgOut(
        **summary.model_dump(),
        description=org.description,
        settings=settings,
        effective_model=effective_model,
        created_at=org.created_at,
    )


async def collections_out(db: AsyncSession, collections: list[Collection]) -> list[CollectionOut]:
    if not collections:
        return []
    ids = [c.id for c in collections]
    docs = dict(
        (
            await db.execute(
                select(Document.collection_id, func.count())
                .where(Document.collection_id.in_(ids))
                .group_by(Document.collection_id)
            )
        ).all()
    )
    sources = dict(
        (
            await db.execute(
                select(Source.collection_id, func.count())
                .where(Source.collection_id.in_(ids))
                .group_by(Source.collection_id)
            )
        ).all()
    )
    access: dict[uuid.UUID, list[uuid.UUID]] = {}
    for cid, uid in (
        await db.execute(
            select(CollectionAccess.collection_id, CollectionAccess.user_id).where(
                CollectionAccess.collection_id.in_(ids)
            )
        )
    ).all():
        access.setdefault(cid, []).append(uid)
    return [
        CollectionOut(
            id=c.id,
            org_id=c.org_id,
            name=c.name,
            description=c.description,
            visibility=c.visibility,
            glyph=c.glyph,
            document_count=docs.get(c.id, 0),
            source_count=sources.get(c.id, 0),
            allowed_user_ids=access.get(c.id, []),
            created_at=c.created_at,
        )
        for c in collections
    ]


def _public_config(source: Source) -> dict:
    """Config as clients see it: each secret field becomes `<name>_set: bool`."""
    config = dict(source.config or {})
    try:
        secret_fields = get_connector_class(source.kind).secret_fields()
    except ValueError:
        secret_fields = []
    for name in secret_fields:
        config[f"{name}_set"] = bool((source.secrets or {}).get(name))
    return config


async def sources_out(db: AsyncSession, sources: list[Source]) -> list[SourceOut]:
    if not sources:
        return []
    ids = [s.id for s in sources]
    docs = dict(
        (
            await db.execute(
                select(Document.source_id, func.count())
                .where(Document.source_id.in_(ids))
                .group_by(Document.source_id)
            )
        ).all()
    )
    chunks = dict(
        (
            await db.execute(
                select(Document.source_id, func.count(Chunk.id))
                .join(Chunk, Chunk.document_id == Document.id)
                .where(Document.source_id.in_(ids))
                .group_by(Document.source_id)
            )
        ).all()
    )
    names = dict(
        (
            await db.execute(
                select(Collection.id, Collection.name).where(
                    Collection.id.in_({s.collection_id for s in sources})
                )
            )
        ).all()
    )
    return [
        SourceOut(
            id=s.id,
            org_id=s.org_id,
            collection_id=s.collection_id,
            collection_name=names.get(s.collection_id, ""),
            kind=s.kind,
            name=s.name,
            config=_public_config(s),
            status=s.status,
            error=s.error,
            document_count=docs.get(s.id, 0),
            chunk_count=chunks.get(s.id, 0),
            sync_interval_minutes=s.sync_interval_minutes,
            last_synced_at=s.last_synced_at,
            created_at=s.created_at,
            progress=SourceProgress(**s.progress) if s.progress else None,
        )
        for s in sources
    ]
