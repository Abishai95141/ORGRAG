"""The Slack bot end to end, against a fake Slack Web API (httpx.MockTransport): the
OAuth install, the signed events endpoint, the answer job, account links, and the
permission rules — restricted collections never reach a channel answer."""

import json
import re
import time
import uuid
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select, text

from app.slackbot import signing
from tests.test_agent_nvidia import FakeNvidia, _answer_turn, _tool_turn
from tests.test_api_integration import _ingest, _join, _org, _signup

pytestmark = pytest.mark.integration

SECRET = "test-signing-secret"


class FakeSlack:
    """Answers the Web API methods the bot uses and records every call."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.users: dict[str, dict] = {}
        self.team = ("T0", "Acme")
        self.channels: list[dict] = []
        self.ephemeral_fails = False
        self.outage: set[str] = set()  # methods answering 429 with a long Retry-After
        self._ts = 0

    def user(self, slack_id: str, email: str | None, name: str = "someone") -> None:
        self.users[slack_id] = {
            "id": slack_id,
            "name": name,
            "profile": {"display_name": name, **({"email": email} if email else {})},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.removeprefix("/api/")
        params = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((method, params))
        if method == "oauth.v2.access":
            assert request.headers["Authorization"].startswith("Basic ")
            if params.get("code") == "bad":
                return httpx.Response(200, json={"ok": False, "error": "invalid_code"})
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "access_token": f"xoxb-secret-{self.team[0]}",
                    "token_type": "bot",
                    "scope": "app_mentions:read,chat:write",
                    "bot_user_id": "UBOT",
                    "app_id": "A1",
                    "team": {"id": self.team[0], "name": self.team[1]},
                    "is_enterprise_install": False,
                },
            )
        assert request.headers["Authorization"].startswith("Bearer xoxb-secret-")
        if method in self.outage:
            return httpx.Response(429, headers={"Retry-After": "60"})
        if method == "users.info":
            found = self.users.get(params["user"])
            body = (
                {"ok": True, "user": found} if found else {"ok": False, "error": "user_not_found"}
            )
            return httpx.Response(200, json=body)
        if method == "chat.postMessage":
            self._ts += 1
            return httpx.Response(200, json={"ok": True, "ts": f"1800000000.{self._ts:06d}"})
        if method == "chat.postEphemeral" and self.ephemeral_fails:
            return httpx.Response(200, json={"ok": False, "error": "user_not_in_channel"})
        if method == "conversations.open":
            return httpx.Response(200, json={"ok": True, "channel": {"id": "DNEW"}})
        if method == "users.conversations":
            return httpx.Response(200, json={"ok": True, "channels": self.channels})
        return httpx.Response(200, json={"ok": True})

    def of(self, method: str) -> list[dict]:
        return [p for m, p in self.calls if m == method]

    def posted(self) -> list[dict]:
        """Every message the bot wrote or rewrote, in order."""
        return [p for m, p in self.calls if m in ("chat.postMessage", "chat.update")]


@pytest.fixture
async def slack(monkeypatch):
    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.models import Job
    from app.slackbot import client

    s = get_settings()
    monkeypatch.setattr(s, "slack_client_id", "1234.5678")
    monkeypatch.setattr(s, "slack_client_secret", "client-secret")
    monkeypatch.setattr(s, "slack_signing_secret", SECRET)
    fake = FakeSlack()
    fake.team = (f"T{uuid.uuid4().hex[:10].upper()}", "Acme Slack")
    monkeypatch.setattr(client, "_transport", httpx.MockTransport(fake))
    yield fake
    # Never leave a question queued for a later test, which would run it without the fake.
    async with SessionLocal() as db:
        for job in (await db.execute(select(Job).where(Job.kind == "slack_answer"))).scalars():
            await db.delete(job)
        await db.commit()


async def _connect(admin: httpx.AsyncClient, slug: str, fake: FakeSlack) -> dict:
    r = await admin.get(f"/api/orgs/{slug}/slack/install", follow_redirects=False)
    assert r.status_code == 307, r.text
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = await admin.get(
        "/api/slack/oauth/callback",
        params={"code": "good", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "connected=" in r.headers["location"], r.headers
    overview = (await admin.get(f"/api/orgs/{slug}/slack")).json()
    return next(w for w in overview["workspaces"] if w["team_id"] == fake.team[0])


async def _send(
    client: httpx.AsyncClient,
    payload: dict,
    *,
    secret: str = SECRET,
    timestamp: int | None = None,
    retry: int | None = None,
) -> httpx.Response:
    body = json.dumps(payload).encode()
    ts = str(int(time.time()) if timestamp is None else timestamp)
    headers = {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": signing.signature(secret, ts, body),
    }
    if retry:
        headers["X-Slack-Retry-Num"] = str(retry)
        headers["X-Slack-Retry-Reason"] = "http_timeout"
    return await client.post("/api/slack/events", content=body, headers=headers)


def _event(team: str, event: dict, event_id: str | None = None) -> dict:
    return {
        "type": "event_callback",
        "team_id": team,
        "api_app_id": "A1",
        "event": event,
        "event_id": event_id or f"Ev{uuid.uuid4().hex[:12].upper()}",
        "event_time": int(time.time()),
    }


def _mention(user: str, text: str, channel: str = "C0QUESTION", ts: str | None = None, **extra):
    return {
        "type": "app_mention",
        "user": user,
        "text": f"<@UBOT> {text}",
        "channel": channel,
        "ts": ts or f"{time.time():.6f}",
        **extra,
    }


def _dm(user: str, text: str, ts: str | None = None, **extra):
    return {
        "type": "message",
        "channel_type": "im",
        "channel": "D200",
        "user": user,
        "text": text,
        "ts": ts or f"{time.time():.6f}",
        **extra,
    }


async def _queued() -> list:
    from app.core.db import SessionLocal
    from app.models import Job

    async with SessionLocal() as db:
        rows = await db.execute(
            select(Job).where(Job.kind == "slack_answer", Job.status == "queued")
        )
        return list(rows.scalars())


async def _run_jobs() -> None:
    """Run every queued job inline, as the worker would."""
    from app.core.db import SessionLocal
    from app.worker.jobs import HANDLERS
    from app.worker.queue import claim_next, finish

    async with SessionLocal() as db:
        while job := await claim_next(db):
            await HANDLERS[job.kind](db, job)
            await finish(db, job)


async def _ask(app_client, fake: FakeSlack, event: dict) -> str:
    """Send one event, run the job, and return the text of the bot's final reply."""
    before = len(fake.posted())
    r = await _send(app_client, _event(fake.team[0], event))
    assert r.status_code == 200, r.text
    await _run_jobs()
    # The placeholder, then the answer (replacing it) and any continuation messages.
    replies = [p["text"] for p in fake.posted()[before:] if not p["text"].startswith(":hourglass")]
    assert replies, "the bot posted no answer"
    return "\n".join(replies)


