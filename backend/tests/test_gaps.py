"""Knowledge gaps and document requests: what the knowledge base didn't answer, grouped
by meaning; "answered in another org" only for people who may see that answer; and
the request → fulfil loop."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_agent_nvidia import FakeNvidia, _answer_turn, _tool_turn
from tests.test_api_integration import _client, _general, _ingest, _join, _org, _signup

pytestmark = pytest.mark.integration

SEARCHED = [
    {
        "id": "s1",
        "kind": "search",
        "label": "No sources found",
        "status": "done",
        "detail": {"queries": ["q"]},
    }
]
ABSTAINED = "I couldn't find that in the knowledge base."


def _missing(*facts) -> dict:
    return {
        "id": "c1",
        "kind": "reflect",
        "label": "Missing: " + "; ".join(facts),
        "status": "done",
        "detail": {"missing": list(facts)},
    }


async def _me(client) -> uuid.UUID:
    return uuid.UUID((await client.get("/api/auth/me")).json()["user"]["id"])


async def _ask(
    org: dict,
    user_id: uuid.UUID,
    question: str,
    answer: str = ABSTAINED,
    *,
    sources=(),
    trace=SEARCHED,
    feedback=None,
    days_ago: float = 0,
    status: str = "complete",
    cites=(),
) -> str:
    """A question and its answer, saved as the engine saves them; `cites` are the
    documents the answer cites (message_citations, as `_persist` writes them)."""
    from app.core.db import SessionLocal
    from app.models import Conversation, Message, MessageCitation

    org_id = uuid.UUID(org["id"])
    at = datetime.now(UTC) - timedelta(days=days_ago)
    async with SessionLocal() as db:
        conv = Conversation(
            org_id=org_id, user_id=user_id, title=question[:60], scope_org_ids=[org_id]
        )
        db.add(conv)
        await db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content=question, created_at=at))
        reply = Message(
            conversation_id=conv.id,
            role="assistant",
            content=answer,
            sources=list(sources),
            trace=list(trace),
            feedback=feedback,
            status=status,
            created_at=at + timedelta(seconds=1),
        )
        db.add(reply)
        await db.flush()
        for doc in cites:
            db.add(
                MessageCitation(
                    message_id=reply.id,
                    document_id=uuid.UUID(doc["id"]),
                    org_id=org_id,
                    source_id=uuid.UUID(doc["source_id"]),
                )
            )
        await db.commit()
        return str(reply.id)


async def _search(org: dict, user_id, query: str, hits: int, kind: str = "search") -> None:
    from app.core.db import SessionLocal
    from app.models import SearchEvent

    async with SessionLocal() as db:
        db.add(
            SearchEvent(
                org_id=uuid.UUID(org["id"]), user_id=user_id, kind=kind, query=query, hits=hits
            )
        )
        await db.commit()


def _ref(doc: dict, org: dict, ref: int = 1, origin: str = "knowledge") -> dict:
    return {
        "ref": ref,
        "origin": origin,
        "document_id": doc["id"] if origin == "knowledge" else None,
        "chunk_id": None,
        "attachment_id": None,
        "title": doc["title"],
        "url": None,
        "snippet": "",
        "org_id": org["id"],
        "org_slug": org["slug"],
        "org_name": org["name"],
        "org_hue": 24,
        "collection_name": "General",
        "source_kind": "upload",
        "score": 1.0,
    }


async def _documents(client, slug: str) -> dict[str, dict]:
    page = (await client.get(f"/api/orgs/{slug}/documents?limit=200")).json()
    return {d["title"]: d for d in page["items"]}


# ── gaps ─────────────────────────────────────────────────────────────────────
async def test_gaps_group_what_the_knowledge_base_did_not_answer(app):
    admin = await _signup(app, "gadmin")
    ann = await _signup(app, "gann")
    ben = await _signup(app, "gben")
    org = await _org(admin, "Gap Co")
    slug = org["slug"]
    await _join(admin, ann, slug)
    await _join(admin, ben, slug)
    a, b = await _me(ann), await _me(ben)
    general = _general(org, (await admin.get(f"/api/orgs/{slug}/collections")).json())
    await _ingest(admin, slug, general, {"deploys.md": "# Deploy policy\n\nManagers approve."})
    doc = (await _documents(admin, slug))["Deploy policy"]
    cited = _ref(doc, org)

    await _ask(org, a, "How long is parental leave?")
    await _ask(org, b, "how long is parental leave for fathers?", days_ago=2)
    await _ask(
        org,
        b,
        "How long is parental leave?",
        "Leave is covered in the handbook [1].",
        sources=[cited],
        trace=[*SEARCHED, _missing("leave length for fathers")],
        cites=[doc],
        days_ago=1,
    )
    await _ask(org, a, "How long is parental leave?", days_ago=45)  # outside the window
    await _ask(org, a, "How long is parental leave?", "", status="error")  # a failed run
    await _ask(org, a, "Thanks!", "You're welcome!", trace=[])  # small talk, no research
    await _ask(org, a, "Who approves deploys?", "Managers [1].", sources=[cited], cites=[doc])
    attached = _ref(doc, org, origin="attachment")
    await _ask(org, a, "Summarize my notes", "Your notes say so [1].", sources=[attached])
    await _search(org, a, "Who approves Friday deploys to production?", hits=0)
    await _search(org, b, "Who approves Friday deploys?", hits=0, kind="chat")  # via its answer
    await _search(org, b, "office wifi", hits=3)

    r = await admin.get(f"/api/orgs/{slug}/gaps")
    assert r.status_code == 200, r.text
    gaps = r.json()
    assert gaps["days"] == 30 and gaps["questions"] == 4 and gaps["truncated"] is False
    assert gaps["total_clusters"] == 2 and gaps["similarity"] == 0.5
    leave, deploys = gaps["clusters"]
    assert leave["label"] == "How long is parental leave?"
    assert leave["count"] == 3 and leave["askers"] == 2
    assert leave["reasons"] == {"unanswered": 2, "incomplete": 1, "downvoted": 0, "no_results": 0}
    assert leave["missing"] == ["leave length for fathers"]
    assert leave["examples"] == [
        "How long is parental leave?",
        "how long is parental leave for fathers?",
    ]
    first_seen = datetime.fromisoformat(leave["first_seen"])
    assert timedelta(days=1.9) < datetime.now(UTC) - first_seen < timedelta(days=2.1)
    assert leave["first_seen"] < leave["last_seen"]
    assert leave["answered_elsewhere"] == [] and leave["requests"] == []
    assert deploys["label"] == "Who approves Friday deploys to production?"
    assert deploys["count"] == 1 and deploys["reasons"]["no_results"] == 1

    wider = (await admin.get(f"/api/orgs/{slug}/gaps?days=60")).json()
    assert wider["clusters"][0]["count"] == 4
    assert wider["clusters"][0]["first_seen"] < leave["first_seen"]
    assert len((await admin.get(f"/api/orgs/{slug}/gaps?limit=1")).json()["clusters"]) == 1
    assert (await admin.get(f"/api/orgs/{slug}/gaps?days=0")).status_code == 422

    # A thumbs-down turns a cited answer into a gap.
    await _ask(org, a, "Who approves deploys?", "Managers [1].", sources=[cited], cites=[doc])
    down = await _ask(
        org, b, "Who approves deploys?", "Managers [1].", sources=[cited], cites=[doc]
    )
    r = await ben.post(f"/api/orgs/{slug}/messages/{down}/feedback", json={"feedback": "down"})
    assert r.status_code == 204
    gaps = (await admin.get(f"/api/orgs/{slug}/gaps")).json()
    (downvoted,) = [c for c in gaps["clusters"] if c["reasons"]["downvoted"]]
    assert (
        downvoted["reasons"]["downvoted"] == 1 and "Who approves deploys?" in downvoted["examples"]
    )


async def test_gaps_are_for_the_orgs_admins_only(app):
    admin = await _signup(app, "giadmin")
    member = await _signup(app, "gimember")
    outsider = await _signup(app, "gioutsider")
    org = await _org(admin, "Gap Iso")
    other = await _org(outsider, "Gap Elsewhere")
    await _join(admin, member, org["slug"])
    await _ask(org, await _me(member), "Where is the travel policy?")
    await _ask(other, await _me(outsider), "What is our dental plan?")

    r = await member.get(f"/api/orgs/{org['slug']}/gaps")
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert (await outsider.get(f"/api/orgs/{org['slug']}/gaps")).status_code == 404
    mine = (await admin.get(f"/api/orgs/{org['slug']}/gaps")).json()
    theirs = (await outsider.get(f"/api/orgs/{other['slug']}/gaps")).json()
    assert [c["label"] for c in mine["clusters"]] == ["Where is the travel policy?"]
    assert [c["label"] for c in theirs["clusters"]] == ["What is our dental plan?"]

    for role, status in (("member", 403), ("admin", 200)):
        key = await admin.post(
            f"/api/orgs/{org['slug']}/api-keys", json={"name": role, "role": role}
        )
        client = await _client(app)
        client.headers["Authorization"] = f"Bearer {key.json()['key']}"
        assert (await client.get(f"/api/orgs/{org['slug']}/gaps")).status_code == status


async def test_the_engines_coverage_check_and_abstentions_become_gaps(app, monkeypatch):
    """Real runs of the agent (scripted model): the coverage check's verdicts are read
    from the trace the engine writes, including a later check clearing an earlier one."""
    from app.agent import engine, sufficiency
    from app.api import chat
    from app.core.config import get_settings
    from tests.test_agent_loop import FakeClient
    from tests.test_agent_loop import _answer_turn as claude_answer
    from tests.test_agent_loop import _tool_turn as claude_tool

    admin = await _signup(app, "gengine")
    org = await _org(admin, "Gap Engine")
    slug = org["slug"]
    general = _general(org, (await admin.get(f"/api/orgs/{slug}/collections")).json())
    policy = "# Deploy policy\n\nFriday deploys need manager approval."
    await _ingest(admin, slug, general, {"deploys.md": policy})

    verdicts: list[dict] = []

    async def fake_check(**kwargs):
        return verdicts.pop(0)

    async def no_title(**kwargs):
        raise RuntimeError("no title model in tests")

    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "sufficiency_check", "always")
    monkeypatch.setattr(sufficiency, "structured_call", fake_check)
    monkeypatch.setattr(chat, "structured_call", no_title)

    async def ask(question: str, turns, checks):
        verdicts[:] = checks
        monkeypatch.setattr(engine, "get_client", lambda: FakeClient(turns))
        conv = (await admin.post(f"/api/orgs/{slug}/conversations", json={})).json()
        r = await admin.post(
            f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
            json={"content": question},
        )
        assert r.status_code == 200, r.text
        assert r.json()["message"]["status"] == "complete"
        return r.json()["message"]

    cited = "Friday deploys need manager approval [1]."
    gap = await ask(
        "Who approves weekend deploys?",
        [claude_tool("weekend deploy"), claude_answer(cited)],
        [{"sufficient": False, "missing": ["who approves weekend deploys"]}],
    )
    assert gap["sources"] and "[1]" in gap["content"]
    await ask(  # missing after the first round, found after the second: not a gap
        "Do Friday deploys need approval?",
        [claude_tool("friday deploy"), claude_tool("deploy approval"), claude_answer(cited)],
        [{"sufficient": False, "missing": ["approval rule"]}, {"sufficient": True, "missing": []}],
    )
    await ask(
        "What is the office wifi password?",
        [claude_tool("wifi password"), claude_answer(ABSTAINED)],
        [{"sufficient": False, "missing": ["the wifi password"]}],
    )

    gaps = (await admin.get(f"/api/orgs/{slug}/gaps")).json()
    by_label = {c["label"]: c for c in gaps["clusters"]}
    assert set(by_label) == {"Who approves weekend deploys?", "What is the office wifi password?"}
    weekend = by_label["Who approves weekend deploys?"]
    assert weekend["reasons"]["incomplete"] == 1 and weekend["reasons"]["unanswered"] == 0
    assert weekend["missing"] == ["who approves weekend deploys"]
    wifi = by_label["What is the office wifi password?"]
    assert wifi["reasons"]["unanswered"] == 1 and wifi["missing"] == ["the wifi password"]


async def test_an_answer_from_an_empty_knowledge_base_is_a_gap(app):
    """No model configured and nothing indexed: the retrieval-only answer finds nothing.
    Its chat search event (0 hits) isn't counted a second time."""
    admin = await _signup(app, "gempty")
    org = await _org(admin, "Gap Empty")
    conv = (await admin.post(f"/api/orgs/{org['slug']}/conversations", json={})).json()
    r = await admin.post(
        f"/api/orgs/{org['slug']}/conversations/{conv['id']}/messages?stream=false",
        json={"content": "What is the expense limit for travel?"},
    )
    assert "couldn't find anything" in r.json()["message"]["content"]
    gaps = (await admin.get(f"/api/orgs/{org['slug']}/gaps")).json()
    (gap,) = gaps["clusters"]
    assert gap["label"] == "What is the expense limit for travel?" and gap["count"] == 1
    assert gap["reasons"]["unanswered"] == 1


