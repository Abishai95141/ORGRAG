"""API hygiene regressions (see docs/API_AUDIT.md): API-key binding, cross-user ids,
races that used to 500, input bounds, the error envelope, and OpenAPI quality."""

import asyncio
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from tests.test_api_integration import _client, _general, _ingest, _join, _org, _signup

pytestmark = pytest.mark.integration


async def _key(admin: AsyncClient, app, slug: str, role: str = "viewer") -> AsyncClient:
    r = await admin.post(f"/api/orgs/{slug}/api-keys", json={"name": "bot", "role": role})
    assert r.status_code == 201, r.text
    bot = await _client(app)
    bot.headers["Authorization"] = f"Bearer {r.json()['key']}"
    bot.key_id = r.json()["id"]  # type: ignore[attr-defined]
    return bot


async def _two_orgs_with_docs(app):
    """An owner of A (General + a restricted "Board") and of B, each with a document."""
    admin = await _signup(app, "owner")
    a, b = await _org(admin, "Key Org A"), await _org(admin, "Key Org B")
    cols = (await admin.get(f"/api/orgs/{a['slug']}/collections")).json()
    await _ingest(admin, a["slug"], _general(a, cols), {"a.md": "# Alpha\n\nThe walrus plan."})
    board = (
        await admin.post(
            f"/api/orgs/{a['slug']}/collections",
            json={"name": "Board", "visibility": "restricted"},
        )
    ).json()
    await _ingest(admin, a["slug"], board["id"], {"board.md": "# Board\n\nThe walrus merger."})
    cols_b = (await admin.get(f"/api/orgs/{b['slug']}/collections")).json()
    await _ingest(admin, b["slug"], _general(b, cols_b), {"b.md": "# Beta\n\nThe walrus memo."})
    return admin, a, b, board


# ── API keys ─────────────────────────────────────────────────────────────────
async def test_api_key_search_stays_in_its_org_and_role(app):
    admin, a, b, _ = await _two_orgs_with_docs(app)
    body = {"query": "walrus", "org_slugs": [a["slug"], b["slug"]], "limit": 10}

    titles = {
        r["title"]
        for r in (await admin.post(f"/api/orgs/{a['slug']}/search", json=body)).json()["results"]
    }
    assert titles == {"Alpha", "Board", "Beta"}  # the session sees everything it may

    bot = await _key(admin, app, a["slug"], role="viewer")
    r = await bot.post(f"/api/orgs/{a['slug']}/search", json=body)
    assert r.status_code == 200
    # Org B is out of the key's reach; the restricted collection is out of a viewer's.
    assert {x["title"] for x in r.json()["results"]} == {"Alpha"}

    admin_bot = await _key(admin, app, a["slug"], role="admin")
    r = await admin_bot.post(f"/api/orgs/{a['slug']}/search", json=body)
    assert {x["title"] for x in r.json()["results"]} == {"Alpha", "Board"}


async def test_api_key_chat_is_bound_to_its_org(app, monkeypatch):
    from app.api import chat

    admin, a, b, board = await _two_orgs_with_docs(app)
    seen = []

    async def fake_run(req, queue):
        seen.append(req)
        await queue.put(("done", {"message": {"id": str(req.assistant_message_id)}}))
        await queue.put(None)

    monkeypatch.setattr(chat, "run_chat", fake_run)
    bot = await _key(admin, app, a["slug"], role="viewer")
    url = f"/api/orgs/{a['slug']}/conversations"
    conv = (await bot.post(url, json={"scope_org_slugs": [a["slug"], b["slug"]]})).json()
    assert conv["scope_org_ids"] == [a["id"]]

    # Even a conversation the session widened to B is narrowed when a key posts to it.
    wide = (await admin.post(url, json={"scope_org_slugs": [a["slug"], b["slug"]]})).json()
    assert wide["scope_org_ids"] == [a["id"], b["id"]]
    r = await bot.post(f"{url}/{wide['id']}/messages?stream=false", json={"content": "walrus?"})
    assert r.status_code == 200, r.text
    req = seen[-1]
    assert req.scope_org_ids == [uuid.UUID(a["id"])]
    assert uuid.UUID(board["id"]) not in req.collection_ids  # viewer can't read Board

    r = await admin.post(f"{url}/{wide['id']}/messages?stream=false", json={"content": "walrus?"})
    assert seen[-1].scope_org_ids == [uuid.UUID(a["id"]), uuid.UUID(b["id"])]
    assert seen[-1].collection_ids is None  # sessions are unchanged


