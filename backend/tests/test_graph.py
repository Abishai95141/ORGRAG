"""Knowledge graph: extraction parsing, storage, access control and the agent tools."""

# ruff: noqa: E501 — inline JSON fixtures read better unwrapped

import json
import uuid

import pytest

from app.graph.extract import build_prompts, parse_extraction
from tests.test_api_integration import _general, _ingest, _join, _org, _signup


# ── parsing (no database) ────────────────────────────────────────────────────
def test_parse_handles_fences_prose_and_bad_records():
    raw = """Here is the extraction:
```json
{"entities": [
  {"name": "\\"Priya Nair\\"", "type": "Person", "description": "Owns vendor records."},
  {"name": "Basalt Analytics", "type": "Organization", "description": "Payroll vendor.",},
  {"name": "", "type": "Person", "description": "nameless"},
  {"name": "Mystery", "type": "Spaceship", "description": "Unknown type becomes other."},
  {"name": "No description", "type": "Concept", "description": ""}
],
"relationships": [
  {"source": "Priya Nair", "target": "Basalt Analytics", "keywords": "owns，record", "description": "Priya owns the record."},
  {"source": "Basalt Analytics", "target": "basalt analytics", "keywords": "x", "description": "self"},
  {"source": "Priya Nair", "target": "", "keywords": "x", "description": "dangling"}
]}
``` hope that helps {:)}"""
    out = parse_extraction(raw)
    assert [(e.name, e.type) for e in out.entities] == [
        ("Priya Nair", "person"),
        ("Basalt Analytics", "organization"),
        ("Mystery", "other"),
    ]
    assert [(r.source, r.target, r.keywords) for r in out.relations] == [
        ("Priya Nair", "Basalt Analytics", "owns,record")
    ]


@pytest.mark.parametrize("raw", ["", "no json at all", "[1, 2, 3]", '{"entities": "nope"}'])
def test_parse_rejects_unusable_output(raw):
    out = parse_extraction(raw)
    assert out.entities == [] and out.relations == []


def test_prompts_use_strata_entity_types():
    system, user = build_prompts("Some text")
    assert "Organization: Companies and outside bodies" in system
    assert "```\nSome text\n```" in user


# ── extraction, storage and access (database) ────────────────────────────────
pytestmark_db = pytest.mark.integration


def _fake_extractor(by_marker: dict[str, dict]):
    """complete_text stand-in: picks a canned extraction by a marker in the chunk."""

    async def fake(*, model, system, prompt, max_tokens):
        for marker, payload in by_marker.items():
            if marker in prompt:
                return json.dumps(payload)
        return '{"entities": [], "relationships": []}'

    return fake


VENDOR = {
    "entities": [
        {"name": "Basalt Analytics", "type": "Organization", "description": "Payroll vendor; BAA executed."},
        {"name": "Priya Nair", "type": "Person", "description": "Record owner."},
        {"name": "Risk Tier High", "type": "Concept", "description": "Highest vendor risk tier."},
    ],
    "relationships": [
        {"source": "Priya Nair", "target": "Basalt Analytics", "keywords": "owns",
         "description": "Priya Nair owns the Basalt Analytics record."},
        {"source": "Basalt Analytics", "target": "Risk Tier High", "keywords": "risk",
         "description": "Basalt Analytics is in the High risk tier."},
    ],
}  # fmt: skip
SECRET = {
    "entities": [
        {"name": "Project Nightjar", "type": "Project", "description": "Confidential acquisition."},
        {"name": "Priya Nair", "type": "Person", "description": "Leads Project Nightjar."},
    ],
    "relationships": [
        {"source": "Priya Nair", "target": "Project Nightjar", "keywords": "leads",
         "description": "Priya Nair leads the confidential Project Nightjar."},
    ],
}  # fmt: skip


