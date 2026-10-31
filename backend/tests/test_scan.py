"""The scan tool: every document matching a filter checked one by one (the per-document
model call is faked; everything else is real), inside the caller's permissions, with
only grounded matches kept, cited and costed, and refused when the filter matches more
documents than the limit."""

import re
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace as NS

import pytest

from app.agent import scan_tool
from tests.test_api_integration import _general, _ingest, _join, _org, _signup

POSTMORTEM = """# Postmortem: {name}

## Summary

{summary}

## Timeline

The on-call engineer was paged at 09:12 and service was restored by 10:05.
"""
INCIDENTS = {
    "Ingest outage": "The ingest API went down because its TLS certificate expired on a Sunday.",
    "Billing delay": "Invoices were late because a queue consumer ran out of memory.",
    "Auth errors": "Logins failed after the identity provider's signing certificate expired.",
    "Search slowness": "Search was slow after an index rebuild; no certificate was involved.",
}
QUESTION = "Does this postmortem say an expired certificate caused the incident?"
NO = {"match": False, "evidence": "", "answer": ""}
VERDICTS = {
    "Postmortem: Ingest outage": {
        "match": True,
        "evidence": "The ingest API went down because its TLS certificate expired on a Sunday.",
        "answer": "TLS certificate expired",
    },
    # Copied with a curly apostrophe and no final stop: still the document's words.
    "Postmortem: Auth errors": {
        "match": True,
        "evidence": "Logins failed after the identity provider’s signing certificate expired",
        "answer": "IdP signing certificate expired",
    },
    # A claimed match whose quote isn't in the document: dropped.
    "Postmortem: Billing delay": {
        "match": True,
        "evidence": "The billing service certificate expired at midnight.",
        "answer": "billing certificate",
    },
    "Postmortem: Webhook failures": {
        "match": True,
        "evidence": "Webhooks failed when the partner certificate expired.",
        "answer": "partner certificate",
    },
    "Board briefing": {
        "match": True,
        "evidence": "An expired certificate is the board's top operational risk.",
        "answer": "board risk",
    },
}
USAGE = {"input": 1000, "output": 40}  # per check: $0.00012 at Haiku 5.5 prices


def _files(names=INCIDENTS) -> dict[str, str]:
    return {
        f"{name.lower().replace(' ', '-')}.md": POSTMORTEM.format(name=name, summary=summary)
        for name, summary in names.items()
    }


class FakeChecks:
    """Stands in for the one model call per document: answers by document title and
    reports token usage like the real call."""

    def __init__(self, verdicts=VERDICTS):
        self.verdicts = verdicts
        self.prompts: dict[str, str] = {}

    async def __call__(self, *, model, system, prompt, schema, on_usage=None, **_):
        assert schema is scan_tool.SCHEMA and "word for word" in system
        title = re.search(r'<document title="([^"]*)"', prompt).group(1)
        self.prompts[title] = prompt
        if on_usage is not None:
            on_usage(model, dict(USAGE))
        return self.verdicts.get(title, NO)


async def _company(app):
    """Admin and member; General holds four postmortems and a hidden one; Board (no
    grants) holds a briefing that would match."""
    admin = await _signup(app, f"sa{uuid.uuid4().hex[:6]}")
    member = await _signup(app, f"sm{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Scan Co")
    slug = org["slug"]
    await _join(admin, member, slug)
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    general = _general(org, cols)
    hidden = {"Webhook failures": "Webhooks failed when the partner certificate expired."}
    await _ingest(admin, slug, general, {**_files(), **_files(hidden)})
    board = (
        await admin.post(
            f"/api/orgs/{slug}/collections",
            json={"name": "Board", "visibility": "restricted", "allowed_user_ids": []},
        )
    ).json()["id"]
    await _ingest(
        admin,
        slug,
        board,
        {"briefing.md": "# Board briefing\n\nAn expired certificate is the board's top "
         "operational risk."},
    )  # fmt: skip
    docs = (await admin.get(f"/api/orgs/{slug}/documents?q=Webhook")).json()["items"]
    webhook = next(d for d in docs if d["title"] == "Postmortem: Webhook failures")
    await admin.patch(f"/api/orgs/{slug}/documents/{webhook['id']}", json={"hidden": True})
    return admin, member, org, general, board