# ── answered in another org ──────────────────────────────────────────────────
B_QUESTION = "How long is parental leave here?"
B_SECRETS = ("Bravo", B_QUESTION, "sixteen weeks", "leave handbook")


async def _answered_in_b(app, monkeypatch, *, restricted: bool = False):
    """Org A has a gap; org B (another admin) answered a similar question well, citing
    its leave handbook (in General, or in a restricted HR collection)."""
    from app.agent import engine
    from app.core.config import get_settings

    x = await _signup(app, f"gx{uuid.uuid4().hex[:6]}")
    y = await _signup(app, f"gy{uuid.uuid4().hex[:6]}")
    a = await _org(x, "Alpha Gaps")
    b = await _org(y, "Bravo Hidden Co")
    await _ask(a, await _me(x), "How long is parental leave?")
    target = _general(b, (await y.get(f"/api/orgs/{b['slug']}/collections")).json())
    if restricted:
        hr = await y.post(
            f"/api/orgs/{b['slug']}/collections",
            json={"name": "HR Private", "visibility": "restricted", "allowed_user_ids": []},
        )
        target = hr.json()["id"]
    await _ingest(
        y,
        b["slug"],
        target,
        {"leave.md": "# Bravo leave handbook\n\nStaff get 16 weeks of parental leave."},
    )
    fake = FakeNvidia(
        [
            _tool_turn("search_knowledge", '{"queries": ["parental leave"]}'),
            _answer_turn("Staff get sixteen weeks of parental leave [1]."),
        ]
    )
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake.stream_chat)
    conv = (await y.post(f"/api/orgs/{b['slug']}/conversations", json={})).json()
    r = await y.post(
        f"/api/orgs/{b['slug']}/conversations/{conv['id']}/messages?stream=false",
        json={"content": B_QUESTION},
    )
    message = r.json()["message"]
    assert message["status"] == "complete" and message["sources"], message
    return x, y, a, b, message, target


