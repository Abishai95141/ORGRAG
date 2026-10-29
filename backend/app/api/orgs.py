import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.serializers import org_out, org_summaries
from app.core.db import get_db
from app.core.deps import OrgContext, admin_ctx, current_user, org_context, owner_ctx
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.core.slugs import is_valid_slug, next_available, slugify
from app.models import Collection, Invitation, Membership, Organization, User
from app.schemas.common import (
    InviteCreate,
    InviteOut,
    InvitePreview,
    MemberOut,
    OrgCreate,
    OrgOut,
    OrgPatch,
    OrgSettings,
    OrgSummary,
    RolePatch,
    UserOut,
)

router = APIRouter(tags=["organizations"])

INVITE_TTL = timedelta(days=7)


# ── organizations ────────────────────────────────────────────────────────────
@router.get("/orgs", response_model=list[OrgSummary])
async def list_orgs(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    return await org_summaries(db, user.id)


@router.post("/orgs", response_model=OrgOut, status_code=201)
async def create_org(
    body: OrgCreate, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    if body.slug:
        slug = body.slug.strip().lower()
        if not is_valid_slug(slug):
            raise bad_request(
                "Slugs use lowercase letters, numbers and hyphens (2–48 chars)", "invalid_slug"
            )
        if (await db.execute(select(Organization.id).where(Organization.slug == slug))).first():
            raise conflict("That URL is already taken", "slug_taken")
    else:
        base = slugify(body.name)
        taken = set(
            (
                await db.execute(
                    select(Organization.slug).where(Organization.slug.like(f"{base}%"))
                )
            ).scalars()
        )
        slug = next_available(base, taken)

    org = Organization(
        slug=slug,
        name=body.name.strip(),
        description=body.description,
        hue=int(uuid.uuid4().int % 360),
        settings=OrgSettings().model_dump(),
    )
    db.add(org)
    await db.flush()
    db.add(Membership(user_id=user.id, org_id=org.id, role="owner"))
    db.add(
        Collection(
            org_id=org.id,
            name="General",
            description="Shared knowledge for everyone in the organization.",
            visibility="org",
            glyph="stack",
        )
    )
    await db.commit()
    return await org_out(db, org, user.id)


@router.get("/orgs/{slug}", response_model=OrgOut)
async def get_org(ctx: OrgContext = Depends(org_context)):
    return await org_out(ctx.db, ctx.org, ctx.user.id)


@router.patch("/orgs/{slug}", response_model=OrgOut)
async def update_org(body: OrgPatch, ctx: OrgContext = Depends(admin_ctx)):
    org = ctx.org
    if body.name is not None:
        org.name = body.name.strip()
    if body.description is not None:
        org.description = body.description
    if body.hue is not None:
        org.hue = body.hue
    if body.settings is not None:
        merged = {**OrgSettings(**(org.settings or {})).model_dump()}
        merged.update(body.settings.model_dump(exclude_none=True))
        org.settings = OrgSettings(**merged).model_dump()
    await ctx.db.commit()
    return await org_out(ctx.db, org, ctx.user.id)


@router.delete("/orgs/{slug}", status_code=204)
async def delete_org(ctx: OrgContext = Depends(owner_ctx)):
    await ctx.db.delete(ctx.org)
    await ctx.db.commit()
    return Response(status_code=204)


# ── members ──────────────────────────────────────────────────────────────────
@router.get("/orgs/{slug}/members", response_model=list[MemberOut])
async def list_members(ctx: OrgContext = Depends(org_context)):
    rows = (
        await ctx.db.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == ctx.org_id)
            .order_by(Membership.joined_at)
        )
    ).all()
    return [
        MemberOut(user=UserOut.model_validate(u), role=m.role, joined_at=m.joined_at)
        for u, m in rows
    ]


async def _owner_count(db: AsyncSession, org_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count()).where(Membership.org_id == org_id, Membership.role == "owner")
        )
    ).scalar_one()


