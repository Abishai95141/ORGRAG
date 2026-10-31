import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.api.serializers import collections_out
from app.core import audit
from app.core.deps import OrgContext, admin_ctx, org_context
from app.core.errors import bad_request, conflict, not_found
from app.models import Collection, CollectionAccess, Document, Membership
from app.retrieval.access import visible_collections
from app.schemas.common import CollectionCreate, CollectionOut, CollectionPatch

router = APIRouter(prefix="/orgs/{slug}/collections", tags=["collections"])


async def _set_access(
    ctx: OrgContext, collection: Collection, user_ids: list[uuid.UUID]
) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """Replace the access list; returns the user ids (granted, revoked)."""
    member_ids = set(
        (
            await ctx.db.execute(
                select(Membership.user_id).where(
                    Membership.org_id == ctx.org_id, Membership.user_id.in_(user_ids)
                )
            )
        ).scalars()
    )
    if missing := set(user_ids) - member_ids:
        raise bad_request(f"{len(missing)} selected user(s) are not members", "not_members")
    current = set(
        (
            await ctx.db.execute(
                select(CollectionAccess.user_id).where(
                    CollectionAccess.collection_id == collection.id
                )
            )
        ).scalars()
    )
    await ctx.db.execute(
        delete(CollectionAccess).where(CollectionAccess.collection_id == collection.id)
    )
    ctx.db.add_all(CollectionAccess(collection_id=collection.id, user_id=u) for u in member_ids)
    return member_ids - current, current - member_ids


def _fields(col: Collection) -> dict:
    return {
        "name": col.name,
        "description": col.description,
        "visibility": col.visibility,
        "glyph": col.glyph,
    }


async def get_collection(ctx: OrgContext, collection_id: uuid.UUID) -> Collection:
    col = await ctx.db.get(Collection, collection_id)
    if col is None or col.org_id != ctx.org_id:
        raise not_found("Collection")
    return col


@router.get("", response_model=list[CollectionOut])
async def list_collections(ctx: OrgContext = Depends(org_context)):
    cols = await visible_collections(ctx.db, ctx.org_id, ctx.user.id, ctx.role)
    return await collections_out(ctx.db, cols)


@router.post("", response_model=CollectionOut, status_code=201)
async def create_collection(body: CollectionCreate, ctx: OrgContext = Depends(admin_ctx)):
    col = Collection(
        org_id=ctx.org_id,
        name=body.name.strip(),
        description=body.description,
        visibility=body.visibility,
        glyph=body.glyph,
    )
    ctx.db.add(col)
    try:
        await ctx.db.flush()
    except IntegrityError as exc:
        raise conflict("A collection with that name already exists", "name_taken") from exc
    granted: set[uuid.UUID] = set()
    if body.visibility == "restricted":
        granted, _ = await _set_access(ctx, col, body.allowed_user_ids)
    audit.record_event(
        ctx,
        "collection.created",
        "collection",
        col.id,
        col.name,
        visibility=col.visibility,
        granted=await audit.emails(ctx.db, granted),
    )
    await ctx.db.commit()
    return (await collections_out(ctx.db, [col]))[0]


@router.patch("/{collection_id}", response_model=CollectionOut)
async def update_collection(
    collection_id: uuid.UUID, body: CollectionPatch, ctx: OrgContext = Depends(admin_ctx)
):
    """Partial update; `description: null` clears it. `allowed_user_ids` replaces the
    access list and must name members of this org."""
    col = await get_collection(ctx, collection_id)
    before = _fields(col)
    if body.name is not None:
        col.name = body.name.strip()
    if "description" in body.model_fields_set:
        col.description = body.description
    if body.visibility is not None:
        col.visibility = body.visibility
    if body.glyph is not None:
        col.glyph = body.glyph
    granted: set[uuid.UUID] = set()
    revoked: set[uuid.UUID] = set()
    if body.allowed_user_ids is not None:
        granted, revoked = await _set_access(ctx, col, body.allowed_user_ids)
    diff = audit.changes(before, _fields(col))
    if diff or granted or revoked:
        # Who can read it is the security-relevant part, so it gets its own action.
        access = "visibility" in diff or granted or revoked
        audit.record_event(
            ctx,
            "collection.access_changed" if access else "collection.updated",
            "collection",
            col.id,
            col.name,
            changes=diff,
            granted=await audit.emails(ctx.db, granted),
            revoked=await audit.emails(ctx.db, revoked),
        )
    try:
        await ctx.db.commit()
    except IntegrityError as exc:
        raise conflict("A collection with that name already exists", "name_taken") from exc
    return (await collections_out(ctx.db, [col]))[0]


@router.delete("/{collection_id}", status_code=204)
async def delete_collection(collection_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    col = await get_collection(ctx, collection_id)
    documents = await ctx.db.scalar(
        select(func.count(Document.id)).where(Document.collection_id == col.id)
    )
    audit.record_event(
        ctx, "collection.deleted", "collection", col.id, col.name, documents=documents or 0
    )
    await ctx.db.delete(col)
    await ctx.db.commit()
    return Response(status_code=204)