def _leaks(text: str, b: dict) -> list[str]:
    return [s for s in (*B_SECRETS, b["slug"]) if s.lower() in text.lower()]


async def _hints(client, slug: str):
    r = await client.get(f"/api/orgs/{slug}/gaps")
    assert r.status_code == 200, r.text
    (gap,) = r.json()["clusters"]
    assert gap["label"] == "How long is parental leave?"
    return gap["answered_elsewhere"], r.text


async def test_an_admin_outside_org_b_learns_nothing_about_it(app, monkeypatch):
    x, y, a, b, message, general_b = await _answered_in_b(app, monkeypatch)

    hints, text = await _hints(x, a["slug"])
    assert hints == [] and _leaks(text, b) == []

    # Joining B doesn't open y's private conversations to them…
    await _join(y, x, b["slug"])
    hints, text = await _hints(x, a["slug"])
    assert hints == [] and _leaks(text, b) == []
    # …but once y shares the answer by link (and x can read the handbook), x sees it.
    share = await y.post(f"/api/orgs/{b['slug']}/messages/{message['id']}/share")
    assert share.status_code in (200, 201), share.text
    hints, _ = await _hints(x, a["slug"])
    (hint,) = hints
    assert hint["kind"] == "answer" and hint["org"]["slug"] == b["slug"]
    assert hint["question"] == B_QUESTION and hint["verified_answer_id"] is None
    assert hint["answer"] == "Staff get sixteen weeks of parental leave."
    assert hint["similarity"] >= 0.5
    (doc,) = hint["documents"]
    assert doc["title"] == "Bravo leave handbook" and doc["org_slug"] == b["slug"]
    assert doc["collection_name"] == "General"

    # An API key is bound to its org: through it, B doesn't exist.
    key = await x.post(f"/api/orgs/{a['slug']}/api-keys", json={"name": "ci", "role": "admin"})
    bot = await _client(app)
    bot.headers["Authorization"] = f"Bearer {key.json()['key']}"
    hints, text = await _hints(bot, a["slug"])
    assert hints == [] and _leaks(text, b) == []

    # B can opt out of cross-org reads.
    patch = {"settings": {"allow_cross_org": False}}
    assert (await y.patch(f"/api/orgs/{b['slug']}", json=patch)).status_code == 200
    hints, text = await _hints(x, a["slug"])
    assert hints == [] and _leaks(text, b) == []
    patch = {"settings": {"allow_cross_org": True}}
    assert (await y.patch(f"/api/orgs/{b['slug']}", json=patch)).status_code == 200

    # B's verified answer is a hint of its own; a thumbs-down takes the chat answer out.
    r = await y.post(
        f"/api/orgs/{b['slug']}/verified-answers",
        json={"message_id": message["id"], "collection_id": general_b},
    )
    assert r.status_code == 201, r.text
    verified = r.json()
    hints, _ = await _hints(x, a["slug"])
    assert sorted(h["kind"] for h in hints) == ["answer", "verified"]
    r = await y.post(
        f"/api/orgs/{b['slug']}/messages/{message['id']}/feedback", json={"feedback": "down"}
    )
    assert r.status_code == 204
    hints, _ = await _hints(x, a["slug"])
    assert [h["kind"] for h in hints] == ["verified"]
    assert hints[0]["verified_answer_id"] == verified["id"]
    assert [d["title"] for d in hints[0]["documents"]] == ["Bravo leave handbook"]

    # Leaving B takes it all away again.
    me = await _me(x)
    assert (await y.delete(f"/api/orgs/{b['slug']}/members/{me}")).status_code == 204
    hints, text = await _hints(x, a["slug"])
    assert hints == [] and _leaks(text, b) == []


