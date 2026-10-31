"""The `slack_answer` job: answer one Slack question in its thread.

1. **Who is asking.** A confirmed `SlackUserLink`, else the Strata user whose email is
   the one Slack reports for them (`users.info`) and who belongs to an org connected
   to the workspace. Nobody: an ephemeral reply (a DM when that can't be shown) with a
   signed, expiring link to connect the accounts.
2. **What they may search.** `scope.asker_scope`: the place's orgs they belong to and,
   in a channel, only org-wide collections.
3. **The answer.** The chat API's own path with `stream=false` (`begin_answer` →
   `run_chat` → `wait_for_answer`), in a Strata conversation per thread and person,
   so it shows in their history, Insights and citations like any chat.
4. **The reply.** A placeholder is posted at once and replaced by the answer as mrkdwn;
   `[n]` citations link to an answer permalink that re-checks each reader's
   permissions when they open it.

What's been done is saved in the job payload, so a retry (Slack unreachable) reposts
the finished reply instead of asking the model again."""

import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.citations import cited_refs
from app.api.chat import DEFAULT_TITLE, begin_answer, wait_for_answer
from app.api.sharing import active_share
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.deps import OrgContext
from app.core.errors import ApiError
from app.models import (
    Conversation,
    Job,
    Membership,
    Message,
    Organization,
    SharedAnswer,
    SlackThread,
    SlackUserLink,
    SlackWorkspace,
    User,
)
from app.schemas.common import MessageCreate, OrgSettings
from app.slackbot import client, mrkdwn, scope
from app.slackbot.client import SlackError
from app.slackbot.signing import make_link_token

log = logging.getLogger("strata.slack")

THINKING = ":hourglass_flowing_sand: Searching Strata…"
HELP = (
    "Ask me a question about your organization's knowledge, e.g. "
    "_@Strata what's our parental leave policy?_ I answer with the sources I used."
)
MAX_QUESTION_CHARS = 20_000
# Slack errors worth retrying the job for; anything else (channel archived, bot
# removed…) won't get better by trying again.
RETRYABLE = {"ratelimited", "unreachable", "http_500", "http_502", "http_503", "http_504"}


@dataclass
class Place:
    """Where a question was asked, and so where its reply goes."""

    channel: str
    user: str  # the asker's Slack user id
    thread_ts: str
    private: bool  # a DM with the bot: only the asker reads the answer


@dataclass
class Reply:
    chunks: list[str]
    ephemeral: bool = False  # only the asker sees it (in a channel)


def _url(path: str) -> str:
    return f"{get_settings().public_url.rstrip('/')}{path}"


async def _save(db: AsyncSession, job: Job, payload: dict[str, Any]) -> None:
    job.payload = dict(payload)
    await db.commit()


# ── 1. identity ──────────────────────────────────────────────────────────────
async def identify(
    db: AsyncSession,
    workspace: SlackWorkspace,
    token: str,
    slack_user_id: str,
    connected: list[Organization],
) -> tuple[User | None, str | None]:
    """The Strata user behind a Slack user, and the Slack user's display name."""
    link = (
        await db.execute(
            select(SlackUserLink).where(
                SlackUserLink.workspace_id == workspace.id,
                SlackUserLink.slack_user_id == slack_user_id,
            )
        )
    ).scalar_one_or_none()
    if link is not None:
        return await db.get(User, link.user_id), link.slack_user_name
    try:
        info = await client.user_info(token, slack_user_id)
    except SlackError as exc:
        log.warning("users.info failed for %s: %s", slack_user_id, exc.error)
        return None, None
    profile = info.get("profile") or {}
    name = profile.get("display_name") or profile.get("real_name") or info.get("name")
    email = str(profile.get("email") or "").strip().lower()
    if info.get("is_bot") or info.get("deleted") or not email:
        return None, name
    user = await db.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(
            func.lower(User.email) == email,
            Membership.org_id.in_([o.id for o in connected]),
        )
        .limit(1)
    )
    return user, name