async def _scope(client, org):
    from app.core.db import SessionLocal
    from app.models import Organization
    from app.retrieval.access import resolve_scope

    me = (await client.get("/api/auth/me")).json()["user"]
    async with SessionLocal() as db:
        primary = await db.get(Organization, uuid.UUID(org["id"]))
        return await resolve_scope(db, user_id=uuid.UUID(me["id"]), primary_org=primary)


async def _scan(scope, flt=None, **kwargs):
    from app.agent.citations import CitationRegistry
    from app.core.db import SessionLocal

    async with SessionLocal() as db:
        registry = CitationRegistry()
        result = await scan_tool.scan(
            db,
            scope,
            registry,
            kwargs.pop("question", QUESTION),
            flt or scan_tool.ScanFilter(collection_ids=scope.collection_ids),
            **kwargs,
        )
        return result, registry


@pytest.fixture
def fake(monkeypatch):
    from app.core.config import get_settings

    checks = FakeChecks()
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(scan_tool, "structured_call", checks)
    return checks


def test_quotes_must_be_the_documents_words():
    row = "| Business associate agreement | **Executed** — signed May 15, 2024 |"
    body = f"{row}\nAT&T’s backup line"
    assert scan_tool.grounded("Business associate agreement | Executed — signed", body)
    assert scan_tool.grounded("business associate agreement Executed - signed May 15", body)
    assert scan_tool.grounded('"AT&amp;T\'s backup line"', body)  # escaped and re-quoted
    assert scan_tool.grounded("Business associate ... signed May 15, 2024", body)
    assert scan_tool.grounded("signed May 15 ... Business associate", body) is None  # order
    assert scan_tool.grounded("Business associate agreement | Pending", body) is None
    assert scan_tool.grounded("Executed", body) is None  # too short to show anything
    assert scan_tool.grounded("", body) is None


def test_full_search_results_point_to_scan():
    """Search is capped at top_k passages; when it returns that many, the result itself
    says so and names scan (the prompt alone wasn't enough, see docs/LOG.md D59)."""
    from app.agent.citations import CitationRegistry
    from app.agent.tools import ToolBox
    from app.retrieval.access import RetrievalScope

    box = ToolBox(None, RetrievalScope(), CitationRegistry(), top_k=8, scan_enabled=True)
    assert "scan, which checks every matching document" in box._capped_note(8)
    assert box._capped_note(7) == ""  # fewer than top_k: search found everything it could
    assert ToolBox(None, RetrievalScope(), CitationRegistry())._capped_note(8) == ""
    both = ToolBox(None, RetrievalScope(), CitationRegistry(), graph=True, scan_enabled=True)
    assert "scan, which checks" in both._capped_note(8) and "list_entities" in both._capped_note(8)