async def test_api_key_me_lists_only_its_org(app):
    admin = await _signup(app, "owner")
    a, _ = await _org(admin, "Me Org A"), await _org(admin, "Me Org B")
    assert len((await admin.get("/api/auth/me")).json()["orgs"]) == 2
    bot = await _key(admin, app, a["slug"])
    assert [o["slug"] for o in (await bot.get("/api/auth/me")).json()["orgs"]] == [a["slug"]]


async def test_api_key_cannot_remove_its_own_user(app):
    owner = await _signup(app, "owner")
    org = await _org(owner, "Leave Co")
    member = await _signup(app, "member")
    await _join(owner, member, org["slug"], role="admin")
    me = (await member.get("/api/auth/me")).json()["user"]
    bot = await _key(member, app, org["slug"], role="viewer")
    r = await bot.delete(f"/api/orgs/{org['slug']}/members/{me['id']}")
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert len((await owner.get(f"/api/orgs/{org['slug']}/members")).json()) == 2


async def test_removing_a_member_revokes_their_keys_and_grants(app):
    owner = await _signup(app, "owner")
    org = await _org(owner, "Offboard Co")
    slug = org["slug"]
    member = await _signup(app, "member")
    await _join(owner, member, slug, role="admin")
    member_id = (await member.get("/api/auth/me")).json()["user"]["id"]
    board = (
        await owner.post(
            f"/api/orgs/{slug}/collections",
            json={"name": "Board", "visibility": "restricted", "allowed_user_ids": [member_id]},
        )
    ).json()
    bot = await _key(member, app, slug, role="member")
    assert (await bot.get(f"/api/orgs/{slug}")).status_code == 200

    assert (await owner.delete(f"/api/orgs/{slug}/members/{member_id}")).status_code == 204
    r = await bot.get(f"/api/orgs/{slug}")
    assert r.status_code == 401 and r.json()["code"] == "invalid_api_key"
    assert (await owner.get(f"/api/orgs/{slug}/api-keys")).json() == []
    cols = {c["id"]: c for c in (await owner.get(f"/api/orgs/{slug}/collections")).json()}
    assert cols[board["id"]]["allowed_user_ids"] == []


def test_every_org_route_goes_through_the_org_context():
    """API keys are bound to their org by `org_context`; a route under /api/orgs/{slug}
    that skipped it would let a key act on any org its creator belongs to."""
    from fastapi.routing import iter_route_contexts

    from app.core.deps import org_context
    from app.main import app

    def uses(dependant) -> bool:
        return any(d.call is org_context or uses(d) for d in dependant.dependencies)

    org_routes = [
        r for r in iter_route_contexts(app.routes) if (r.path or "").startswith("/api/orgs/")
    ]
    assert len(org_routes) > 30
    for route in org_routes:
        assert uses(route.dependant), f"{route.methods} {route.path} skips org_context"