async def _general(admin, slug) -> str:
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    return next(c["id"] for c in cols if c["name"] == "General")


# ── events endpoint ──────────────────────────────────────────────────────────
async def test_events_require_a_valid_fresh_signature(app, slack):
    from tests.test_api_integration import _client

    anon = await _client(app)
    challenge = {"type": "url_verification", "token": "x", "challenge": "c-123"}

    r = await anon.post("/api/slack/events", json=challenge)
    assert r.status_code == 401 and r.json()["code"] == "invalid_signature"
    assert (await _send(anon, challenge, secret="wrong")).status_code == 401
    stale = await _send(anon, challenge, timestamp=int(time.time()) - 6 * 60)
    assert stale.status_code == 401

    r = await _send(anon, challenge)
    assert r.status_code == 200 and r.json() == {"challenge": "c-123"}


async def test_events_answer_503_without_slack_settings(app, monkeypatch):
    from app.core.config import get_settings
    from tests.test_api_integration import _client

    monkeypatch.setattr(get_settings(), "slack_signing_secret", None)
    r = await (await _client(app)).post("/api/slack/events", json={"type": "url_verification"})
    assert r.status_code == 503 and r.json()["code"] == "slack_not_configured"


async def test_events_enqueue_questions_once_and_ignore_the_rest(app, slack):
    admin = await _signup(app, "slackadmin")
    org = await _org(admin, "Queue Co")
    await _connect(admin, org["slug"], slack)
    team = slack.team[0]

    mention = _event(team, _mention("U1", "where is the runbook?", ts="1700000000.000100"))
    assert (await _send(admin, mention)).status_code == 200
    jobs = await _queued()
    assert len(jobs) == 1
    payload = jobs[0].payload
    assert payload["team_id"] == team and payload["channel"] == "C0QUESTION"
    assert payload["user"] == "U1" and payload["private"] is False
    assert payload["ts"] == "1700000000.000100" and payload["event_id"] == mention["event_id"]

    # Slack retries when we're slow: same event_id, acknowledged, not asked twice.
    for attempt in (1, 2):
        assert (await _send(admin, mention, retry=attempt)).status_code == 200
    assert len(await _queued()) == 1

    ignored = [
        _mention("UBOT", "talking to myself"),
        _mention("U1", "from a bot", bot_id="B1"),
        _dm("U1", "edited", subtype="message_changed"),
        _dm("U1", "joined", subtype="channel_join"),
        {**_mention("U1", "in a DM"), "channel": "D200"},  # answered via message.im
        {"type": "message", "channel_type": "channel", "channel": "C1", "user": "U1",
         "text": "chatter", "ts": "1.2"},
        {"type": "reaction_added", "user": "U1", "reaction": "eyes"},
    ]  # fmt: skip
    for event in ignored:
        assert (await _send(admin, _event(team, event))).status_code == 200
    assert (await _send(admin, _event("TUNKNOWN", _mention("U1", "hi")))).status_code == 200
    assert len(await _queued()) == 1

    dm = _event(team, _dm("U1", "private question", subtype="file_share"))
    assert (await _send(admin, dm)).status_code == 200
    jobs = await _queued()
    assert len(jobs) == 2 and any(j.payload["private"] for j in jobs)


