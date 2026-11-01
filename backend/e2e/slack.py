"""The Slack bot end to end on the scenario's orgs (e2e/README.md): the real app,
database, agent and models, in-process, with only Slack's Web API simulated (no real
workspace to install into). Run it after scenario.py with the worker stopped: it runs
the queued jobs itself, as the worker would.

usage: DATABASE_URL=… python -m e2e.slack RUN_DIR
"""

import asyncio
import json
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

RUN_DIR = Path(sys.argv[1])
STATE = json.loads((RUN_DIR / "state.json").read_text())
USERS = json.loads((RUN_DIR / "users.json").read_text())
SECRET = "e2e-signing-secret"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": str(detail)[:600]})
    print(
        ("PASS " if ok else "FAIL ")
        + f"[slack] {name}"
        + ("" if ok else f" :: {str(detail)[:400]}"),
        flush=True,
    )
    return ok


class FakeSlack:
    def __init__(self, team):
        self.team = team
        self.calls = []
        self.users = {}
        self.shared = {}
        self.channels = [
            {"id": "C0GENERAL", "name": "general", "is_private": False},
            {"id": "C0PLATFORM", "name": "platform", "is_private": False},
        ]
        self._ts = 0

    def user(self, uid, email, name):
        self.users[uid] = {
            "id": uid,
            "name": name,
            "team_id": self.team[0],
            "is_email_confirmed": True,
            "profile": {"display_name": name, "email": email},
        }

    def __call__(self, request):
        method = request.url.path.removeprefix("/api/")
        params = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((method, params))
        if method == "oauth.v2.access":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "access_token": "xoxb-e2e",
                    "token_type": "bot",
                    "bot_user_id": "UBOT",
                    "app_id": "A1",
                    "scope": "app_mentions:read,chat:write",
                    "team": {"id": self.team[0], "name": self.team[1]},
                    "is_enterprise_install": False,
                },
            )
        if method == "users.info":
            u = self.users.get(params["user"])
            return httpx.Response(
                200, json={"ok": True, "user": u} if u else {"ok": False, "error": "user_not_found"}
            )
        if method == "chat.postMessage":
            self._ts += 1
            return httpx.Response(200, json={"ok": True, "ts": f"1800000000.{self._ts:06d}"})
        if method == "conversations.info":
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "channel": {"id": params["channel"], **self.shared.get(params["channel"], {})},
                },
            )
        if method == "conversations.open":
            return httpx.Response(200, json={"ok": True, "channel": {"id": "DNEW"}})
        if method == "users.conversations":
            return httpx.Response(200, json={"ok": True, "channels": self.channels})
        return httpx.Response(200, json={"ok": True})

    def of(self, method):
        return [p for m, p in self.calls if m == method]

    def posted(self):
        return [
            p
            for m, p in self.calls
            if m in ("chat.postMessage", "chat.update", "chat.postEphemeral")
        ]