# ── cross-user ids ───────────────────────────────────────────────────────────
async def test_document_feedback_message_must_be_the_callers(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Vote Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    await _ingest(admin, slug, _general(org, cols), {"a.md": "# Travel\n\nUse the portal."})
    doc_id = (await admin.get(f"/api/orgs/{slug}/documents")).json()["items"][0]["id"]
    member = await _signup(app, "member")
    await _join(admin, member, slug)

    conv = (await admin.post(f"/api/orgs/{slug}/conversations", json={})).json()
    r = await admin.post(
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
        json={"content": "How do I book travel?"},
    )
    message_id = r.json()["message"]["id"]
    url = f"/api/orgs/{slug}/documents/{doc_id}/feedback"

    r = await member.post(url, json={"vote": "up", "message_id": message_id})
    assert r.status_code == 404 and r.json()["code"] == "not_found"
    r = await admin.post(url, json={"vote": "up", "message_id": message_id})
    assert r.status_code == 200 and r.json()["feedback_up"] == 1


# ── races ────────────────────────────────────────────────────────────────────
async def test_concurrent_writes_conflict_instead_of_failing(app):
    email = f"race-{uuid.uuid4().hex[:6]}@example.com"
    clients = [await _client(app) for _ in range(4)]
    body = {"email": email, "password": "password123", "name": "Race"}
    codes = [
        r.status_code
        for r in await asyncio.gather(*[c.post("/api/auth/signup", json=body) for c in clients])
    ]
    assert sorted(codes) == [201, 409, 409, 409]

    admin = await _signup(app, "admin")
    slug = f"race-{uuid.uuid4().hex[:6]}"
    rs = await asyncio.gather(
        *[admin.post("/api/orgs", json={"name": "Race", "slug": slug}) for _ in range(4)]
    )
    assert sorted(r.status_code for r in rs) == [201, 409, 409, 409]
    assert all(r.json()["code"] == "slug_taken" for r in rs if r.status_code == 409)


async def test_concurrent_votes_keep_one_vote_and_true_totals(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Votes Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    await _ingest(admin, slug, _general(org, cols), {"a.md": "# Doc\n\nText."})
    doc_id = (await admin.get(f"/api/orgs/{slug}/documents")).json()["items"][0]["id"]
    url = f"/api/orgs/{slug}/documents/{doc_id}/feedback"
    rs = await asyncio.gather(*[admin.post(url, json={"vote": "up"}) for _ in range(6)])
    assert {r.status_code for r in rs} == {200}
    doc = (await admin.get(f"/api/orgs/{slug}/documents/{doc_id}")).json()
    assert (doc["feedback_up"], doc["my_vote"]) == (1, "up")


async def test_double_accepting_an_invite_does_not_fail(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Invite Race Co")
    member = await _signup(app, "member")
    token = (
        await admin.post(f"/api/orgs/{org['slug']}/invites", json={"email": member.email})
    ).json()["token"]
    rs = await asyncio.gather(*[member.post(f"/api/invites/{token}/accept") for _ in range(3)])
    assert 200 in {r.status_code for r in rs} and {r.status_code for r in rs} <= {200, 400}


# ── input validation ─────────────────────────────────────────────────────────
async def test_out_of_range_input_is_422_not_500(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Bounds Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    cases = [
        ("post", f"/api/orgs/{slug}/agents", {"name": "A", "glyph": "x" * 40}),
        ("post", f"/api/orgs/{slug}/agents", {"name": "   "}),
        ("post", f"/api/orgs/{slug}/agents", {"name": "A", "instructions": "x" * 20001}),
        ("post", f"/api/orgs/{slug}/collections", {"name": "C", "glyph": "rocket"}),
        (
            "post",
            f"/api/orgs/{slug}/sources",
            {
                "kind": "upload",
                "name": "U",
                "collection_id": cols[0]["id"],
                "sync_interval_minutes": 10**12,
            },
        ),
        (
            "post",
            f"/api/orgs/{slug}/search",
            {"query": "x", "collection_ids": [str(uuid.uuid4())] * 101},
        ),
        ("get", f"/api/orgs/{slug}/documents?offset=99999999999999999999", None),
        ("get", f"/api/orgs/{slug}/conversations?offset=99999999999999999999", None),
        ("post", "/api/auth/login", {"email": admin.email, "password": "x" * 201}),
    ]
    for method, url, body in cases:
        r = await getattr(admin, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 422, (url, body and str(body)[:80], r.text)
        assert r.json()["code"] == "validation_error"

    r = await admin.patch(f"/api/orgs/{slug}", json={"settings": {"model": "no-such-model"}})
    assert r.status_code == 422 and r.json()["code"] == "unknown_model"
    r = await admin.patch(f"/api/orgs/{slug}", json={"settings": {"rerank": False}})
    assert r.status_code == 200  # the current/default model is always accepted


async def test_descriptions_can_be_cleared(app):
    admin = await _signup(app, "admin")
    org = (await admin.post("/api/orgs", json={"name": "Desc Co", "description": "Hi"})).json()
    slug = org["slug"]
    r = await admin.patch(f"/api/orgs/{slug}", json={"description": None})
    assert r.json()["description"] is None
    col = (
        await admin.post(f"/api/orgs/{slug}/collections", json={"name": "C", "description": "x"})
    ).json()
    r = await admin.patch(f"/api/orgs/{slug}/collections/{col['id']}", json={"description": None})
    assert r.json()["description"] is None
    r = await admin.patch(f"/api/orgs/{slug}/collections/{col['id']}", json={"name": "C2"})
    assert r.json()["name"] == "C2"


async def test_upload_batches_are_checked_before_anything_is_stored(app):
    from app.core.config import get_settings

    admin = await _signup(app, "admin")
    org = await _org(admin, "Upload Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    source = (
        await admin.post(
            f"/api/orgs/{slug}/sources",
            json={"kind": "upload", "name": "Docs", "collection_id": cols[0]["id"]},
        )
    ).json()
    url = f"/api/orgs/{slug}/sources/{source['id']}/files"
    folder = Path(get_settings().file_storage_dir) / source["id"]

    files = [("files", ("ok.md", b"# Ok", "text/markdown")), ("files", ("bad.exe", b"MZ", "x"))]
    r = await admin.post(url, files=files)
    assert r.status_code == 400 and r.json()["code"] == "unsupported_file"
    assert not folder.exists() or not any(folder.iterdir())

    many = [("files", (f"f{i}.md", b"# x", "text/markdown")) for i in range(21)]
    r = await admin.post(url, files=many)
    assert r.status_code == 400 and r.json()["code"] == "too_many_files"


async def test_document_search_treats_wildcards_literally(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Like Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    await _ingest(
        admin, slug, _general(org, cols), {"a.md": "# 100% done\n\nA.", "b.md": "# Plain\n\nB."}
    )
    page = (await admin.get(f"/api/orgs/{slug}/documents", params={"q": "%"})).json()
    assert [d["title"] for d in page["items"]] == ["100% done"]
    page = (await admin.get(f"/api/orgs/{slug}/documents", params={"q": "_"})).json()
    assert page["total"] == 0


# ── contract consistency ─────────────────────────────────────────────────────
async def test_conversation_list_is_a_page(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Page Co")
    url = f"/api/orgs/{org['slug']}/conversations"
    for _ in range(3):
        await admin.post(url, json={})
    page = (await admin.get(url, params={"limit": 2})).json()
    assert (page["total"], page["limit"], page["offset"], len(page["items"])) == (3, 2, 0, 2)


async def test_connector_catalogue_needs_auth(app):
    assert (await (await _client(app)).get("/api/connectors")).status_code == 401


async def test_trailing_slashes_route_without_redirects(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Slash Co")
    r = await admin.get(f"/api/orgs/{org['slug']}/agents/")
    assert r.status_code == 200 and r.json() == []
    r = await admin.post("/api/orgs/", json={"name": "Slash Two"})
    assert r.status_code == 201


async def test_error_envelope_for_framework_errors(app):
    admin = await _signup(app, "admin")
    org = await _org(admin, "Envelope Co")
    slug = org["slug"]

    r = await admin.put(f"/api/orgs/{slug}")
    assert r.status_code == 405 and r.json()["code"] == "method_not_allowed"
    assert set(r.headers["allow"].split(", ")) == {"GET", "PATCH", "DELETE"}

    r = await admin.post(
        f"/api/orgs/{slug}/agents", content=b"{nope", headers={"content-type": "application/json"}
    )
    assert r.status_code == 422 and r.json()["detail"] == "The request body is not valid JSON"

    r = await admin.post(
        f"/api/orgs/{slug}/agents", json={"name": "A", "instructions": "x" * 2_000_000}
    )
    assert r.status_code == 413 and r.json()["code"] == "request_too_large"
    assert r.json()["request_id"]

    r = await admin.post(
        f"/api/orgs/{slug}/attachments",
        content=b"garbage",
        headers={"content-type": "multipart/form-data; boundary=zz"},
    )
    assert r.status_code == 400 and r.json()["code"] == "bad_request"

    r = await admin.get(f"/api/orgs/{slug}", headers={"X-Request-ID": "bad id\twith junk"})
    assert r.headers["x-request-id"] != "bad id\twith junk" and len(r.headers["x-request-id"]) == 16


async def test_streamed_bodies_over_the_cap_are_413():
    from starlette.applications import Starlette
    from starlette.exceptions import HTTPException
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from app.core.http import BodyLimit
    from app.main import http_error

    async def echo(request: Request):
        return JSONResponse({"n": len(await request.body())})

    inner = Starlette(routes=[Route("/", echo, methods=["POST"])])
    inner.add_exception_handler(HTTPException, http_error)
    limited = BodyLimit(inner)
    limited.json_cap = 10

    async def chunks():
        for _ in range(5):
            yield b"abcdef"

    client = AsyncClient(transport=ASGITransport(app=limited), base_url="http://t")
    assert (await client.post("/", content=b"12345")).json() == {"n": 5}
    r = await client.post("/", content=chunks())  # no Content-Length: counted as read
    assert r.status_code == 413 and r.json()["code"] == "request_too_large"


async def test_unhandled_and_integrity_errors_keep_the_envelope_and_cors(app, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    from app.api import meta

    client = await _client(app)

    def boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(meta, "get_settings", boom)
    r = await client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 500 and r.json()["code"] == "internal_error"
    assert "kaboom" not in r.text
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert r.headers["x-request-id"] == r.json()["request_id"]

    def dup():
        raise IntegrityError("INSERT …", {}, Exception("duplicate key"))

    monkeypatch.setattr(meta, "get_settings", dup)
    r = await client.get("/api/health")
    assert r.status_code == 409 and r.json()["code"] == "conflict"


def test_openapi_is_usable_by_third_parties():
    from app.main import app

    spec = app.openapi()
    assert "bearer" in spec["components"]["securitySchemes"]
    assert "ErrorOut" in spec["components"]["schemas"]
    ids = []
    for path, ops in spec["paths"].items():
        for method, op in ops.items():
            ids.append(op["operationId"])
            assert "-" in op["operationId"] and " " not in op["operationId"], op["operationId"]
            assert op["responses"]["default"]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorOut"
            }, f"{method} {path}"
    assert len(ids) == len(set(ids))
    assert spec["paths"]["/api/orgs/{slug}/conversations"]["get"]["responses"]["200"]