def _link_prompt(workspace: SlackWorkspace, place: Place, name: str | None) -> Reply:
    token = make_link_token(workspace.id, workspace.team_id, place.user, name)
    url = _url(f"/slack/link/{token}")
    text = (
        "I don't know who you are in Strata yet, so I can't tell what you're allowed to "
        f"read. {mrkdwn.link(url, 'Connect your Slack account to Strata')} (the link works "
        "for an hour), then ask me again."
    )
    return Reply([text], ephemeral=not place.private)


# ── 3. the answer ────────────────────────────────────────────────────────────
async def _thread_conversation(
    db: AsyncSession,
    workspace: SlackWorkspace,
    place: Place,
    user: User,
    org: Organization,
    org_ids: list[uuid.UUID],
) -> tuple[Conversation, SlackThread]:
    """This person's conversation for this thread: the one the thread used last, while
    nothing else was added to it; otherwise a new one."""
    row = (
        await db.execute(
            select(SlackThread).where(
                SlackThread.workspace_id == workspace.id,
                SlackThread.channel_id == place.channel,
                SlackThread.thread_ts == place.thread_ts,
                SlackThread.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    conv = await db.get(Conversation, row.conversation_id) if row else None
    if conv is not None:
        newest = await db.scalar(
            select(Message.id)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        # Continued in the web app (where restricted collections may be in scope), or
        # the channel now maps to another org: don't carry that history into the thread.
        if conv.org_id != org.id or newest != row.last_message_id:
            conv = None
    if conv is None:
        conv = Conversation(
            org_id=org.id,
            user_id=user.id,
            title=DEFAULT_TITLE,
            scope_org_ids=org_ids,
            mode=OrgSettings(**(org.settings or {})).default_mode,
        )
        db.add(conv)
        await db.flush()
        if row is None:
            row = SlackThread(
                workspace_id=workspace.id,
                channel_id=place.channel,
                thread_ts=place.thread_ts,
                user_id=user.id,
                conversation_id=conv.id,
            )
            db.add(row)
        row.conversation_id, row.last_message_id = conv.id, None
    conv.scope_org_ids = org_ids
    return conv, row


async def _ask(
    workspace: SlackWorkspace, place: Place, user: User, asked: scope.AskerScope, question: str
) -> tuple[dict[str, Any] | None, str | None, Conversation]:
    """Run the agent like a `stream=false` chat request. Returns the saved answer (or
    None and the error shown in Slack) and its conversation."""
    org = asked.orgs[0]
    async with SessionLocal() as db:
        ctx = OrgContext(
            org=await db.get(Organization, org.id),
            user=await db.get(User, user.id),
            role=asked.roles[org.id],
            db=db,
        )
        conv, thread = await _thread_conversation(
            db, workspace, place, user, org, [o.id for o in asked.orgs]
        )
        # The channel allow-list can be longer than a client may send (MAX_ID_LIST).
        body = MessageCreate(content=question).model_copy(
            update={"collection_ids": asked.collection_ids}
        )
        queue, start = await begin_answer(ctx, conv, body)  # commits and closes `db`
        thread_id = thread.id
    message, error = None, None
    try:
        message = (await wait_for_answer(queue))["message"]
    except ApiError as exc:
        error = exc.detail
    async with SessionLocal() as db:
        thread = await db.get(SlackThread, thread_id)
        if thread is not None:
            # A failed answer keeps the thread on this conversation too (only complete
            # answers are part of a conversation's history).
            thread.last_message_id = uuid.UUID(start["assistant_message_id"])
            await db.commit()
    return message, error, conv


async def _share(message: dict[str, Any], org_id: uuid.UUID, user_id: uuid.UUID) -> str | None:
    """A permalink for an answer citing the knowledge base. Each person who opens it
    sees the answer only if they can read every source it cites (app.api.sharing)."""
    if message["status"] != "complete":
        return None
    if not any((s.get("origin") or "knowledge") == "knowledge" for s in message["sources"]):
        return None
    async with SessionLocal() as db:
        share = await active_share(db, uuid.UUID(message["id"]))
        if share is None:
            share = SharedAnswer(
                token=secrets.token_urlsafe(16),
                org_id=org_id,
                message_id=uuid.UUID(message["id"]),
                created_by_id=user_id,
            )
            db.add(share)
            await db.commit()
        return _url(f"/a/{share.token}")


# ── 4. the reply ─────────────────────────────────────────────────────────────
def _names(orgs: list[Organization]) -> str:
    return ", ".join(o.name for o in orgs)


def scope_notes(asked: scope.AskerScope, private: bool) -> list[str]:
    """What the answer didn't search and why, when it matters."""
    notes = []
    if len(asked.orgs) > 1:
        notes.append(f"Searched {_names(asked.orgs)}")
    if not private and asked.restricted_skipped:
        notes.append("Restricted collections are only searched in DMs with me")
    if not private and asked.not_member:
        notes.append(f"Not searched: {_names(asked.not_member)} (you're not a member)")
    if asked.no_cross_org:
        notes.append(
            f"Not searched: {_names(asked.no_cross_org)} (doesn't allow cross-org questions)"
        )
    return notes


def compose(
    message: dict[str, Any],
    *,
    share_url: str | None,
    open_url: str | None,
    notes: list[str],
    multi_org: bool,
) -> list[str]:
    """The answer as Slack messages: the text with linked [n] citations, the cited
    sources as a numbered list, then the scope notes and a link to open it in Strata."""
    content = message["content"] or ""
    sources = {s["ref"]: s for s in message["sources"]}

    def link_for(ref: int) -> str | None:
        source = sources.get(ref)
        if source is None:
            return None
        origin = source.get("origin") or "knowledge"
        if origin == "web":
            return source.get("url")
        if origin == "knowledge" and share_url:
            return f"{share_url}?source={ref}"
        return None

    parts = [mrkdwn.to_mrkdwn(content, link_for) or "_(The answer is empty.)_"]
    if message["status"] == "stopped":
        parts.append("_The answer was stopped before it finished._")
    cited = [sources[r] for r in dict.fromkeys(cited_refs(content)) if r in sources]
    if cited:
        lines = ["*Sources*"]
        for s in cited:
            url = link_for(s["ref"])
            title = mrkdwn.link(url, s["title"]) if url else mrkdwn.escape(s["title"])
            where = [s.get("org_name") if multi_org else None, s.get("collection_name")]
            if (s.get("origin") or "knowledge") == "web":
                where = ["web"]
            meta = " · ".join(w for w in where if w)
            lines.append(f"{s['ref']}. {title}" + (f" — _{mrkdwn.escape(meta)}_" if meta else ""))
        parts.append("\n".join(lines))
    footer = [f"_{mrkdwn.escape(n)}_" for n in notes]
    if open_url:
        footer.append(mrkdwn.link(open_url, "Open in Strata"))
    if footer:
        parts.append(" · ".join(footer))
    return mrkdwn.split("\n\n".join(parts))


async def _answer(
    db: AsyncSession, job: Job, payload: dict[str, Any], workspace: SlackWorkspace, place: Place
) -> Reply:
    token = workspace.secrets["bot_token"]
    question = mrkdwn.from_slack(payload.get("text") or "", workspace.bot_user_id)
    if not question:
        return Reply([HELP])
    if len(question) > MAX_QUESTION_CHARS:
        return Reply([":warning: That question is too long for me (over 20,000 characters)."])
    connected = await scope.connected_orgs(db, workspace.id)
    user, name = await identify(db, workspace, token, place.user, connected)
    if user is None:
        return _link_prompt(workspace, place, name)
    orgs = await scope.place_orgs(
        db, workspace.id, place.channel, private=place.private, connected=connected
    )
    asked = await scope.asker_scope(db, user, orgs, private=place.private)
    if not asked.orgs:
        if not orgs and not place.private:
            text = (
                "This channel doesn't search any organization any more. An admin can "
                "choose its organizations in Strata, under Settings → Slack."
            )
        elif place.private:
            text = (
                f"Your Strata account ({user.email}) isn't a member of an organization "
                "connected to this Slack workspace."
            )
        else:
            text = (
                f"Your Strata account ({user.email}) isn't a member of {_names(orgs)}, "
                "which this channel searches."
            )
        return Reply([f":lock: {mrkdwn.escape(text)}"], ephemeral=not place.private)

    if "placeholder_ts" not in payload:
        try:
            payload["placeholder_ts"] = await client.post_message(
                token, place.channel, THINKING, place.thread_ts
            )
            await _save(db, job, payload)
        except SlackError as exc:
            log.warning("Could not post the placeholder in %s: %s", place.channel, exc.error)
            if exc.error in RETRYABLE:
                raise

    message, error, conv = await _ask(workspace, place, user, asked, question)
    if message is None:
        return Reply([f":warning: I couldn't answer that: {mrkdwn.escape(error or 'error')}"])
    share_url = await _share(message, asked.orgs[0].id, user.id)
    open_url = _url(f"/o/{asked.orgs[0].slug}/chat/{conv.id}") if place.private else share_url
    chunks = compose(
        message,
        share_url=share_url,
        open_url=open_url,
        notes=scope_notes(asked, place.private),
        multi_org=len(asked.orgs) > 1,
    )
    return Reply(chunks)


async def _deliver(
    db: AsyncSession, job: Job, payload: dict[str, Any], token: str, place: Place
) -> None:
    """Post the reply saved in the payload, from where an earlier attempt stopped."""
    chunks: list[str] = payload["reply"]
    if payload.get("ephemeral"):
        if payload.get("posted"):
            return
        try:
            for chunk in chunks:
                await client.post_ephemeral(
                    token, place.channel, place.user, chunk, place.thread_ts
                )
        except SlackError as exc:
            if exc.error in RETRYABLE:
                raise
            # Not shown (e.g. the asker isn't in the channel): say it in a DM instead.
            dm = await client.open_dm(token, place.user)
            for chunk in chunks:
                await client.post_message(token, dm, chunk)
        payload["posted"] = len(chunks)
        await _save(db, job, payload)
        return
    for k in range(int(payload.get("posted") or 0), len(chunks)):
        if k == 0 and payload.get("placeholder_ts"):
            await client.update_message(token, place.channel, payload["placeholder_ts"], chunks[0])
        else:
            await client.post_message(token, place.channel, chunks[k], place.thread_ts)
        payload["posted"] = k + 1
        await _save(db, job, payload)


async def handle_slack_answer(db: AsyncSession, job: Job) -> None:
    payload = dict(job.payload or {})
    workspace = (
        await db.execute(select(SlackWorkspace).where(SlackWorkspace.team_id == payload["team_id"]))
    ).scalar_one_or_none()
    token = (workspace.secrets or {}).get("bot_token") if workspace else None
    if workspace is None or not token:
        log.info("Slack workspace %s was disconnected; dropping its question", payload["team_id"])
        return
    place = Place(
        channel=payload["channel"],
        user=payload["user"],
        # Replies go in the question's thread, or start one under the question.
        thread_ts=payload.get("thread_ts") or payload["ts"],
        private=bool(payload.get("private")),
    )
    if "reply" not in payload:
        try:
            reply = await _answer(db, job, payload, workspace, place)
        except SlackError as exc:
            if exc.error in RETRYABLE:
                raise
            log.warning("Slack question in %s failed: %s", place.channel, exc)
            reply = Reply([":warning: Something went wrong talking to Slack; please ask again."])
        except Exception:
            # A bug or an outage, not something a retry of the whole run should repeat
            # (it would ask the model again): say so in the thread.
            log.exception("Slack question in %s failed", place.channel)
            await db.rollback()
            reply = Reply([":warning: Something went wrong on Strata's side; please ask again."])
        payload["reply"] = reply.chunks
        payload["ephemeral"] = reply.ephemeral
        await _save(db, job, payload)
    try:
        await _deliver(db, job, payload, token, place)
    except SlackError as exc:
        if exc.error in RETRYABLE:
            raise
        log.warning("Could not post the Slack reply in %s: %s", place.channel, exc)
