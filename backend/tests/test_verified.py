"""Verified answers: promoted from chat, indexed and boosted, permission-safe, expiring."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_agent_nvidia import FakeNvidia, _answer_turn, _tool_turn
from tests.test_api_integration import _general, _ingest, _join, _org, _signup

pytestmark = pytest.mark.integration

ANSWER = "Staff get sixteen weeks of parental leave [1]."


async def _setup(app, monkeypatch, *, restricted: bool = False):
    """An org with a leave policy (in General, or in a restricted Board collection), a
    member, and one answered question in the admin's chat."""
    from app.agent import engine
    from app.core.config import get_settings

    admin = await _signup(app, f"va{uuid.uuid4().hex[:6]}")
    member = await _signup(app, f"vm{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Verify Co")
    slug = org["slug"]
    await _join(admin, member, slug)
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    general = _general(org, cols)
    target = general
    if restricted:
        board = await admin.post(
            f"/api/orgs/{slug}/collections",
            json={"name": "Board", "visibility": "restricted", "allowed_user_ids": []},
        )
        target = board.json()["id"]
    await _ingest(
        admin, slug, target, {"leave.md": "# Leave policy\n\nStaff get 16 weeks of parental leave."}
    )
    fake = FakeNvidia(
        [_tool_turn("search_knowledge", '{"queries": ["parental leave"]}'), _answer_turn(ANSWER)]
    )
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake.stream_chat)
    conv = (await admin.post(f"/api/orgs/{slug}/conversations", json={})).json()
    r = await admin.post(
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
        json={"content": "How long is parental leave?"},
    )
    message = r.json()["message"]
    assert message["status"] == "complete" and message["sources"], message
    return admin, member, slug, general, target, conv, message, fake


async def test_an_answer_is_verified_indexed_and_cited_first(app, monkeypatch):
    admin, member, slug, general, _, conv, message, fake = await _setup(app, monkeypatch)
    r = await admin.post(
        f"/api/orgs/{slug}/verified-answers",
        json={"message_id": message["id"], "collection_id": general},
    )
    assert r.status_code == 201, r.text
    va = r.json()
    assert va["question"] == "How long is parental leave?" and va["answer"] == ANSWER
    assert va["status"] == "active" and va["owner"]["email"] == admin.email
    assert [s["title"] for s in va["sources"]] == ["Leave policy"]
    expires = datetime.fromisoformat(va["expires_at"]) - datetime.now(UTC)
    assert timedelta(days=89) < expires <= timedelta(days=90)

    # The chat shows the answer as verified.
    detail = (await admin.get(f"/api/orgs/{slug}/conversations/{conv['id']}")).json()
    assert detail["messages"][-1]["verified_answer_id"] == va["id"]

    # It's a boosted document, found first, and its citations carry the verified kind.
    doc = (await admin.get(f"/api/orgs/{slug}/documents/{va['document_id']}")).json()
    assert doc["boost"] == 2 and not doc["hidden"]
    assert "[1]" not in doc["content"] and "Based on:" in doc["content"]
    hits = (
        await member.post(f"/api/orgs/{slug}/search", json={"query": "parental leave weeks"})
    ).json()["results"]
    assert hits[0]["title"] == "How long is parental leave?"
    assert hits[0]["source_kind"] == "verified"

    # The model is told which passages are verified.
    fake.turns = [
        _tool_turn("search_knowledge", '{"queries": ["parental leave"]}'),
        _answer_turn("Sixteen weeks [1]."),
    ]
    conv2 = (await member.post(f"/api/orgs/{slug}/conversations", json={})).json()
    await member.post(
        f"/api/orgs/{slug}/conversations/{conv2['id']}/messages?stream=false",
        json={"content": "parental leave?"},
    )
    tool_result = fake.calls[-1]["messages"][-1]["content"]
    assert 'verified="true"' in tool_result

    # Members can read verified answers but not manage them; the hidden source isn't a source.
    assert [v["id"] for v in (await member.get(f"/api/orgs/{slug}/verified-answers")).json()] == [
        va["id"]
    ]
    r = await member.post(
        f"/api/orgs/{slug}/verified-answers",
        json={"question": "Q", "answer": "A", "collection_id": general},
    )
    assert r.status_code == 403
    sources = (await admin.get(f"/api/orgs/{slug}/sources")).json()
    assert all(s["kind"] != "verified" for s in sources)


