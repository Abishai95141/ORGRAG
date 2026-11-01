"""Per-org audit log: who read an org's data (also from another org's workspace) and
the admin actions taken in it; export and retention."""

import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_agent_nvidia import FakeNvidia, _answer_turn, _tool_turn
from tests.test_api_integration import _client, _general, _ingest, _join, _org, _signup

pytestmark = pytest.mark.integration


async def _access(client, slug: str, **params) -> list[dict]:
    r = await client.get(f"/api/orgs/{slug}/audit/access", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def _events(client, slug: str, **params) -> list[dict]:
    r = await client.get(f"/api/orgs/{slug}/audit/events", params={"limit": 200, **params})
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def _ask(client, slug: str, question: str, scope: list[str] | None = None) -> dict:
    conv = (
        await client.post(f"/api/orgs/{slug}/conversations", json={"scope_org_slugs": scope})
    ).json()
    r = await client.post(
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
        json={"content": question},
    )
    assert r.status_code == 200, r.text
    return {**r.json()["message"], "conversation_id": conv["id"]}


async def _two_orgs(app):
    """Org A (Alice owns it) and org B (Bob owns it); Bob is also a member of A. Each
    holds one document about the Kestrel launch; only B's mentions the budget."""
    alice = await _signup(app, "alice")
    bob = await _signup(app, "bob")
    a = (await _org(alice, "Org A"))["slug"]
    b = (await _org(bob, "Org B"))["slug"]
    await _join(alice, bob, a)
    for client, slug, body in (
        (alice, a, "# Runway A\n\nKestrel launches from the blue runway."),
        (bob, b, "# Runway B\n\nKestrel launches from the red runway. Budget: forty million."),
    ):
        cols = (await client.get(f"/api/orgs/{slug}/collections")).json()
        await _ingest(client, slug, _general({}, cols), {"runway.md": body})
    return alice, bob, a, b


# ── data access ──────────────────────────────────────────────────────────────
async def test_an_admin_sees_another_orgs_member_reading_their_data(app):
    alice, bob, a, b = await _two_orgs(app)
    question = "Which runway does Kestrel launch from?"
    message = await _ask(bob, b, question, scope=[a])  # asked in B's workspace, across A
    assert {s["org_slug"] for s in message["sources"]} == {a, b}

    (entry,) = await _access(alice, a)
    assert entry["kind"] == "chat" and entry["channel"] == "app" and entry["cross_org"] is True
    assert entry["actor"]["type"] == "user" and entry["actor"]["email"] == bob.email
    assert entry["actor"]["name"] == "bob" and entry["query"] == question
    assert [d["title"] for d in entry["documents"]] == ["Runway A"]
    assert entry["document_count"] == 1 and entry["documents"][0]["cited"] is True
    # Nothing of B's: not its documents, its name or slug, nor the answer's id.
    raw = json.dumps(entry)
    assert "Runway B" not in raw and "Org B" not in raw and b not in raw
    assert entry["message_id"] is None

    # B's own log has the same question with only B's documents.
    (own,) = await _access(bob, b)
    assert own["cross_org"] is False and own["message_id"] == message["id"]
    assert [d["title"] for d in own["documents"]] == ["Runway B"]

    # A question that read only B's documents never reaches A's log.
    await _ask(bob, b, "What is the budget?")
    assert [e["query"] for e in await _access(alice, a)] == [question]
    assert [e["query"] for e in await _access(bob, b)] == ["What is the budget?", question]

    # Deleting the conversation doesn't erase the record; Bob can't read A's log.
    r = await bob.delete(f"/api/orgs/{b}/conversations/{message['conversation_id']}")
    assert r.status_code == 204
    assert len(await _access(alice, a)) == 1
    assert (await bob.get(f"/api/orgs/{a}/audit/access")).status_code == 403


async def test_documents_retrieved_but_not_cited_count_as_read(app, monkeypatch):
    from app.agent import engine
    from app.core.config import get_settings

    alice, bob, a, b = await _two_orgs(app)
    fake = FakeNvidia(
        [
            _tool_turn("search_knowledge", '{"queries": ["kestrel runway"]}'),
            _answer_turn("The sources don't say which runway is used."),
        ]
    )
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake.stream_chat)
    message = await _ask(bob, b, "Runway?", scope=[a])
    assert message["status"] == "complete" and len(message["sources"]) == 2

    (entry,) = await _access(alice, a)
    assert entry["kind"] == "chat" and entry["cited_count"] == 0
    assert [(d["title"], d["cited"]) for d in entry["documents"]] == [("Runway A", False)]