async def test_app_uninstalled_forgets_the_workspace(app, slack):
    from app.core.db import SessionLocal
    from app.models import SlackWorkspace

    admin = await _signup(app, "slackadmin")
    org = await _org(admin, "Uninstall Co")
    ws = await _connect(admin, org["slug"], slack)
    # Another user's token being revoked isn't the bot's.
    other = {"type": "tokens_revoked", "tokens": {"oauth": ["U5"], "bot": ["UOTHER"]}}
    assert (await _send(admin, _event(slack.team[0], other))).status_code == 200
    assert len((await admin.get(f"/api/orgs/{org['slug']}/slack")).json()["workspaces"]) == 1

    r = await _send(admin, _event(slack.team[0], {"type": "app_uninstalled"}))
    assert r.status_code == 200
    async with SessionLocal() as db:
        assert await db.get(SlackWorkspace, uuid.UUID(ws["id"])) is None
    assert (await admin.get(f"/api/orgs/{org['slug']}/slack")).json()["workspaces"] == []

    ws = await _connect(admin, org["slug"], slack)
    revoked = {"type": "tokens_revoked", "tokens": {"bot": ["UBOT"]}}
    assert (await _send(admin, _event(slack.team[0], revoked))).status_code == 200
    assert (await admin.get(f"/api/orgs/{org['slug']}/slack")).json()["workspaces"] == []


# ── install (OAuth v2) ───────────────────────────────────────────────────────
async def test_install_round_trip_stores_the_token_encrypted(app, slack):
    from app.core.db import SessionLocal
    from app.models import SlackWorkspace

    admin = await _signup(app, "slackadmin")
    member = await _signup(app, "member")
    org = await _org(admin, "Install Co")
    slug = org["slug"]
    await _join(admin, member, slug)

    assert (await member.get(f"/api/orgs/{slug}/slack")).status_code == 403
    assert (await member.get(f"/api/orgs/{slug}/slack/install")).status_code == 403

    r = await admin.get(f"/api/orgs/{slug}/slack/install", follow_redirects=False)
    assert r.status_code == 307
    url = urlsplit(r.headers["location"])
    query = parse_qs(url.query)
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://slack.com/oauth/v2/authorize"
    assert query["client_id"] == ["1234.5678"]
    assert "users:read.email" in query["scope"][0].split(",")
    assert query["redirect_uri"] == ["http://localhost:3000/api/slack/oauth/callback"]
    state = query["state"][0]

    async def callback(client, **params):
        return await client.get("/api/slack/oauth/callback", params=params, follow_redirects=False)

    # A forged state, or someone else's browser finishing the flow, connects nothing.
    r = await callback(admin, code="good", state=state[:-3] + "abc")
    assert r.status_code == 400 and r.json()["code"] == "invalid_state"
    r = await callback(member, code="good", state=state)
    assert r.status_code == 303 and "slack_error=wrong_user" in r.headers["location"]
    r = await callback(admin, error="access_denied", state=state)
    assert "slack_error=access_denied" in r.headers["location"]
    r = await callback(admin, code="bad", state=state)
    assert "slack_error=invalid_code" in r.headers["location"]
    assert slack.of("oauth.v2.access")[-1]["redirect_uri"].endswith("/api/slack/oauth/callback")

    r = await callback(admin, code="good", state=state)
    assert r.status_code == 303
    assert r.headers["location"].startswith(f"http://localhost:3000/o/{slug}/settings/slack?")
    overview = (await admin.get(f"/api/orgs/{slug}/slack")).json()
    assert overview["configured"] is True
    assert overview["events_url"] == "http://localhost:3000/api/slack/events"
    [ws] = overview["workspaces"]
    assert ws["team_id"] == slack.team[0] and ws["team_name"] == "Acme Slack"
    assert ws["default_org"]["slug"] == slug and ws["connected_by"]["email"] == admin.email
    assert "xoxb" not in json.dumps(overview)

    async with SessionLocal() as db:
        raw = (
            await db.execute(
                text("SELECT secrets::text FROM slack_workspaces WHERE team_id = :t"),
                {"t": slack.team[0]},
            )
        ).scalar_one()
        stored = json.loads(raw)
        assert set(stored) == {"v", "ct"} and "xoxb" not in raw  # ciphertext only
        workspace = (
            await db.execute(select(SlackWorkspace).where(SlackWorkspace.team_id == slack.team[0]))
        ).scalar_one()
        assert workspace.secrets == {"bot_token": f"xoxb-secret-{slack.team[0]}"}

    # Connecting again (e.g. after adding scopes) keeps one workspace and one connection.
    await _connect(admin, slug, slack)
    assert len((await admin.get(f"/api/orgs/{slug}/slack")).json()["workspaces"]) == 1


