"""Retrieval limits for API keys.

Retrieval resolves its own scope from the user's memberships (every org they belong
to, with their full role there). An API key is narrower: one org, at most the key's
role. These helpers turn a key's limits into the explicit org/collection filters the
search and chat routes pass down."""

import uuid

from app.core.deps import OrgContext
from app.retrieval.access import visible_collections


async def key_collection_ids(ctx: OrgContext, requested: list[uuid.UUID] | None) -> list[uuid.UUID]:
    """Collections the key may read: visible under the key's (capped) role in its own
    org, intersected with `requested` when given. Never empty — an empty list would
    mean "no narrowing" downstream, so a random id stands in for "nothing"."""
    visible = [c.id for c in await visible_collections(ctx.db, ctx.org_id, ctx.user.id, ctx.role)]
    if requested:
        wanted = set(requested)
        visible = [c for c in visible if c in wanted]
    return visible or [uuid.uuid4()]
