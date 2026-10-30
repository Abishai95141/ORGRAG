"""Slack connector: indexes channel conversations through the Web API with a bot token.
Each thread becomes one document; messages without replies are grouped into one
document per channel per UTC day."""

import asyncio
import html
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.connectors.base import Connector, ConnectorError, ProgressFn, RawDocument

_API = "https://slack.com/api"
_CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{6,}$")
# Join/leave/topic notices etc. carry no knowledge worth indexing.
_SKIP_SUBTYPES = {
    "channel_join",
    "channel_leave",
    "channel_topic",
    "channel_purpose",
    "channel_name",
    "channel_archive",
    "channel_unarchive",
    "group_join",
    "group_leave",
    "bot_add",
    "bot_remove",
    "pinned_item",
    "unpinned_item",
}
_AUTH_ERRORS = {"invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive"}
_MAX_TRIES = 5
_MAX_LOOKBACK = 730
# Tests swap in an httpx.MockTransport here.
_transport: httpx.AsyncBaseTransport | None = None


class SlackConnector(Connector):
    kind = "slack"
    display_name = "Slack"
    description = "Index conversations and threads from Slack channels the bot has joined."
    supports_incremental = True
    config_schema = {
        "type": "object",
        "properties": {
            "token": {"type": "string", "title": "Bot token (xoxb-…)", "secret": True},
            "channels": {
                "type": "array",
                "items": {"type": "string"},
                "title": "Channels (names or IDs; every channel the bot is in if empty)",
            },
            "lookback_days": {
                "type": "integer",
                "title": "Days of history to index",
                "minimum": 1,
                "maximum": _MAX_LOOKBACK,
                "default": 90,
            },
        },
    }

    def __init__(self, config: dict[str, Any], secrets: dict[str, Any]):
        super().__init__(config, secrets)
        self._users: dict[str, str] = {}

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        raw = config.get("channels") or []
        if isinstance(raw, str):
            raw = re.split(r"[\s,]+", raw)
        if not isinstance(raw, list):
            raise ValueError("Channels must be a list of channel names or IDs")
        channels: list[str] = []
        for item in raw:
            name = str(item).strip().lstrip("#")
            if not name:
                continue
            if not _CHANNEL_ID.match(name):
                name = name.lower()
                if not re.match(r"^[a-z0-9][a-z0-9._-]*$", name):
                    raise ValueError(f"Not a valid Slack channel name or ID: {item}")
            if name not in channels:
                channels.append(name)
        try:
            days = int(config.get("lookback_days") or 90)
        except (TypeError, ValueError) as exc:
            raise ValueError("Days of history must be a whole number") from exc
        return {"channels": channels, "lookback_days": max(1, min(days, _MAX_LOOKBACK))}

    def _client(self) -> httpx.AsyncClient:
        token = str(self.secrets.get("token") or "").strip()
        if not token:
            raise ConnectorError("Add a Slack bot token (xoxb-…) to this source")
        return httpx.AsyncClient(
            base_url=_API,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            transport=_transport,
        )

    async def _call(self, client: httpx.AsyncClient, method: str, **params: Any) -> dict:
        """Call a Web API method, waiting out 429s; `ok: false` becomes ConnectorError."""
        for attempt in range(_MAX_TRIES):
            resp = await client.get(f"/{method}", params=params)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry = resp.headers.get("Retry-After", "")
                await asyncio.sleep(
                    min(float(retry), 60.0) if retry.isdigit() else min(2**attempt, 30)
                )
                continue
            if resp.status_code >= 400:
                raise ConnectorError(f"Slack API error on {method}: HTTP {resp.status_code}")
            data = resp.json()
            if not data.get("ok"):
                raise ConnectorError(_error_message(method, data))
            return data
        raise ConnectorError(f"Slack kept rate-limiting {method}; try again later")

    async def _paginate(
        self, client: httpx.AsyncClient, method: str, key: str, **params: Any
    ) -> AsyncIterator[dict[str, Any]]:
        cursor = None
        while True:
            page = {"limit": 200, **params, **({"cursor": cursor} if cursor else {})}
            data = await self._call(client, method, **page)
            for item in data.get(key, []):
                yield item
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                return

    async def _channels(self, client: httpx.AsyncClient, wanted: list[str]) -> list[dict]:
        found: list[dict] = []
        async for ch in self._paginate(
            client,
            "conversations.list",
            "channels",
            types="public_channel,private_channel",
            exclude_archived="true",
        ):
            if wanted:
                if _keys(ch) & set(wanted):
                    found.append(ch)
            elif ch.get("is_member"):
                found.append(ch)
        if wanted:
            known = set().union(*(_keys(c) for c in found))
            if missing := [w for w in wanted if w not in known]:
                raise ConnectorError(
                    "Channel not found or not visible to the bot: "
                    + ", ".join(f"#{m}" for m in missing)
                )
            if outside := [c["name"] for c in found if not c.get("is_member")]:
                raise ConnectorError(
                    "Invite the bot to these channels first (/invite @bot): "
                    + ", ".join(f"#{n}" for n in outside)
                )
        return found

    async def _user_name(self, client: httpx.AsyncClient, user_id: str) -> str:
        if user_id not in self._users:
            try:
                user = (await self._call(client, "users.info", user=user_id)).get("user", {})
            except ConnectorError:
                user = {}
            profile = user.get("profile") or {}
            self._users[user_id] = (
                profile.get("display_name")
                or profile.get("real_name")
                or user.get("real_name")
                or user.get("name")
                or user_id
            )
        return self._users[user_id]

    async def iter_documents(
        self, progress: ProgressFn, since: datetime | None = None
    ) -> AsyncIterator[RawDocument]:
        cfg = self.validate_config(self.config)
        now = datetime.now(UTC)
        oldest = _day_start(now - timedelta(days=cfg["lookback_days"]))
        if since is not None:
            # A day document is rebuilt whole, so re-read from the start of since's day.
            oldest = max(oldest, _day_start(since))
        async with self._client() as client:
            await self._call(client, "auth.test")
            channels = await self._channels(client, cfg["channels"])
            for i, channel in enumerate(channels):
                await progress(i, len(channels), f"#{channel.get('name', channel['id'])}")
                async for doc in self._channel_docs(client, channel, oldest):
                    yield doc
            await progress(len(channels), len(channels), None)

    async def _channel_docs(
        self, client: httpx.AsyncClient, channel: dict, oldest: datetime
    ) -> AsyncIterator[RawDocument]:
        cid, name = channel["id"], channel.get("name", channel["id"])
        days: dict[str, list[dict]] = {}
        async for msg in self._paginate(
            client, "conversations.history", "messages", channel=cid, oldest=_ts(oldest)
        ):
            if msg.get("subtype") in _SKIP_SUBTYPES:
                continue
            thread_ts = msg.get("thread_ts")
            if thread_ts and thread_ts != msg["ts"]:
                continue  # a reply also sent to the channel; it's part of its thread
            if thread_ts and msg.get("reply_count"):
                replies = [
                    m
                    async for m in self._paginate(
                        client, "conversations.replies", "messages", channel=cid, ts=thread_ts
                    )
                    if m.get("subtype") not in _SKIP_SUBTYPES
                ]
                yield await self._document(client, cid, name, replies or [msg], thread=True)
            else:
                days.setdefault(_when(msg["ts"]).date().isoformat(), []).append(msg)
        for day in sorted(days):
            messages = sorted(days[day], key=lambda m: float(m["ts"]))
            yield await self._document(client, cid, name, messages, thread=False)

    async def _document(
        self, client: httpx.AsyncClient, cid: str, channel: str, messages: list[dict], thread: bool
    ) -> RawDocument:
        first = messages[0]
        first_day = _when(first["ts"]).date()
        lines: list[str] = []
        names: list[str] = []
        for msg in messages:
            who = await self._author(client, msg)
            if who not in names:
                names.append(who)
            when = _when(msg["ts"])
            stamp = when.strftime("%H:%M" if when.date() == first_day else "%Y-%m-%d %H:%M")
            text = await self._render_text(client, msg)
            lines.append(f"**{who}** ({stamp}): {text}")
        if thread:
            subject = (await self._render_text(client, first)).strip().splitlines() or [""]
            subject_line = _shorten(subject[0]) or first_day.isoformat()
            heading = f"#{channel} — {subject_line}"
            external_id = f"{cid}:{first['ts']}"
            extra: dict[str, Any] = {"thread_ts": first["ts"]}
        else:
            heading = f"#{channel} — {first_day.isoformat()}"
            external_id = f"{cid}:day:{first_day.isoformat()}"
            extra = {"date": first_day.isoformat()}
        updated = max(
            max(float(m["ts"]), float((m.get("edited") or {}).get("ts") or 0)) for m in messages
        )
        body = f"# {heading}\n\n" + "\n\n".join(lines) + "\n"
        return RawDocument(
            external_id=external_id,
            title=heading,
            data=body.encode("utf-8"),
            mime="text/markdown",
            url=f"https://slack.com/archives/{cid}/p{first['ts'].replace('.', '')}",
            metadata={
                "channel": channel,
                "channel_id": cid,
                "participants": names,
                "message_count": len(messages),
                **extra,
            },
            updated_at=datetime.fromtimestamp(updated, UTC),
        )

    async def _author(self, client: httpx.AsyncClient, msg: dict) -> str:
        if user := msg.get("user"):
            return await self._user_name(client, user)
        return msg.get("username") or (msg.get("bot_profile") or {}).get("name") or "bot"

    async def _render_text(self, client: httpx.AsyncClient, msg: dict) -> str:
        """Turn Slack mrkdwn escapes (<@U1>, <#C1|name>, <url|label>) into Markdown."""
        text = msg.get("text") or ""
        for user_id in set(re.findall(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", text)):
            name = await self._user_name(client, user_id)
            text = re.sub(rf"<@{user_id}(?:\|[^>]*)?>", lambda _, n=name: f"@{n}", text)
        text = re.sub(r"<#[A-Z0-9]+\|([^>]*)>", r"#\1", text)
        text = re.sub(r"<!(here|channel|everyone)[^>]*>", r"@\1", text)
        text = re.sub(r"<!subteam\^[A-Z0-9]+\|([^>]*)>", r"\1", text)
        text = re.sub(r"<((?:https?|mailto):[^|>]+)\|([^>]+)>", r"[\2](\1)", text)
        text = re.sub(r"<((?:https?|mailto):[^>]+)>", r"\1", text)
        text = html.unescape(text)
        files = [f.get("name") or f.get("title") for f in msg.get("files") or []]
        if names := [f for f in files if f]:
            text = (text + " " if text else "") + " ".join(f"[file: {n}]" for n in names)
        return text.strip()


def _error_message(method: str, data: dict) -> str:
    error = data.get("error") or "unknown_error"
    if error in _AUTH_ERRORS:
        return f"Slack rejected the bot token ({error})"
    if error == "missing_scope":
        needed = data.get("needed") or "?"
        return f"The Slack app is missing the {needed} scope (needed for {method})"
    if error == "not_in_channel":
        return f"The bot is not a member of a channel it tried to read ({method})"
    return f"Slack API error on {method}: {error}"


def _keys(channel: dict) -> set[str]:
    """What a configured entry may match: the id (as typed or lowercased) or the name."""
    return {channel["id"], channel["id"].lower(), channel.get("name") or channel["id"]}


def _day_start(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _ts(dt: datetime) -> str:
    return f"{dt.timestamp():.6f}"


def _when(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), UTC)


def _shorten(text: str, limit: int = 80) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