# ── answering ────────────────────────────────────────────────────────────────
async def test_mention_is_answered_in_the_thread_with_linked_citations(app, slack, monkeypatch):
    from app.agent import engine
    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.models import Message

    admin = await _signup(app, "slackadmin")
    org = await _org(admin, "Answer Co")
    slug = org["slug"]
    await _ingest(
        admin,
        slug,
        await _general(admin, slug),
        {"leave.md": "# Leave policy\n\nStaff get 16 weeks of paid parental leave."},
    )
    await _connect(admin, slug, slack)
    slack.user("U1", admin.email.upper(), "ann")  # emails match case-insensitively

    answer = "Staff get **16 weeks** of paid leave [1].\n\n| Who | Weeks |\n|---|---|\n| All | 16 |"
    fake = FakeNvidia(
        [
            _tool_turn("search_knowledge", '{"queries": ["parental leave"]}'),
            _answer_turn(answer),
            _answer_turn("Ask HR about extensions."),
        ]
    )
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake.stream_chat)

    root = "1700000100.000100"
    r = await _send(admin, _event(slack.team[0], _mention("U1", "how much leave?", ts=root)))
    assert r.status_code == 200
    await _run_jobs()

    placeholder, update = slack.of("chat.postMessage")[0], slack.of("chat.update")[0]
    assert placeholder["thread_ts"] == root and placeholder["channel"] == "C0QUESTION"
    assert "Searching" in placeholder["text"] and update["ts"].startswith("1800000000.")
    reply = update["text"]
    share = re.search(r"http://localhost:3000/a/([\w-]+)\?source=1", reply)
    assert share, reply
    assert reply.startswith(
        f"Staff get *16 weeks* of paid leave <http://localhost:3000/a/{share.group(1)}"
        "?source=1|[1]>."
    )
    assert "```\nWho | Weeks\n----+------\nAll | 16\n```" in reply  # table → code block
    assert (
        "*Sources*\n1. <http://localhost:3000/a/" in reply and "|Leave policy> — _General_" in reply
    )
    assert reply.rstrip().endswith("|Open in Strata>")
    assert "how much leave?" in fake.calls[0]["messages"][-1]["content"]

    # Recorded in Strata like any chat: history, sources, citations, a permalink.
    convs = (await admin.get(f"/api/orgs/{slug}/conversations")).json()["items"]
    assert len(convs) == 1
    detail = (await admin.get(f"/api/orgs/{slug}/conversations/{convs[0]['id']}")).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][0]["content"] == "how much leave?"
    assistant = detail["messages"][1]
    assert assistant["sources"][0]["title"] == "Leave policy"
    assert assistant["share_token"] == share.group(1)
    insights = (await admin.get(f"/api/orgs/{slug}/insights?days=7")).json()
    assert insights["top_sources"][0]["citations"] == 1
    shared = (await admin.get(f"/api/shared/{share.group(1)}")).json()
    assert shared["access"] == "full"

    # A follow-up in the same thread continues the conversation.
    await _send(admin, _event(slack.team[0], _mention("U1", "and extensions?", thread_ts=root)))
    await _run_jobs()
    assert slack.of("chat.update")[-1]["text"].startswith("Ask HR about extensions.")
    detail = (await admin.get(f"/api/orgs/{slug}/conversations/{convs[0]['id']}")).json()
    assert len(detail["messages"]) == 4
    assert "and extensions?" in fake.calls[2]["messages"][-1]["content"]

    # Once the conversation goes on in the web app, the thread starts a fresh one: its
    # history may hold answers from collections a channel must not see.
    async with SessionLocal() as db:
        db.add(Message(conversation_id=uuid.UUID(convs[0]["id"]), role="user", content="web"))
        await db.commit()
    fake.turns.append(_answer_turn("Fresh start."))
    await _send(admin, _event(slack.team[0], _mention("U1", "anything else?", thread_ts=root)))
    await _run_jobs()
    assert slack.of("chat.update")[-1]["text"] == "Fresh start."
    assert len((await admin.get(f"/api/orgs/{slug}/conversations")).json()["items"]) == 2