async def test_verifying_cannot_widen_who_sees_restricted_sources(app, monkeypatch):
    admin, member, slug, general, board, _, message, _ = await _setup(
        app, monkeypatch, restricted=True
    )
    r = await admin.post(
        f"/api/orgs/{slug}/verified-answers",
        json={"message_id": message["id"], "collection_id": general},
    )
    assert r.status_code == 400 and r.json()["code"] == "sources_not_visible", r.text
    assert "Board" in r.json()["detail"]
    r = await admin.post(
        f"/api/orgs/{slug}/verified-answers",
        json={"message_id": message["id"], "collection_id": board},
    )
    assert r.status_code == 201, r.text
    # The member can't see Board, so can't see its verified answers either.
    assert (await member.get(f"/api/orgs/{slug}/verified-answers")).json() == []
    r = await member.get(f"/api/orgs/{slug}/verified-answers/{r.json()['id']}")
    assert r.status_code == 404


async def test_only_your_own_complete_answers_can_be_verified(app, monkeypatch):
    admin, member, slug, general, _, _, message, _ = await _setup(app, monkeypatch)
    # Another admin can't promote an answer from a conversation that isn't theirs.
    other = await _signup(app, f"vo{uuid.uuid4().hex[:6]}")
    await _join(admin, other, slug, role="admin")
    r = await other.post(
        f"/api/orgs/{slug}/verified-answers",
        json={"message_id": message["id"], "collection_id": general},
    )
    assert r.status_code == 404
    r = await admin.post(
        f"/api/orgs/{slug}/verified-answers",
        json={"collection_id": general, "question": "Only a question"},
    )
    assert r.status_code == 400 and r.json()["code"] == "missing_answer"


async def test_expiry_hides_and_reverify_restores(app, monkeypatch):
    from app.core.db import SessionLocal
    from app.ingestion.verified import expire_due

    admin, member, slug, general, _, _, message, _ = await _setup(app, monkeypatch)
    va = (
        await admin.post(
            f"/api/orgs/{slug}/verified-answers",
            json={"message_id": message["id"], "collection_id": general, "expires_in_days": 10},
        )
    ).json()
    assert va["status"] == "expiring"  # within 14 days

    async with SessionLocal() as db:
        assert await expire_due(db, datetime.now(UTC) + timedelta(days=11)) >= 1
        await db.commit()
    doc = (await admin.get(f"/api/orgs/{slug}/documents/{va['document_id']}")).json()
    assert doc["hidden"] is True

    # Re-verifying vouches again: new verifier, new expiry, back in search.
    other = await _signup(app, f"vr{uuid.uuid4().hex[:6]}")
    await _join(admin, other, slug, role="admin")
    r = await other.post(
        f"/api/orgs/{slug}/verified-answers/{va['id']}/reverify", json={"expires_in_days": None}
    )
    assert r.status_code == 200, r.text
    again = r.json()
    assert again["status"] == "active" and again["expires_at"] is None
    assert again["verified_by"]["email"] == other.email
    doc = (await admin.get(f"/api/orgs/{slug}/documents/{va['document_id']}")).json()
    assert doc["hidden"] is False


async def test_edit_move_and_delete(app, monkeypatch):
    admin, member, slug, general, _, _, message, _ = await _setup(app, monkeypatch)
    va = (
        await admin.post(
            f"/api/orgs/{slug}/verified-answers",
            json={"message_id": message["id"], "collection_id": general},
        )
    ).json()
    eng = (await admin.post(f"/api/orgs/{slug}/collections", json={"name": "Handbook"})).json()
    r = await admin.patch(
        f"/api/orgs/{slug}/verified-answers/{va['id']}",
        json={
            "answer": "Sixteen weeks, paid in full.",
            "collection_id": eng["id"],
            "owner_id": (await member.get("/api/auth/me")).json()["user"]["id"],
            "expires_at": None,
        },
    )
    assert r.status_code == 200, r.text
    moved = r.json()
    assert moved["collection_name"] == "Handbook" and moved["expires_at"] is None
    assert moved["owner"]["email"] == member.email
    doc = (await admin.get(f"/api/orgs/{slug}/documents/{va['document_id']}")).json()
    assert doc["collection_id"] == eng["id"] and "paid in full" in doc["content"]

    r = await admin.patch(
        f"/api/orgs/{slug}/verified-answers/{va['id']}",
        json={"owner_id": str(uuid.uuid4())},
    )
    assert r.status_code == 400 and r.json()["code"] == "invalid_owner"

    assert (await admin.delete(f"/api/orgs/{slug}/verified-answers/{va['id']}")).status_code == 204
    assert (await admin.get(f"/api/orgs/{slug}/verified-answers")).json() == []
    r = await admin.get(f"/api/orgs/{slug}/documents/{va['document_id']}")
    assert r.status_code == 404
