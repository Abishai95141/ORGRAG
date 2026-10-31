"""API-layer tests: document curation and feedback, API keys, connector secrets,
pruning, attachments, error envelopes and rate limiting."""

import uuid

import pytest

from tests.test_api_integration import _client, _general, _ingest, _join, _org, _signup

pytestmark = pytest.mark.integration


async def _docs_org(app, files: dict[str, str]):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Platform Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    source = await _ingest(admin, slug, _general(org, cols), files)
    return admin, org, slug, source


# ── documents ────────────────────────────────────────────────────────────────
async def test_hidden_documents_leave_search_and_boost_changes_order(app):
    admin, org, slug, _ = await _docs_org(
        app,
        {
            "a.md": "# Expense policy A\n\nMeals are reimbursed up to forty dollars.",
            "b.md": "# Expense policy B\n\nMeals are reimbursed up to forty dollars per day.",
        },
    )
    page = (await admin.get(f"/api/orgs/{slug}/documents?sort=title&limit=1")).json()
    assert page["total"] == 2 and page["limit"] == 1 and page["offset"] == 0
    assert len(page["items"]) == 1 and page["items"][0]["boost"] == 0

    search = {"query": "meal reimbursement", "limit": 5}
    results = (await admin.post(f"/api/orgs/{slug}/search", json=search)).json()["results"]
    by_title = {r["title"]: r["document_id"] for r in results}
    assert set(by_title) == {"Expense policy A", "Expense policy B"}

    loser = results[-1]["document_id"]
    r = await admin.patch(f"/api/orgs/{slug}/documents/{loser}", json={"boost": 3})
    assert r.status_code == 200 and r.json()["boost"] == 3
    results = (await admin.post(f"/api/orgs/{slug}/search", json=search)).json()["results"]
    assert results[0]["document_id"] == loser

    r = await admin.patch(f"/api/orgs/{slug}/documents/{loser}", json={"hidden": True})
    assert r.json()["hidden"] is True
    results = (await admin.post(f"/api/orgs/{slug}/search", json=search)).json()["results"]
    assert loser not in {r["document_id"] for r in results}
    hidden = (await admin.get(f"/api/orgs/{slug}/documents?hidden=true")).json()
    assert [d["id"] for d in hidden["items"]] == [loser]

    r = await admin.patch(f"/api/orgs/{slug}/documents/{loser}", json={"boost": 9})
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


async def test_document_feedback_is_one_vote_per_person(app):
    admin, org, slug, _ = await _docs_org(app, {"a.md": "# Travel\n\nBook through the portal."})
    member = await _signup(app, "member")
    await _join(admin, member, slug, role="viewer")
    doc_id = (await admin.get(f"/api/orgs/{slug}/documents")).json()["items"][0]["id"]
    url = f"/api/orgs/{slug}/documents/{doc_id}/feedback"

    assert (await admin.post(url, json={"vote": "up"})).json()["feedback_up"] == 1
    assert (await admin.post(url, json={"vote": "up"})).json()["feedback_up"] == 1
    r = (await member.post(url, json={"vote": "down"})).json()
    assert (r["feedback_up"], r["feedback_down"]) == (1, 1)
    r = (await admin.post(url, json={"vote": None})).json()
    assert (r["feedback_up"], r["feedback_down"]) == (0, 1)
    assert (await member.get(f"/api/orgs/{slug}/documents/{doc_id}")).json()["my_vote"] == "down"

    # Viewers can vote but not curate.
    r = await member.patch(f"/api/orgs/{slug}/documents/{doc_id}", json={"boost": 1})
    assert r.status_code == 403 and r.json()["code"] == "forbidden"


# ── api keys ─────────────────────────────────────────────────────────────────
async def test_api_keys_act_inside_their_org_with_a_capped_role(app):
    admin, org, slug, _ = await _docs_org(app, {"a.md": "# Onboarding\n\nWelcome aboard."})
    other = await _org(admin, "Other Co")

    r = await admin.post(f"/api/orgs/{slug}/api-keys", json={"name": "CI", "role": "viewer"})
    assert r.status_code == 201, r.text
    created = r.json()
    raw = created["key"]
    assert raw.startswith("strata_sk_") and created["prefix"] == raw[:16]
    listed = (await admin.get(f"/api/orgs/{slug}/api-keys")).json()
    assert [k["name"] for k in listed] == ["CI"] and "key" not in listed[0]

    bot = await _client(app)
    bot.headers["Authorization"] = f"Bearer {raw}"
    r = await bot.post(f"/api/orgs/{slug}/search", json={"query": "welcome"})
    assert r.status_code == 200 and r.json()["results"]
    assert (await bot.get("/api/auth/me")).status_code == 200
    # Another org the user belongs to is invisible to this key.
    assert (await bot.get(f"/api/orgs/{other['slug']}")).status_code == 404
    # Viewer cap: no admin actions even though the creator is an owner.
    r = await bot.patch(f"/api/orgs/{slug}", json={"name": "Hijacked"})
    assert r.status_code == 403
    # Keys can't manage keys, or reach non-org routes.
    assert (await bot.get(f"/api/orgs/{slug}/api-keys")).status_code == 403
    assert (await bot.post("/api/orgs", json={"name": "X"})).status_code == 403

    assert (await admin.delete(f"/api/orgs/{slug}/api-keys/{created['id']}")).status_code == 204
    r = await bot.get(f"/api/orgs/{slug}")
    assert r.status_code == 401 and r.json()["code"] == "invalid_api_key"

    r = await admin.post(f"/api/orgs/{slug}/api-keys", json={"name": "Root", "role": "owner"})
    assert r.status_code == 422