async def test_searches_are_attributed_to_every_org_they_return(app):
    alice, bob, a, b = await _two_orgs(app)
    r = await bob.post(
        f"/api/orgs/{b}/search", json={"query": "kestrel runway", "org_slugs": [b, a]}
    )
    assert {x["org_slug"] for x in r.json()["results"]} == {a, b}
    (entry,) = await _access(alice, a, kind="search")
    assert entry["query"] == "kestrel runway" and entry["cross_org"] is True
    assert entry["actor"]["email"] == bob.email
    assert [d["title"] for d in entry["documents"]] == ["Runway A"]
    assert entry["documents"][0]["cited"] is False

    # A search of B only isn't in A's log.
    await bob.post(f"/api/orgs/{b}/search", json={"query": "forty million budget"})
    assert [e["query"] for e in await _access(alice, a)] == ["kestrel runway"]
    assert len(await _access(bob, b, kind="search")) == 2


async def test_api_key_reads_are_attributed_to_the_key(app, monkeypatch):
    from app.agent import engine
    from app.core.config import get_settings

    alice, _, a, _ = await _two_orgs(app)
    key = (
        await alice.post(f"/api/orgs/{a}/api-keys", json={"name": "CI bot", "role": "member"})
    ).json()
    bot = await _client(app)
    bot.headers["Authorization"] = f"Bearer {key['key']}"

    found = (await bot.post(f"/api/orgs/{a}/search", json={"query": "kestrel"})).json()
    doc_id = found["results"][0]["document_id"]
    assert (await bot.get(f"/api/orgs/{a}/documents/{doc_id}")).status_code == 200
    fake = FakeNvidia(
        [
            _tool_turn("search_knowledge", '{"queries": ["kestrel"]}'),
            _answer_turn("Kestrel uses the blue runway [1]."),
        ]
    )
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake.stream_chat)
    await _ask(bot, a, "Which runway?")

    entries = await _access(alice, a, channel="api")
    assert [e["kind"] for e in entries] == ["chat", "read", "search"]
    for e in entries:
        actor = e["actor"]
        assert actor["type"] == "api_key" and actor["api_key_name"] == "CI bot"
        assert actor["api_key_id"] == key["id"] and actor["email"] == alice.email
        assert e["cross_org"] is False
    assert entries[0]["documents"][0]["cited"] is True
    assert entries[1]["documents"] == [
        {"id": doc_id, "title": "Runway A", "collection": "General", "cited": False}
    ]

    # Revoking the key keeps its entries, still named, and filterable by its id.
    assert (await alice.delete(f"/api/orgs/{a}/api-keys/{key['id']}")).status_code == 204
    kept = await _access(alice, a, actor_id=key["id"])
    assert len(kept) == 3 and {e["actor"]["api_key_name"] for e in kept} == {"CI bot"}
    by_doc = await _access(alice, a, document_id=doc_id)
    assert {e["kind"] for e in by_doc} == {"chat", "read", "search"}


