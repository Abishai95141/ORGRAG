"""Slack bot routes (roadmap §6 #5): connecting a workspace (OAuth v2), the events
endpoint Slack calls, channel → org scope mappings, and Slack ↔ Strata account links.

The answering itself happens in the worker (`app/slackbot/answer.py`); the events
endpoint only verifies, deduplicates and queues, so Slack gets its 200 within three
seconds. See docs/ARCHITECTURE.md "Slack" for the permission rules."""

import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import any_, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import OrgContext, admin_ctx, current_user, role_at_least, session_user
from app.core.errors import ApiError, bad_request, conflict, forbidden, not_found
from app.models import (
    Job,
    Membership,
    Organization,
    SlackChannel,
    SlackConnection,
    SlackEvent,
    SlackUserLink,
    SlackWorkspace,
    User,
)
from app.schemas.common import (
    PersonOut,
    SlackBotChannel,
    SlackChannelIn,
    SlackChannelOut,
    SlackLinkOut,
    SlackLinkPreview,
    SlackOrgOut,
    SlackOverview,
    SlackWorkspaceOut,
)
from app.slackbot import client, scope
from app.slackbot.client import SlackError
from app.slackbot.signing import (
    InvalidSignature,
    make_state,
    read_link_token,
    read_state,
    verify_request,
)

log = logging.getLogger("strata.slack")
router = APIRouter(tags=["slack"])

EVENT_TTL = timedelta(days=1)  # how long an event id is remembered for dedupe
# Channels an admin can map: public (C…) and private (G…) channels, not DMs (D…).
_CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{6,31}$")
# Message subtypes that are still a person writing (others: edits, joins, bots…).
_USER_SUBTYPES = {None, "file_share", "thread_broadcast"}


def _require_configured() -> None:
    if not get_settings().slack_configured:
        raise ApiError(
            503,
            "Slack isn't set up on this server: set SLACK_CLIENT_ID, SLACK_CLIENT_SECRET "
            "and SLACK_SIGNING_SECRET",
            "slack_not_configured",
        )


def _public(path: str) -> str:
    return f"{get_settings().public_url.rstrip('/')}{path}"


async def _person(db: AsyncSession, user_id: uuid.UUID | None) -> PersonOut | None:
    user = await db.get(User, user_id) if user_id else None
    if user is None:
        return None
    return PersonOut(id=user.id, name=user.name, email=user.email, avatar_hue=user.avatar_hue)


async def _my_orgs(
    db: AsyncSession, user_id: uuid.UUID, org_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SlackOrgOut]:
    """Of `org_ids`, the orgs the caller belongs to (with their role there)."""
    rows = await db.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == user_id, Organization.id.in_(org_ids))
    )
    return {
        org.id: SlackOrgOut(slug=org.slug, name=org.name, hue=org.hue, role=role)
        for org, role in rows.all()
    }