async def test_a_member_of_org_b_needs_the_cited_collection(app, monkeypatch):
    x, y, a, b, message, hr = await _answered_in_b(app, monkeypatch, restricted=True)
    share = await y.post(f"/api/orgs/{b['slug']}/messages/{message['id']}/share")
    assert share.status_code in (200, 201), share.text
    await _join(y, x, b["slug"])
    hints, text = await _hints(x, a["slug"])
    assert hints == [] and _leaks(text, b) == []

    r = await y.patch(
        f"/api/orgs/{b['slug']}/collections/{hr}", json={"allowed_user_ids": [str(await _me(x))]}
    )
    assert r.status_code == 200, r.text
    hints, _ = await _hints(x, a["slug"])
    assert [d["collection_name"] for h in hints for d in h["documents"]] == ["HR Private"]


# ── document requests ────────────────────────────────────────────────────────
async def test_document_request_lifecycle(app):
    admin = await _signup(app, "dradmin")
    member = await _signup(app, "drmember")
    other = await _signup(app, "drother")
    outsider = await _signup(app, "droutsider")
    org = await _org(admin, "Request Co")
    slug = org["slug"]
    await _join(admin, member, slug)
    await _join(admin, other, slug)
    general = _general(org, (await admin.get(f"/api/orgs/{slug}/collections")).json())
    board = (
        await admin.post(
            f"/api/orgs/{slug}/collections",
            json={"name": "Board", "visibility": "restricted", "allowed_user_ids": []},
        )
    ).json()["id"]
    await _ingest(
        admin,
        slug,
        general,
        {"leave.md": "# Leave policy\n\n16 weeks.", "old.md": "# Old leave policy\n\n12 weeks."},
    )
    await _ingest(admin, slug, board, {"minutes.md": "# Board minutes\n\nApproved."})
    docs = await _documents(admin, slug)
    r = await admin.patch(
        f"/api/orgs/{slug}/documents/{docs['Old leave policy']['id']}", json={"hidden": True}
    )
    assert r.status_code == 200
    elsewhere = await _org(outsider, "Elsewhere Co")
    elsewhere_general = _general(
        elsewhere, (await outsider.get(f"/api/orgs/{elsewhere['slug']}/collections")).json()
    )
    await _ingest(outsider, elsewhere["slug"], elsewhere_general, {"x.md": "# Foreign doc\n\nx"})
    foreign = (await _documents(outsider, elsewhere["slug"]))["Foreign doc"]
    answer = await _ask(org, await _me(member), "How long is parental leave?")

    base = f"/api/orgs/{slug}/doc-requests"
    r = await member.post(
        base,
        json={"title": "Parental leave policy", "note": "For new parents", "message_id": answer},
    )
    assert r.status_code == 201, r.text
    req = r.json()
    assert req["status"] == "pending" and req["question"] == "How long is parental leave?"
    assert req["note"] == "For new parents" and req["message_id"] == answer
    assert req["requester"]["email"] == member.email and req["conversation_id"]
    assert req["document"] is None and req["decided_at"] is None

    # Asking again for the same thing returns the open request.
    again = await member.post(base, json={"title": "  parental LEAVE policy? "})
    assert again.status_code == 200 and again.json()["id"] == req["id"]
    # Only your own answers can be attached; outsiders can't ask at all.
    r = await other.post(base, json={"title": "Leave", "message_id": answer})
    assert r.status_code == 404
    assert (await outsider.post(base, json={"title": "Leave"})).status_code == 404
    r = await member.post(base, json={"title": "   "})
    assert r.status_code == 422

    # Requesters follow their own; only admins see the queue and decide.
    mine = (await member.get(f"{base}/mine")).json()
    assert mine["total"] == 1 and mine["items"][0]["id"] == req["id"]
    assert (await other.get(f"{base}/mine")).json()["total"] == 0
    assert (await member.get(base)).status_code == 403
    fulfil = f"{base}/{req['id']}/fulfill"
    leave = docs["Leave policy"]
    assert (await member.post(fulfil, json={"document_id": leave["id"]})).status_code == 403
    queue = (await admin.get(base)).json()
    assert [i["id"] for i in queue["items"]] == [req["id"]] and queue["items"][0][
        "conversation_id"
    ] is None

    # The document must be this org's, in search, and readable by the requester.
    r = await admin.post(fulfil, json={"document_id": foreign["id"]})
    assert r.status_code == 404
    r = await admin.post(fulfil, json={"document_id": str(uuid.uuid4())})
    assert r.status_code == 404
    r = await admin.post(fulfil, json={"document_id": docs["Old leave policy"]["id"]})
    assert r.status_code == 400 and r.json()["code"] == "document_hidden"
    r = await admin.post(fulfil, json={"document_id": docs["Board minutes"]["id"]})
    assert r.status_code == 400 and r.json()["code"] == "requester_cannot_read"
    # Another org's admin can't reach this request.
    r = await outsider.post(
        f"/api/orgs/{elsewhere['slug']}/doc-requests/{req['id']}/fulfill",
        json={"document_id": foreign["id"]},
    )
    assert r.status_code == 404

    r = await admin.post(fulfil, json={"document_id": leave["id"], "note": "Added the handbook"})
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["status"] == "fulfilled" and done["resolution"] == "Added the handbook"
    assert (
        done["document"]["title"] == "Leave policy" and done["decided_by"]["email"] == admin.email
    )
    r = await admin.post(fulfil, json={"document_id": leave["id"]})
    assert r.status_code == 409 and r.json()["code"] == "already_decided"
    r = await admin.post(f"{base}/{req['id']}/dismiss")
    assert r.status_code == 409 and r.json()["code"] == "already_decided"
    (seen,) = (await member.get(f"{base}/mine")).json()["items"]
    assert seen["status"] == "fulfilled" and seen["document"]["id"] == leave["id"]
    assert (await admin.get(base)).json()["items"] == []
    assert [i["id"] for i in (await admin.get(f"{base}?status=fulfilled")).json()["items"]] == [
        req["id"]
    ]

    # Dismissing closes a request (with a note, or none); then it can be asked again.
    first = (await member.post(base, json={"title": "Board minutes"})).json()
    r = await admin.post(f"{base}/{first['id']}/dismiss", json={"note": "Ask your manager"})
    assert r.json()["status"] == "dismissed" and r.json()["resolution"] == "Ask your manager"
    second = await member.post(base, json={"title": "Board minutes"})
    assert second.status_code == 201 and second.json()["id"] != first["id"]
    r = await admin.post(f"{base}/{second.json()['id']}/dismiss")
    assert r.status_code == 200 and r.json()["resolution"] is None
    statuses = [i["status"] for i in (await member.get(f"{base}/mine")).json()["items"]]
    assert statuses == ["dismissed", "dismissed", "fulfilled"]
    assert (await admin.get(f"{base}?status=all")).json()["total"] == 3
    # Each decision is in the audit log, with who asked.
    log = (await admin.get(f"/api/orgs/{slug}/audit/events?action=doc_request")).json()["items"]
    assert sorted(e["action"] for e in log) == [
        "doc_request.dismissed", "doc_request.dismissed", "doc_request.fulfilled",
    ]  # fmt: skip
    done = next(e for e in log if e["action"] == "doc_request.fulfilled")
    assert done["details"]["requester"] == member.email
    assert done["details"]["note"] == "Added the handbook"

    # Someone who left the org can't be answered any more.
    left = (await other.post(base, json={"title": "Expense policy"})).json()
    assert (await admin.delete(f"/api/orgs/{slug}/members/{await _me(other)}")).status_code == 204
    r = await admin.post(f"{base}/{left['id']}/fulfill", json={"document_id": leave["id"]})
    assert r.status_code == 409 and r.json()["code"] == "not_a_member"