async def _membership(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> Membership:
    m = await db.get(Membership, (user_id, org_id))
    if m is None:
        raise not_found("Member")
    return m


@router.patch("/orgs/{slug}/members/{user_id}", response_model=MemberOut)
async def update_member(user_id: uuid.UUID, body: RolePatch, ctx: OrgContext = Depends(admin_ctx)):
    m = await _membership(ctx.db, ctx.org_id, user_id)
    # Only owners may grant or revoke ownership.
    if (m.role == "owner" or body.role == "owner") and ctx.role != "owner":
        raise forbidden("Only owners can change ownership")
    if m.role == "owner" and body.role != "owner" and await _owner_count(ctx.db, ctx.org_id) <= 1:
        raise conflict("An organization needs at least one owner", "last_owner")
    m.role = body.role
    await ctx.db.commit()
    user = await ctx.db.get(User, user_id)
    return MemberOut(user=UserOut.model_validate(user), role=m.role, joined_at=m.joined_at)


@router.delete("/orgs/{slug}/members/{user_id}", status_code=204)
async def remove_member(user_id: uuid.UUID, ctx: OrgContext = Depends(org_context)):
    if user_id != ctx.user.id:
        ctx.require("admin")
    m = await _membership(ctx.db, ctx.org_id, user_id)
    if m.role == "owner":
        if ctx.role != "owner" and user_id != ctx.user.id:
            raise forbidden("Only owners can remove an owner")
        if await _owner_count(ctx.db, ctx.org_id) <= 1:
            raise conflict("Transfer ownership before leaving", "last_owner")
    await ctx.db.delete(m)
    await ctx.db.commit()
    return Response(status_code=204)


# ── invites ──────────────────────────────────────────────────────────────────
@router.get("/orgs/{slug}/invites", response_model=list[InviteOut])
async def list_invites(ctx: OrgContext = Depends(admin_ctx)):
    rows = await ctx.db.execute(
        select(Invitation)
        .where(Invitation.org_id == ctx.org_id, Invitation.accepted_at.is_(None))
        .order_by(Invitation.created_at.desc())
    )
    return list(rows.scalars())


@router.post("/orgs/{slug}/invites", response_model=InviteOut, status_code=201)
async def create_invite(body: InviteCreate, ctx: OrgContext = Depends(admin_ctx)):
    if body.role == "owner" and ctx.role != "owner":
        raise forbidden("Only owners can invite owners")
    email = body.email.lower()
    already = (
        await ctx.db.execute(
            select(Membership.user_id)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == ctx.org_id, func.lower(User.email) == email)
        )
    ).first()
    if already:
        raise conflict("That person is already a member", "already_member")
    await ctx.db.execute(
        delete(Invitation).where(
            Invitation.org_id == ctx.org_id,
            func.lower(Invitation.email) == email,
            Invitation.accepted_at.is_(None),
        )
    )
    invite = Invitation(
        org_id=ctx.org_id,
        email=email,
        role=body.role,
        token=secrets.token_urlsafe(24),
        expires_at=datetime.now(UTC) + INVITE_TTL,
        created_by=ctx.user.id,
    )
    ctx.db.add(invite)
    await ctx.db.commit()
    return invite


@router.delete("/orgs/{slug}/invites/{invite_id}", status_code=204)
async def revoke_invite(invite_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    invite = await ctx.db.get(Invitation, invite_id)
    if invite is None or invite.org_id != ctx.org_id:
        raise not_found("Invite")
    await ctx.db.delete(invite)
    await ctx.db.commit()
    return Response(status_code=204)


async def _invite_by_token(db: AsyncSession, token: str) -> tuple[Invitation, Organization]:
    row = (
        await db.execute(
            select(Invitation, Organization)
            .join(Organization, Organization.id == Invitation.org_id)
            .where(Invitation.token == token)
        )
    ).first()
    if row is None:
        raise not_found("Invite")
    return row[0], row[1]


@router.get("/invites/{token}", response_model=InvitePreview)
async def preview_invite(token: str, db: AsyncSession = Depends(get_db)):
    invite, org = await _invite_by_token(db, token)
    expired = invite.accepted_at is not None or invite.expires_at < datetime.now(UTC)
    return InvitePreview(
        org={"name": org.name, "slug": org.slug, "hue": org.hue},
        email=invite.email,
        role=invite.role,
        expired=expired,
    )


@router.post("/invites/{token}/accept", response_model=OrgSummary)
async def accept_invite(
    token: str, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
):
    invite, org = await _invite_by_token(db, token)
    if invite.accepted_at is not None or invite.expires_at < datetime.now(UTC):
        raise bad_request("This invite has expired", "invite_expired")
    if invite.email.lower() != user.email.lower():
        raise forbidden(f"This invite was sent to {invite.email}")
    if await db.get(Membership, (user.id, org.id)) is None:
        db.add(Membership(user_id=user.id, org_id=org.id, role=invite.role))
    invite.accepted_at = datetime.now(UTC)
    await db.commit()
    return (await org_summaries(db, user.id, [org.id]))[0]