async def _extract_all(slug: str, monkeypatch, canned: dict[str, dict]):
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.graph import extract
    from app.models import Document, Organization

    monkeypatch.setattr(extract, "complete_text", _fake_extractor(canned))
    async with SessionLocal() as db:
        org = (await db.execute(select(Organization).where(Organization.slug == slug))).scalar_one()
        for doc in (await db.execute(select(Document).where(Document.org_id == org.id))).scalars():
            if doc.graph_hash != doc.content_hash:
                await extract.extract_document(db, doc, None)
        await extract.prune_orphans(db, org.id)
        await db.commit()


async def _setup(app, monkeypatch):
    admin = await _signup(app, "admin")
    member = await _signup(app, "member")
    org = await _org(admin, "Graph Co")
    slug = org["slug"]
    await _join(admin, member, slug)
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    r = await admin.post(
        f"/api/orgs/{slug}/collections",
        json={"name": "Board", "visibility": "restricted", "allowed_user_ids": []},
    )
    board = r.json()
    await _ingest(
        admin, slug, _general(org, cols), {"basalt.md": "# Basalt record\n\nVENDOR-MARK text."}
    )
    await _ingest(admin, slug, board["id"], {"nightjar.md": "# Nightjar\n\nSECRET-MARK text."})
    await admin.patch(f"/api/orgs/{slug}", json={"settings": {"knowledge_graph": True}})
    await _extract_all(slug, monkeypatch, {"VENDOR-MARK": VENDOR, "SECRET-MARK": SECRET})
    return admin, member, slug


@pytest.mark.integration
async def test_graph_respects_collection_access(app, monkeypatch):
    admin, member, slug = await _setup(app, monkeypatch)
    stats = (await admin.get(f"/api/orgs/{slug}/graph")).json()
    assert stats["enabled"] and stats["documents_pending"] == 0 and stats["entities"] == 4

    admin_names = {
        e["name"] for e in (await admin.get(f"/api/orgs/{slug}/graph/entities")).json()["items"]
    }
    member_page = (await member.get(f"/api/orgs/{slug}/graph/entities")).json()
    member_names = {e["name"] for e in member_page["items"]}
    assert "Project Nightjar" in admin_names
    assert "Project Nightjar" not in member_names and member_page["total"] == 3

    # Priya is visible to both, but the member only sees what the vendor doc says.
    priya_admin = next(
        e
        for e in (await admin.get(f"/api/orgs/{slug}/graph/entities?q=Priya")).json()["items"]
        if e["name"] == "Priya Nair"
    )
    detail = (await member.get(f"/api/orgs/{slug}/graph/entities/{priya_admin['id']}")).json()
    assert "Nightjar" not in detail["description"]
    assert [link["entity"]["name"] for link in detail["relations"]] == ["Basalt Analytics"]
    admin_detail = (await admin.get(f"/api/orgs/{slug}/graph/entities/{priya_admin['id']}")).json()
    assert {link["entity"]["name"] for link in admin_detail["relations"]} == {
        "Basalt Analytics",
        "Project Nightjar",
    }
    nightjar = next(
        e
        for e in (await admin.get(f"/api/orgs/{slug}/graph/entities?q=Nightjar")).json()["items"]
        if e["name"] == "Project Nightjar"
    )
    assert (
        await member.get(f"/api/orgs/{slug}/graph/entities/{nightjar['id']}")
    ).status_code == 404

    r = await admin.get(f"/api/orgs/{slug}/graph/entities?type=spaceship")
    assert r.status_code == 400 and r.json()["code"] == "invalid_type"
    assert (await member.post(f"/api/orgs/{slug}/graph/rebuild")).status_code == 403


