"""The MCP server, driven in-process by the official MCP client over streamable HTTP."""

import contextlib
import json

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tests.test_agent_nvidia import FakeNvidia, _answer_turn, _tool_turn
from tests.test_api_integration import _general, _ingest, _org, _signup

pytestmark = pytest.mark.integration


@contextlib.asynccontextmanager
async def mcp_session(app, key: str | None):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with app.router.lifespan_context(app):
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test", headers=headers
        )
        async with (
            http,
            streamable_http_client("http://test/api/mcp", http_client=http) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            yield session


def _data(result) -> dict:
    assert not result.is_error, result.content
    return result.structured_content or json.loads(result.content[0].text)


async def _setup(app):
    """An org with a General doc and a restricted Board doc (no grants), and two keys from
    its admin: one with the admin role, one capped at member (which can't see Board)."""
    admin = await _signup(app, "mcpadmin")
    org = await _org(admin, "MCP Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    board = (
        await admin.post(
            f"/api/orgs/{slug}/collections",
            json={"name": "Board", "visibility": "restricted", "allowed_user_ids": []},
        )
    ).json()
    await _ingest(
        admin,
        slug,
        _general(org, cols),
        {"leave.md": "# Leave policy\n\nStaff get sixteen weeks of parental leave."},
    )
    await _ingest(
        admin, slug, board["id"], {"merger.md": "# Merger plan\n\nProject Heron: parental leave."}
    )
    admin_key = (
        await admin.post(f"/api/orgs/{slug}/api-keys", json={"name": "Admin MCP", "role": "admin"})
    ).json()
    member_key = (
        await admin.post(f"/api/orgs/{slug}/api-keys", json={"name": "Narrow", "role": "member"})
    ).json()
    return admin, slug, admin_key, member_key


async def test_requests_without_a_valid_api_key_are_refused(app):
    admin = await _signup(app, "mcpauth")
    await _org(admin, "Auth Co")  # admin now holds a session cookie
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as http:
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            hdrs = {"Accept": "application/json, text/event-stream"}
            assert (await http.post("/api/mcp", json=body, headers=hdrs)).status_code == 401
            bad = {**hdrs, "Authorization": "Bearer strata_sk_not-a-key"}
            assert (await http.post("/api/mcp", json=body, headers=bad)).status_code == 401
            # A browser session is not a credential here (no cookie-borne requests).
            cookie = {**hdrs, "Cookie": "; ".join(f"{k}={v}" for k, v in admin.cookies.items())}
            assert (await http.post("/api/mcp", json=body, headers=cookie)).status_code == 401


async def test_tools_are_listed_with_annotations(app):
    _, _, admin_key, _ = await _setup(app)
    async with mcp_session(app, admin_key["key"]) as s:
        tools = {t.name: t for t in (await s.list_tools()).tools}
    assert set(tools) == {"search", "read_document", "list_collections", "ask"}
    assert tools["search"].annotations.read_only_hint is True
    assert tools["ask"].annotations.read_only_hint is False
    assert "query" in tools["search"].input_schema["properties"]


async def test_a_key_sees_only_what_its_role_sees(app):
    _, _, admin_key, member_key = await _setup(app)

    async with mcp_session(app, admin_key["key"]) as s:
        found = _data(await s.call_tool("search", {"query": "parental leave"}))["results"]
        titles = {r["title"] for r in found}
        assert titles == {"Leave policy", "Merger plan"}
        assert all(r["organization"] == "MCP Co" for r in found)
        board_doc = next(r["document_id"] for r in found if r["title"] == "Merger plan")
        names = {c["name"] for c in _data(await s.call_tool("list_collections", {}))["collections"]}
        assert "Board" in names

    async with mcp_session(app, member_key["key"]) as s:
        found = _data(await s.call_tool("search", {"query": "parental leave"}))["results"]
        assert [r["title"] for r in found] == ["Leave policy"]
        names = {c["name"] for c in _data(await s.call_tool("list_collections", {}))["collections"]}
        assert "Board" not in names
        denied = await s.call_tool("read_document", {"document_id": board_doc})
        assert denied.is_error and "No such document" in denied.content[0].text
        page = _data(await s.call_tool("read_document", {"document_id": found[0]["document_id"]}))
        assert "sixteen weeks" in page["content"] and page["pages"] == 1


async def test_hidden_documents_and_bad_input_are_reported_as_tool_errors(app):
    admin, slug, admin_key, _ = await _setup(app)
    docs = (await admin.get(f"/api/orgs/{slug}/documents")).json()["items"]
    leave = next(d for d in docs if d["title"] == "Leave policy")
    await admin.patch(f"/api/orgs/{slug}/documents/{leave['id']}", json={"hidden": True})
    async with mcp_session(app, admin_key["key"]) as s:
        hidden = await s.call_tool("read_document", {"document_id": leave["id"]})
        assert hidden.is_error and "No such document" in hidden.content[0].text
        bad = await s.call_tool("read_document", {"document_id": "nope"})
        assert bad.is_error and "document_id" in bad.content[0].text
        empty = await s.call_tool("search", {"query": ""})
        assert empty.is_error


async def test_revoked_key_stops_working(app):
    admin, slug, admin_key, _ = await _setup(app)
    async with mcp_session(app, admin_key["key"]) as s:
        assert _data(await s.call_tool("list_collections", {}))["organization"] == "MCP Co"
    assert (await admin.delete(f"/api/orgs/{slug}/api-keys/{admin_key['id']}")).status_code == 204
    with pytest.raises(BaseException):  # noqa: B017 — the client surfaces the 401 as an error
        async with mcp_session(app, admin_key["key"]) as s:
            await s.list_tools()


async def test_ask_runs_the_agent_and_returns_cited_sources(app, monkeypatch):
    from app.agent import engine
    from app.core.config import get_settings

    admin, slug, admin_key, _ = await _setup(app)
    answer = "Staff get sixteen weeks of parental leave [1]."
    fake = FakeNvidia(
        [_tool_turn("search_knowledge", '{"queries": ["parental leave"]}'), _answer_turn(answer)]
    )
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake.stream_chat)
    async with mcp_session(app, admin_key["key"]) as s:
        out = _data(await s.call_tool("ask", {"question": "How much parental leave?"}))
    assert out["answer"] == answer and out["status"] == "complete"
    assert [src["ref"] for src in out["sources"]] == [1]
    assert out["sources"][0]["organization"] == "MCP Co"
    convs = (await admin.get(f"/api/orgs/{slug}/conversations")).json()["items"]
    assert out["conversation_id"] in {c["id"] for c in convs}
