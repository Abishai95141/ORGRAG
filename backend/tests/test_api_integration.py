"""End-to-end API tests against a real Postgres + pgvector."""

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _signup(app, name: str) -> AsyncClient:
    client = await _client(app)
    email = f"{name}-{uuid.uuid4().hex[:6]}@example.com"
    r = await client.post(
        "/api/auth/signup", json={"email": email, "password": "password123", "name": name}
    )
    assert r.status_code == 201, r.text
    client.email = email  # type: ignore[attr-defined]
    return client


async def _org(client: AsyncClient, name: str) -> dict:
    r = await client.post("/api/orgs", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def _join(admin: AsyncClient, member: AsyncClient, slug: str, role: str = "member"):
    r = await admin.post(f"/api/orgs/{slug}/invites", json={"email": member.email, "role": role})
    assert r.status_code == 201, r.text
    r = await member.post(f"/api/invites/{r.json()['token']}/accept")
    assert r.status_code == 200, r.text


async def _ingest(admin: AsyncClient, slug: str, collection_id: str, files: dict[str, str]):
    """Create an upload source, upload files, and run the queued sync job inline."""
    from app.core.db import SessionLocal
    from app.worker.jobs import HANDLERS
    from app.worker.queue import claim_next, finish

    r = await admin.post(
        f"/api/orgs/{slug}/sources",
        json={"kind": "upload", "name": "Docs", "collection_id": collection_id},
    )
    assert r.status_code == 201, r.text
    source = r.json()
    upload = [("files", (name, body.encode(), "text/markdown")) for name, body in files.items()]
    r = await admin.post(f"/api/orgs/{slug}/sources/{source['id']}/files", files=upload)
    assert r.status_code == 200, r.text
    assert r.json()["source"]["status"] == "queued"
    async with SessionLocal() as db:
        while job := await claim_next(db):
            await HANDLERS[job.kind](db, job)
            await finish(db, job)
    r = await admin.get(f"/api/orgs/{slug}/sources/{source['id']}")
    return r.json()


def _general(org: dict, collections: list[dict]) -> str:
    return next(c["id"] for c in collections if c["name"] == "General")


# ── auth ─────────────────────────────────────────────────────────────────────
async def test_signup_login_me_logout(app):
    client = await _signup(app, "alice")
    me = (await client.get("/api/auth/me")).json()
    assert me["user"]["email"] == client.email and me["orgs"] == []

    dup = await client.post(
        "/api/auth/signup", json={"email": client.email, "password": "password123", "name": "x"}
    )
    assert dup.status_code == 409 and dup.json()["code"] == "email_taken"

    assert (await client.post("/api/auth/logout")).status_code == 204
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401

    bad = await client.post("/api/auth/login", json={"email": client.email, "password": "nope"})
    assert bad.status_code == 401 and bad.json()["code"] == "invalid_credentials"
    ok = await client.post(
        "/api/auth/login", json={"email": client.email.upper(), "password": "password123"}
    )
    assert ok.status_code == 200 and "strata_session" in ok.cookies


async def test_validation_error_shape(app):
    client = await _client(app)
    r = await client.post("/api/auth/signup", json={"email": "x@y.com", "password": "short"})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


# ── organizations & isolation ────────────────────────────────────────────────
async def test_create_org_defaults_and_slug_uniqueness(app):
    client = await _signup(app, "owner")
    a = await _org(client, "Acme Rockets")
    b = await _org(client, "Acme Rockets")
    assert a["role"] == "owner" and a["settings"]["model"] == "claude-opus-5-5"
    assert a["slug"].startswith("acme-rockets") and a["slug"] != b["slug"]
    cols = (await client.get(f"/api/orgs/{a['slug']}/collections")).json()
    assert [c["name"] for c in cols] == ["General"]


async def test_non_members_cannot_see_an_org(app):
    alice = await _signup(app, "alice")
    mallory = await _signup(app, "mallory")
    org = await _org(alice, "Private Co")
    for path in ("", "/collections", "/sources", "/members", "/conversations", "/agents"):
        r = await mallory.get(f"/api/orgs/{org['slug']}{path}")
        assert r.status_code == 404, (path, r.status_code)
    r = await mallory.post(f"/api/orgs/{org['slug']}/search", json={"query": "anything"})
    assert r.status_code == 404


async def test_member_role_limits(app):
    admin = await _signup(app, "admin")
    member = await _signup(app, "member")
    org = await _org(admin, "Roles Inc")
    await _join(admin, member, org["slug"])
    r = await member.post(f"/api/orgs/{org['slug']}/collections", json={"name": "Secret"})
    assert r.status_code == 403
    r = await member.patch(f"/api/orgs/{org['slug']}", json={"name": "Hacked"})
    assert r.status_code == 403
    assert (await member.get(f"/api/orgs/{org['slug']}/insights")).status_code == 403
    r = await admin.post(f"/api/orgs/{org['slug']}/collections", json={"name": "Secret"})
    assert r.status_code == 201


async def test_invite_email_must_match(app):
    admin = await _signup(app, "admin")
    other = await _signup(app, "other")
    org = await _org(admin, "Invites Ltd")
    r = await admin.post(
        f"/api/orgs/{org['slug']}/invites", json={"email": "someone@else.com", "role": "member"}
    )
    token = r.json()["token"]
    preview = (await (await _client(app)).get(f"/api/invites/{token}")).json()
    assert preview["org"]["slug"] == org["slug"] and preview["expired"] is False
    assert (await other.post(f"/api/invites/{token}/accept")).status_code == 403


async def test_last_owner_cannot_be_demoted(app):
    owner = await _signup(app, "owner")
    org = await _org(owner, "Solo")
    me = (await owner.get("/api/auth/me")).json()["user"]
    r = await owner.patch(f"/api/orgs/{org['slug']}/members/{me['id']}", json={"role": "admin"})
    assert r.status_code == 409 and r.json()["code"] == "last_owner"


# ── retrieval permissions ────────────────────────────────────────────────────
async def test_restricted_collection_is_invisible_until_granted(app):
    admin = await _signup(app, "admin")
    member = await _signup(app, "member")
    org = await _org(admin, "Restricted Co")
    slug = org["slug"]
    await _join(admin, member, slug)

    r = await admin.post(
        f"/api/orgs/{slug}/collections",
        json={"name": "Board", "visibility": "restricted", "allowed_user_ids": []},
    )
    board = r.json()
    source = await _ingest(
        admin,
        slug,
        board["id"],
        {"board.md": "# Board memo\n\nThe zeppelin acquisition closes in March."},
    )
    assert source["status"] == "ready" and source["document_count"] == 1

    names = [c["name"] for c in (await member.get(f"/api/orgs/{slug}/collections")).json()]
    assert "Board" not in names
    assert (await member.get(f"/api/orgs/{slug}/sources/{source['id']}")).status_code == 404
    hits = await member.post(f"/api/orgs/{slug}/search", json={"query": "zeppelin acquisition"})
    assert hits.json()["results"] == []

    admin_hits = await admin.post(
        f"/api/orgs/{slug}/search", json={"query": "zeppelin acquisition"}
    )
    assert admin_hits.json()["results"][0]["title"] == "Board memo"

    member_id = (await member.get("/api/auth/me")).json()["user"]["id"]
    r = await admin.patch(
        f"/api/orgs/{slug}/collections/{board['id']}", json={"allowed_user_ids": [member_id]}
    )
    assert r.status_code == 200 and r.json()["allowed_user_ids"] == [member_id]
    hits = await member.post(f"/api/orgs/{slug}/search", json={"query": "zeppelin acquisition"})
    assert hits.json()["results"][0]["title"] == "Board memo"


async def test_cross_org_search_only_spans_member_orgs(app):
    alice = await _signup(app, "alice")
    bob = await _signup(app, "bob")
    x = await _org(alice, "Org X")
    y = await _org(alice, "Org Y")
    await _join(alice, bob, x["slug"])  # bob is in X only

    for org, text in (
        (x, "Kestrel launch uses the blue runway."),
        (y, "Kestrel launch uses the red runway."),
    ):
        cols = (await alice.get(f"/api/orgs/{org['slug']}/collections")).json()
        await _ingest(alice, org["slug"], _general(org, cols), {"launch.md": f"# Launch\n\n{text}"})

    body = {"query": "kestrel launch runway", "org_slugs": [x["slug"], y["slug"]]}
    alice_orgs = {
        r["org_slug"]
        for r in (await alice.post(f"/api/orgs/{x['slug']}/search", json=body)).json()["results"]
    }
    bob_orgs = {
        r["org_slug"]
        for r in (await bob.post(f"/api/orgs/{x['slug']}/search", json=body)).json()["results"]
    }
    assert alice_orgs == {x["slug"], y["slug"]}
    assert bob_orgs == {x["slug"]}

    # Org Y opts out of cross-org questions.
    r = await alice.patch(f"/api/orgs/{y['slug']}", json={"settings": {"allow_cross_org": False}})
    assert r.json()["settings"]["allow_cross_org"] is False
    alice_orgs = {
        r["org_slug"]
        for r in (await alice.post(f"/api/orgs/{x['slug']}/search", json=body)).json()["results"]
    }
    assert alice_orgs == {x["slug"]}


# ── ingestion & documents ────────────────────────────────────────────────────
async def test_upload_ingest_documents_and_reindex(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Docs Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    source = await _ingest(
        admin,
        slug,
        _general(org, cols),
        {
            "pto.md": "# PTO policy\n\n## Accrual\n\nStaff accrue twenty days.\n\n## Rollover\n\n"
            "Forty hours roll over."
        },
    )
    assert source["chunk_count"] == 2 and source["last_synced_at"]

    docs = (await admin.get(f"/api/orgs/{slug}/sources/{source['id']}/documents")).json()
    assert docs["total"] == 1 and docs["items"][0]["title"] == "PTO policy"
    detail = (await admin.get(f"/api/orgs/{slug}/documents/{docs['items'][0]['id']}")).json()
    assert "Forty hours" in detail["content"]

    r = await admin.post(
        f"/api/orgs/{slug}/sources/{source['id']}/files",
        files=[("files", ("bad.exe", b"x", "application/octet-stream"))],
    )
    assert r.status_code == 400 and r.json()["code"] == "unsupported_file"


# ── chat ─────────────────────────────────────────────────────────────────────
def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if not ln.startswith(":")]
        if not lines:
            continue
        name = lines[0].removeprefix("event: ")
        events.append((name, json.loads(lines[1].removeprefix("data: "))))
    return events


async def test_chat_streams_retrieval_only_answer_and_persists(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Chat Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    await _ingest(
        admin,
        slug,
        _general(org, cols),
        {"deploys.md": "# Deploy policy\n\nFriday deploys need manager approval."},
    )

    conv = (await admin.post(f"/api/orgs/{slug}/conversations", json={})).json()
    async with admin.stream(
        "POST",
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages",
        json={"content": "Can I deploy on Friday?"},
    ) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join([chunk async for chunk in resp.aiter_text()])
    events = _parse_sse(body)
    names = [e for e, _ in events]
    assert names[0] == "start" and names[-1] == "done"
    assert "step" in names and "sources" in names and "delta" in names

    first_step = next(d for e, d in events if e == "step")
    assert first_step["kind"] == "search" and first_step["status"] == "running"
    sources = next(d for e, d in events if e == "sources")["sources"]
    assert sources[0]["title"] == "Deploy policy" and sources[0]["org_slug"] == slug
    done = events[-1][1]["message"]
    assert done["status"] == "complete" and "[1]" in done["content"]
    assert all(s["status"] == "done" for s in done["trace"])

    detail = (await admin.get(f"/api/orgs/{slug}/conversations/{conv['id']}")).json()
    assert detail["conversation"]["title"] == "Can I deploy on Friday?"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]

    r = await admin.post(
        f"/api/orgs/{slug}/messages/{done['id']}/feedback", json={"feedback": "up"}
    )
    assert r.status_code == 204
    insights = (await admin.get(f"/api/orgs/{slug}/insights?days=7")).json()
    assert insights["totals"]["questions"] == 1 and insights["feedback"]["up"] == 1
    assert insights["top_sources"][0]["citations"] == 1
    assert len(insights["questions_per_day"]) == 7


async def test_other_users_cannot_read_a_conversation(app):
    admin = await _signup(app, "admin")
    member = await _signup(app, "member")
    org = await _org(admin, "Private Chats")
    await _join(admin, member, org["slug"])
    conv = (await admin.post(f"/api/orgs/{org['slug']}/conversations", json={})).json()
    r = await member.get(f"/api/orgs/{org['slug']}/conversations/{conv['id']}")
    assert r.status_code == 404
