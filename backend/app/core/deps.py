"""Request dependencies: the current user and the org context (membership + role)."""

import uuid
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError, forbidden, not_found
from app.core.security import decode_session_token
from app.models import Membership, Organization, User

ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[minimum]


def _token_from(request: Request) -> str | None:
    token = request.cookies.get(get_settings().session_cookie)
    if token:
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    token = _token_from(request)
    user_id = decode_session_token(token) if token else None
    user = await db.get(User, user_id) if user_id else None
    if user is None:
        raise ApiError(401, "Sign in to continue", "unauthenticated")
    return user


@dataclass
class OrgContext:
    org: Organization
    user: User
    role: str
    db: AsyncSession

    @property
    def org_id(self) -> uuid.UUID:
        return self.org.id

    @property
    def is_admin(self) -> bool:
        return role_at_least(self.role, "admin")

    def require(self, minimum: str) -> None:
        if not role_at_least(self.role, minimum):
            raise forbidden(f"This action requires the {minimum} role")


async def org_context(
    slug: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> OrgContext:
    row = (
        await db.execute(
            select(Organization, Membership.role)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Organization.slug == slug, Membership.user_id == user.id)
        )
    ).first()
    # Non-members get the same 404 as a missing org so slugs can't be probed.
    if row is None:
        raise not_found("Organization")
    org, role = row
    return OrgContext(org=org, user=user, role=role, db=db)


def require_role(minimum: str):
    async def dep(ctx: OrgContext = Depends(org_context)) -> OrgContext:
        ctx.require(minimum)
        return ctx

    return dep


admin_ctx = require_role("admin")
member_ctx = require_role("member")
owner_ctx = require_role("owner")