@pytest.mark.integration
async def test_the_agent_scans_every_readable_document_and_cites_grounded_matches(
    app, monkeypatch, fake
):
    from sqlalchemy import select

    from app.agent import engine
    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.models import Chunk, Message
    from tests.test_agent_loop import FakeClient, _answer_turn

    _, member, org, _, _ = await _company(app)
    slug = org["slug"]
    call = NS(type="tool_use", id="toolu_s", name="scan", input={"question": QUESTION})
    answer = (
        "Two of the 4 postmortems checked blame an expired certificate:\n"
        "- Ingest outage [1]\n- Auth errors [2]"
    )
    client = FakeClient([([], NS(stop_reason="tool_use", content=[call])), _answer_turn(answer)])
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(engine, "get_client", lambda: client)
    conv = (await member.post(f"/api/orgs/{slug}/conversations", json={})).json()
    r = await member.post(
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
        json={"content": "Which incidents were caused by an expired certificate?"},
    )
    message = r.json()["message"]
    assert message["status"] == "complete"

    # The tool and its guidance are offered alongside search.
    first = client.calls[0]
    assert {"search_knowledge", "scan"} <= {t["name"] for t in first["tools"]}
    assert "use scan when" in first["system"][0]["text"]
    # Every readable, visible document was checked; the hidden one and the restricted
    # collection's never reached the model.
    assert set(fake.prompts) == {
        "Postmortem: Ingest outage", "Postmortem: Billing delay",
        "Postmortem: Auth errors", "Postmortem: Search slowness",
    }  # fmt: skip
    assert QUESTION in fake.prompts["Postmortem: Auth errors"]
    result = client.calls[1]["messages"][-1]["content"][0]["content"]
    assert "Checked 4 documents" in result and "2 matches" in result
    assert "Complete: every matching document in scope was checked" in result
    assert "1 claimed match was dropped" in result  # the billing quote isn't in its document
    assert '<source ref="1"' in result and '<source ref="2"' in result
    assert "TLS certificate expired" in result and "billing service" not in result

    # The matches are the answer's sources, each opening on the chunk with its quote.
    by_title = {s["title"]: s for s in message["sources"]}
    assert set(by_title) == {"Postmortem: Ingest outage", "Postmortem: Auth errors"}
    async with SessionLocal() as db:
        for title, s in by_title.items():
            chunk = await db.get(Chunk, uuid.UUID(s["chunk_id"]))
            assert "certificate expired" in chunk.content, title
        usage = (await db.execute(select(Message.usage).where(Message.id == uuid.UUID(
            message["id"])))).scalar_one()  # fmt: skip
    # The trace step says what was checked and found.
    (step,) = [s for s in message["trace"] if s["kind"] == "scan"]
    assert step["label"] == "Found 2 of 4" and step["status"] == "done"
    assert step["detail"]["scanned"] == 4 and step["detail"]["matched"] == 2
    assert step["detail"]["unverified"] == 1 and step["detail"]["failed"] == 0
    assert step["detail"]["queries"] == [QUESTION] and sorted(step["detail"]["refs"]) == [1, 2]
    # The four checks are part of the answer's usage and cost.
    haiku = usage["claude-haiku-5-5"]
    assert haiku["calls"] == 4 and haiku["input"] == 4000 and haiku["output"] == 160
    assert haiku["cost_usd"] == pytest.approx(0.00048)


@pytest.mark.integration
async def test_a_refused_scan_gives_its_turn_back(app, monkeypatch, fake):
    """Quick mode has 4 turns; refusing a too-wide scan costs one SQL count, so the
    agent gets one more turn to narrow it (once per answer)."""
    from app.agent import engine
    from app.core.config import get_settings
    from tests.test_agent_loop import FakeClient, _answer_turn, _tool_turn

    _, member, org, _, _ = await _company(app)
    slug = org["slug"]

    def scan(**args):
        call = NS(type="tool_use", id="toolu_s", name="scan", input={"question": QUESTION, **args})
        return [], NS(stop_reason="tool_use", content=[call])

    client = FakeClient(
        [
            scan(limit=1),  # 4 documents match: refused
            scan(limit=3, must_mention=["certificate"]),
            _tool_turn("certificate incidents"),
            _tool_turn("expired certificate"),
            _answer_turn("Ingest outage [1] and Auth errors [2] (3 postmortems checked)."),
        ]
    )
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(engine, "get_client", lambda: client)
    conv = (await member.post(f"/api/orgs/{slug}/conversations", json={})).json()
    r = await member.post(
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
        json={"content": "Which incidents were caused by an expired certificate?"},
    )
    message = r.json()["message"]
    assert message["status"] == "complete" and len(client.calls) == 5
    assert "tool_choice" not in client.calls[3]  # the 4th turn could still use tools
    assert client.calls[4]["tool_choice"] == {"type": "none"}
    refused = client.calls[1]["messages"][-1]["content"][0]["content"]
    assert "4 documents match" in refused and "none were checked" in refused
    labels = [s["label"] for s in message["trace"] if s["kind"] == "scan"]
    assert labels == ["4 documents match, too many to scan", "Found 2 of 3"]