async def _connection(ctx: OrgContext, workspace_id: uuid.UUID) -> SlackConnection:
    conn = (
        await ctx.db.execute(
            select(SlackConnection).where(
                SlackConnection.org_id == ctx.org_id, SlackConnection.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if conn is None:
        raise not_found("Slack workspace")
    return conn


async def _workspace_out(
    ctx: OrgContext, workspace: SlackWorkspace, conn: SlackConnection
) -> SlackWorkspaceOut:
    connected = await scope.connected_orgs(ctx.db, workspace.id)
    mine = await _my_orgs(ctx.db, ctx.user.id, [o.id for o in connected])
    return SlackWorkspaceOut(
        id=workspace.id,
        team_id=workspace.team_id,
        team_name=workspace.team_name,
        bot_user_id=workspace.bot_user_id,
        connected_at=conn.created_at,
        connected_by=await _person(ctx.db, conn.connected_by_id),
        default_org=mine.get(workspace.default_org_id) if workspace.default_org_id else None,
        default_elsewhere=bool(workspace.default_org_id) and workspace.default_org_id not in mine,
        orgs=[mine[o.id] for o in connected if o.id in mine],
        other_org_count=sum(1 for o in connected if o.id not in mine),
    )


async def _channel_out(ctx: OrgContext, mapping: SlackChannel) -> SlackChannelOut:
    mine = await _my_orgs(ctx.db, ctx.user.id, list(mapping.org_ids))
    return SlackChannelOut(
        channel_id=mapping.channel_id,
        channel_name=mapping.channel_name,
        orgs=[mine[i] for i in mapping.org_ids if i in mine],
        other_org_count=sum(1 for i in mapping.org_ids if i not in mine),
        updated_by=await _person(ctx.db, mapping.updated_by_id),
        updated_at=mapping.updated_at,
    )


# ── workspaces ───────────────────────────────────────────────────────────────
@router.get("/orgs/{slug}/slack", response_model=SlackOverview)
async def slack_overview(ctx: OrgContext = Depends(admin_ctx)):
    """Whether Slack is set up on this server, and the workspaces this org connected."""
    rows = await ctx.db.execute(
        select(SlackWorkspace, SlackConnection)
        .join(SlackConnection, SlackConnection.workspace_id == SlackWorkspace.id)
        .where(SlackConnection.org_id == ctx.org_id)
        .order_by(SlackWorkspace.team_name)
    )
    return SlackOverview(
        configured=get_settings().slack_configured,
        events_url=_public("/api/slack/events"),
        redirect_url=client.redirect_uri(),
        workspaces=[await _workspace_out(ctx, ws, conn) for ws, conn in rows.all()],
    )


@router.get(
    "/orgs/{slug}/slack/install",
    status_code=307,
    response_class=RedirectResponse,
    responses={307: {"description": "Redirect to Slack's authorize page"}},
)
async def slack_install(ctx: OrgContext = Depends(admin_ctx)):
    """Start connecting a Slack workspace: redirects to Slack with a signed state bound
    to this org and admin (valid 10 minutes)."""
    if ctx.api_key is not None:
        raise forbidden("Sign in with a session to connect Slack; API keys can't")
    _require_configured()
    return RedirectResponse(client.authorize_url(make_state(ctx.org_id, ctx.user.id)), 307)


@router.get(
    "/slack/oauth/callback",
    status_code=303,
    response_class=RedirectResponse,
    responses={303: {"description": "Back to the org's Slack settings"}},
)
async def slack_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Slack sends the admin back here. The state must be valid and the browser signed
    in as the admin who started; then the code is exchanged (`oauth.v2.access`) and the
    workspace, its bot token encrypted, is connected to the org."""
    _require_configured()
    claim = read_state(state) if state else None
    org = await db.get(Organization, claim.org_id) if claim else None
    if claim is None or org is None:
        raise bad_request(
            "This Slack connection request is invalid or expired; start again from "
            "Settings → Slack",
            "invalid_state",
        )

    def back(**params: str) -> RedirectResponse:
        return RedirectResponse(_public(f"/o/{org.slug}/settings/slack?{urlencode(params)}"), 303)

    try:
        user = await current_user(request, db, None)
    except ApiError:
        user = None
    if user is None or user.id != claim.user_id:
        return back(slack_error="wrong_user")
    role = await db.scalar(
        select(Membership.role).where(Membership.org_id == org.id, Membership.user_id == user.id)
    )
    if role is None or not role_at_least(role, "admin"):
        return back(slack_error="not_admin")
    if error or not code:
        return back(slack_error=error or "missing_code")
    try:
        data = await client.oauth_access(code)
    except SlackError as exc:
        log.warning("oauth.v2.access failed: %s", exc.error)
        return back(slack_error=exc.error)
    team = data.get("team") or {}
    if data.get("is_enterprise_install") or not team.get("id"):
        return back(slack_error="enterprise_install")
    if data.get("token_type") != "bot" or not data.get("access_token"):
        return back(slack_error="no_bot_token")

    workspace = (
        await db.execute(select(SlackWorkspace).where(SlackWorkspace.team_id == team["id"]))
    ).scalar_one_or_none()
    if workspace is None:
        workspace = SlackWorkspace(team_id=team["id"])
        db.add(workspace)
    workspace.team_name = str(team.get("name") or team["id"])[:255]
    workspace.bot_user_id = str(data.get("bot_user_id") or "")
    workspace.app_id = data.get("app_id")
    workspace.scopes = str(data.get("scope") or "")
    workspace.secrets = {"bot_token": data["access_token"]}
    await db.flush()
    exists = await db.scalar(
        select(SlackConnection.id).where(
            SlackConnection.org_id == org.id, SlackConnection.workspace_id == workspace.id
        )
    )
    if workspace.default_org_id is None:  # the first org to connect answers unmapped channels
        workspace.default_org_id = org.id
    if exists is None:
        db.add(SlackConnection(org_id=org.id, workspace_id=workspace.id, connected_by_id=user.id))
        audit.add_event(
            db, org.id, audit.Actor.of_user(user), "slack.connected", "slack_workspace",
            workspace.id, workspace.team_name, team_id=workspace.team_id,
        )  # fmt: skip
    await db.commit()
    log.info("Slack workspace %s connected to %s", workspace.team_id, org.slug)
    return back(connected=workspace.team_name)


@router.delete("/orgs/{slug}/slack/workspaces/{workspace_id}", status_code=204)
async def disconnect_workspace(workspace_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    """This org stops answering in the workspace and leaves every channel's scope there.
    When no org is left connected, the bot token is revoked and the workspace forgotten."""
    conn = await _connection(ctx, workspace_id)
    workspace = await ctx.db.get(SlackWorkspace, workspace_id)
    if workspace is not None and workspace.default_org_id == ctx.org_id:
        workspace.default_org_id = None  # unmapped channels stop answering, not switch orgs
    await ctx.db.delete(conn)
    mappings = await ctx.db.execute(
        select(SlackChannel).where(
            SlackChannel.workspace_id == workspace_id, ctx.org_id == any_(SlackChannel.org_ids)
        )
    )
    for mapping in mappings.scalars():
        # Kept even when empty: a channel set up to search only this org now searches
        # nothing, rather than falling back to the workspace's default org.
        mapping.org_ids = [i for i in mapping.org_ids if i != ctx.org_id]
    await ctx.db.flush()
    remaining = await ctx.db.scalar(
        select(func.count(SlackConnection.id)).where(SlackConnection.workspace_id == workspace_id)
    )
    token = None
    if not remaining and workspace is not None:
        token = (workspace.secrets or {}).get("bot_token")
        await ctx.db.delete(workspace)
    audit.record_event(
        ctx, "slack.disconnected", "slack_workspace", workspace_id,
        workspace.team_name if workspace else None, uninstalled=bool(token),
    )  # fmt: skip
    await ctx.db.commit()
    if token:
        try:
            await client.revoke(token)
        except SlackError as exc:  # the token is gone from Strata either way
            log.warning("auth.revoke failed for %s: %s", workspace_id, exc.error)
    return Response(status_code=204)


# ── channel → org scope ──────────────────────────────────────────────────────
@router.get(
    "/orgs/{slug}/slack/workspaces/{workspace_id}/channels", response_model=list[SlackChannelOut]
)
async def list_channels(workspace_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    """Channels mapped to search this org. Unmapped channels search the default org."""
    await _connection(ctx, workspace_id)
    rows = await ctx.db.execute(
        select(SlackChannel)
        .where(SlackChannel.workspace_id == workspace_id, ctx.org_id == any_(SlackChannel.org_ids))
        .order_by(SlackChannel.channel_name, SlackChannel.channel_id)
    )
    return [await _channel_out(ctx, m) for m in rows.scalars()]


@router.get(
    "/orgs/{slug}/slack/workspaces/{workspace_id}/bot-channels",
    response_model=list[SlackBotChannel],
)
async def list_bot_channels(workspace_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    """Channels the bot has been added to, from Slack (for picking one to map)."""
    await _connection(ctx, workspace_id)
    workspace = await ctx.db.get(SlackWorkspace, workspace_id)
    try:
        channels = await client.bot_channels(workspace.secrets["bot_token"])
    except SlackError as exc:
        raise ApiError(502, f"Slack returned an error: {exc.error}", "slack_error") from exc
    mapped = set(
        (
            await ctx.db.execute(
                select(SlackChannel.channel_id).where(
                    SlackChannel.workspace_id == workspace_id,
                    ctx.org_id == any_(SlackChannel.org_ids),
                )
            )
        ).scalars()
    )
    return [
        SlackBotChannel(
            id=c["id"],
            name=c.get("name") or c["id"],
            is_private=bool(c.get("is_private")),
            mapped=c["id"] in mapped,
        )
        for c in sorted(channels, key=lambda c: (c.get("name") or "").lower())
        if c.get("id")
    ]


def _scope_events(
    ctx: OrgContext,
    workspace_id: uuid.UUID,
    channel_id: str,
    channel_name: str | None,
    old: list[uuid.UUID],
    new: list[uuid.UUID],
) -> None:
    """An event in each org a channel starts or stops answering from. Each org's entry
    names only that org's change, never the other orgs in the channel's scope."""
    actor = audit.Actor.of(ctx)
    label = f"#{channel_name}" if channel_name else channel_id
    for org_id, action in [(o, "slack.channel_added") for o in new if o not in old] + [
        (o, "slack.channel_removed") for o in old if o not in new
    ]:
        audit.add_event(
            ctx.db, org_id, actor, action, "slack_channel", channel_id, label,
            workspace_id=str(workspace_id),
        )  # fmt: skip


async def _check_change(ctx: OrgContext, old: list[uuid.UUID], new: list[uuid.UUID]) -> None:
    """Adding an org to a channel's scope takes an admin of that org: it decides where
    its knowledge is posted. Narrowing a scope takes an admin of an org already in it."""
    rows = await ctx.db.execute(
        select(Organization, Membership.role)
        .join(Membership, Membership.org_id == Organization.id)
        .where(Membership.user_id == ctx.user.id, Organization.id.in_([*old, *new]))
    )
    mine = rows.all()
    admin_of = {org.id for org, role in mine if role_at_least(role, "admin")}
    names = {org.id: org.name for org, _ in mine}  # only orgs the caller is in are named
    for org_id in [i for i in new if i not in old]:
        if org_id not in admin_of:
            name = names.get(org_id, "that organization")
            raise forbidden(f"Adding {name} to a channel's scope takes an admin of {name}")
    if any(i not in new for i in old) and not admin_of & set(old):
        raise forbidden(
            "Only an admin of an organization this channel already searches can remove "
            "organizations from it"
        )


@router.put(
    "/orgs/{slug}/slack/workspaces/{workspace_id}/channels/{channel_id}",
    response_model=SlackChannelOut,
)
async def set_channel(
    workspace_id: uuid.UUID,
    channel_id: str,
    body: SlackChannelIn,
    ctx: OrgContext = Depends(admin_ctx),
):
    """Set which orgs a channel searches, in order. Every org must have connected the
    workspace; adding one takes an admin of it."""
    await _connection(ctx, workspace_id)
    if not _CHANNEL_ID.match(channel_id):
        raise bad_request("Not a Slack channel id (C… or G…)", "invalid_channel")
    slugs = list(dict.fromkeys(body.org_slugs))
    rows = (
        await ctx.db.execute(
            select(Organization, Membership.role)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == ctx.user.id, Organization.slug.in_(slugs))
        )
    ).all()
    found = {org.slug: (org, role) for org, role in rows}
    if missing := [s for s in slugs if s not in found]:
        raise bad_request(
            f"You aren't a member of {', '.join(missing)}; a channel can only search "
            "organizations you belong to",
            "unknown_org",
        )
    connected = await scope.connected_orgs(ctx.db, workspace_id)
    connected_ids = {o.id for o in connected}
    if offline := [found[s][0].name for s in slugs if found[s][0].id not in connected_ids]:
        raise bad_request(
            f"{', '.join(offline)} hasn't connected this Slack workspace", "org_not_connected"
        )
    mapping = await scope.channel_mapping(ctx.db, workspace_id, channel_id)
    workspace = await ctx.db.get(SlackWorkspace, workspace_id)
    unmapped = [o.id for o in scope.default_orgs(workspace.default_org_id, connected)]
    old = list(mapping.org_ids) if mapping else unmapped
    # Orgs in the scope the caller isn't a member of can't be named here; they're kept
    # (after the caller's), not dropped by an edit that couldn't show them.
    mine = set(await _my_orgs(ctx.db, ctx.user.id, old))
    new = [found[s][0].id for s in slugs]
    new += [i for i in old if i not in mine and i not in new]
    await _check_change(ctx, old, new)
    if mapping is None:
        mapping = SlackChannel(workspace_id=workspace_id, channel_id=channel_id)
        ctx.db.add(mapping)
    mapping.org_ids = new
    mapping.channel_name = body.channel_name or mapping.channel_name
    mapping.updated_by_id = ctx.user.id
    mapping.updated_at = datetime.now(UTC)
    _scope_events(ctx, workspace_id, channel_id, mapping.channel_name, old, new)
    await ctx.db.commit()
    return await _channel_out(ctx, mapping)


@router.delete(
    "/orgs/{slug}/slack/workspaces/{workspace_id}/channels/{channel_id}", status_code=204
)
async def reset_channel(
    workspace_id: uuid.UUID, channel_id: str, ctx: OrgContext = Depends(admin_ctx)
):
    """Remove a channel's mapping: it searches the workspace's default org again."""
    await _connection(ctx, workspace_id)
    mapping = await scope.channel_mapping(ctx.db, workspace_id, channel_id)
    if mapping is None:
        raise not_found("Channel mapping")
    connected = await scope.connected_orgs(ctx.db, workspace_id)
    workspace = await ctx.db.get(SlackWorkspace, workspace_id)
    default = [o.id for o in scope.default_orgs(workspace.default_org_id, connected)]
    await _check_change(ctx, list(mapping.org_ids), default)
    _scope_events(ctx, workspace_id, channel_id, mapping.channel_name, mapping.org_ids, default)
    await ctx.db.delete(mapping)
    await ctx.db.commit()
    return Response(status_code=204)


# ── events ───────────────────────────────────────────────────────────────────
def _question(event: dict, workspace: SlackWorkspace) -> dict | None:
    """The job payload for an event that asks the bot something, else None: a mention
    in a channel, or a message in a DM with the bot. Bots (the bot itself included),
    edits and other message subtypes are ignored."""
    kind, channel = event.get("type"), str(event.get("channel") or "")
    if kind == "app_mention" and not channel.startswith("D"):
        private = False  # a DM's mention also arrives as message.im, answered there
    elif kind == "message" and event.get("channel_type") == "im":
        private = True
    else:
        return None
    user = event.get("user")
    if event.get("bot_id") or event.get("subtype") not in _USER_SUBTYPES:
        return None
    if not user or user == workspace.bot_user_id or not event.get("ts") or not channel:
        return None
    return {
        "team_id": workspace.team_id,
        "channel": channel,
        "user": str(user),
        "text": str(event.get("text") or ""),
        "ts": str(event["ts"]),
        "thread_ts": event.get("thread_ts"),
        "private": private,
    }


@router.post("/slack/events")
async def slack_events(request: Request, db: AsyncSession = Depends(get_db)):
    """Slack's Events API. The signature is checked before anything else; questions
    are queued as `slack_answer` jobs (once per `event_id`, so Slack's retries are
    acknowledged without asking again) and answered in the thread by the worker."""
    secret = get_settings().slack_signing_secret
    if not secret:
        _require_configured()
    body = await request.body()
    try:
        verify_request(
            secret,
            request.headers.get("X-Slack-Request-Timestamp"),
            request.headers.get("X-Slack-Signature"),
            body,
        )
    except InvalidSignature as exc:
        log.warning("Rejected a Slack request: %s", exc)
        raise ApiError(401, "Invalid Slack request signature", "invalid_signature") from exc
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise bad_request("The request body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise bad_request("Expected a JSON object")
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    if payload.get("type") != "event_callback":
        return {"ok": True}

    event = payload.get("event") or {}
    teams = [
        payload.get("team_id"),
        *(a.get("team_id") for a in payload.get("authorizations") or []),
    ]
    workspace = None
    for team_id in [t for t in teams if t]:
        workspace = (
            await db.execute(select(SlackWorkspace).where(SlackWorkspace.team_id == team_id))
        ).scalar_one_or_none()
        if workspace is not None:
            break
    if workspace is None:
        return {"ok": True}  # not (or no longer) connected

    kind = event.get("type")
    revoked = (event.get("tokens") or {}).get("bot") or []
    if kind == "app_uninstalled" or (kind == "tokens_revoked" and workspace.bot_user_id in revoked):
        log.info("Slack app removed from %s; forgetting the workspace", workspace.team_id)
        await db.delete(workspace)
        await db.commit()
        return {"ok": True}

    job = _question(event, workspace)
    event_id = payload.get("event_id")
    if job is None or not event_id:
        return {"ok": True}
    now = datetime.now(UTC)
    fresh = await db.scalar(
        insert(SlackEvent)
        .values(event_id=str(event_id)[:64], team_id=workspace.team_id, received_at=now)
        .on_conflict_do_nothing()
        .returning(SlackEvent.event_id)
    )
    if fresh is None:
        log.info(
            "Slack retry %s of event %s: already queued",
            request.headers.get("X-Slack-Retry-Num", "?"),
            event_id,
        )
        return {"ok": True}
    await db.execute(delete(SlackEvent).where(SlackEvent.received_at < now - EVENT_TTL))
    db.add(Job(kind="slack_answer", payload={**job, "event_id": str(event_id)}))
    await db.commit()
    return {"ok": True}


# ── account links ────────────────────────────────────────────────────────────
async def _claimed(db: AsyncSession, token: str):
    claim = read_link_token(token)
    workspace = await db.get(SlackWorkspace, claim.workspace_id) if claim else None
    if claim is None or workspace is None or workspace.team_id != claim.team_id:
        raise ApiError(
            404,
            "This link is invalid, or its Slack workspace was disconnected",
            "link_not_found",
        )
    return claim, workspace


async def _eligible(db: AsyncSession, user: User, workspace: SlackWorkspace) -> bool:
    """The user belongs to an org connected to the workspace."""
    return (
        await db.scalar(
            select(Membership.org_id)
            .join(SlackConnection, SlackConnection.org_id == Membership.org_id)
            .where(Membership.user_id == user.id, SlackConnection.workspace_id == workspace.id)
            .limit(1)
        )
    ) is not None


async def _existing_link(
    db: AsyncSession, workspace: SlackWorkspace, slack_user_id: str
) -> SlackUserLink | None:
    return (
        await db.execute(
            select(SlackUserLink).where(
                SlackUserLink.workspace_id == workspace.id,
                SlackUserLink.slack_user_id == slack_user_id,
            )
        )
    ).scalar_one_or_none()


def _link_out(link: SlackUserLink, workspace: SlackWorkspace) -> SlackLinkOut:
    return SlackLinkOut(
        id=link.id,
        team_id=workspace.team_id,
        team_name=workspace.team_name,
        slack_user_id=link.slack_user_id,
        slack_user_name=link.slack_user_name,
        created_at=link.created_at,
    )


async def _used(db: AsyncSession, claim) -> bool:
    """Links carry a one-time id, recorded with the dedupe ids of Slack events (pruned
    after a day, well past the link's hour)."""
    if not claim.jti:
        return False
    return await db.get(SlackEvent, f"link:{claim.jti}") is not None


@router.get("/slack/link/{token}", response_model=SlackLinkPreview)
async def preview_link(
    token: str, user: User = Depends(session_user), db: AsyncSession = Depends(get_db)
):
    """What confirming this account link would do (for the confirmation page)."""
    claim, workspace = await _claimed(db, token)
    existing = await _existing_link(db, workspace, claim.slack_user_id)
    return SlackLinkPreview(
        team_id=workspace.team_id,
        team_name=workspace.team_name,
        slack_user_id=claim.slack_user_id,
        slack_user_name=claim.slack_user_name,
        slack_email=claim.slack_email,
        expired=claim.expired,
        used=await _used(db, claim),
        linked="none" if existing is None else "you" if existing.user_id == user.id else "other",
        eligible=await _eligible(db, user, workspace),
    )


@router.post("/slack/link/{token}", response_model=SlackLinkOut, status_code=201)
async def confirm_link(
    token: str,
    response: Response,
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
):
    """Confirm, signed in, that the Slack user the link was made for is you: the bot
    then answers them as you, with your permissions."""
    claim, workspace = await _claimed(db, token)
    if claim.expired:
        raise bad_request("This link has expired; ask the bot again for a new one", "link_expired")
    existing = await _existing_link(db, workspace, claim.slack_user_id)
    if existing is not None:
        if existing.user_id != user.id:
            raise conflict(
                "This Slack account is already connected to another Strata account",
                "slack_user_linked",
            )
        response.status_code = 200
        return _link_out(existing, workspace)
    if not await _eligible(db, user, workspace):
        raise forbidden(
            f"None of your organizations has connected the {workspace.team_name} Slack workspace"
        )
    if await _used(db, claim):
        raise conflict("This link was already used; ask the bot again for a new one", "link_used")
    # Spent for good: after a disconnect, the same link can't connect the account again.
    db.add(SlackEvent(event_id=f"link:{claim.jti}", team_id=workspace.team_id))
    link = SlackUserLink(
        workspace_id=workspace.id,
        slack_user_id=claim.slack_user_id,
        slack_user_name=(claim.slack_user_name or "")[:255] or None,
        user_id=user.id,
    )
    db.add(link)
    await db.commit()
    return _link_out(link, workspace)


@router.get("/slack/links", response_model=list[SlackLinkOut])
async def list_links(user: User = Depends(session_user), db: AsyncSession = Depends(get_db)):
    """The Slack accounts connected to your Strata account."""
    rows = await db.execute(
        select(SlackUserLink, SlackWorkspace)
        .join(SlackWorkspace, SlackWorkspace.id == SlackUserLink.workspace_id)
        .where(SlackUserLink.user_id == user.id)
        .order_by(SlackWorkspace.team_name, SlackUserLink.created_at)
    )
    return [_link_out(link, ws) for link, ws in rows.all()]


@router.delete("/slack/links/{link_id}", status_code=204)
async def delete_link(
    link_id: uuid.UUID, user: User = Depends(session_user), db: AsyncSession = Depends(get_db)
):
    """Disconnect a Slack account from yours (the bot then matches it by email again)."""
    link = await db.get(SlackUserLink, link_id)
    if link is None or link.user_id != user.id:
        raise not_found("Slack account link")
    await db.delete(link)
    await db.commit()
    return Response(status_code=204)
