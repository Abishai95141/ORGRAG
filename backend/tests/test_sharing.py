"""Answer permalinks: each viewer sees what their own permissions allow."""

import uuid

import pytest

from tests.test_agent_nvidia import FakeNvidia, _answer_turn, _tool_turn
from tests.test_api_integration import _client, _general, _ingest, _join, _org, _signup

pytestmark = pytest.mark.integration


async def _answer(app, monkeypatch, *, restricted: bool):
    """An admin's answer citing a policy in General, or in a restricted Board
    collection; plus a member and an outsider."""
    from app.agent import engine
    from app.core.config import get_settings

    admin = await _signup(app, f"sa{uuid.uuid4().hex[:6]}")
    member = await _signup(app, f"sm{uuid.uuid4().hex[:6]}")
    outsider = await _signup(app, f"so{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Share Co")
    slug = org["slug"]
    await _join(admin, member, slug)
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    target = _general(org, cols)
    if restricted:
        board = await admin.post(
            f"/api/orgs/{slug}/collections",
            json={"name": "Board", "visibility": "restricted", "allowed_user_ids": []},
        )
        target = board.json()["id"]
    await _ingest(
        admin, slug, target, {"plan.md": "# Merger plan\n\nProject Heron closes in March."}
    )
    fake = FakeNvidia(
        [
            _tool_turn("search_knowledge", '{"queries": ["Project Heron"]}'),
            _answer_turn("Project Heron closes in March [1]."),
        ]
    )
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake.stream_chat)
    conv = (await admin.post(f"/api/orgs/{slug}/conversations", json={})).json()
    r = await admin.post(
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
        json={"content": "When does Heron close?"},
    )
    message = r.json()["message"]
    assert message["status"] == "complete"
    return admin, member, outsider, slug, target, conv, message


async def test_a_member_who_can_read_the_sources_sees_the_answer(app, monkeypatch):
    admin, member, outsider, slug, _, conv, message = await _answer(
        app, monkeypatch, restricted=False
    )
    r = await admin.post(f"/api/orgs/{slug}/messages/{message['id']}/share")
    assert r.status_code == 200, r.text
    share = r.json()
    assert share["url"].endswith(f"/a/{share['token']}") and len(share["token"]) >= 20
    again = (await admin.post(f"/api/orgs/{slug}/messages/{message['id']}/share")).json()
    assert again["token"] == share["token"]  # one link per answer
    detail = (await admin.get(f"/api/orgs/{slug}/conversations/{conv['id']}")).json()
    assert detail["messages"][-1]["share_token"] == share["token"]

    seen = (await member.get(f"/api/shared/{share['token']}")).json()
    assert seen["access"] == "full" and seen["missing"] == []
    assert seen["question"] == "When does Heron close?"
    assert seen["answer"] == "Project Heron closes in March [1]."
    assert [s["title"] for s in seen["sources"]] == ["Merger plan"]
    assert seen["org"]["slug"] == slug and seen["author_name"]

    # Someone outside the org learns only which org it belongs to.
    outside = (await outsider.get(f"/api/shared/{share['token']}")).json()
    assert outside["access"] == "none" and outside["question"] is None
    assert outside["answer"] is None and outside["missing"][0]["kind"] == "org"
    # Signed-out visitors are asked to sign in; API keys can't open links.
    anon = await _client(app)
    assert (await anon.get(f"/api/shared/{share['token']}")).status_code == 401

    # Only the asker can share their answer.
    r = await member.post(f"/api/orgs/{slug}/messages/{message['id']}/share")
    assert r.status_code == 404

    # Revoking turns the link off for everyone.
    assert (
        await admin.delete(f"/api/orgs/{slug}/messages/{message['id']}/share")
    ).status_code == 204
    r = await member.get(f"/api/shared/{share['token']}")
    assert r.status_code == 404 and r.json()["code"] == "share_not_found"
    fresh = (await admin.post(f"/api/orgs/{slug}/messages/{message['id']}/share")).json()
    assert fresh["token"] != share["token"]


async def test_restricted_sources_hide_the_answer_until_access_is_granted(app, monkeypatch):
    admin, member, _, slug, board, _, message = await _answer(app, monkeypatch, restricted=True)
    token = (await admin.post(f"/api/orgs/{slug}/messages/{message['id']}/share")).json()["token"]

    seen = (await member.get(f"/api/shared/{token}")).json()
    assert seen["access"] == "restricted" and seen["answer"] is None and seen["sources"] == []
    assert seen["question"] == "When does Heron close?"
    (missing,) = seen["missing"]
    assert missing["kind"] == "collection" and missing["collection_name"] == "Board"
    assert missing["collection_id"] == board and missing["requested"] is False

    r = await member.post(
        f"/api/orgs/{slug}/collections/{board}/access-requests", json={"note": "For the memo"}
    )
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "pending" and req["note"] == "For the memo"
    # Asking twice returns the same pending request.
    again = await member.post(f"/api/orgs/{slug}/collections/{board}/access-requests", json={})
    assert again.json()["id"] == req["id"]
    assert (await member.get(f"/api/shared/{token}")).json()["missing"][0]["requested"] is True

    # Members can't see or decide requests; admins approve, which grants access.
    assert (await member.get(f"/api/orgs/{slug}/access-requests")).status_code == 403
    pending = (await admin.get(f"/api/orgs/{slug}/access-requests")).json()
    assert [p["id"] for p in pending] == [req["id"]]
    assert pending[0]["user"]["email"] == member.email
    r = await admin.post(f"/api/orgs/{slug}/access-requests/{req['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved"
    r = await admin.post(f"/api/orgs/{slug}/access-requests/{req['id']}/approve")
    assert r.status_code == 409 and r.json()["code"] == "already_decided"

    seen = (await member.get(f"/api/shared/{token}")).json()
    assert seen["access"] == "full" and seen["answer"].startswith("Project Heron")
    r = await member.post(f"/api/orgs/{slug}/collections/{board}/access-requests", json={})
    assert r.status_code == 409 and r.json()["code"] == "already_has_access"


async def test_dismissed_requests_grant_nothing(app, monkeypatch):
    admin, member, _, slug, board, _, message = await _answer(app, monkeypatch, restricted=True)
    req = (
        await member.post(f"/api/orgs/{slug}/collections/{board}/access-requests", json={})
    ).json()
    r = await admin.post(f"/api/orgs/{slug}/access-requests/{req['id']}/dismiss")
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert (await admin.get(f"/api/orgs/{slug}/access-requests")).json() == []
    token = (await admin.post(f"/api/orgs/{slug}/messages/{message['id']}/share")).json()["token"]
    seen = (await member.get(f"/api/shared/{token}")).json()
    assert seen["access"] == "restricted" and seen["missing"][0]["requested"] is False