async def test_mcp_reads_are_attributed_to_the_mcp_channel(app, monkeypatch):
    from app.agent import engine
    from app.core.config import get_settings
    from tests.test_mcp import _data, _setup, mcp_session

    admin, slug, admin_key, _ = await _setup(app)
    fake = FakeNvidia(
        [
            _tool_turn("search_knowledge", '{"queries": ["parental leave"]}'),
            _answer_turn("Sixteen weeks [1]."),
        ]
    )
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake.stream_chat)
    async with mcp_session(app, admin_key["key"]) as s:
        found = _data(await s.call_tool("search", {"query": "parental leave"}))["results"]
        doc_id = next(r["document_id"] for r in found if r["title"] == "Leave policy")
        _data(await s.call_tool("read_document", {"document_id": doc_id}))
        _data(await s.call_tool("ask", {"question": "How much parental leave?"}))
    ask, read, search = await _access(admin, slug, channel="mcp")
    assert search["kind"] == "search" and search["query"] == "parental leave"
    assert {d["title"] for d in search["documents"]} == {"Leave policy", "Merger plan"}
    assert read["kind"] == "read" and read["query"] is None
    assert [d["title"] for d in read["documents"]] == ["Leave policy"]
    # The agent run behind `ask` is attributed to the key too, through the MCP server.
    assert ask["kind"] == "chat" and ask["query"] == "How much parental leave?"
    assert ask["cited_count"] == 1
    for entry in (ask, read, search):
        assert entry["actor"]["api_key_name"] == "Admin MCP"
        assert entry["actor"]["email"] == admin.email


async def test_opening_a_shared_answer_is_a_read_once_the_viewer_can_see_it(app, monkeypatch):
    from tests.test_sharing import _answer

    admin, member, _, slug, board, _, message = await _answer(app, monkeypatch, restricted=True)
    token = (await admin.post(f"/api/orgs/{slug}/messages/{message['id']}/share")).json()["token"]
    # Restricted: the viewer sees the question only, so nothing of the documents.
    assert (await member.get(f"/api/shared/{token}")).json()["access"] == "restricted"
    assert await _access(admin, slug, kind="shared") == []

    req = (
        await member.post(f"/api/orgs/{slug}/collections/{board}/access-requests", json={})
    ).json()
    await admin.post(f"/api/orgs/{slug}/access-requests/{req['id']}/approve")
    assert (await member.get(f"/api/shared/{token}")).json()["access"] == "full"
    (entry,) = await _access(admin, slug, kind="shared")
    assert entry["actor"]["email"] == member.email and entry["query"] == "When does Heron close?"
    assert entry["message_id"] == message["id"] and entry["cited_count"] == 1
    assert [d["title"] for d in entry["documents"]] == ["Merger plan"]