async def test_model_errors_are_shown_in_the_thread(app, slack, monkeypatch):
    from app.agent import engine
    from app.core.config import get_settings

    admin = await _signup(app, "slackadmin")
    org = await _org(admin, "Error Co")
    await _connect(admin, org["slug"], slack)
    slack.user("U1", admin.email)

    def broken(**kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", broken)
    await _ingest(
        admin, org["slug"], await _general(admin, org["slug"]), {"a.md": "# A\n\nSomething."}
    )
    await _send(admin, _event(slack.team[0], _mention("U1", "what happens?")))
    await _run_jobs()
    reply = slack.of("chat.update")[-1]["text"]
    assert reply.startswith(":warning: I couldn't answer that:")


async def test_long_answers_continue_in_the_thread(app, slack, monkeypatch):
    from app.agent import engine
    from app.core.config import get_settings

    admin = await _signup(app, "slackadmin")
    org = await _org(admin, "Long Co")
    slug = org["slug"]
    await _ingest(
        admin, slug, await _general(admin, slug), {"faq.md": "# FAQ\n\nEverything you need."}
    )
    await _connect(admin, slug, slack)
    slack.user("U1", admin.email)
    long_answer = "\n\n".join(f"Point {i}: " + "detail " * 60 + "[1]" for i in range(25))
    fake = FakeNvidia(
        [_tool_turn("search_knowledge", '{"queries": ["faq"]}'), _answer_turn(long_answer)]
    )
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake.stream_chat)

    root = "1700000200.000100"
    await _send(admin, _event(slack.team[0], _mention("U1", "tell me everything", ts=root)))
    await _run_jobs()
    [update] = slack.of("chat.update")
    rest = slack.of("chat.postMessage")[1:]  # after the placeholder
    assert update["text"].startswith("Point 0:") and len(rest) >= 2
    assert all(len(p["text"]) <= 3500 for p in [update, *rest])
    assert all(p["thread_ts"] == root and p["channel"] == "C0QUESTION" for p in rest)
    assert "Point 24:" in rest[-1]["text"] and rest[-1]["text"].endswith("|Open in Strata>")


async def test_a_slack_outage_retries_the_reply_without_asking_again(app, slack):
    from datetime import UTC, datetime

    from app.core.db import SessionLocal
    from app.slackbot.client import SlackError
    from app.worker.jobs import HANDLERS
    from app.worker.queue import claim_next, finish

    admin = await _signup(app, "slackadmin")
    org = await _org(admin, "Outage Co")
    slug = org["slug"]
    await _ingest(
        admin, slug, await _general(admin, slug), {"vpn.md": "# VPN\n\nUse the blue client."}
    )
    await _connect(admin, slug, slack)
    slack.user("U1", admin.email)
    slack.outage = {"chat.update"}
    await _send(admin, _event(slack.team[0], _mention("U1", "which vpn client?")))

    async with SessionLocal() as db:
        job = await claim_next(db)
        with pytest.raises(SlackError):
            await HANDLERS[job.kind](db, job)
        await finish(db, job, error="ratelimited")  # what the worker does: back to the queue
        assert job.status == "queued" and job.payload["reply"] and not job.payload.get("posted")
        job.run_after = datetime.now(UTC)
        await db.commit()

    slack.outage = set()
    await _run_jobs()
    assert len(slack.of("chat.postMessage")) == 1  # one placeholder, one run
    assert len(slack.of("chat.update")) == 2 and "VPN" in slack.of("chat.update")[-1]["text"]
    convs = (await admin.get(f"/api/orgs/{slug}/conversations")).json()["items"]
    detail = (await admin.get(f"/api/orgs/{slug}/conversations/{convs[0]['id']}")).json()
    assert len(convs) == 1 and len(detail["messages"]) == 2


