import json
from datetime import UTC, datetime

import httpx
import pytest

from app.connectors import notion
from app.connectors.base import ConnectorError
from app.connectors.notion import NotionConnector


def nid(char: str) -> str:
    return f"{char * 8}-{char * 4}-{char * 4}-{char * 4}-{char * 12}"


A, B, D, R, X, Z = nid("a"), nid("b"), nid("d"), nid("e"), nid("f"), nid("c")


def rt(text, href=None, **annotations):
    return {"plain_text": text, "annotations": annotations, "href": href}


def page(pid, title, edited, parent, **extra):
    props = extra.pop("properties", {})
    return {
        "object": "page",
        "id": pid,
        "url": f"https://www.notion.so/{title.replace(' ', '-')}-{pid.replace('-', '')}",
        "last_edited_time": edited,
        "created_time": "2024-01-01T00:00:00.000Z",
        "parent": parent,
        "archived": False,
        "properties": {"Name": {"type": "title", "title": [rt(title)]}, **props},
        **extra,
    }


def block(bid, kind, text="", children=False, **data):
    payload = {"rich_text": [rt(text)] if text else [], **data}
    return {"object": "block", "id": bid, "type": kind, "has_children": children, kind: payload}


ROW_PROPS = {
    "Status": {"type": "status", "status": {"name": "Done"}},
    "Tags": {"type": "multi_select", "multi_select": [{"name": "auth"}, {"name": "web"}]},
    "Due": {"type": "date", "date": {"start": "2024-05-10", "end": None}},
    "Shipped": {"type": "checkbox", "checkbox": True},
    "Points": {"type": "number", "number": 3},
    "Links": {"type": "relation", "relation": [{"id": A}]},
    "Notes": {"type": "rich_text", "rich_text": []},
}
OBJECTS = {
    X: page(X, "Other", "2024-05-04T08:00:00.000Z", {"type": "workspace", "workspace": True}),
    A: page(A, "Handbook", "2024-05-03T10:00:00.000Z", {"type": "workspace", "workspace": True}),
    R: page(
        R,
        "Fix login",
        "2024-05-02T12:00:00.000Z",
        {"type": "database_id", "database_id": D},
        properties=ROW_PROPS,
    ),
    B: page(B, "Onboarding", "2024-05-01T09:00:00.000Z", {"type": "page_id", "page_id": A}),
    D: {
        "object": "database",
        "id": D,
        "last_edited_time": "2024-04-01T00:00:00.000Z",
        "parent": {"type": "page_id", "page_id": A},
    },
    Z: {**page(Z, "Old", "2024-03-01T00:00:00.000Z", {"type": "workspace"}), "archived": True},
}
BLOCKS = {
    A: [
        block("h1", "heading_1", "Intro"),
        {
            **block("p1", "paragraph"),
            "paragraph": {"rich_text": [rt("Hello "), rt("world", bold=True), rt(" and "),
                                        rt("docs", href="/abc")]},
        },
        block("l1", "bulleted_list_item", "one"),
        block("l2", "bulleted_list_item", "two", children=True),
        block("n1", "numbered_list_item", "first"),
        block("t1", "to_do", "done", checked=True),
        block("tg", "toggle", "More", children=True),
        block("q1", "quote", "Be kind"),
        block("c1", "callout", "Heads up", icon={"type": "emoji", "emoji": "💡"}),
        {**block("cd", "code", "print('hi')", language="python")},
        block("dv", "divider"),
        block("tb", "table", children=True, table_width=2, has_column_header=True),
        {**block(B, "child_page"), "child_page": {"title": "Onboarding"}},
        {**block(D, "child_database"), "child_database": {"title": "Tasks"}},
    ],
    "l2": [block("l2a", "bulleted_list_item", "nested")],
    "tg": [block("tgp", "paragraph", "hidden")],
    "tb": [
        {"type": "table_row", "table_row": {"cells": [[rt("Plan")], [rt("Price")]]}},
        {"type": "table_row", "table_row": {"cells": [[rt("Growth")], [rt("1|2")]]}},
    ],
    B: [block("bp", "paragraph", "Welcome aboard")],
    R: [block("rp", "paragraph", "Steps")],
}  # fmt: skip


class FakeNotion:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.search_bodies: list[dict] = []
        self.rate_limit_once = True
        self.status: int | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret_test"
        assert request.headers["Notion-Version"] == "2022-06-28"
        path = request.url.path.removeprefix("/v1")
        self.calls.append((request.method, path))
        if self.status:
            return httpx.Response(self.status, json={"code": "unauthorized", "message": "nope"})
        if path == "/search":
            if self.rate_limit_once:
                self.rate_limit_once = False
                return httpx.Response(429, headers={"Retry-After": "0"})
            return self._search(json.loads(request.content))
        kind, oid, *rest = path.strip("/").split("/")
        if rest == ["children"]:
            return self._children(oid, request.url.params.get("start_cursor"))
        obj = OBJECTS.get(oid)
        if obj is None or kind[:-1] != obj["object"]:
            return httpx.Response(404, json={"code": "object_not_found", "message": "Not found"})
        return httpx.Response(200, json=obj)

    def _search(self, body):
        self.search_bodies.append(body)
        objs = sorted(OBJECTS.values(), key=lambda o: o["last_edited_time"], reverse=True)
        if body.get("filter") == {"property": "object", "value": "page"}:
            objs = [o for o in objs if o["object"] == "page"]
        objs = [o for o in objs if not o.get("archived")]
        start = int(body.get("start_cursor") or 0)
        more = start + 2 < len(objs)
        return httpx.Response(
            200,
            json={
                "results": objs[start : start + 2],
                "has_more": more,
                "next_cursor": str(start + 2) if more else None,
            },
        )

    def _children(self, oid, cursor):
        items = BLOCKS.get(oid, [])
        start = int(cursor or 0)
        size = 10
        more = start + size < len(items)
        return httpx.Response(
            200,
            json={
                "results": items[start : start + size],
                "has_more": more,
                "next_cursor": str(start + size) if more else None,
            },
        )