# ── connectors & sync ────────────────────────────────────────────────────────
async def test_connector_secrets_are_stored_apart_and_never_returned(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Secret Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    catalogue = {c["kind"] for c in (await admin.get("/api/connectors")).json()}
    assert {"gdrive", "slack", "notion"} <= catalogue

    r = await admin.post(
        f"/api/orgs/{slug}/sources",
        json={
            "kind": "slack",
            "name": "Team Slack",
            "collection_id": cols[0]["id"],
            "config": {"token": "xoxb-secret", "channels": ["general"]},
        },
    )
    assert r.status_code == 201, r.text
    source = r.json()
    assert "xoxb" not in r.text and source["config"]["token_set"] is True
    r = await admin.post(
        f"/api/orgs/{slug}/sources",
        json={
            "kind": "gdrive",
            "name": "Drive",
            "collection_id": cols[0]["id"],
            "config": {"service_account_json": '{"client_email": "x"}'},
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["config"]["service_account_json_set"] is True
    assert "client_email" not in r.text

    r = await admin.post(
        f"/api/orgs/{slug}/sources",
        json={"kind": "dropbox", "name": "X", "collection_id": cols[0]["id"]},
    )
    assert r.status_code == 422


async def test_incremental_sync_prunes_with_the_id_listing(app, monkeypatch):
    from app.connectors import registry
    from app.connectors.base import Connector, RawDocument
    from app.core.db import SessionLocal
    from app.ingestion.pipeline import sync_source
    from app.models import Document, Source

    upstream = {"a": "# A\n\nAlpha text.", "b": "# B\n\nBeta text."}

    class FakeConnector(Connector):
        kind = "upload"
        display_name = description = "Fake"
        config_schema = {"type": "object", "properties": {}}
        supports_incremental = True

        async def iter_documents(self, progress, since=None):
            for key in sorted(upstream) if since is None else []:
                yield RawDocument(key, key, upstream[key].encode(), "text/markdown")

        async def list_ids(self):
            return set(upstream)

    admin, org, slug, source = await _docs_org(app, {"seed.md": "# Seed\n\nSeed."})
    monkeypatch.setitem(registry.CONNECTORS, "upload", FakeConnector)
    from sqlalchemy import select

    async with SessionLocal() as db:
        src = await db.get(Source, uuid.UUID(source["id"]))
        stats = await sync_source(db, src, full=True)
        assert stats["created"] == 2 and stats["removed"] == 1  # seed.md is gone upstream
        await db.commit()

        del upstream["b"]
        stats = await sync_source(db, src, full=False)
        assert stats["removed"] == 1
        query = select(Document.external_id).where(Document.source_id == src.id)
        assert (await db.execute(query)).scalars().all() == ["a"]

        upstream.clear()  # an empty listing looks like lost access: keep the index
        assert (await sync_source(db, src, full=False))["removed"] == 0


async def test_full_sync_keeps_documents_it_could_not_fetch(app, monkeypatch):
    """Audit I1: transient failures and partial listings must not delete documents."""
    from sqlalchemy import select

    from app.connectors import registry
    from app.connectors.base import Connector, RawDocument
    from app.core.db import SessionLocal
    from app.ingestion.pipeline import sync_source
    from app.models import Document, Source

    upstream = {f"doc{i}": f"# Doc {i}\n\nText {i}." for i in range(20)}
    behaviour = {"skip": set(), "complete": True}

    class FlakyConnector(Connector):
        kind = "upload"
        display_name = description = "Flaky"
        config_schema = {"type": "object", "properties": {}}

        async def iter_documents(self, progress, since=None):
            self.complete = behaviour["complete"]
            for key in sorted(upstream):
                if key in behaviour["skip"]:
                    self.skip(key)
                    continue
                yield RawDocument(key, key, upstream[key].encode(), "text/markdown")

    admin, org, slug, source = await _docs_org(app, {"seed.md": "# Seed\n\nSeed."})
    monkeypatch.setitem(registry.CONNECTORS, "upload", FlakyConnector)

    async with SessionLocal() as db:
        src = await db.get(Source, uuid.UUID(source["id"]))

        async def ids() -> set[str]:
            q = select(Document.external_id).where(Document.source_id == src.id)
            return set((await db.execute(q)).scalars())

        assert (await sync_source(db, src))["created"] == 20
        await db.commit()
        assert len(await ids()) == 20

        behaviour["skip"] = {"doc3"}  # rate-limited this run; still exists upstream
        stats = await sync_source(db, src)
        assert stats["skipped"] == 1 and stats["removed"] == 0 and "doc3" in await ids()

        behaviour["skip"] = set()
        behaviour["complete"] = False  # e.g. GitHub truncated tree / crawler page cap
        del upstream["doc5"]
        assert (await sync_source(db, src))["removed"] == 0 and "doc5" in await ids()

        behaviour["complete"] = True
        behaviour["skip"] = {f"doc{i}" for i in range(6, 9)}  # 3 of 19 failing: too many
        assert (await sync_source(db, src))["removed"] == 0 and "doc5" in await ids()

        behaviour["skip"] = set()
        stats = await sync_source(db, src)  # a clean run prunes the real deletion
        assert stats["removed"] == 1 and "doc5" not in await ids()


# ── attachments ──────────────────────────────────────────────────────────────
async def test_attachments_validate_and_bind_to_one_conversation(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Files Co")
    slug = org["slug"]
    r = await admin.post(
        f"/api/orgs/{slug}/attachments",
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 400 and r.json()["code"] == "unsupported_file"
    r = await admin.post(
        f"/api/orgs/{slug}/attachments",
        files={"file": ("notes.md", b"# Notes\n\nHi.", "text/markdown")},
    )
    assert r.status_code == 201
    att = r.json()
    assert att["conversation_id"] is None and att["token_count"] > 0

    c1 = (await admin.post(f"/api/orgs/{slug}/conversations", json={})).json()
    c2 = (await admin.post(f"/api/orgs/{slug}/conversations", json={})).json()
    send = {"content": "Summarize", "attachment_ids": [att["id"]]}
    r = await admin.post(
        f"/api/orgs/{slug}/conversations/{c1['id']}/messages?stream=false", json=send
    )
    assert r.status_code == 200, r.text
    r = await admin.post(
        f"/api/orgs/{slug}/conversations/{c2['id']}/messages?stream=false", json=send
    )
    assert r.status_code == 400 and r.json()["code"] == "attachment_in_use"

    stranger = await _signup(app, "stranger")
    await _join(admin, stranger, slug)
    assert (await stranger.get(f"/api/orgs/{slug}/attachments/{att['id']}")).status_code == 404


# ── consistency ──────────────────────────────────────────────────────────────
async def test_errors_share_one_envelope_with_request_ids(app):
    client = await _client(app)
    r = await client.get("/api/orgs", headers={"X-Request-ID": "trace-123"})
    assert r.status_code == 401
    assert r.json() == {
        "detail": "Sign in to continue",
        "code": "unauthenticated",
        "request_id": "trace-123",
    }
    assert r.headers["x-request-id"] == "trace-123"

    admin = await _signup(app, "admin")
    r = await admin.get(f"/api/orgs/nope-{uuid.uuid4().hex[:6]}")
    assert r.status_code == 404 and set(r.json()) == {"detail", "code", "request_id"}

    health = (await client.get("/api/health")).json()
    assert {"llm_providers", "default_model", "rerank_provider", "web_search_provider"} <= set(
        health
    )
    models = (await admin.get("/api/models")).json()
    assert {m["provider"] for m in models} == {"anthropic", "nvidia", "explabs"}


async def test_login_is_rate_limited(app):
    from app.api.auth import login_limiter

    user = await _signup(app, "victim")
    client = await _client(app)
    body = {"email": user.email, "password": "wrong-password"}
    codes = [(await client.post("/api/auth/login", json=body)).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    r = await client.post("/api/auth/login", json={**body, "password": "password123"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    login_limiter._hits.clear()


def test_every_route_documents_its_response():
    from app.main import app

    spec = app.openapi()
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            assert op.get("tags"), f"{method} {path} has no tag"
            # Browser flows (the Slack install and OAuth callback) succeed with a redirect.
            success = {"200", "201", "202", "204", "303", "307"}
            assert set(op["responses"]) & success, f"{method} {path}"