@pytest.mark.integration
async def test_hidden_documents_drop_out_of_the_graph(app, monkeypatch):
    """Audit S5: hiding a document must remove what it contributed, even for admins."""
    admin, _, slug = await _setup(app, monkeypatch)
    docs = (await admin.get(f"/api/orgs/{slug}/documents")).json()["items"]
    nightjar_doc = next(d for d in docs if d["title"] == "Nightjar")

    async def names(query: str = "") -> set[str]:
        page = (await admin.get(f"/api/orgs/{slug}/graph/entities{query}")).json()
        return {e["name"] for e in page["items"]}

    assert "Project Nightjar" in await names()
    r = await admin.patch(f"/api/orgs/{slug}/documents/{nightjar_doc['id']}", json={"hidden": True})
    assert r.status_code == 200 and r.json()["hidden"] is True

    assert "Project Nightjar" not in await names()
    assert await names("?q=confidential") == set()
    page = (await admin.get(f"/api/orgs/{slug}/graph/entities?q=Priya")).json()["items"]
    priya = next(e for e in page if e["name"] == "Priya Nair")
    detail = (await admin.get(f"/api/orgs/{slug}/graph/entities/{priya['id']}")).json()
    assert "Nightjar" not in detail["description"]
    assert [link["entity"]["name"] for link in detail["relations"]] == ["Basalt Analytics"]

    await admin.patch(f"/api/orgs/{slug}/documents/{nightjar_doc['id']}", json={"hidden": False})
    assert "Project Nightjar" in await names()


@pytest.mark.integration
async def test_reindexing_replaces_a_documents_graph(app, monkeypatch):
    from sqlalchemy import func, select

    from app.core.db import SessionLocal
    from app.models import KGEntity, KGMention, Organization

    admin, _, slug = await _setup(app, monkeypatch)
    sources = (await admin.get(f"/api/orgs/{slug}/sources")).json()
    board_source = next(s for s in sources if s["collection_name"] == "Board")
    r = await admin.delete(f"/api/orgs/{slug}/sources/{board_source['id']}")
    assert r.status_code == 204
    await _extract_all(slug, monkeypatch, {})
    async with SessionLocal() as db:
        org = (await db.execute(select(Organization).where(Organization.slug == slug))).scalar_one()
        names = set(
            (await db.execute(select(KGEntity.name).where(KGEntity.org_id == org.id))).scalars()
        )
        orphans = (
            await db.execute(
                select(func.count())
                .select_from(KGEntity)
                .where(
                    ~KGEntity.id.in_(
                        select(KGMention.entity_id).where(KGMention.entity_id.is_not(None))
                    )
                )
            )
        ).scalar()
    assert "Project Nightjar" not in names and orphans == 0


@pytest.mark.integration
async def test_agent_lists_entities_with_citations(app, monkeypatch):
    from app.agent import engine
    from app.core.config import get_settings
    from app.llm.nvidia import ChatTurn, ToolCall

    admin, member, slug = await _setup(app, monkeypatch)
    calls: list[dict] = []
    turns = [
        ([("tool_start", "list_entities")], ChatTurn(tool_calls=[ToolCall("c1", "list_entities", '{"contains": "High risk"}')], finish_reason="tool_calls")),
        ([("text", "One vendor is high risk: Basalt Analytics [1].")], ChatTurn(content="One vendor is high risk: Basalt Analytics [1].", finish_reason="stop")),
    ]  # fmt: skip

    def fake_stream(**kwargs):
        if "You are Strata" in kwargs["messages"][0]["content"]:
            calls.append(kwargs)
            events, turn = turns.pop(0)
        else:
            events, turn = [], ChatTurn(content='{"title": "x"}')

        async def gen():
            for e in events:
                yield e
            yield "done", turn

        return gen()

    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake_stream)
    conv = (await member.post(f"/api/orgs/{slug}/conversations", json={})).json()
    r = await member.post(
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
        json={"content": "Which vendors are high risk?"},
    )
    assert r.status_code == 200, r.text
    names = {t["function"]["name"] for t in calls[0]["tools"]}
    assert {"list_entities", "explore_entity"} <= names
    tool_msg = calls[1]["messages"][-1]["content"]
    # The vendor matches through its relation to the risk tier; the tier itself matches too.
    assert (
        tool_msg.startswith("2 entities in the knowledge graph match")
        and "- Basalt Analytics [organization]" in tool_msg
    )
    assert "Nightjar" not in tool_msg
    message = r.json()["message"]
    assert message["sources"][0]["title"] == "Basalt record"