# ── admin actions ────────────────────────────────────────────────────────────
def _by_action(events: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in events:
        out.setdefault(e["action"], []).append(e)
    return out


async def test_member_invite_key_and_settings_changes_are_recorded(app):
    owner = await _signup(app, "owner")
    bob = await _signup(app, "bob")
    carol = await _signup(app, "carol")
    org = await _org(owner, "Events Co")
    slug = org["slug"]
    bob_id = (await bob.get("/api/auth/me")).json()["user"]["id"]

    invite = (
        await owner.post(f"/api/orgs/{slug}/invites", json={"email": bob.email, "role": "member"})
    ).json()
    assert (await bob.post(f"/api/invites/{invite['token']}/accept")).status_code == 200
    stale = (
        await owner.post(f"/api/orgs/{slug}/invites", json={"email": carol.email, "role": "viewer"})
    ).json()
    await owner.delete(f"/api/orgs/{slug}/invites/{stale['id']}")
    await owner.patch(f"/api/orgs/{slug}/members/{bob_id}", json={"role": "admin"})
    await owner.patch(
        f"/api/orgs/{slug}",
        json={"name": "Events Inc", "settings": {"web_search": True, "retrieval_top_k": 12}},
    )
    key = (
        await owner.post(f"/api/orgs/{slug}/api-keys", json={"name": "Deploy", "role": "viewer"})
    ).json()
    await owner.delete(f"/api/orgs/{slug}/api-keys/{key['id']}")
    await owner.delete(f"/api/orgs/{slug}/members/{bob_id}")

    events = await _events(owner, slug)
    actions = _by_action(events)
    assert [e["action"] for e in reversed(events)] == [
        "org.created",
        "invite.created",
        "member.joined",
        "invite.created",
        "invite.revoked",
        "member.role_changed",
        "org.updated",
        "api_key.created",
        "api_key.revoked",
        "member.removed",
    ]
    joined = actions["member.joined"][0]
    assert joined["actor"]["email"] == bob.email and joined["details"] == {"role": "member"}
    assert actions["invite.revoked"][0]["target_label"] == carol.email
    role = actions["member.role_changed"][0]
    assert role["target_id"] == bob_id and role["target_label"] == bob.email
    assert role["details"]["role"] == {"from": "member", "to": "admin"}
    assert role["actor"]["email"] == owner.email and role["channel"] == "app"
    changed = actions["org.updated"][0]["details"]["changes"]
    assert changed["name"] == {"from": "Events Co", "to": "Events Inc"}
    assert changed["settings.web_search"] == {"from": False, "to": True}
    assert changed["settings.retrieval_top_k"] == {"from": 8, "to": 12}
    assert set(changed) == {"name", "settings.web_search", "settings.retrieval_top_k"}
    created = actions["api_key.created"][0]
    assert created["target_label"] == "Deploy" and created["details"]["role"] == "viewer"
    assert actions["member.removed"][0]["details"] == {"role": "admin", "keys_revoked": 0}

    # No secrets anywhere: invite tokens, the API key (or its hash), passwords.
    raw = json.dumps(events)
    for secret in (invite["token"], stale["token"], key["key"], key["prefix"], "password123"):
        assert secret not in raw


async def test_collection_source_and_document_changes_are_recorded(app):
    admin = await _signup(app, "admin")
    member = await _signup(app, "member")
    org = await _org(admin, "Content Co")
    slug = org["slug"]
    await _join(admin, member, slug)
    member_id = (await member.get("/api/auth/me")).json()["user"]["id"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()

    board = (
        await admin.post(
            f"/api/orgs/{slug}/collections",
            json={"name": "Board", "visibility": "restricted", "allowed_user_ids": [member_id]},
        )
    ).json()
    await admin.patch(f"/api/orgs/{slug}/collections/{board['id']}", json={"name": "Board 2"})
    await admin.patch(f"/api/orgs/{slug}/collections/{board['id']}", json={"allowed_user_ids": []})
    github_token = "ghp_" + uuid.uuid4().hex
    rotated = "ghp_" + uuid.uuid4().hex
    gh = (
        await admin.post(
            f"/api/orgs/{slug}/sources",
            json={
                "kind": "github",
                "name": "Handbook",
                "collection_id": board["id"],
                "config": {"repo": "acme/handbook", "token": github_token},
            },
        )
    ).json()
    await admin.patch(
        f"/api/orgs/{slug}/sources/{gh['id']}",
        json={"config": {"repo": "acme/handbook", "token": rotated}},
    )
    await admin.post(f"/api/orgs/{slug}/sources/{gh['id']}/sync")
    await admin.delete(f"/api/orgs/{slug}/sources/{gh['id']}")  # its queued sync is moot
    await _ingest(admin, slug, _general(org, cols), {"pto.md": "# PTO\n\nTwenty days."})
    doc = (await admin.get(f"/api/orgs/{slug}/documents")).json()["items"][0]
    await admin.patch(f"/api/orgs/{slug}/documents/{doc['id']}", json={"hidden": True, "boost": 2})
    await admin.patch(f"/api/orgs/{slug}/documents/{doc['id']}", json={"boost": 2})  # no change
    await admin.delete(f"/api/orgs/{slug}/documents/{doc['id']}")
    await admin.delete(f"/api/orgs/{slug}/collections/{board['id']}")

    actions = _by_action(await _events(admin, slug))
    created = actions["collection.created"][0]
    assert created["target_label"] == "Board"
    assert created["details"] == {"visibility": "restricted", "granted": [member.email]}
    assert actions["collection.updated"][0]["details"]["changes"] == {
        "name": {"from": "Board", "to": "Board 2"}
    }
    access = actions["collection.access_changed"][0]["details"]
    assert access["revoked"] == [member.email] and access["granted"] == []
    source = actions["source.created"]
    gh_created = next(e for e in source if e["details"]["kind"] == "github")
    assert gh_created["details"]["config"] == {"repo": "acme/handbook", "paths": []}
    assert gh_created["details"]["write_only_fields"] == ["token"]
    assert actions["source.updated"][0]["details"] == {
        "changes": {},
        "write_only_fields": ["token"],
    }
    assert "source.synced" in actions and "source.files_uploaded" in actions
    assert actions["source.files_uploaded"][0]["details"] == {"files": ["pto.md"]}
    assert actions["source.deleted"][0]["target_label"] == "Handbook"
    assert actions["document.hidden"][0]["target_label"] == "PTO"
    assert [e["details"]["boost"] for e in actions["document.boosted"]] == [{"from": 0, "to": 2}]
    assert actions["document.deleted"][0]["details"] == {"source": "Docs"}
    assert actions["collection.deleted"][0]["target_label"] == "Board 2"

    raw = json.dumps(actions)
    assert github_token not in raw and rotated not in raw


async def test_sharing_access_requests_and_verified_answers_are_recorded(app, monkeypatch):
    from tests.test_sharing import _answer

    admin, member, _, slug, board, _, message = await _answer(app, monkeypatch, restricted=True)
    share = (await admin.post(f"/api/orgs/{slug}/messages/{message['id']}/share")).json()
    await admin.delete(f"/api/orgs/{slug}/messages/{message['id']}/share")
    req = (
        await member.post(
            f"/api/orgs/{slug}/collections/{board}/access-requests", json={"note": "memo"}
        )
    ).json()
    await admin.post(f"/api/orgs/{slug}/access-requests/{req['id']}/dismiss")
    again = (
        await member.post(f"/api/orgs/{slug}/collections/{board}/access-requests", json={})
    ).json()
    await admin.post(f"/api/orgs/{slug}/access-requests/{again['id']}/approve")
    va = (
        await admin.post(
            f"/api/orgs/{slug}/verified-answers",
            json={"message_id": message["id"], "collection_id": board},
        )
    ).json()
    await admin.patch(
        f"/api/orgs/{slug}/verified-answers/{va['id']}", json={"answer": "Heron: March."}
    )
    await admin.post(f"/api/orgs/{slug}/verified-answers/{va['id']}/reverify", json={})
    await admin.delete(f"/api/orgs/{slug}/verified-answers/{va['id']}")

    actions = _by_action(await _events(admin, slug))
    shared = actions["share.created"][0]
    assert shared["target_label"] == "When does Heron close?"
    assert shared["details"] == {"message_id": message["id"]}
    assert actions["share.revoked"][0]["target_id"] == shared["target_id"]
    dismissed = actions["access_request.dismissed"][0]
    assert dismissed["target_label"] == member.email
    assert dismissed["details"]["collection"] == "Board" and dismissed["details"]["note"] == "memo"
    assert actions["access_request.approved"][0]["target_id"] == again["id"]
    created = actions["verified_answer.created"][0]
    assert created["target_label"] == "When does Heron close?"
    assert created["details"]["collection"] == "Board"
    assert actions["verified_answer.updated"][0]["details"]["changes"] == {
        "answer": {"changed": True}
    }
    assert "verified_answer.reverified" in actions and "verified_answer.deleted" in actions
    assert share["token"] not in json.dumps(actions)


async def test_agent_changes_are_recorded_with_their_diff(app):
    owner = await _signup(app, "owner")
    slug = (await _org(owner, "Agent Co"))["slug"]
    body = {"name": "Helper", "instructions": "Be brief.", "mode": "quick", "collection_ids": []}
    agent = (await owner.post(f"/api/orgs/{slug}/agents", json=body)).json()
    r = await owner.patch(
        f"/api/orgs/{slug}/agents/{agent['id']}", json={"name": "Desk", "mode": "deep"}
    )
    assert r.status_code == 200, r.text
    assert (await owner.delete(f"/api/orgs/{slug}/agents/{agent['id']}")).status_code == 204
    events = await _events(owner, slug, action="agent")
    assert [e["action"] for e in events] == ["agent.deleted", "agent.updated", "agent.created"]
    changes = events[1]["details"]["changes"]
    assert changes["name"] == {"from": "Helper", "to": "Desk"}
    assert changes["mode"] == {"from": "quick", "to": "deep"}


async def test_a_change_that_fails_records_nothing(app):
    owner = await _signup(app, "owner")
    org = await _org(owner, "Atomic Co")
    slug = org["slug"]
    me = (await owner.get("/api/auth/me")).json()["user"]
    team = (await owner.post(f"/api/orgs/{slug}/collections", json={"name": "Team"})).json()
    # The event is added before the commit that fails, and goes with it.
    r = await owner.patch(f"/api/orgs/{slug}/collections/{team['id']}", json={"name": "General"})
    assert r.status_code == 409 and r.json()["code"] == "name_taken"
    r = await owner.patch(f"/api/orgs/{slug}/members/{me['id']}", json={"role": "admin"})
    assert r.status_code == 409
    assert [e["action"] for e in await _events(owner, slug)] == [
        "collection.created",
        "org.created",
    ]


# ── visibility, filters, pagination ──────────────────────────────────────────
async def test_only_admins_of_the_org_can_read_its_log(app):
    admin = await _signup(app, "admin")
    member = await _signup(app, "member")
    outsider = await _signup(app, "outsider")
    org = await _org(admin, "Private Log")
    slug = org["slug"]
    await _join(admin, member, slug)
    paths = ("/audit/access", "/audit/events", "/audit/export")
    for path in paths:
        assert (await member.get(f"/api/orgs/{slug}{path}")).status_code == 403
        assert (await outsider.get(f"/api/orgs/{slug}{path}")).status_code == 404

    for role, status in (("member", 403), ("admin", 200)):
        key = (
            await admin.post(f"/api/orgs/{slug}/api-keys", json={"name": role, "role": role})
        ).json()
        bot = await _client(app)
        bot.headers["Authorization"] = f"Bearer {key['key']}"
        for path in paths:
            assert (await bot.get(f"/api/orgs/{slug}{path}")).status_code == status, (role, path)
    exported = [e for e in await _events(admin, slug) if e["action"] == "audit.exported"]
    assert len(exported) == 1 and exported[0]["actor"]["api_key_name"] == "admin"
    assert exported[0]["channel"] == "api"


async def test_filters_and_cursor_pagination(app):
    owner = await _signup(app, "owner")
    org = await _org(owner, "Filter Co")
    slug = org["slug"]
    started = datetime.now(UTC)
    for i in range(5):
        r = await owner.post(f"/api/orgs/{slug}/collections", json={"name": f"Team {i}"})
        assert r.status_code == 201
    others = await _signup(app, "other")
    await _join(owner, others, slug)

    seen, cursor = [], None
    while True:
        params = {"limit": 2, "action": "collection", **({"cursor": cursor} if cursor else {})}
        page = (await owner.get(f"/api/orgs/{slug}/audit/events", params=params)).json()
        assert len(page["items"]) <= 2 and page["limit"] == 2
        seen += [e["target_label"] for e in page["items"]]
        if not (cursor := page["next_cursor"]):
            break
    assert seen == [f"Team {i}" for i in reversed(range(5))]

    assert {e["action"] for e in await _events(owner, slug, action="member")} == {"member.joined"}
    assert len(await _events(owner, slug, action="collection.created")) == 5
    assert await _events(owner, slug, action="collection.deleted") == []
    assert [e["target_label"] for e in await _events(owner, slug, q="team 3")] == ["Team 3"]
    other_id = (await others.get("/api/auth/me")).json()["user"]["id"]
    assert [e["action"] for e in await _events(owner, slug, actor_id=other_id)] == ["member.joined"]
    before = await _events(owner, slug, until=started.isoformat())
    assert [e["action"] for e in before] == ["org.created"]
    # Five collections, then the invite and its acceptance.
    assert len(await _events(owner, slug, since=started.isoformat())) == 7

    bad = await owner.get(f"/api/orgs/{slug}/audit/events", params={"cursor": "nope"})
    assert bad.status_code == 400 and bad.json()["code"] == "invalid_cursor"
    later = (started + timedelta(hours=1)).isoformat()
    r = await owner.get(
        f"/api/orgs/{slug}/audit/access", params={"since": later, "until": started.isoformat()}
    )
    assert r.status_code == 400 and r.json()["code"] == "invalid_range"
    r = await owner.get(f"/api/orgs/{slug}/audit/events", params={"action": "Drop%"})
    assert r.status_code == 422


# ── export ───────────────────────────────────────────────────────────────────
async def test_export_csv_and_jsonl_with_filters(app):
    alice, bob, a, b = await _two_orgs(app)
    await bob.post(f"/api/orgs/{b}/search", json={"query": "=HYPERLINK(1)", "org_slugs": [a]})
    await _ask(alice, a, "Which runway?")

    r = await alice.get(f"/api/orgs/{a}/audit/export", params={"format": "csv"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"].startswith(f'attachment; filename="strata-{a}-audit')
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [row["kind"] for row in rows] == ["search", "chat"]  # oldest first
    search = rows[0]
    assert search["query"] == "'=HYPERLINK(1)"  # never a live formula in a spreadsheet
    assert search["actor_email"] == bob.email and search["cross_org"] == "True"
    assert search["documents"] == "Runway A" and "Runway B" not in r.text
    assert rows[1]["documents"] == "Runway A (cited)" and rows[1]["cross_org"] == "False"

    r = await alice.get(f"/api/orgs/{a}/audit/export", params={"format": "jsonl", "kind": "chat"})
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in r.text.splitlines()]
    assert len(lines) == 1 and lines[0]["kind"] == "chat" and lines[0]["query"] == "Which runway?"

    r = await alice.get(f"/api/orgs/{a}/audit/export", params={"log": "events", "format": "jsonl"})
    events = [json.loads(line) for line in r.text.splitlines()]
    assert events[0]["action"] == "org.created"
    assert [e["action"] for e in events].count("audit.exported") == 3  # this one included

    r = await alice.get(f"/api/orgs/{a}/audit/export", params={"log": "events", "action": "audit"})
    header, *rest = list(csv.reader(io.StringIO(r.text)))
    assert header[:2] == ["time", "channel"] and "details" in header
    assert len(rest) == 4
    recorded = [e for e in await _events(alice, a, action="audit.exported")]
    assert recorded[0]["details"] == {
        "log": "events",
        "format": "csv",
        "filters": {"action": "audit"},
    }
    assert recorded[2]["details"]["filters"] == {"kind": "chat"}


# ── retention ────────────────────────────────────────────────────────────────
async def test_retention_purges_entries_past_the_window(app, monkeypatch):
    from sqlalchemy import func, select

    from app.core import audit
    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.models import AuditEvent, DataAccess

    owner = await _signup(app, "owner")
    org = await _org(owner, "Retention Co")
    old = datetime.now(UTC) - timedelta(days=400)
    actor = audit.Actor(user_id=None, name="Former", email="former@example.com")
    async with SessionLocal() as db:
        for when in (old, old + timedelta(days=10)):
            audit.add_event(db, uuid.UUID(org["id"]), actor, "audit.exported", "audit_log")
            db.add(
                DataAccess(
                    org_id=uuid.UUID(org["id"]),
                    kind="search",
                    query="old",
                    documents=[],
                    created_at=when,
                    **actor.columns(),
                )
            )
        await db.flush()
        await db.execute(
            AuditEvent.__table__.update()
            .where(AuditEvent.org_id == uuid.UUID(org["id"]), AuditEvent.actor_name == "Former")
            .values(created_at=old)
        )
        await db.commit()

        async def count(model) -> int:
            return await db.scalar(select(func.count()).where(model.org_id == uuid.UUID(org["id"])))

        monkeypatch.setattr(get_settings(), "audit_retention_days", 0)  # keep forever
        assert await audit.purge_expired(db) == 0
        monkeypatch.setattr(get_settings(), "audit_retention_days", 365)
        assert await audit.purge_expired(db) >= 4
        assert await count(DataAccess) == 0
        assert await count(AuditEvent) == 1  # org.created, from today
    assert [e["action"] for e in await _events(owner, org["slug"])] == ["org.created"]
