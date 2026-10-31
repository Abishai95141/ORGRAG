"""Which orgs and collections one Slack question may search.

- **The place's orgs.** A DM with the bot searches every org connected to the workspace.
  A channel searches its mapping (`SlackChannel.org_ids`, in order) or, unmapped, the
  workspace's default org (the earliest connection). An org that isn't connected (any
  more) is dropped, so every org a channel searches has connected the workspace.
- **The asker's orgs.** Only those among the place's orgs the asker is a member of. The
  first is the conversation's org; later ones that turned off cross-org questions are
  skipped, as they are in chat.
- **Channels are public to their members.** An answer posted in a channel is read by
  everyone in it, so there the scope is narrowed to org-wide collections
  (`org_wide_collection_ids`), intersected with what the asker can read when the run
  resolves its scope. Restricted collections are searched only in DMs. This is an
  allow-list applied in SQL on every search, never an instruction to the model."""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Membership, Organization, SlackChannel, SlackConnection, User
from app.retrieval.access import org_wide_collection_ids, visible_collections
from app.schemas.common import OrgSettings


@dataclass
class AskerScope:
    orgs: list[Organization]  # searched; the first holds the conversation
    roles: dict[uuid.UUID, str]
    # None: everything the asker can read (DMs). Otherwise the allow-list, never empty
    # (an empty list would mean "no narrowing" downstream).
    collection_ids: list[uuid.UUID] | None
    not_member: list[Organization] = field(default_factory=list)
    no_cross_org: list[Organization] = field(default_factory=list)
    # Restricted collections the asker can read but this place doesn't search.
    restricted_skipped: int = 0


async def connected_orgs(db: AsyncSession, workspace_id: uuid.UUID) -> list[Organization]:
    """Orgs connected to the workspace, earliest connection (the default) first."""
    rows = await db.execute(
        select(Organization)
        .join(SlackConnection, SlackConnection.org_id == Organization.id)
        .where(SlackConnection.workspace_id == workspace_id)
        .order_by(SlackConnection.created_at, Organization.name)
    )
    return list(rows.scalars())


def default_orgs(default_org_id: uuid.UUID | None, connected: list[Organization]):
    """What an unmapped channel searches: the workspace's default org while it's connected."""
    return [o for o in connected if o.id == default_org_id]


async def channel_mapping(
    db: AsyncSession, workspace_id: uuid.UUID, channel_id: str
) -> SlackChannel | None:
    return (
        await db.execute(
            select(SlackChannel).where(
                SlackChannel.workspace_id == workspace_id, SlackChannel.channel_id == channel_id
            )
        )
    ).scalar_one_or_none()


async def place_orgs(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    channel_id: str,
    *,
    private: bool,
    connected: list[Organization],
    default_org_id: uuid.UUID | None,
) -> list[Organization]:
    if private:
        return connected
    mapping = await channel_mapping(db, workspace_id, channel_id)
    if mapping is None:
        return default_orgs(default_org_id, connected)
    by_id = {o.id: o for o in connected}
    return [by_id[i] for i in dict.fromkeys(mapping.org_ids) if i in by_id]


async def asker_scope(
    db: AsyncSession, user: User, orgs: list[Organization], *, private: bool
) -> AskerScope:
    roles = dict(
        (
            await db.execute(
                select(Membership.org_id, Membership.role).where(
                    Membership.user_id == user.id, Membership.org_id.in_([o.id for o in orgs])
                )
            )
        ).all()
    )
    scope = AskerScope(orgs=[], roles=roles, collection_ids=None)
    for org in orgs:
        if org.id not in roles:
            scope.not_member.append(org)
        elif scope.orgs and not OrgSettings(**(org.settings or {})).allow_cross_org:
            scope.no_cross_org.append(org)
        else:
            scope.orgs.append(org)
    if private or not scope.orgs:
        return scope
    scope.collection_ids = await org_wide_collection_ids(db, [o.id for o in scope.orgs]) or [
        uuid.uuid4()  # nothing org-wide: an id that matches no collection
    ]
    for org in scope.orgs:
        visible = await visible_collections(db, org.id, user.id, roles[org.id])
        scope.restricted_skipped += sum(1 for c in visible if c.visibility != "org")
    return scope
