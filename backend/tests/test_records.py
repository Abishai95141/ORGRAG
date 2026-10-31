"""Structured records: schemas found in documents, extraction with offsets, exact
queries within the caller's permissions, and the agent's query_records tool."""

import uuid
from types import SimpleNamespace as NS

import pytest

from app.records.tables import find_structure
from app.records.values import infer_type, parse_date, parse_number
from tests.test_api_integration import _general, _ingest, _join, _org, _signup

VENDOR = """# Vendor record: {name}

| Field | Value |
|---|---|
| Vendor | {name} |
| Risk tier | {risk} |
| Business associate agreement | **{baa}** — reviewed by Procurement |
| Annual cost | ${cost} |
| Contract renewal | {renewal} |

## Notes

{name} supplies services to the clinics.
"""
VENDORS = [
    ("Basalt Analytics", "High", "Executed", "96,000", "March 31, 2027"),
    ("Cinder Data", "Low", "Expired", "12,500", "January 15, 2027"),
    ("Harbor Cloud", "High", "Executed", "48,000", "October 1, 2026"),
    ("Umber Works", "Medium", "Not required", "8,000", "June 30, 2027"),
]
REGISTER = """# Team directory

| Code | Team | Engineering manager |
|---|---|---|
| DP | Data Plane | Priya Raman |
| PLAT | Platform | Oluwaseun Adeyemi |
| IDB | Identity & Billing | Harriet Vance |
| OBS | Observability | Tom Whitfield |
"""


def _vendor_files() -> dict[str, str]:
    return {
        f"{n.lower().replace(' ', '-')}.md": VENDOR.format(name=n, risk=r, baa=b, cost=c, renewal=d)
        for n, r, b, c, d in VENDORS
    }


def test_values_and_offsets():
    doc = VENDOR.format(
        **dict(zip(["name", "risk", "baa", "cost", "renewal"], VENDORS[0], strict=True))
    )
    (table,) = find_structure(doc).tables
    assert table.vertical
    cell = table.rows[2][1]
    assert doc[cell.start : cell.end] == cell.text == "**Executed** — reviewed by Procurement"
    assert parse_number("$96,000") == 96000 and parse_number("INC-2264") is None
    assert parse_date("Jul 9") is None and parse_date("Jul 9", 2026) == "2026-07-09"
    assert infer_type(["Low", "High", "Low", "Medium", "High"])["type"] == "choice"