async def test_unknown_slack_user_gets_a_link_to_connect_accounts(app, slack):
    admin = await _signup(app, "slackadmin")
    stranger = await _signup(app, "stranger")
    other = await _signup(app, "other")
    org = await _org(admin, "Link Co")
    slug = org["slug"]
    await _join(admin, other, slug)
    await _ingest(
        admin, slug, await _general(admin, slug), {"vpn.md": "# VPN\n\nUse the blue client."}
    )
    await _connect(admin, slug, slack)
    slack.user("U9", "nobody-in-strata@elsewhere.test", "ann")

    r = await _send(admin, _event(slack.team[0], _mention("U9", "vpn client?", ts="1.5")))
    assert r.status_code == 200
    await _run_jobs()
    [prompt] = slack.of("chat.postEphemeral")
    assert (
        prompt["user"] == "U9"
        and prompt["channel"] == "C0QUESTION"
        and prompt["thread_ts"] == "1.5"
    )
    assert not slack.of("chat.postMessage")  # nothing public until we know who's asking
    token = re.search(r"http://localhost:3000/slack/link/([\w.-]+)\|", prompt["text"]).group(1)

    # Someone outside the workspace's orgs can't take the link.
    preview = (await stranger.get(f"/api/slack/link/{token}")).json()
    assert preview["eligible"] is False and preview["linked"] == "none"
    r = await stranger.post(f"/api/slack/link/{token}")
    assert r.status_code == 403

    preview = (await admin.get(f"/api/slack/link/{token}")).json()
    assert preview["slack_user_id"] == "U9" and preview["slack_user_name"] == "ann"
    assert preview["team_name"] == "Acme Slack" and preview["eligible"] is True
    r = await admin.post(f"/api/slack/link/{token}")
    assert r.status_code == 201 and r.json()["slack_user_id"] == "U9"
    assert (await admin.post(f"/api/slack/link/{token}")).status_code == 200  # idempotent
    r = await other.post(f"/api/slack/link/{token}")
    assert r.status_code == 409 and r.json()["code"] == "slack_user_linked"
    assert (await admin.get(f"/api/slack/link/{token}")).json()["linked"] == "you"
    links = (await admin.get("/api/slack/links")).json()
    assert [(link["slack_user_id"], link["team_id"]) for link in links] == [("U9", slack.team[0])]

    # Now the same Slack user is answered as that Strata user.
    reply = await _ask(admin, slack, _mention("U9", "vpn client?"))
    assert "VPN" in reply
    convs = (await admin.get(f"/api/orgs/{slug}/conversations")).json()["items"]
    assert [c["title"] for c in convs] == ["vpn client?"]

    # Unlinking puts them back to unknown.
    assert (await admin.delete(f"/api/slack/links/{links[0]['id']}")).status_code == 204
    assert (await other.delete(f"/api/slack/links/{links[0]['id']}")).status_code == 404
    await _send(admin, _event(slack.team[0], _mention("U9", "again?")))
    await _run_jobs()
    assert len(slack.of("chat.postEphemeral")) == 2

    # When the ephemeral can't be shown, the link goes to a DM instead.
    slack.ephemeral_fails = True
    await _send(admin, _event(slack.team[0], _mention("U9", "and now?")))
    await _run_jobs()
    assert slack.of("conversations.open")[-1]["users"] == "U9"
    assert "/slack/link/" in slack.of("chat.postMessage")[-1]["text"]
    assert slack.of("chat.postMessage")[-1]["channel"] == "DNEW"


async def test_expired_or_forged_link_tokens(app, slack):
    from datetime import timedelta

    admin = await _signup(app, "slackadmin")
    org = await _org(admin, "Expiry Co")
    ws = await _connect(admin, org["slug"], slack)
    old = signing._encode(
        {"w": ws["id"], "t": slack.team[0], "u": "U1", "n": None},
        signing._LINK_AUDIENCE,
        timedelta(seconds=-1),
    )
    assert (await admin.get(f"/api/slack/link/{old}")).json()["expired"] is True
    r = await admin.post(f"/api/slack/link/{old}")
    assert r.status_code == 400 and r.json()["code"] == "link_expired"
    other_team = signing.make_link_token(uuid.UUID(ws["id"]), "TOTHER", "U1", None)
    assert (await admin.get(f"/api/slack/link/{other_team}")).status_code == 404
    assert (await admin.get("/api/slack/link/garbage")).status_code == 404