@pytest.mark.integration
async def test_scans_see_only_what_the_caller_can_read(app, fake):
    from app.agent.citations import CitationRegistry
    from app.agent.tools import ToolBox, ToolInputError
    from app.core.db import SessionLocal

    admin, member, org, _, _ = await _company(app)

    # An admin reads Board too; hidden documents stay out for everyone.
    result, registry = await _scan(await _scope(admin, org))
    assert result.checked == 5 and "Postmortem: Webhook failures" not in fake.prompts
    assert {m.title for m in result.matches} == {
        "Postmortem: Ingest outage", "Postmortem: Auth errors", "Board briefing",
    }  # fmt: skip
    assert len(registry) == 3 and result.unverified == 1 and result.complete

    # A member can't name the restricted collection to reach it.
    fake.prompts.clear()
    scope = await _scope(member, org)
    assert all(c.name != "Board" for c in scope.collections.values())
    async with SessionLocal() as db:
        box = ToolBox(db, scope, CitationRegistry())
        with pytest.raises(ToolInputError, match="No collection named 'Board'"):
            await box.scan({"question": QUESTION, "collections": ["Board"]})
        result = await box.scan({"question": QUESTION, "collections": ["general"]})
    assert result.checked == 4 and "Board briefing" not in fake.prompts


@pytest.mark.integration
async def test_a_filter_matching_too_many_documents_scans_nothing(app, fake, monkeypatch):
    from app.core.config import get_settings

    admin, _, org, general, _ = await _company(app)
    scope = await _scope(admin, org)
    only_general = scan_tool.ScanFilter(collection_ids=[uuid.UUID(general)])

    result, registry = await _scan(scope, only_general, limit=3)
    assert result.refused and result.total == 4 and result.checked == 0
    assert fake.prompts == {} and len(registry) == 0 and not result.complete
    note = result.note()
    assert "4 documents match" in note and "limit of 3" in note and "General 4" in note
    assert "must_mention" in note

    # Narrowed by a keyword every match must contain, it fits and runs.
    narrowed = scan_tool.ScanFilter(
        collection_ids=[uuid.UUID(general)],
        must_mention=["certificates"],  # stemmed: matches "certificate"
    )
    result, _ = await _scan(scope, narrowed, limit=3)
    assert result.total == 3 and result.checked == 3
    assert "Postmortem: Billing delay" not in fake.prompts
    assert {m.title for m in result.matches} == {
        "Postmortem: Ingest outage", "Postmortem: Auth errors",
    }  # fmt: skip

    # SCAN_MAX_DOCUMENTS caps whatever limit the caller asks for.
    monkeypatch.setattr(get_settings(), "scan_max_documents", 2)
    fake.prompts.clear()
    result, _ = await _scan(scope, narrowed, limit=100)
    assert result.refused and result.limit == 2 and fake.prompts == {}


