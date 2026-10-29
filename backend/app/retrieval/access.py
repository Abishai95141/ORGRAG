"""Retrieval scope = the allow-list of collection ids a user may read.

Computed once per request and applied as a SQL filter on every search, so neither
the model nor a crafted request can reach content outside it."""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Collection, CollectionAccess, Membership, Organization

ADMIN_ROLES = ("owner", "admin")


@dataclass
class RetrievalScope:
    orgs: dict[uuid.UUID, Organization] = field(default_factory=dict)
    collections: dict[uuid.UUID, Collection] = field(default_factory=dict)

    @property
    def collection_ids(self) -> list[uuid.UUID]:
        return list(self.collections)

    def narrow(self, collection_ids: list[uuid.UUID] | None) -> "RetrievalScope":
        if not collection_ids:
            return self
        wanted = set(collection_ids)
        return RetrievalScope(
            orgs=self.orgs,
            collections={k: v for k, v in self.collections.items() if k in wanted},
        )


async def visible_collections(
    db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, role: str
) -> list[Collection]:
    stmt = select(Collection).where(Collection.org_id == org_id)
    if role not in ADMIN_ROLES:
        granted = select(CollectionAccess.collection_id).where(CollectionAccess.user_id == user_id)
        stmt = stmt.where(or_(Collection.visibility == "org", Collection.id.in_(granted)))
    return list((await db.execute(stmt.order_by(Collection.name))).scalars())


async def resolve_scope(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    primary_org: Organization,
    org_slugs: list[str] | None = None,
    org_ids: list[uuid.UUID] | None = None,
    collection_ids: list[uuid.UUID] | None = None,
) -> RetrievalScope:
    """Orgs the caller asked for ∩ orgs they belong to ∩ orgs that allow cross-org reads
    (the primary org is always allowed)."""
    stmt = (
        select(Organization, Membership.role)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user_id)
    )
    if org_slugs:
        stmt = stmt.where(or_(Organization.slug.in_(org_slugs), Organization.id == primary_org.id))
    elif org_ids:
        stmt = stmt.where(or_(Organization.id.in_(org_ids), Organization.id == primary_org.id))
    else:
        stmt = stmt.where(Organization.id == primary_org.id)

    scope = RetrievalScope()
    for org, role in (await db.execute(stmt)).all():
        if org.id != primary_org.id and not (org.settings or {}).get("allow_cross_org", True):
            continue
        scope.orgs[org.id] = org
        for col in await visible_collections(db, org.id, user_id, role):
            scope.collections[col.id] = col
    return scope.narrow(collection_ids)