# ── the channel vs DM rule ───────────────────────────────────────────────────
async def test_restricted_collections_are_used_in_dms_but_never_in_channels(app, slack):
    from app.core.db import SessionLocal
    from app.models import Conversation, Message

    admin = await _signup(app, "slackadmin")
    org = await _org(admin, "Board Co")
    slug = org["slug"]
    board = (
        await admin.post(
            f"/api/orgs/{slug}/collections",
            json={"name": "Board", "visibility": "restricted", "allowed_user_ids": []},
        )
    ).json()
    await _ingest(
        admin,
        slug,
        board["id"],
        {"board.md": "# Board memo\n\nThe zeppelin acquisition closes in March."},
    )
    await _ingest(
        admin,
        slug,
        await _general(admin, slug),
        {"deploys.md": "# Deploy policy\n\nFriday deploys need manager approval."},
    )
    await _connect(admin, slug, slack)
    slack.user("U1", admin.email)
    # The admin can read Board in Strata (and search finds it there).
    hits = await admin.post(f"/api/orgs/{slug}/search", json={"query": "zeppelin acquisition"})
    assert hits.json()["results"][0]["title"] == "Board memo"

    channel_reply = await _ask(admin, slack, _mention("U1", "zeppelin acquisition timing?"))
    assert "Board memo" not in channel_reply and "zeppelin" not in channel_reply.lower()
    assert "Restricted collections are only searched in DMs with me" in channel_reply

    dm_reply = await _ask(admin, slack, _dm("U1", "zeppelin acquisition timing?"))
    assert "Board memo" in dm_reply and "Restricted collections" not in dm_reply
    assert slack.posted()[-1]["channel"] == "D200"
    assert "/chat/" in dm_reply  # a DM links to the asker's own conversation

    # The rule is in the retrieval scope: the channel answer's sources never held it.
    async with SessionLocal() as db:
        answers = (
            (
                await db.execute(
                    select(Message)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(Conversation.org_id == uuid.UUID(org["id"]))
                    .where(Message.role == "assistant")
                    .order_by(Message.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
    dm_answer, channel_answer = answers
    assert "Board memo" in {s["title"] for s in dm_answer.sources}
    assert "Board memo" not in {s["title"] for s in channel_answer.sources}
    assert all(s["collection_name"] != "Board" for s in channel_answer.sources)


# ── channel → org scope ──────────────────────────────────────────────────────
async def test_channel_scope_mapping_rules(app, slack):
    alice = await _signup(app, "alice")  # admin of A and B
    bob = await _signup(app, "bob")  # admin of A, member of B
    a = await _org(alice, "Scope A")
    b = await _org(alice, "Scope B")
    c = await _org(alice, "Scope C")  # never connects the workspace
    await _join(alice, bob, a["slug"], "admin")
    ws = await _connect(alice, a["slug"], slack)
    await _connect(alice, b["slug"], slack)
    url = f"/api/orgs/{a['slug']}/slack/workspaces/{ws['id']}/channels"

    r = await alice.put(f"{url}/C0SHARED1", json={"org_slugs": [a["slug"], c["slug"]]})
    assert r.status_code == 400 and r.json()["code"] == "org_not_connected"
    r = await bob.put(f"{url}/C0SHARED1", json={"org_slugs": [a["slug"], b["slug"]]})
    assert r.status_code == 400 and r.json()["code"] == "unknown_org"  # not in B
    await _join(alice, bob, b["slug"], "member")
    r = await bob.put(f"{url}/C0SHARED1", json={"org_slugs": [a["slug"], b["slug"]]})
    assert r.status_code == 403  # adding B takes an admin of B
    assert (await bob.put(f"{url}/D0DIRECT1", json={"org_slugs": [a["slug"]]})).status_code == 400

    r = await alice.put(
        f"{url}/C0SHARED1", json={"org_slugs": [a["slug"], b["slug"]], "channel_name": "shared"}
    )
    assert r.status_code == 200, r.text
    assert [o["slug"] for o in r.json()["orgs"]] == [a["slug"], b["slug"]]
    listed = (await bob.get(url)).json()
    assert [(ch["channel_id"], ch["channel_name"]) for ch in listed] == [("C0SHARED1", "shared")]

    # Bob (admin of A, already in scope) may narrow the scope, but not add B back.
    r = await bob.put(f"{url}/C0SHARED1", json={"org_slugs": [a["slug"]]})
    assert r.status_code == 200 and [o["slug"] for o in r.json()["orgs"]] == [a["slug"]]
    assert (
        await bob.put(f"{url}/C0SHARED1", json={"org_slugs": [a["slug"], b["slug"]]})
    ).status_code == 403

    # B maps a channel to itself; A's admin can't take it over or reset it.
    b_url = f"/api/orgs/{b['slug']}/slack/workspaces/{ws['id']}/channels"
    assert (
        await alice.put(f"{b_url}/C0BTEAM02", json={"org_slugs": [b["slug"]]})
    ).status_code == 200
    assert (await bob.put(f"{url}/C0BTEAM02", json={"org_slugs": [a["slug"]]})).status_code == 403
    assert (await bob.delete(f"{url}/C0BTEAM02")).status_code == 403
    assert (await bob.delete(f"{url}/C0SHARED1")).status_code == 204  # back to the default (A)
    assert (await bob.get(url)).json() == []

    # An org in the scope that Bob isn't in survives his edit (he couldn't see or name it).
    d = await _org(alice, "Scope D")
    await _connect(alice, d["slug"], slack)
    r = await alice.put(f"{url}/C0WITHD001", json={"org_slugs": [d["slug"], a["slug"]]})
    assert [o["slug"] for o in r.json()["orgs"]] == [d["slug"], a["slug"]]
    r = await bob.put(f"{url}/C0WITHD001", json={"org_slugs": [a["slug"], b["slug"]]})
    assert r.status_code == 403  # still can't add B
    r = await bob.put(f"{url}/C0WITHD001", json={"org_slugs": [a["slug"]], "channel_name": "x"})
    assert r.status_code == 200 and r.json()["other_org_count"] == 1
    assert [o["slug"] for o in r.json()["orgs"]] == [a["slug"]]
    listed = (await alice.get(url)).json()
    assert [[o["slug"] for o in ch["orgs"]] for ch in listed] == [[a["slug"], d["slug"]]]
    assert (await bob.delete(f"{url}/C0WITHD001")).status_code == 204

    # The channel picker lists the bot's channels, marking those mapped to this org.
    slack.channels = [
        {"id": "C0BTEAM02", "name": "b-team", "is_private": True},
        {"id": "C0GENERAL", "name": "general"},
    ]
    picker = (
        await alice.get(f"/api/orgs/{b['slug']}/slack/workspaces/{ws['id']}/bot-channels")
    ).json()
    assert [(ch["name"], ch["mapped"], ch["is_private"]) for ch in picker] == [
        ("b-team", True, True),
        ("general", False, False),
    ]

    # B disconnecting leaves its channel searching nothing (not falling back to A's default).
    assert (
        await alice.delete(f"/api/orgs/{b['slug']}/slack/workspaces/{ws['id']}")
    ).status_code == 204
    slack.user("UA", alice.email)
    await _send(alice, _event(slack.team[0], _mention("UA", "hello?", channel="C0BTEAM02")))
    await _run_jobs()
    assert "isn't a member" not in slack.of("chat.postEphemeral")[-1]["text"]
    assert "doesn't search any" in slack.of("chat.postEphemeral")[-1]["text"]

    # The last org to disconnect revokes the token and forgets the workspace.
    assert (
        await alice.delete(f"/api/orgs/{d['slug']}/slack/workspaces/{ws['id']}")
    ).status_code == 204
    assert not slack.of("auth.revoke")  # A is still connected
    assert (
        await alice.delete(f"/api/orgs/{a['slug']}/slack/workspaces/{ws['id']}")
    ).status_code == 204
    assert slack.of("auth.revoke")
    assert (await alice.get(f"/api/orgs/{a['slug']}/slack")).json()["workspaces"] == []
    assert (await alice.get(url)).status_code == 404


async def test_multi_org_channel_searches_only_the_askers_orgs(app, slack):
    alice = await _signup(app, "alice")  # in A and B
    carol = await _signup(app, "carol")  # in A only
    a = await _org(alice, "Kestrel A")
    b = await _org(alice, "Kestrel B")
    await _join(alice, carol, a["slug"])
    for org, runway in ((a, "blue"), (b, "red")):
        await _ingest(
            alice,
            org["slug"],
            await _general(alice, org["slug"]),
            {"launch.md": f"# Launch {runway}\n\nKestrel launch uses the {runway} runway."},
        )
    ws = await _connect(alice, a["slug"], slack)
    await _connect(alice, b["slug"], slack)
    url = f"/api/orgs/{a['slug']}/slack/workspaces/{ws['id']}/channels/C0KESTREL"
    assert (await alice.put(url, json={"org_slugs": [a["slug"], b["slug"]]})).status_code == 200
    slack.user("UA", alice.email)
    slack.user("UC", carol.email)

    both = await _ask(alice, slack, _mention("UA", "kestrel launch runway", channel="C0KESTREL"))
    assert "Launch blue" in both and "Launch red" in both
    assert "Kestrel A" in both and "Kestrel B" in both  # sources name their org

    only_a = await _ask(carol, slack, _mention("UC", "kestrel launch runway", channel="C0KESTREL"))
    assert "Launch blue" in only_a and "Launch red" not in only_a
    assert "Not searched: Kestrel B (you're not a member)" in only_a
    convs = (await carol.get(f"/api/orgs/{a['slug']}/conversations")).json()["items"]
    assert convs[0]["scope_org_ids"] == [a["id"]]

    # An org that turned off cross-org questions isn't searched beside another.
    await alice.patch(f"/api/orgs/{b['slug']}", json={"settings": {"allow_cross_org": False}})
    no_b = await _ask(alice, slack, _mention("UA", "kestrel launch runway", channel="C0KESTREL"))
    assert "Launch red" not in no_b and "doesn't allow cross-org questions" in no_b

    # An unmapped channel searches the default org (the first to connect): A.
    default = await _ask(
        alice, slack, _mention("UA", "kestrel launch runway", channel="C0UNMAPPED")
    )
    assert "Launch blue" in default and "Launch red" not in default