async def test_the_same_request_sent_twice_at_once_returns_the_first(app, monkeypatch):
    """Two identical requests racing: the second insert hits the unique index, rolls back
    and returns the first (200), not a 500."""
    from app.api import doc_requests

    admin = await _signup(app, f"ga{uuid.uuid4().hex[:6]}")
    member = await _signup(app, f"gm{uuid.uuid4().hex[:6]}")
    slug = (await _org(admin, "Race Co"))["slug"]
    await _join(admin, member, slug)
    base = f"/api/orgs/{slug}/doc-requests"
    first = await member.post(base, json={"title": "Travel policy"})
    assert first.status_code == 201

    real, calls = doc_requests._open_request, []

    async def racing(ctx, key):  # the other request isn't visible yet at the first check
        calls.append(key)
        return None if len(calls) == 1 else await real(ctx, key)

    monkeypatch.setattr(doc_requests, "_open_request", racing)
    again = await member.post(base, json={"title": "Travel policy"})
    assert again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"] and len(calls) == 2


async def test_requests_attach_to_their_gap_and_fulfilling_addresses_it(app):
    admin = await _signup(app, "dlink")
    member = await _signup(app, "dlinkm")
    org = await _org(admin, "Linked Gaps")
    slug = org["slug"]
    await _join(admin, member, slug)
    general = _general(org, (await admin.get(f"/api/orgs/{slug}/collections")).json())
    await _ingest(admin, slug, general, {"leave.md": "# Leave policy\n\n16 weeks."})
    leave_doc = (await _documents(admin, slug))["Leave policy"]
    me = await _me(member)
    answer = await _ask(org, me, "How long is parental leave?", days_ago=1)
    await _ask(org, me, "How long is parental leave?", days_ago=1)
    await _ask(org, me, "Who approves Friday deploys?", days_ago=1)
    base = f"/api/orgs/{slug}/doc-requests"
    req = (await member.post(base, json={"title": "Leave handbook", "message_id": answer})).json()

    clusters = (await admin.get(f"/api/orgs/{slug}/gaps")).json()["clusters"]
    leave, deploys = clusters
    assert [r["id"] for r in leave["requests"]] == [req["id"]] and not leave["addressed"]
    assert deploys["requests"] == []

    # An admin can file one straight from a gap, with its question.
    r = await admin.post(base, json={"title": "Deploy runbook", "question": deploys["label"]})
    filed = r.json()
    assert r.status_code == 201 and filed["question"] == "Who approves Friday deploys?"
    clusters = (await admin.get(f"/api/orgs/{slug}/gaps")).json()["clusters"]
    assert [r["title"] for r in clusters[1]["requests"]] == ["Deploy runbook"]

    # Fulfilled after the last question: the gap is addressed and sorts last.
    r = await admin.post(f"{base}/{req['id']}/fulfill", json={"document_id": leave_doc["id"]})
    assert r.status_code == 200
    clusters = (await admin.get(f"/api/orgs/{slug}/gaps")).json()["clusters"]
    assert [c["label"] for c in clusters] == ["Who approves Friday deploys?", leave["label"]]
    assert clusters[1]["addressed"] and clusters[1]["requests"][0]["status"] == "fulfilled"
    # Asked again since: the document didn't settle it.
    await _ask(org, me, "How long is parental leave?")
    clusters = (await admin.get(f"/api/orgs/{slug}/gaps")).json()["clusters"]
    assert clusters[0]["label"] == leave["label"] and not clusters[0]["addressed"]


@pytest.mark.parametrize(
    ("trace", "researched"),
    [
        ([{"kind": "search", "detail": {"queries": ["leave"]}}], True),
        ([{"kind": "records", "detail": {"record_type": "vendor_record"}}], True),
        ([{"kind": "scan", "detail": {"queries": ["Is it a PHI vendor?"], "scanned": 60}}], True),
        ([{"kind": "read", "detail": {}}], True),
        # "What collections are there?" lists; it doesn't look for an answer.
        ([{"kind": "search", "detail": {"listing": True}}], False),
        ([{"kind": "plan", "detail": {}}, {"kind": "answer", "detail": {}}], False),
    ],
)
async def test_what_counts_as_researching_an_answer(app, trace, researched):
    from sqlalchemy import func, literal, select
    from sqlalchemy.dialects.postgresql import JSONB

    from app.core.db import SessionLocal
    from app.gaps import signals

    async with SessionLocal() as db:
        found = await db.scalar(
            select(func.jsonb_path_exists(literal(trace, JSONB), signals._path(signals.RESEARCHED)))
        )
    assert found is researched
