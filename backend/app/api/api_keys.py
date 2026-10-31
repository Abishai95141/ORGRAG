"""Org API keys for programmatic access. Managing keys needs a signed-in admin
session — a key can never mint or revoke keys."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select

from app.core import audit
from app.core.deps import ROLE_RANK, OrgContext, admin_ctx, session_user
from app.core.errors import forbidden, not_found
from app.core.security import hash_api_key, new_api_key
from app.models import ApiKey, User
from app.schemas.common import ApiKeyCreate, ApiKeyCreated, ApiKeyOut

router = APIRouter(
    prefix="/orgs/{slug}/api-keys", tags=["api keys"], dependencies=[Depends(session_user)]
)


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(ctx: OrgContext = Depends(admin_ctx)):
    rows = await ctx.db.execute(
        select(ApiKey).where(ApiKey.org_id == ctx.org_id).order_by(ApiKey.created_at.desc())
    )
    return list(rows.scalars())


@router.post("", response_model=ApiKeyCreated, status_code=201)
async def create_key(body: ApiKeyCreate, ctx: OrgContext = Depends(admin_ctx)):
    if ROLE_RANK[body.role] > ROLE_RANK[ctx.role]:
        raise forbidden("A key can't have a higher role than yours")
    raw = new_api_key()
    key = ApiKey(
        org_id=ctx.org_id,
        user_id=ctx.user.id,
        name=body.name.strip(),
        prefix=raw[:16],
        key_hash=hash_api_key(raw),
        role=body.role,
        expires_at=(
            datetime.now(UTC) + timedelta(days=body.expires_in_days)
            if body.expires_in_days
            else None
        ),
    )
    ctx.db.add(key)
    await ctx.db.flush()
    # Name, role and expiry only: never the key, its hash or its prefix.
    audit.record_event(
        ctx,
        "api_key.created",
        "api_key",
        key.id,
        key.name,
        role=key.role,
        expires_at=key.expires_at,
    )
    await ctx.db.commit()
    return ApiKeyCreated(**ApiKeyOut.model_validate(key).model_dump(), key=raw)


@router.delete("/{key_id}", status_code=204)
async def revoke_key(key_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    key = await ctx.db.get(ApiKey, key_id)
    if key is None or key.org_id != ctx.org_id:
        raise not_found("API key")
    owner = await ctx.db.get(User, key.user_id)
    audit.record_event(
        ctx,
        "api_key.revoked",
        "api_key",
        key.id,
        key.name,
        role=key.role,
        created_by=owner.email if owner else None,
    )
    await ctx.db.delete(key)
    await ctx.db.commit()
    return Response(status_code=204)
