from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.connectors import slack
from app.connectors.base import ConnectorError
from app.connectors.slack import SlackConnector

# Recent enough to fall inside the default 90-day lookback.
DAY1 = (datetime.now(UTC) - timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
DAY2 = DAY1 + timedelta(days=1)


def ts(day: datetime, hour: int, minute: int = 0) -> str:
    return f"{(day + timedelta(hours=hour, minutes=minute)).timestamp():.6f}"


THREAD = ts(DAY2, 10)
MESSAGES = [  # newest first, as conversations.history returns them
    {"ts": ts(DAY2, 10, 6), "user": "U2", "text": "also in channel", "thread_ts": THREAD},
    {"ts": THREAD, "user": "U1", "text": "Deploy is done <@U2>", "thread_ts": THREAD,
     "reply_count": 2},
    {"ts": ts(DAY2, 9), "user": "U2", "text": "morning &amp; coffee"},
    {"ts": ts(DAY1, 16), "user": "U3", "subtype": "channel_join", "text": "joined"},
    {"ts": ts(DAY1, 15, 30), "user": "U1", "text": "see <https://x.com/a|the docs>",
     "files": [{"name": "plan.pdf"}]},
    {"ts": ts(DAY1, 14), "bot_profile": {"name": "deploybot"}, "subtype": "bot_message",
     "text": "build #12 passed"},
]  # fmt: skip
REPLIES = [
    MESSAGES[1],
    {"ts": ts(DAY2, 10, 5), "user": "U2", "text": "thanks", "thread_ts": THREAD},
    {"ts": ts(DAY2 + timedelta(days=1), 8), "user": "U1", "text": "follow-up",
     "thread_ts": THREAD, "edited": {"ts": ts(DAY2 + timedelta(days=1), 8, 30)}},
]  # fmt: skip
USERS = {
    "U1": {"name": "alice.w", "profile": {"display_name": "alice"}},
    "U2": {"name": "bob", "profile": {"display_name": "", "real_name": "Bob B"}},
}


class FakeSlack:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.errors: dict[str, dict] = {}
        self.rate_limit_once = {"conversations.history"}
        self.channels = [
            {"id": "C1", "name": "general", "is_member": True},
            {"id": "C2", "name": "random", "is_member": False},
            {"id": "G3", "name": "secret", "is_member": True, "is_private": True},
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer xoxb-test"
        method = request.url.path.removeprefix("/api/")
        params = dict(request.url.params)
        self.calls.append((method, params))
        if method in self.rate_limit_once:
            self.rate_limit_once.discard(method)
            return httpx.Response(429, headers={"Retry-After": "0"})
        if method in self.errors:
            return httpx.Response(200, json={"ok": False, **self.errors[method]})
        return httpx.Response(
            200, json={"ok": True, **getattr(self, method.replace(".", "_"))(params)}
        )

    def auth_test(self, _):
        return {"team": "Acme", "url": "https://acme.slack.com/"}

    def conversations_list(self, params):
        assert params["types"] == "public_channel,private_channel"
        if params.get("cursor") == "page2":
            return {"channels": self.channels[1:], "response_metadata": {"next_cursor": ""}}
        return {"channels": self.channels[:1], "response_metadata": {"next_cursor": "page2"}}

    def conversations_history(self, params):
        if params["channel"] != "C1":
            return {"messages": []}
        msgs = [m for m in MESSAGES if float(m["ts"]) >= float(params["oldest"])]
        start = int(params.get("cursor") or 0)
        nxt = str(start + 3) if start + 3 < len(msgs) else ""
        return {"messages": msgs[start : start + 3], "response_metadata": {"next_cursor": nxt}}

    def conversations_replies(self, params):
        assert params["ts"] == THREAD
        return {"messages": REPLIES}

    def users_info(self, params):
        return {"user": USERS[params["user"]]}


@pytest.fixture
def fake(monkeypatch):
    api = FakeSlack()
    monkeypatch.setattr(slack, "_transport", httpx.MockTransport(api))
    return api


async def _noop(*_):
    return None


async def _collect(conn, since=None):
    return [d async for d in conn.iter_documents(_noop, since)]


def _conn(**config):
    return SlackConnector(config, {"token": "xoxb-test"})


def test_validate_config():
    assert SlackConnector.validate_config({"channels": "#General, C0123ABCD #general"}) == {
        "channels": ["general", "C0123ABCD"],
        "lookback_days": 90,
    }
    assert SlackConnector.validate_config({"lookback_days": "1000"})["lookback_days"] == 730
    assert SlackConnector.validate_config({"lookback_days": 0})["lookback_days"] == 90
    with pytest.raises(ValueError, match="whole number"):
        SlackConnector.validate_config({"lookback_days": "abc"})
    with pytest.raises(ValueError, match="channel"):
        SlackConnector.validate_config({"channels": ["bad name!"]})
    assert SlackConnector.secret_fields() == ["token"]


async def test_full_sync_builds_thread_and_day_documents(fake):
    docs = await _collect(_conn())
    # Only channels the bot is in; C2 is skipped, G3 is empty.
    assert [p["channel"] for m, p in fake.calls if m == "conversations.history"][-2:] == [
        "C1",
        "G3",
    ]
    by_id = {d.external_id: d for d in docs}
    day1, day2 = DAY1.date().isoformat(), DAY2.date().isoformat()
    assert list(by_id) == [f"C1:{THREAD}", f"C1:day:{day1}", f"C1:day:{day2}"]

    thread = by_id[f"C1:{THREAD}"]
    assert thread.title == "#general — Deploy is done @Bob B"
    assert thread.mime == "text/markdown"
    assert thread.url == f"https://slack.com/archives/C1/p{THREAD.replace('.', '')}"
    next_day = (DAY2 + timedelta(days=1)).date().isoformat()
    assert thread.data.decode() == (
        "# #general — Deploy is done @Bob B\n\n"
        "**alice** (10:00): Deploy is done @Bob B\n\n"
        "**Bob B** (10:05): thanks\n\n"
        f"**alice** ({next_day} 08:00): follow-up\n"
    )
    assert thread.updated_at == DAY2 + timedelta(days=1, hours=8, minutes=30)  # edited
    assert thread.metadata["participants"] == ["alice", "Bob B"]
    assert thread.metadata["message_count"] == 3

    first_day = by_id[f"C1:day:{day1}"]
    assert first_day.title == f"#general — {day1}"
    assert first_day.data.decode() == (
        f"# #general — {day1}\n\n"
        "**deploybot** (14:00): build #12 passed\n\n"
        "**alice** (15:30): see [the docs](https://x.com/a) [file: plan.pdf]\n"
    )
    assert first_day.updated_at == DAY1 + timedelta(hours=15, minutes=30)
    assert first_day.url == f"https://slack.com/archives/C1/p{ts(DAY1, 14).replace('.', '')}"
    assert "**Bob B** (09:00): morning & coffee" in by_id[f"C1:day:{day2}"].data.decode()

    # users.info is cached; the 429 on conversations.history was retried.
    assert sum(1 for m, _ in fake.calls if m == "users.info") == 2
    assert sum(1 for m, _ in fake.calls if m == "conversations.history") == 4


async def test_incremental_refetches_from_start_of_since_day(fake):
    since = DAY2 + timedelta(hours=9, minutes=30)
    docs = await _collect(_conn(channels=["general"]), since)
    assert [d.external_id for d in docs] == [
        f"C1:{THREAD}",
        f"C1:day:{DAY2.date().isoformat()}",
    ]
    oldest = [p["oldest"] for m, p in fake.calls if m == "conversations.history"]
    assert float(oldest[0]) == DAY2.timestamp()


async def test_lookback_limits_history(fake):
    await _collect(_conn(lookback_days=1))
    oldest = next(p["oldest"] for m, p in fake.calls if m == "conversations.history")
    assert (
        float(oldest)
        == (datetime.now(UTC) - timedelta(days=1))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )


async def test_list_ids_is_not_supported(fake):
    assert await _conn().list_ids() is None
    assert fake.calls == []


async def test_errors(fake):
    with pytest.raises(ConnectorError, match="Invite the bot.*#random"):
        await _collect(_conn(channels=["random", "C1"]))
    with pytest.raises(ConnectorError, match="not visible.*#nope"):
        await _collect(_conn(channels=["nope"]))
    with pytest.raises(ConnectorError, match="bot token"):
        await _collect(SlackConnector({}, {}))

    fake.errors["conversations.list"] = {"error": "missing_scope", "needed": "groups:read"}
    with pytest.raises(ConnectorError, match="groups:read"):
        await _collect(_conn())
    fake.errors["auth.test"] = {"error": "invalid_auth"}
    with pytest.raises(ConnectorError, match="rejected the bot token \\(invalid_auth\\)"):
        await _collect(_conn())