@pytest.mark.integration
async def test_schemas_are_suggested_activated_extracted_and_queried(app):
    admin = await _signup(app, f"ra{uuid.uuid4().hex[:6]}")
    member = await _signup(app, f"rm{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Records Co")
    slug = org["slug"]
    await _join(admin, member, slug)
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    general = _general(org, cols)
    await _ingest(admin, slug, general, {**_vendor_files(), "teams.md": REGISTER})

    # Members can't run suggestions; admins get one schema per structure.
    url = f"/api/orgs/{slug}/collections/{general}/record-schemas/suggest"
    assert (await member.post(url)).status_code == 403
    found = {s["name"]: s for s in (await admin.post(url)).json()}
    vendor, teams = found["vendor_record"], found["team_directory"]
    assert vendor["origin"] == "fields_table" and vendor["key_field"] == "vendor"
    assert vendor["stats"]["records"] == 4 and vendor["status"] == "suggested"
    types = {f["name"]: f for f in vendor["fields"]}
    assert types["risk_tier"]["type"] == "choice" and types["annual_cost"]["type"] == "number"
    assert types["contract_renewal"]["type"] == "date"
    assert teams["origin"] == "rows_table" and teams["key_field"] == "code"
    # Suggested schemas are admin-only and can't be queried yet.
    assert [s["name"] for s in (await member.get(f"/api/orgs/{slug}/record-schemas")).json()] == []

    # Activating queues extraction; the worker extracts with offsets.
    r = await admin.patch(
        f"/api/orgs/{slug}/record-schemas/{vendor['id']}", json={"status": "active"}
    )
    assert r.status_code == 200 and r.json()["status"] == "active"
    await _run_jobs()
    q = f"/api/orgs/{slug}/records/query"
    body = {
        "schema": "vendor_record",
        "filters": [{"field": "risk_tier", "op": "eq", "value": "high"}],
    }
    out = (await member.post(q, json=body)).json()
    assert out["total"] == 2 and {r["key"] for r in out["rows"]} == {
        "basalt analytics",
        "harbor cloud",
    }
    row = out["rows"][0]
    assert row["document"]["title"].startswith("Vendor record:")
    assert row["provenance"]["risk_tier"]["text"] == "High"
    assert row["values"]["annual_cost"] in (96000, 48000)

    # Dates, numbers, counts and groups are exact.
    renewals = {"schema": "vendor_record", "filters": [
        {"field": "contract_renewal", "op": "gte", "value": "2027-01-01"},
        {"field": "contract_renewal", "op": "lte", "value": "March 31, 2027"},
    ]}  # fmt: skip
    assert (await member.post(q, json=renewals)).json()["total"] == 2
    cheap = {"schema": "vendor_record", "mode": "count",
             "filters": [{"field": "annual_cost", "op": "lt", "value": "$20,000"}]}  # fmt: skip
    assert (await member.post(q, json=cheap)).json()["total"] == 2
    by_baa = {
        "schema": "vendor_record",
        "mode": "group",
        "group_by": "business_associate_agreement",
    }
    groups = (await member.post(q, json=by_baa)).json()
    assert {g["value"]: g["count"] for g in groups["groups"]} == {
        "Executed": 2, "Expired": 1, "Not required": 1,
    }  # fmt: skip
    severe = [{"field": "risk_tier", "op": "eq", "value": "Severe"}]
    r = await member.post(q, json={"schema": "vendor_record", "filters": severe})
    assert r.status_code == 400 and "High, Low, Medium" in r.json()["detail"]
    r = await member.post(q, json={"schema": "vendor_record", "filters": [{"field": "nope"}]})
    assert r.status_code == 400 and r.json()["code"] == "invalid_query"
    assert (await member.post(q, json={"schema": "team_directory"})).status_code == 404  # inactive

    # A hidden document's records drop out of every query.
    docs = (await admin.get(f"/api/orgs/{slug}/documents?q=Basalt")).json()
    basalt = next(d for d in docs["items"] if d["title"] == "Vendor record: Basalt Analytics")
    await admin.patch(f"/api/orgs/{slug}/documents/{basalt['id']}", json={"hidden": True})
    assert (await member.post(q, json=body)).json()["total"] == 1

    # Archiving removes the records; members no longer see the type.
    await admin.patch(
        f"/api/orgs/{slug}/record-schemas/{vendor['id']}", json={"status": "archived"}
    )
    assert (await member.post(q, json=body)).status_code == 404


@pytest.mark.integration
async def test_records_follow_collection_permissions(app):
    admin = await _signup(app, f"rp{uuid.uuid4().hex[:6]}")
    member = await _signup(app, f"rq{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Board Co")
    slug = org["slug"]
    await _join(admin, member, slug)
    board = (
        await admin.post(
            f"/api/orgs/{slug}/collections",
            json={"name": "Board", "visibility": "restricted", "allowed_user_ids": []},
        )
    ).json()["id"]
    await _ingest(admin, slug, board, _vendor_files())
    (vendor,) = [
        s
        for s in (
            await admin.post(f"/api/orgs/{slug}/collections/{board}/record-schemas/suggest")
        ).json()
        if s["name"] == "vendor_record"
    ]
    await admin.patch(f"/api/orgs/{slug}/record-schemas/{vendor['id']}", json={"status": "active"})
    await _run_jobs()
    q = {"schema": "vendor_record", "mode": "count"}
    assert (await admin.post(f"/api/orgs/{slug}/records/query", json=q)).json()["total"] == 4
    # The member can't read Board: no schema, no records, not even a count.
    assert (await member.get(f"/api/orgs/{slug}/record-schemas")).json() == []
    assert (await member.post(f"/api/orgs/{slug}/records/query", json=q)).status_code == 404
    rows = (
        await admin.post(f"/api/orgs/{slug}/records/query", json={"schema": "vendor_record"})
    ).json()
    assert (
        await member.get(f"/api/orgs/{slug}/records/{rows['rows'][0]['id']}")
    ).status_code == 404


