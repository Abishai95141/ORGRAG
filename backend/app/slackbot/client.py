"""Slack Web API client. Every call Strata makes to Slack goes through `call`, so tests
substitute an `httpx.MockTransport` (`_transport`) and nothing reaches slack.com.

Requests are form-encoded POSTs, which every Web API method accepts. Rate limits (429
with Retry-After) and 5xx responses are retried a few times; `ok: false` becomes
SlackError carrying Slack's error code."""

import asyncio
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

API = "https://slack.com/api"
AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
# What the bot needs: mentions and DMs in, replies out (and a DM when an ephemeral
# reply can't be shown), the asker's email to match a Strata account, and its channel
# list for the admin's channel picker.
BOT_SCOPES = (
    "app_mentions:read",
    "channels:read",
    "chat:write",
    "groups:read",
    "im:history",
    "im:write",
    "users:read",
    "users:read.email",
)
MAX_TRIES = 3
MAX_WAIT_SECONDS = 20.0
# Tests swap in an httpx.MockTransport here.
_transport: httpx.AsyncBaseTransport | None = None


class SlackError(Exception):
    def __init__(self, method: str, error: str, retry_after: float | None = None):
        super().__init__(f"Slack {method} failed: {error}")
        self.method = method
        self.error = error
        self.retry_after = retry_after


def _form(params: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        out[key] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return out


async def call(
    method: str, *, token: str | None = None, basic: tuple[str, str] | None = None, **params: Any
) -> dict[str, Any]:
    """Call a Web API method with a bot token (or client credentials, for OAuth)."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(
        base_url=API, headers=headers, timeout=20, transport=_transport
    ) as client:
        for attempt in range(MAX_TRIES):
            try:
                resp = await client.post(f"/{method}", data=_form(params), auth=basic)
            except httpx.HTTPError as exc:
                if attempt + 1 == MAX_TRIES:
                    raise SlackError(method, f"unreachable ({type(exc).__name__})") from exc
                await asyncio.sleep(2**attempt)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                retry = resp.headers.get("Retry-After", "")
                wait = float(retry) if retry.isdigit() else float(2**attempt)
                if attempt + 1 == MAX_TRIES or wait > MAX_WAIT_SECONDS:
                    code = "ratelimited" if resp.status_code == 429 else f"http_{resp.status_code}"
                    raise SlackError(method, code, retry_after=wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise SlackError(method, f"http_{resp.status_code}")
            data = resp.json()
            if not data.get("ok"):
                raise SlackError(method, str(data.get("error") or "unknown_error"))
            return data
    raise SlackError(method, "unreachable")  # pragma: no cover - the loop always returns


# ── OAuth ────────────────────────────────────────────────────────────────────
def redirect_uri() -> str:
    return f"{get_settings().public_url.rstrip('/')}/api/slack/oauth/callback"


def authorize_url(state: str) -> str:
    query = {
        "client_id": get_settings().slack_client_id,
        "scope": ",".join(BOT_SCOPES),
        "redirect_uri": redirect_uri(),
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(query)}"


async def oauth_access(code: str) -> dict[str, Any]:
    """Exchange an OAuth code for the workspace's bot token (`oauth.v2.access`)."""
    s = get_settings()
    return await call(
        "oauth.v2.access",
        basic=(s.slack_client_id or "", s.slack_client_secret or ""),
        code=code,
        redirect_uri=redirect_uri(),
    )


async def revoke(token: str) -> None:
    await call("auth.revoke", token=token)


# ── messages ─────────────────────────────────────────────────────────────────
async def post_message(token: str, channel: str, text: str, thread_ts: str | None = None) -> str:
    """Post to a channel (in a thread when `thread_ts`); returns the message's ts."""
    data = await call(
        "chat.postMessage",
        token=token,
        channel=channel,
        text=text,
        thread_ts=thread_ts,
        unfurl_links=False,
        unfurl_media=False,
    )
    return str(data["ts"])


async def update_message(token: str, channel: str, ts: str, text: str) -> None:
    await call("chat.update", token=token, channel=channel, ts=ts, text=text, unfurl_links=False)


async def post_ephemeral(
    token: str, channel: str, user: str, text: str, thread_ts: str | None = None
) -> None:
    """A message only `user` sees, in a channel the bot is in."""
    await call(
        "chat.postEphemeral",
        token=token,
        channel=channel,
        user=user,
        text=text,
        thread_ts=thread_ts,
    )


async def open_dm(token: str, user: str) -> str:
    """The bot's DM channel with `user` (opened if needed)."""
    return str((await call("conversations.open", token=token, users=user))["channel"]["id"])


# ── people & channels ────────────────────────────────────────────────────────
async def user_info(token: str, user: str) -> dict[str, Any]:
    return (await call("users.info", token=token, user=user))["user"]


async def channel_info(token: str, channel: str) -> dict[str, Any]:
    return (await call("conversations.info", token=token, channel=channel))["channel"]


async def bot_channels(token: str, max_pages: int = 10) -> list[dict[str, Any]]:
    """Channels the bot is a member of (public and private), for the channel picker."""
    channels: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(max_pages):
        data = await call(
            "users.conversations",
            token=token,
            types="public_channel,private_channel",
            exclude_archived=True,
            limit=200,
            cursor=cursor,
        )
        channels += data.get("channels") or []
        cursor = (data.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break
    return channels