@pytest.fixture
def fake(monkeypatch):
    api = FakeNotion()
    monkeypatch.setattr(notion, "_transport", httpx.MockTransport(api))
    return api


async def _noop(*_):
    return None


async def _collect(conn, since=None):
    return [d async for d in conn.iter_documents(_noop, since)]


def _conn(**config):
    return NotionConnector(config, {"token": "secret_test"})


def test_validate_config():
    out = NotionConnector.validate_config(
        {
            "root_page_ids": [
                "https://www.notion.so/acme/Team-Handbook-0123456789ABCDEF0123456789abcdef?pvs=4",
                "01234567-89ab-cdef-0123-456789abcdef",
                " ",
            ]
        }
    )
    assert out == {"root_page_ids": ["01234567-89ab-cdef-0123-456789abcdef"]}
    assert NotionConnector.validate_config({}) == {"root_page_ids": []}
    with pytest.raises(ValueError, match="Notion page"):
        NotionConnector.validate_config({"root_page_ids": ["handbook"]})
    assert NotionConnector.secret_fields() == ["token"]


async def test_full_sync_renders_markdown(fake):
    docs = await _collect(_conn())
    by_id = {d.external_id: d for d in docs}
    assert list(by_id) == [X, A, R, B]  # most recently edited first, archived skipped

    handbook = by_id[A]
    assert handbook.title == "Handbook"
    assert handbook.mime == "text/markdown"
    assert handbook.url == f"https://www.notion.so/Handbook-{'a' * 32}"
    assert handbook.updated_at == datetime(2024, 5, 3, 10, tzinfo=UTC)
    assert handbook.metadata["parent_type"] == "workspace"
    assert handbook.data.decode() == (
        "# Handbook\n\n"
        "## Intro\n\n"
        "Hello **world** and [docs](https://www.notion.so/abc)\n\n"
        "- one\n"
        "- two\n"
        "  - nested\n"
        "1. first\n"
        "- [x] done\n"
        "- More\n"
        "  hidden\n\n"
        "> Be kind\n\n"
        "> 💡 Heads up\n\n"
        "```python\nprint('hi')\n```\n\n"
        "---\n\n"
        "| Plan | Price |\n"
        "| --- | --- |\n"
        "| Growth | 1\\|2 |\n\n"
        f"[Onboarding](https://www.notion.so/{'b' * 32})\n\n"
        f"[Tasks](https://www.notion.so/{'d' * 32})\n"
    )

    row = by_id[R].data.decode()
    assert row.startswith(
        "# Fix login\n\n"
        "- **Status**: Done\n"
        "- **Tags**: auth, web\n"
        "- **Due**: 2024-05-10\n"
        "- **Shipped**: Yes\n"
        "- **Points**: 3\n\n"
        "Steps"
    )
    assert "Links" not in row and "Notes" not in row
    assert by_id[R].metadata["database_id"] == D
    assert by_id[B].data.decode() == "# Onboarding\n\nWelcome aboard\n"
    assert fake.search_bodies[0]["filter"] == {"property": "object", "value": "page"}
    assert ("POST", "/search") in fake.calls  # retried after the 429


async def test_root_pages_limit_scope(fake):
    docs = await _collect(_conn(root_page_ids=[A]))
    assert sorted(d.external_id for d in docs) == sorted([A, R, B])
    assert "filter" not in fake.search_bodies[0]  # databases are needed for ancestry


async def test_incremental_stops_early_and_rounds_to_minute(fake):
    since = datetime(2024, 5, 2, 12, 0, 30, tzinfo=UTC)
    docs = await _collect(_conn(), since)
    # R was edited "12:00" (Notion rounds to the minute), so it may postdate `since`.
    assert [d.external_id for d in docs] == [X, A, R]
    assert len(fake.search_bodies) == 2  # stopped at B without reading further pages

    fake.calls.clear()
    fake.search_bodies.clear()
    since = datetime(2024, 5, 2, tzinfo=UTC)
    docs = await _collect(_conn(root_page_ids=[A]), since)
    assert [d.external_id for d in docs] == [A, R]
    # D was outside the early-stopped search, so R's ancestry was looked up.
    assert ("GET", f"/databases/{D}") in fake.calls


async def test_list_ids(fake):
    assert await _conn().list_ids() == {X, A, R, B}
    assert await _conn(root_page_ids=[A]).list_ids() == {A, R, B}
    assert not any(path.endswith("/children") for _, path in fake.calls)


async def test_errors(fake):
    with pytest.raises(ConnectorError, match="not found. Share it"):
        await _collect(_conn(root_page_ids=["9" * 32]))
    with pytest.raises(ConnectorError, match="integration secret"):
        await _collect(NotionConnector({}, {}))
    fake.status = 401
    with pytest.raises(ConnectorError, match="unauthorized"):
        await _collect(_conn())
    fake.status = 403
    with pytest.raises(ConnectorError, match="lacks a capability"):
        await _conn().list_ids()