@pytest.mark.integration
async def test_graph_tools_absent_when_disabled(app, monkeypatch):
    from app.agent.tools import tool_definitions

    names = {d["name"] for d in tool_definitions(web=False)}
    assert "list_entities" not in names and "explore_entity" not in names
    _ = uuid.uuid4()


async def test_extraction_falls_back_when_primary_is_out_of_quota(monkeypatch):
    from types import SimpleNamespace as NS

    from app.core.config import get_settings
    from app.graph import extract
    from app.llm.nvidia import NvidiaError

    used: list[str] = []

    async def fake(*, model, system, prompt, max_tokens):
        used.append(model)
        if model == "explabs:free":
            raise NvidiaError(429, "daily token limit reached")
        return json.dumps(VENDOR)

    monkeypatch.setattr(extract, "complete_text", fake)
    monkeypatch.setattr(extract, "_primary_paused_until", 0.0)
    monkeypatch.setattr(get_settings(), "graph_extraction_fallback_model", "explabs:paid")
    chunk = NS(content="text", heading="", ordinal=0)

    first = await extract.extract_chunk("explabs:free", chunk, "Doc")
    second = await extract.extract_chunk("explabs:free", chunk, "Doc")
    assert used == ["explabs:free", "explabs:paid", "explabs:paid"]  # paused primary
    assert first.entities and second.entities

    # Errors that aren't about quota still surface instead of silently spending credits.
    async def broken(*, model, system, prompt, max_tokens):
        raise NvidiaError(400, "bad request")

    monkeypatch.setattr(extract, "complete_text", broken)
    monkeypatch.setattr(extract, "_primary_paused_until", 0.0)
    with pytest.raises(NvidiaError):
        await extract.extract_chunk("explabs:free", chunk, "Doc")


VARIANTS = {
    "entities": [
        {"name": "Cobalt Data", "type": "Organization", "description": "Backup vendor."},
        {"name": "High Risk Tier", "type": "Concept", "description": "Top risk class."},
    ],
    "relationships": [
        {"source": "Cobalt Data", "target": "High Risk Tier", "keywords": "risk",
         "description": "Cobalt Data is high risk."},
    ],
}  # fmt: skip
VARIANTS_2 = {
    "entities": [
        {"name": "Elm Works", "type": "Organization", "description": "Courier vendor."},
        {"name": "Risk Tier High", "type": "Concept", "description": "Top risk class."},
    ],
    "relationships": [
        {"source": "Elm Works", "target": "Risk Tier High", "keywords": "risk",
         "description": "Elm Works is in the high risk tier."},
    ],
}  # fmt: skip


@pytest.mark.integration
async def test_name_variants_merge_and_lists_show_links(app, monkeypatch):
    from sqlalchemy import select

    from app.agent.citations import CitationRegistry
    from app.agent.tools import ToolBox
    from app.core.db import SessionLocal
    from app.models import Organization, User
    from app.retrieval.access import resolve_scope

    admin = await _signup(app, "admin")
    org = await _org(admin, "Variant Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    await _ingest(
        admin, slug, _general(org, cols), {"a.md": "# A\n\nMARK-A", "b.md": "# B\n\nMARK-B"}
    )
    await _extract_all(slug, monkeypatch, {"MARK-A": VARIANTS, "MARK-B": VARIANTS_2})
    async with SessionLocal() as db:
        o = (await db.execute(select(Organization).where(Organization.slug == slug))).scalar_one()
        me = (await db.execute(select(User).where(User.email == admin.email))).scalar_one()
        tools = ToolBox(
            db,
            await resolve_scope(db, user_id=me.id, primary_org=o),
            CitationRegistry(),
            graph=True,
        )
        explored = await tools.explore_entity({"name": "risk tier high"})
        assert "Connected to 2 entities" in explored.text
        assert "Cobalt Data" in explored.text and "Elm Works" in explored.text
        listed = await tools.list_entities({"type": "organization"})
        assert (
            "Cobalt Data [organization]" in listed.text and "linked: High Risk Tier" in listed.text
        )