@pytest.mark.integration
async def test_title_date_and_keyword_filters(app, fake):
    from sqlalchemy import update

    from app.core.db import SessionLocal
    from app.models import Document

    admin, _, org, general, _ = await _company(app)
    scope = await _scope(admin, org)
    async with SessionLocal() as db:
        for title, day in (("Ingest outage", 3), ("Auth errors", 20), ("Search slowness", 28)):
            await db.execute(
                update(Document)
                .where(
                    Document.title == f"Postmortem: {title}",
                    Document.org_id == uuid.UUID(org["id"]),
                )
                .values(source_updated_at=datetime(2026, 10, day, 12, tzinfo=UTC))
            )
        await db.commit()
    ids = [uuid.UUID(general)]

    titled = scan_tool.ScanFilter(collection_ids=ids, title_contains="AUTH")
    assert (await _scan(scope, titled))[0].total == 1
    october = scan_tool.ScanFilter(
        collection_ids=ids,
        updated_after=scan_tool.parse_day("2026-10-03", "updated_after"),  # inclusive
        updated_before=scan_tool.parse_day("2026-10-20", "updated_before"),  # inclusive
    )
    result, _ = await _scan(scope, october)
    assert result.total == 2 and "edited 2026-10-03 to 2026-10-20" in result.note()
    phrase = scan_tool.ScanFilter(collection_ids=ids, must_mention=['"signing certificate"'])
    assert (await _scan(scope, phrase))[0].total == 1
    either = scan_tool.ScanFilter(collection_ids=ids, must_mention=["memory or rebuild"])
    assert (await _scan(scope, either))[0].total == 2
    nothing = scan_tool.ScanFilter(collection_ids=ids, must_mention=["kubernetes"])
    result, _ = await _scan(scope, nothing)
    assert result.total == 0 and "No documents match" in result.note()

    with pytest.raises(scan_tool.ScanError, match="no searchable words"):
        await _scan(scope, scan_tool.ScanFilter(collection_ids=ids, must_mention=["the"]))
    with pytest.raises(scan_tool.ScanError, match="YYYY-MM-DD"):
        scan_tool.parse_day("last week", "updated_after")
    with pytest.raises(scan_tool.ScanError, match="question"):
        await _scan(scope, titled, question="  ")


@pytest.mark.integration
async def test_long_documents_are_read_through_their_most_relevant_chunks(app, fake):
    from app.core.db import SessionLocal
    from app.models import Chunk

    admin = await _signup(app, f"sl{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Long Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    filler = [
        f"## Maintenance window {i}\n\n"
        + f"Routine patching of rack {i} finished on schedule; crews replaced fans, "
        f"reseated cables and logged readings for row {i}. " * 6
        for i in range(40)
    ]
    root = "## Root cause\n\nThe outage began when the wildcard certificate expired at 02:00."
    body = "# Operations log 2026\n\nThe yearly operations log.\n\n" + "\n\n".join(filler)
    body += "\n\n" + root + "\n"
    assert len(body) > 2 * scan_tool.DOC_CHARS
    await _ingest(admin, slug, _general(org, cols), {"ops.md": body})
    fake.verdicts = {
        "Operations log 2026": {
            "match": True,
            "evidence": "The outage began when the wildcard certificate expired at 02:00.",
            "answer": "wildcard certificate expired",
        }
    }
    result, registry = await _scan(await _scope(admin, org))

    prompt = fake.prompts["Operations log 2026"]
    shown = prompt.split(">\n", 2)[-1]
    # Not cut at the first N characters: the root cause at the very end is in, with the
    # opening, and gaps are marked; the whole stays within the per-document budget.
    assert 'excerpt="' in prompt and "wildcard certificate expired" in shown
    assert "The yearly operations log." in shown and "[…]" in shown
    assert len(shown) < scan_tool.DOC_CHARS + 2000
    (match,) = result.matches
    async with SessionLocal() as db:
        cited = await db.get(Chunk, registry.get(match.ref).hit.chunk_id)
    assert "wildcard certificate expired" in cited.content


@pytest.mark.integration
async def test_failed_checks_make_the_scan_incomplete(app, fake, monkeypatch):
    admin, _, org, general, _ = await _company(app)
    scope = await _scope(admin, org)
    real = fake.__call__

    async def flaky(**kwargs):
        if "Billing delay" in kwargs["prompt"]:
            raise TimeoutError
        return await real(**kwargs)

    monkeypatch.setattr(scan_tool, "structured_call", flaky)
    result, _ = await _scan(scope, scan_tool.ScanFilter(collection_ids=[uuid.UUID(general)]))
    assert result.checked == 4 and result.failed == 1 and not result.complete
    assert "1 document couldn't be checked" in result.note()
    assert "Complete" not in result.note()

    async def down(**kwargs):
        raise TimeoutError

    monkeypatch.setattr(scan_tool, "structured_call", down)
    with pytest.raises(scan_tool.ScanError, match="None of the 4 documents"):
        await _scan(scope, scan_tool.ScanFilter(collection_ids=[uuid.UUID(general)]))