@pytest.mark.integration
async def test_only_changed_documents_are_extracted_again(app):
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Document, RecordRun, RecordSchema
    from app.records.extract import extract_schema

    admin = await _signup(app, f"rx{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Delta Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    await _ingest(admin, slug, _general(org, cols), _vendor_files())
    suggest = f"/api/orgs/{slug}/collections/{_general(org, cols)}/record-schemas/suggest"
    (vendor,) = [s for s in (await admin.post(suggest)).json() if s["name"] == "vendor_record"]
    await admin.patch(f"/api/orgs/{slug}/record-schemas/{vendor['id']}", json={"status": "active"})
    await _run_jobs()
    async with SessionLocal() as db:
        schema = await db.get(RecordSchema, uuid.UUID(vendor["id"]))
        stats = await extract_schema(db, schema)
        assert stats["extracted"] == 0 and stats["unchanged"] == 4  # nothing changed
        doc = (
            await db.execute(
                select(Document).where(
                    Document.title == "Vendor record: Umber Works",
                    Document.org_id == uuid.UUID(org["id"]),
                )
            )
        ).scalar_one()
        doc.content = doc.content.replace("| Medium |", "| High |")
        doc.content_hash = "changed"
        await db.commit()
        stats = await extract_schema(db, schema)
        assert stats["extracted"] == 1 and stats["unchanged"] == 3
        run = await db.get(RecordRun, (schema.id, doc.id))
        assert run.content_hash == "changed" and run.records == 1
    high = {"schema": "vendor_record", "mode": "count",
            "filters": [{"field": "risk_tier", "op": "eq", "value": "High"}]}  # fmt: skip
    assert (await admin.post(f"/api/orgs/{slug}/records/query", json=high)).json()["total"] == 3


@pytest.mark.integration
async def test_the_agent_queries_records_and_cites_them(app, monkeypatch):
    from app.models import RecordSchema
    from tests.test_agent_loop import FakeClient, _answer_turn

    admin = await _signup(app, f"rt{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Agent Records")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    general = _general(org, cols)
    await _ingest(admin, slug, general, _vendor_files())
    (vendor,) = [
        s for s in (
            await admin.post(f"/api/orgs/{slug}/collections/{general}/record-schemas/suggest")
        ).json() if s["name"] == "vendor_record"
    ]  # fmt: skip
    await admin.patch(f"/api/orgs/{slug}/record-schemas/{vendor['id']}", json={"status": "active"})
    await _run_jobs()

    call = NS(
        type="tool_use",
        id="toolu_r",
        name="query_records",
        input={
            "record_type": "vendor_record",
            "filters": [{"field": "business_associate_agreement", "op": "eq", "value": "Executed"}],
        },
    )
    answer = "Two vendors have an executed BAA: Basalt Analytics [1] and Harbor Cloud [2]."
    fake = FakeClient([([], NS(stop_reason="tool_use", content=[call])), _answer_turn(answer)])
    from app.agent import engine
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(engine, "get_client", lambda: fake)
    conv = (await admin.post(f"/api/orgs/{slug}/conversations", json={})).json()
    r = await admin.post(
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
        json={"content": "Which vendors have an executed BAA?"},
    )
    message = r.json()["message"]
    assert message["status"] == "complete"
    # The tool is offered, the prompt lists the record type, and the result is exact.
    first = fake.calls[0]
    assert "query_records" in [t["name"] for t in first["tools"]]
    assert "vendor_record" in first["system"][0]["text"]
    result = fake.calls[1]["messages"][-1]["content"][0]["content"]
    assert "2 matching record(s)" in result and "Exact" in result
    assert "| Basalt Analytics |" in result and "[1]" in result and "[2]" in result
    titles = {s["title"] for s in message["sources"]}
    assert titles == {"Vendor record: Basalt Analytics", "Vendor record: Harbor Cloud"}
    steps = [s for s in message["trace"] if s["kind"] == "records"]
    assert steps[0]["label"] == "Found 2 records"
    assert RecordSchema  # imported for the tables the tool reads


def test_list_results_carry_exact_tallies():
    """A breakdown ("10 pending, 6 expired") comes from code, not from the model counting
    table rows; filtered fields (constant) and free text get no tally."""
    from app.agent.records_tool import _tallies
    from app.records.query import Row

    fields = [
        {"name": "vendor", "label": "Vendor", "type": "text"},
        {"name": "baa", "label": "BAA", "type": "choice", "options": ["Executed", "Expired"]},
        {"name": "phi", "label": "Processes PHI", "type": "boolean"},
        {"name": "tier", "label": "Risk tier", "type": "choice", "options": ["High", "Low"]},
    ]
    values = [("Pending", True, "High"), ("Expired", True, "Low"), ("Pending", True, None)]
    rows = [
        Row(uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), "t", None, f"v{i}",
            {"vendor": f"v{i}", "baa": b, "phi": p, **({"tier": t} if t else {})}, {})
        for i, (b, p, t) in enumerate(values)
    ]  # fmt: skip
    lines = _tallies(rows, fields, skip={"phi"})
    assert lines[1] == "Tallies over all 3 records:"
    assert "- BAA: Pending 2, Expired 1" in lines
    assert "- Risk tier: (blank) 1, High 1, Low 1" in lines
    assert not any("Processes PHI" in x or "Vendor" in x for x in lines)
    assert _tallies(rows[:1], fields, skip=set()) == []  # one value each: nothing to break down