async def main():
    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.main import app
    from app.slackbot import client as slack_client
    from app.slackbot import signing
    from app.worker.jobs import HANDLERS
    from app.worker.queue import claim_next, finish

    s = get_settings()
    s.slack_client_id, s.slack_client_secret, s.slack_signing_secret = (
        "1234.5678",
        "client-secret",
        SECRET,
    )
    fake = FakeSlack((f"T{uuid.uuid4().hex[:9].upper()}", "Warehouse Ops"))
    slack_client._transport = httpx.MockTransport(fake)
    nw, hx = STATE["nw"], STATE["hx"]
    transport = httpx.ASGITransport(app=app)

    async def login(key):
        c = httpx.AsyncClient(transport=transport, base_url="http://localhost:8000", timeout=600)
        r = await c.post(
            "/api/auth/login", json={"email": USERS[key]["email"], "password": USERS["password"]}
        )
        assert r.status_code == 200, r.text
        return c

    priya, marcus, lena = await login("priya"), await login("marcus"), await login("lena")
    anon = httpx.AsyncClient(transport=transport, base_url="http://localhost:8000")

    async def send(payload, secret=SECRET, retry=None):
        body = json.dumps(payload).encode()
        ts = str(int(time.time()))
        h = {
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": ts,
            "X-Slack-Signature": signing.signature(secret, ts, body),
        }
        if retry:
            h["X-Slack-Retry-Num"] = str(retry)
        return await anon.post("/api/slack/events", content=body, headers=h)

    def event(ev, event_id=None):
        return {
            "type": "event_callback",
            "team_id": fake.team[0],
            "api_app_id": "A1",
            "event": ev,
            "event_id": event_id or f"Ev{uuid.uuid4().hex[:12].upper()}",
            "event_time": int(time.time()),
        }

    def mention(user, text, channel="C0GENERAL", ts=None, **kw):
        return {
            "type": "app_mention",
            "user": user,
            "text": f"<@UBOT> {text}",
            "channel": channel,
            "ts": ts or f"{time.time():.6f}",
            **kw,
        }

    def dm(user, text, ts=None):
        return {
            "type": "message",
            "channel_type": "im",
            "channel": "D0LENA",
            "user": user,
            "text": text,
            "ts": ts or f"{time.time():.6f}",
        }

    async def run_jobs():
        async with SessionLocal() as db:
            while job := await claim_next(db):
                await HANDLERS[job.kind](db, job)
                await finish(db, job)

    async def ask(ev):
        before = len(fake.posted())
        r = await send(event(ev))
        check(f"event accepted ({ev['type']})", r.status_code == 200, r.text)
        t0 = time.monotonic()
        await run_jobs()
        replies = [
            p.get("text", "")
            for p in fake.posted()[before:]
            if not p.get("text", "").startswith(":hourglass")
        ]
        print(f"   ({time.monotonic() - t0:.1f}s) reply: {' / '.join(replies)[:900]}", flush=True)
        return "\n".join(replies)

    # Install, as Priya (an owner of Northwind).
    r = await priya.get(f"/api/orgs/{nw}/slack")
    check(
        "overview: configured",
        r.status_code == 200
        and r.json()["configured"]
        and r.json()["events_url"].endswith("/api/slack/events"),
        r.text,
    )
    r = await priya.get(f"/api/orgs/{nw}/slack/install", follow_redirects=False)
    check(
        "install redirects to Slack",
        r.status_code == 307 and "slack.com/oauth/v2/authorize" in r.headers["location"],
        r.headers,
    )
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = await marcus.get(
        "/api/slack/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    check(
        "another admin can't finish Priya's install",
        r.status_code == 303 and "slack_error=wrong_user" in r.headers["location"],
        r.headers.get("location"),
    )
    r = await priya.get(
        "/api/slack/oauth/callback", params={"code": "c", "state": "forged"}, follow_redirects=False
    )
    check("forged state refused", r.status_code == 400, r.status_code)
    r = await priya.get(
        "/api/slack/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    check(
        "install completes",
        r.status_code == 303 and "connected=" in r.headers["location"],
        r.headers.get("location"),
    )
    ws = next(
        w
        for w in (await priya.get(f"/api/orgs/{nw}/slack")).json()["workspaces"]
        if w["team_id"] == fake.team[0]
    )
    check("workspace default org is Northwind", ws["default_org"]["slug"] == nw, ws)
    check("bot token never returned", "xoxb" not in json.dumps(ws), "")

    r = await send({"type": "url_verification", "challenge": "abc123"})
    check("url_verification", r.json().get("challenge") == "abc123", r.text)
    r = await send(event(dm("U0LENA", "hi")), secret="wrong-secret")
    check("bad signature refused", r.status_code == 401, r.status_code)

    # Lena isn't linked: she gets a private link, nothing is posted publicly.
    fake.user("U0LENA", USERS["lena"]["email"], "lena")
    before = len(fake.calls)
    await ask(
        mention("U0LENA", "What's the per diem for international travel?", ts="1800000001.000100")
    )
    prompts = fake.of("chat.postEphemeral")
    check(
        "unlinked user gets an ephemeral link", prompts and prompts[-1]["user"] == "U0LENA", prompts
    )
    check(
        "nothing public before linking",
        not [p for m, p in fake.calls[before:] if m == "chat.postMessage"],
        fake.calls[before:],
    )
    token = re.search(r"/slack/link/([\w.-]+)\|", prompts[-1]["text"]).group(1)
    preview = (await lena.get(f"/api/slack/link/{token}")).json()
    check(
        "link preview",
        preview["slack_email"] == USERS["lena"]["email"]
        and preview["linked"] == "none"
        and preview["eligible"],
        preview,
    )
    r = await lena.post(f"/api/slack/link/{token}")
    check("Lena links her account", r.status_code == 201, r.text)
    r = await marcus.post(f"/api/slack/link/{token}")
    check("used link refused for someone else", r.status_code == 409, r.text)
    links = (await lena.get("/api/slack/links")).json()
    check("Lena's links", len(links) == 1 and links[0]["slack_user_id"] == "U0LENA", links)

    # DM: every connected org she belongs to, restricted collections included.
    reply = await ask(
        dm(
            "U0LENA",
            "What's the per diem for international travel, and when do I have to submit receipts?",
        )
    )
    check("DM answer", "110" in reply and "30 days" in reply and "/a/" in reply, reply[:500])
    reply = await ask(
        dm("U0LENA", "Which vendors have a SOC 2 Type II report expiring before March 31, 2027?")
    )
    check(
        "DM can use restricted collections Lena reads",
        "Datadog" in reply and "Vanta" in reply,
        reply[:600],
    )

    # A channel: only org-wide collections, whoever asks.
    root = "1800000002.000100"
    reply = await ask(
        mention(
            "U0LENA",
            "Which vendors have a SOC 2 Type II report expiring before March 31, 2027?",
            ts=root,
        )
    )
    check(
        "channel answer leaves out restricted vendor data",
        not any(v in reply for v in ("Twilio", "HubSpot", "Snowflake", "2026-11-30", "2027-01-20")),
        reply[:600],
    )
    check(
        "channel footer says restricted collections are DM-only",
        "Restricted collections are only searched in DMs with me" in reply,
        reply[-300:],
    )
    reply = await ask(
        mention(
            "U0LENA",
            "Then who in Engineering owns the on-call escalation for SEV1s, and how do I reach them?",
            thread_ts=root,
        )
    )
    check("thread follow-up answered", "Kenji Watanabe" in reply, reply[:500])

    # A channel shared with another company gets nothing.
    fake.shared["C0PARTNER"] = {"is_ext_shared": True}
    before = len(fake.calls)
    await ask(mention("U0LENA", "What's the per diem?", channel="C0PARTNER"))
    notes = [p for m, p in fake.calls[before:] if m == "chat.postEphemeral"]
    check(
        "shared channel refused privately",
        notes and "shared with other organizations" in notes[-1]["text"],
        notes,
    )
    check(
        "nothing posted in the shared channel",
        not [p for m, p in fake.calls[before:] if m == "chat.postMessage"],
        "",
    )

    # Retries are acknowledged once.
    ev = event(
        dm("U0LENA", "What's the hotel cap in London?"),
        event_id=f"EvRETRY{uuid.uuid4().hex[:8].upper()}",
    )
    r1 = await send(ev)
    r2 = await send(ev, retry=1)
    from sqlalchemy import select

    from app.models import Job

    async with SessionLocal() as db:
        queued = list(
            (
                await db.execute(
                    select(Job).where(Job.kind == "slack_answer", Job.status == "queued")
                )
            ).scalars()
        )
    check(
        "retry acked without a second job",
        r1.status_code == r2.status_code == 200 and len(queued) == 1,
        len(queued),
    )
    await run_jobs()

    # Channel scope mapping, from the admin side.
    bots = await priya.get(f"/api/orgs/{nw}/slack/workspaces/{ws['id']}/bot-channels")
    check(
        "bot channels",
        bots.status_code == 200 and {c["id"] for c in bots.json()} == {"C0GENERAL", "C0PLATFORM"},
        bots.text,
    )
    r = await priya.put(
        f"/api/orgs/{nw}/slack/workspaces/{ws['id']}/channels/C0PLATFORM",
        json={"org_slugs": [nw], "channel_name": "platform"},
    )
    check(
        "map #platform",
        r.status_code == 200 and [o["slug"] for o in r.json()["orgs"]] == [nw],
        r.text,
    )
    r = await priya.put(
        f"/api/orgs/{nw}/slack/workspaces/{ws['id']}/channels/C0PLATFORM", json={"org_slugs": [hx]}
    )
    check("Helix isn't connected: refused", r.status_code == 400, r.text)
    r = await lena.put(
        f"/api/orgs/{nw}/slack/workspaces/{ws['id']}/channels/C0PLATFORM", json={"org_slugs": [nw]}
    )
    check("member can't map", r.status_code == 403, r.status_code)
    chans = (await priya.get(f"/api/orgs/{nw}/slack/workspaces/{ws['id']}/channels")).json()
    check("channels list", [c["channel_id"] for c in chans] == ["C0PLATFORM"], chans)
    r = await priya.delete(f"/api/orgs/{nw}/slack/workspaces/{ws['id']}/channels/C0PLATFORM")
    check("unmap", r.status_code == 204, r.status_code)

    # A second org in the same workspace (Helix's owner connects it). A channel's scope is
    # a list of orgs: adding one takes an admin of it, narrowing takes an admin of an org
    # already in it, and orgs the caller isn't in are kept. Each org's log records only
    # its own start or stop, never the other org.
    omar = await login("omar")
    r = await omar.get(f"/api/orgs/{hx}/slack/install", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = await omar.get(
        "/api/slack/oauth/callback", params={"code": "c", "state": state}, follow_redirects=False
    )
    check(
        "Helix connects the same workspace",
        r.status_code == 303 and "connected=" in r.headers["location"],
        r.headers.get("location"),
    )
    hws = next(
        w
        for w in (await omar.get(f"/api/orgs/{hx}/slack")).json()["workspaces"]
        if w["team_id"] == fake.team[0]
    )
    check(
        "Helix sees Northwind only as a count",
        hws["other_org_count"] == 1
        and [o["slug"] for o in hws["orgs"]] == [hx]
        and hws["default_org"] is None,
        hws,
    )
    chan = f"/slack/workspaces/{hws['id']}/channels/C0PLATFORM"
    r = await omar.put(
        f"/api/orgs/{hx}{chan}", json={"org_slugs": [hx], "channel_name": "platform"}
    )
    check(
        "Helix adds itself to #platform; Northwind stays (Omar isn't in it)",
        r.status_code == 200
        and [o["slug"] for o in r.json()["orgs"]] == [hx]
        and r.json()["other_org_count"] == 1,
        r.text,
    )
    reply = await ask(
        mention("U0LENA", "What's the per diem for international travel?", channel="C0PLATFORM")
    )
    check("Lena still gets Northwind's answer in #platform", "110" in reply, reply[:300])
    nw_chan = f"/slack/workspaces/{ws['id']}/channels/C0PLATFORM"
    r = await priya.put(f"/api/orgs/{nw}{nw_chan}", json={"org_slugs": [hx]})
    check(
        "Priya (Northwind admin, Helix member) takes Northwind out",
        r.status_code == 200 and [o["slug"] for o in r.json()["orgs"]] == [hx],
        r.text,
    )
    reply = await ask(
        mention("U0LENA", "What's the per diem for international travel?", channel="C0PLATFORM")
    )
    check("#platform no longer answers Lena from Northwind", "110" not in reply, reply[:300])
    r = await priya.delete(f"/api/orgs/{nw}{nw_chan}")
    check(
        "resetting needs an admin of an org in the scope (Helix only)",
        r.status_code == 403,
        r.status_code,
    )
    r = await priya.put(f"/api/orgs/{nw}{nw_chan}", json={"org_slugs": [nw, hx]})
    check(
        "Priya adds Northwind back",
        r.status_code == 200 and [o["slug"] for o in r.json()["orgs"]] == [nw, hx],
        r.text,
    )
    r = await priya.delete(f"/api/orgs/{nw}{nw_chan}")
    check("then resets #platform to the default org", r.status_code == 204, r.status_code)
    hx_events = (await omar.get(f"/api/orgs/{hx}/audit/events", params={"action": "slack"})).json()[
        "items"
    ]
    check(
        "Helix log: connected, channel added and removed",
        {"slack.connected", "slack.channel_added", "slack.channel_removed"}
        <= {e["action"] for e in hx_events},
        [e["action"] for e in hx_events],
    )
    check(
        "Helix log never names Northwind",
        "Northwind Robotics" not in json.dumps(hx_events) and nw not in json.dumps(hx_events),
        json.dumps(hx_events)[:400],
    )
    r = await omar.delete(f"/api/orgs/{hx}/slack/workspaces/{hws['id']}")
    check(
        "Helix disconnects; Northwind keeps the workspace",
        r.status_code == 204 and not fake.of("auth.revoke"),
        fake.of("auth.revoke"),
    )

    # Audit trail: Slack answers are reads on the slack channel; connection events.
    reads = (await priya.get(f"/api/orgs/{nw}/audit/access", params={"channel": "slack"})).json()[
        "items"
    ]
    check(
        "Slack answers in the access log",
        reads
        and {e["actor"]["email"] for e in reads} == {USERS["lena"]["email"]}
        and all(e["kind"] == "chat" for e in reads),
        [(e["kind"], e["query"]) for e in reads],
    )
    acts = [
        e["action"]
        for e in (
            await priya.get(f"/api/orgs/{nw}/audit/events", params={"action": "slack"})
        ).json()["items"]
    ]
    check(
        "slack events audited",
        {"slack.connected", "slack.channel_removed", "slack.channel_added"} <= set(acts),
        acts,
    )
    nw_events = (await priya.get(f"/api/orgs/{nw}/audit/events", params={"action": "slack"})).json()
    check(
        "Northwind log never names Helix",
        "Helix" not in json.dumps(nw_events) and hx not in json.dumps(nw_events),
        json.dumps(nw_events)[:400],
    )
    convs = (await lena.get(f"/api/orgs/{nw}/conversations")).json()["items"]
    check("Slack threads are in Lena's chat history", len(convs) >= 3, len(convs))

    # Unlink and disconnect.
    r = await lena.delete(f"/api/slack/links/{links[0]['id']}")
    check("unlink", r.status_code == 204, r.status_code)
    r = await priya.delete(f"/api/orgs/{nw}/slack/workspaces/{ws['id']}")
    check("disconnect", r.status_code == 204, r.status_code)
    check("token revoked", fake.of("auth.revoke"), fake.calls[-3:])
    r = await priya.get(f"/api/orgs/{nw}/slack")
    check("no workspaces left", r.json()["workspaces"] == [], r.json())
    acts = [
        e["action"]
        for e in (
            await priya.get(f"/api/orgs/{nw}/audit/events", params={"action": "slack"})
        ).json()["items"]
    ]
    check("disconnect audited", "slack.disconnected" in acts, acts)

    fails = [r for r in RESULTS if not r["ok"]]
    (RUN_DIR / "slack_results.json").write_text(json.dumps(RESULTS, indent=1))
    print(f"\nslack: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed")
    for f in fails:
        print("FAIL", f["name"], "|", f["detail"][:400])


asyncio.run(main())
