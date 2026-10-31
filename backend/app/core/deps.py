"""Request dependencies: the current user and the org context (membership + role).

Two credentials are accepted: the session JWT (cookie, or `Authorization: Bearer`)
and org API keys (`Authorization: Bearer strata_sk_…`). An API key acts as the
user who created it, only inside its own org, with at most the key's role."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import ApiError, forbidden, not_found
from app.core.security import API_KEY_PREFIX, decode_session_token, hash_api_key
from app.models import ApiKey, Membership, Organization, User

ROLE_RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


def role_at_least(role: str, minimum: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[minimum]


# Non-org routes an API key may call; everything else must be under its org.
API_KEY_GLOBAL_ROUTES = {
    ("GET", "/api/auth/me"),
    ("GET", "/api/connectors"),
    ("GET", "/api/models"),
}
LAST_USED_RESOLUTION = timedelta(minutes=5)

# Declared so /api/docs offers an "Authorize" box; parsing stays lenient (no 403 when
# the header is missing — the session cookie may be used instead).
bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="bearer",
    description="An org API key (`strata_sk_…`) or a session JWT.",
)


async def find_api_key(db: AsyncSession, raw: str) -> ApiKey | None:
    """The live key for a raw `strata_sk_…` token (None if unknown or expired); records
    its use at most every few minutes."""
    key = (
        await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_api_key(raw)))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if key is None or (key.expires_at and key.expires_at <= now):
        return None
    if key.last_used_at is None or now - key.last_used_at > LAST_USED_RESOLUTION:
        key.last_used_at = now
        await db.commit()
    return key


async def _api_key_user(request: Request, db: AsyncSession, raw: str) -> User:
    path, method = request.url.path, request.method
    key = await find_api_key(db, raw)
    if key is None:
        raise ApiError(401, "The API key is invalid or has expired", "invalid_api_key")
    if not path.startswith("/api/orgs/") and (method, path) not in API_KEY_GLOBAL_ROUTES:
        raise forbidden("API keys can only call their organization's routes")
    request.state.api_key = key
    user = await db.get(User, key.user_id)
    if user is None:
        raise ApiError(401, "The API key is invalid or has expired", "invalid_api_key")
    return user


async def current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    bearer = credentials.credentials.strip() if credentials else None
    if bearer and bearer.startswith(API_KEY_PREFIX):
        return await _api_key_user(request, db, bearer)
    token = request.cookies.get(get_settings().session_cookie) or bearer
    user_id = decode_session_token(token) if token else None
    user = await db.get(User, user_id) if user_id else None
    if user is None:
        raise ApiError(401, "Sign in to continue", "unauthenticated")
    return user


def api_key_of(request: Request) -> ApiKey | None:
    return getattr(request.state, "api_key", None)


async def session_user(request: Request, user: User = Depends(current_user)) -> User:
    """For routes that must not be reachable with an API key (e.g. managing keys)."""
    if api_key_of(request) is not None:
        raise forbidden("Sign in with a session to do this; API keys can't")
    return user


@dataclass
class OrgContext:
    org: Organization
    user: User
    role: str
    db: AsyncSession
    # Set when the caller authenticated with an API key (bound to this org).
    api_key: ApiKey | None = None
    # The request came through the MCP server (attributed as such in the audit log).
    via_mcp: bool = False

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
    request: Request,
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
    key = api_key_of(request)
    # Non-members (and keys for another org) get the same 404 as a missing org so
    # slugs can't be probed.
    if row is None or (key is not None and key.org_id != row[0].id):
        raise not_found("Organization")
    org, role = row
    return OrgContext(org=org, user=user, role=_capped(role, key), db=db, api_key=key)


def _capped(role: str, key: ApiKey | None) -> str:
    """A key acts with at most its own role."""
    return key.role if key is not None and ROLE_RANK[key.role] < ROLE_RANK[role] else role


async def api_key_context(db: AsyncSession, key: ApiKey) -> OrgContext | None:
    """The org context an API key acts in outside a route (the MCP server): its creator,
    inside its org, with at most its role. None once the creator has left the org."""
    row = (
        await db.execute(
            select(Organization, Membership.role, User)
            .join(Membership, Membership.org_id == Organization.id)
            .join(User, User.id == Membership.user_id)
            .where(Organization.id == key.org_id, Membership.user_id == key.user_id)
        )
    ).first()
    if row is None:
        return None
    org, role, user = row
    return OrgContext(org=org, user=user, role=_capped(role, key), db=db, api_key=key, via_mcp=True)


def require_role(minimum: str):
    async def dep(ctx: OrgContext = Depends(org_context)) -> OrgContext:
        ctx.require(minimum)
        return ctx

    return dep


admin_ctx = require_role("admin")
member_ctx = require_role("member")
owner_ctx = require_role("owner")