async def test_prose_extractions_are_grouped_typed_and_located(monkeypatch):
    """LangExtract's output (aligned extractions) becomes records: values grouped by
    their `record` attribute, typed, with the offsets LangExtract aligned."""
    import langextract

    from app.records import extract

    text = (
        "Our courier is Parcelwise, renewing on March 31, 2027 at a cost of $14,000. "
        "Parcelwise never sees patient data. Shredding goes to Ironleaf, renewing on "
        "June 1, 2027; Dana Cole owns the contract."
    )

    def span(s, cls, record, value=None):
        start = text.index(s)
        return NS(
            extraction_class=cls,
            extraction_text=s,
            attributes={"record": record, **({"value": value} if value else {})},
            char_interval=NS(start_pos=start, end_pos=start + len(s)),
        )

    found = [
        span("Parcelwise", "vendor", "Parcelwise"),
        span("March 31, 2027", "renewal", "Parcelwise"),
        span("$14,000", "annual_cost", "Parcelwise"),
        # Prose doesn't say "No": the model's reading types it, the words stay the evidence.
        span("never sees patient data", "phi", "Parcelwise", value="no"),
        span("Ironleaf", "vendor", "Ironleaf"),
        span("June 1, 2027", "renewal", "Ironleaf"),
        # A text value narrows to the reading when it's written inside the span…
        span("Dana Cole owns the contract", "owner", "Ironleaf", value="Dana Cole"),
        # …and a reading that isn't in the text never becomes a value.
        span("Shredding", "service", "Ironleaf", value="Secure destruction"),
        NS(extraction_class="renewal", extraction_text="unaligned", attributes={},
           char_interval=None),  # not aligned to the text: dropped
    ]  # fmt: skip
    calls = []
    monkeypatch.setattr(
        langextract, "extract", lambda **kw: calls.append(kw) or NS(extractions=found)
    )
    schema = NS(
        name="vendor",
        label="Vendor",
        key_field="vendor",
        fields=[
            {"name": "vendor", "label": "Vendor", "type": "text"},
            {"name": "renewal", "label": "Renewal", "type": "date"},
            {"name": "annual_cost", "label": "Annual cost", "type": "number"},
            {"name": "phi", "label": "Processes PHI", "type": "boolean"},
            {"name": "owner", "label": "Owner", "type": "text"},
            {"name": "service", "label": "Service", "type": "text"},
        ],
    )
    doc = NS(content=text, title="Suppliers", source_updated_at=None)
    records = {r.key: r for r in extract._from_prose_sync(doc, schema, NS(max_workers=2))}
    assert set(records) == {"parcelwise", "ironleaf"}
    assert records["parcelwise"].values == {
        "vendor": "Parcelwise", "renewal": "2027-03-31", "annual_cost": 14000, "phi": False,
    }  # fmt: skip
    p = records["parcelwise"].provenance["renewal"]
    assert text[p["start"] : p["end"]] == "March 31, 2027"
    assert records["parcelwise"].provenance["phi"]["text"] == "never sees patient data"
    iron = records["ironleaf"]
    assert iron.values["owner"] == "Dana Cole" and iron.values["service"] == "Shredding"
    o = iron.provenance["owner"]
    assert text[o["start"] : o["end"]] == "Dana Cole"
    # The prompt and the worked example come from the schema itself.
    example = calls[0]["examples"][0]
    assert all(e.extraction_text in example.text for e in example.extractions)
    assert "Use the exact words from the text" in calls[0]["prompt_description"]


async def _run_jobs():
    from app.core.db import SessionLocal
    from app.worker.jobs import HANDLERS
    from app.worker.queue import claim_next, finish

    async with SessionLocal() as db:
        while job := await claim_next(db):
            await HANDLERS[job.kind](db, job)
            await finish(db, job)
