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
    assert parse_number("$186,000 (2026)") == 186000  # a year alongside is a note
    assert parse_number("1 hour 25 minutes") is None  # two quantities: not one number
    assert parse_number("$5,000 to $25,000") is None
    durations = ["47 minutes", "2 hours 10 minutes", "5 hours", "1 day", "3 days"]
    assert infer_type(durations)["type"] == "text"
    assert infer_type(["12 months", "24 months", "36 months", "12 months"])["type"] == "number"
    owners = ["Kenji Watanabe", "Marcus Bell", "Kenji Watanabe", "Rachel Moore", "Marcus Bell"]
    assert infer_type(owners)["type"] == "text"  # the next new owner must not be dropped
    assert infer_type(["Active", "Terminated", "Active", "Active"])["type"] == "choice"
    assert parse_date("Jul 9") is None and parse_date("Jul 9", 2026) == "2026-07-09"
    assert infer_type(["Low", "High", "Low", "Medium", "High"])["type"] == "choice"
    # Field names start with a letter, as the API requires ("2FA enabled" → f_2fa_enabled).
    from app.records.tables import normalize

    assert normalize("2FA enabled") == "f_2fa_enabled" and normalize("Risk tier") == "risk_tier"


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

    # One schema, with extraction state; members see only active ones.
    one = f"/api/orgs/{slug}/record-schemas/{vendor['id']}"
    detail = (await member.get(one)).json()
    assert detail["extracting"] is False and detail["failures"] == []
    assert detail["stats"]["records"] == 4 and "samples" not in detail["stats"]
    assert (await member.get(f"/api/orgs/{slug}/record-schemas/{teams['id']}")).status_code == 404
    rec = {r["key"]: r["id"] for r in out["rows"]}
    r = await member.get(f"/api/orgs/{slug}/records/{rec['harbor cloud']}")
    assert r.status_code == 200 and r.json()["key"] == "harbor cloud"
    hidden = await member.get(f"/api/orgs/{slug}/records/{rec['basalt analytics']}")
    assert hidden.status_code == 404  # its document is hidden

    # Renaming is audited like any other edit.
    r = await admin.patch(one, json={"label": "Vendor file"})
    assert r.status_code == 200 and r.json()["label"] == "Vendor file"

    # Archiving removes the records; members no longer see the type.
    await admin.patch(
        f"/api/orgs/{slug}/record-schemas/{vendor['id']}", json={"status": "archived"}
    )
    assert (await member.post(q, json=body)).status_code == 404
    assert (await member.get(one)).status_code == 404

    # The audit log has the admin's changes and the member's reads.
    events = (await admin.get(f"/api/orgs/{slug}/audit/events?action=record_schema")).json()
    actions = [e["action"] for e in events["items"]]
    assert actions.count("record_schema.activated") == 1
    assert actions.count("record_schema.archived") == 1
    assert "record_schema.suggested" in actions
    activated = next(e for e in events["items"] if e["action"] == "record_schema.activated")
    assert activated["details"]["changes"]["status"] == {"from": "suggested", "to": "active"}
    renamed = next(e for e in events["items"] if e["action"] == "record_schema.updated")
    assert renamed["details"]["changes"]["label"]["to"] == "Vendor file"
    assert renamed["target_label"] == "Vendor file"
    member_id = (await member.get("/api/auth/me")).json()["user"]["id"]
    reads = (await admin.get(f"/api/orgs/{slug}/audit/access?actor_id={member_id}")).json()
    searches = [e for e in reads["items"] if e["kind"] == "search"]
    assert any(e["query"] == "records vendor_record: risk_tier eq 'high'" for e in searches)
    assert {d["title"] for e in searches for d in e["documents"]} >= {
        "Vendor record: Basalt Analytics", "Vendor record: Harbor Cloud",
    }  # fmt: skip
    assert any(e["kind"] == "read" for e in reads["items"])  # GET /records/{id}
    # Every query that returned records is logged (a count returns its records too);
    # a group-by and failed queries return none.
    assert len(searches) == 4


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
    # An active type saved under another name (as an earlier suggester named it) is the
    # same structure: suggesting again returns it, not a duplicate under the new name.
    async with SessionLocal() as db:
        (await db.get(RecordSchema, uuid.UUID(vendor["id"]))).name = "general"
        await db.commit()
    again = (await admin.post(suggest)).json()
    same = [s for s in again if s["origin"] == "fields_table"]
    assert [(s["id"], s["name"], s["status"]) for s in same] == [
        (vendor["id"], "general", "active")
    ]


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
    # The fields the answer needs become the columns; the rest are named as not shown.
    narrow = NS(
        type="tool_use",
        id="toolu_f",
        name="query_records",
        input={**call.input, "fields": ["annual_cost"]},
    )
    typo = NS(
        type="tool_use", id="toolu_t", name="query_records",
        input={"record_type": "vendor_record", "fields": ["cost"]},
    )  # fmt: skip
    answer = "Two vendors have an executed BAA: Basalt Analytics [1] and Harbor Cloud [2]."
    fake = FakeClient(
        [
            ([], NS(stop_reason="tool_use", content=[call])),
            ([], NS(stop_reason="tool_use", content=[narrow, typo])),
            _answer_turn(answer),
        ]
    )
    from app.agent import engine
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(engine, "get_client", lambda: fake)
    # The coverage check reads the records too, not only search passages.
    from app.agent import sufficiency

    checked = []

    async def fake_check(**kwargs):
        checked.append(kwargs["prompt"])
        return {"sufficient": True, "missing": []}

    monkeypatch.setattr(get_settings(), "sufficiency_check", "always")
    monkeypatch.setattr(sufficiency, "structured_call", fake_check)
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
    narrowed, bad = (c["content"] for c in fake.calls[2]["messages"][-1]["content"])
    header = next(line for line in narrowed.splitlines() if line.startswith("| Vendor"))
    assert header.count("|") == 5 and "Annual cost" in header  # key, filter, asked, Source
    assert "Not shown: " in narrowed and "risk_tier" in narrowed.split("Not shown: ")[1]
    assert "No field cost in vendor_record" in bad
    assert checked and "| Basalt Analytics |" in checked[0]
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


def test_llm_schemas_read_register_tables_row_by_row(monkeypatch):
    """A prose schema's documents can hold a register: a table whose columns name the
    key field and another field is read exactly (every row, under the right column) and
    hidden from the model, which reads the rest. In other tables, a value the model puts
    under a column named for a different field is dropped."""
    import langextract

    from app.records import extract

    text = (
        "# Contracts\n\n"
        "| Vendor | Auto-Renew | Notice Period (days) | Customer Data Access |\n"
        "|---|---|---|---|\n"
        "| Stripe | Yes | 30 | Yes |\n"
        "| Figma | No | 60 | No |\n\n"
        "| Supplier | Termination for convenience |\n"
        "|---|---|\n"
        "| Okta | No |\n\n"
        "Stripe's agreement is governed by the laws of Delaware.\n"
    )
    schema = NS(
        name="contract_terms", label="Contract terms", key_field="vendor", origin="llm",
        fields=[
            {"name": "vendor", "label": "Vendor", "type": "text"},
            {"name": "auto_renew", "label": "Renews automatically", "type": "boolean"},
            {"name": "notice_days", "label": "Notice period (days)", "type": "number"},
            {"name": "termination_for_convenience", "label": "Termination for convenience",
             "type": "boolean"},
            {"name": "governing_law", "label": "Governing law", "type": "text"},
        ],
    )  # fmt: skip
    assert extract.column_field("Notice Period (days)", schema)["name"] == "notice_days"
    assert extract.column_field("Auto-Renew", schema)["name"] == "auto_renew"
    assert extract.column_field("Customer Data Access", schema) is None
    assert extract.column_field("Renewal Date", schema) is None  # not "renewal price cap"
    doc = NS(content=text, title="Contracts", source_updated_at=None)
    structure = find_structure(text)
    rows, spans = extract.from_tables(doc, structure, schema)
    assert {r.key: r.values for r in rows} == {
        "stripe": {"vendor": "Stripe", "auto_renew": True, "notice_days": 30},
        "figma": {"vendor": "Figma", "auto_renew": False, "notice_days": 60},
    }
    assert all(r.method == "rows_table" for r in rows)
    p = rows[0].provenance["notice_days"]
    assert text[p["start"] : p["end"]] == "30"
    assert len(spans) == 1  # the Supplier table has no key column: the model reads it
    rest = extract._blank(text, spans)
    assert len(rest) == len(text) and "Stripe | Yes" not in rest and "Okta" in rest

    def span(s, cls, record, nth=0, value=None):
        start = -1
        for _ in range(nth + 1):
            start = text.index(s, start + 1)
        return NS(
            extraction_class=cls, extraction_text=s,
            attributes={"record": record, **({"value": value} if value else {})},
            char_interval=NS(start_pos=start, end_pos=start + len(s)),
        )  # fmt: skip

    okta_no = text.index("| Okta | No |") + len("| Okta | ")
    found = [
        span("Okta", "vendor", "Okta"),
        # The "No" under "Termination for convenience" isn't auto-renewal: dropped…
        NS(extraction_class="auto_renew", extraction_text="No", attributes={"record": "Okta"},
           char_interval=NS(start_pos=okta_no, end_pos=okta_no + 2)),
        # …and is kept for the field its column names.
        NS(extraction_class="termination_for_convenience", extraction_text="No",
           attributes={"record": "Okta"}, char_interval=NS(start_pos=okta_no, end_pos=okta_no + 2)),
        span("the laws of Delaware", "governing_law", "Stripe", value="Delaware"),
    ]  # fmt: skip
    calls = []
    monkeypatch.setattr(
        langextract, "extract", lambda **kw: calls.append(kw) or NS(extractions=found)
    )
    prose = extract._from_prose_sync(doc, schema, NS(max_workers=1), rest, structure, spans)
    assert calls[0]["text_or_documents"] == rest
    merged = {r.key: r for r in extract._merge(rows, prose)}
    assert merged["okta"].values == {"vendor": "Okta", "termination_for_convenience": False}
    assert merged["stripe"].values["governing_law"] == "Delaware"  # prose fills the row in
    assert merged["stripe"].values["notice_days"] == 30
    # A document that is only a register needs no model call.
    only = "| Vendor | Auto-Renew |\n|---|---|\n| Stripe | Yes |\n"
    _, only_spans = extract.from_tables(NS(content=only, title="", source_updated_at=None),
                                        find_structure(only), schema)  # fmt: skip
    assert not extract._blank(only, only_spans).strip()


async def _run_jobs():
    from app.core.db import SessionLocal
    from app.worker.jobs import HANDLERS
    from app.worker.queue import claim_next, finish

    async with SessionLocal() as db:
        while job := await claim_next(db):
            await HANDLERS[job.kind](db, job)
            await finish(db, job)


DIRECTORY = """# Team directory and change approvers

| Code | Team | Engineering manager | Change approver | Delegate |
|---|---|---|---|---|
| PLAT | Platform | Oluwaseun Adeyemi | Oluwaseun Adeyemi | Kofi Mensah |
| DP | Data Plane | Priya Raman | Lin Zhou | Mei Tanaka |
| SEC | Security | Tomasz Wierzbicki (Head of Security) | Tomasz Wierzbicki | Security on-call |
| OBS | Observability | Ravi Subramanian | Ravi Subramanian | Mei Tanaka, SRE (primary) |
"""


@pytest.mark.integration
async def test_resolve_role_finds_role_columns_and_values_naming_a_role(app):
    """The next hop of a multi-hop question: who is 'the change approver' of PLAT, who is
    'the Head of Security'."""
    from app.core.db import SessionLocal
    from app.records import query as records_query
    from app.records.resolve import resolve

    admin = await _signup(app, f"rr{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Roles Co")
    slug = org["slug"]
    general = _general(org, (await admin.get(f"/api/orgs/{slug}/collections")).json())
    await _ingest(admin, slug, general, {"directory.md": DIRECTORY})
    found = (
        await admin.post(f"/api/orgs/{slug}/collections/{general}/record-schemas/suggest")
    ).json()
    (directory,) = [s for s in found if s["origin"] == "rows_table"]
    await admin.patch(
        f"/api/orgs/{slug}/record-schemas/{directory['id']}", json={"status": "active"}
    )
    await _run_jobs()

    async with SessionLocal() as db:
        schemas = await records_query.schemas_in_scope(db, [uuid.UUID(general)])

        async def holders(role, of=None):
            return [
                (m.by, m.value) for m in await resolve(db, schemas, [uuid.UUID(general)], role, of)
            ]

        # A column named for the role, narrowed to the row mentioning the code or the name.
        assert await holders("change approver", "PLAT") == [("column", "Oluwaseun Adeyemi")]
        assert await holders("Change Approver", "data plane") == [("column", "Lin Zhou")]
        # A value that names the role.
        assert await holders("Head of Security") == [
            ("value", "Tomasz Wierzbicki (Head of Security)")
        ]
        # Word-bounded: "Security" matches the team and the delegate cell, not other rows.
        assert {v for _, v in await holders("security on-call")} == {"Security on-call"}
        assert await holders("chief of staff") == []
        # Roles that end in punctuation still match as whole words.
        assert await holders("SRE (primary)") == [("value", "Mei Tanaka, SRE (primary)")]
        assert await holders("SRE (prim") == []
        # Another collection's records are out of scope.
        assert await resolve(db, schemas, [uuid.uuid4()], "change approver", "PLAT") == []


@pytest.mark.integration
async def test_a_failing_document_is_recorded_and_the_rest_still_extract(app, monkeypatch):
    """One document's extraction raising (a model error, say) is recorded on its run,
    shown to admins (not members), and the other documents are still extracted."""
    from app.records import extract

    admin = await _signup(app, f"rf{uuid.uuid4().hex[:6]}")
    member = await _signup(app, f"rg{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Failing Co")
    slug = org["slug"]
    await _join(admin, member, slug)
    general = _general(org, (await admin.get(f"/api/orgs/{slug}/collections")).json())
    await _ingest(admin, slug, general, _vendor_files())
    found = (
        await admin.post(f"/api/orgs/{slug}/collections/{general}/record-schemas/suggest")
    ).json()
    vendor = next(s for s in found if s["name"] == "vendor_record")

    real = extract.extract_document

    async def flaky(db, schema, doc):
        if doc.title.endswith("Basalt Analytics"):  # the first document in title order
            raise RuntimeError("model unavailable")
        return await real(db, schema, doc)

    monkeypatch.setattr(extract, "extract_document", flaky)
    await admin.patch(f"/api/orgs/{slug}/record-schemas/{vendor['id']}", json={"status": "active"})
    with pytest.raises(RuntimeError, match="1 documents failed"):  # the worker retries the job
        await _run_jobs()

    one = f"/api/orgs/{slug}/record-schemas/{vendor['id']}"
    detail = (await admin.get(one)).json()
    assert detail["stats"]["failed"] == 1 and detail["stats"]["records"] == 3
    (failure,) = detail["failures"]
    assert failure["title"] == "Vendor record: Basalt Analytics"
    assert failure["error"] == "model unavailable"
    assert (await member.get(one)).json()["failures"] == []  # titles and errors: admins only
    q = {"schema": "vendor_record"}
    assert (await member.post(f"/api/orgs/{slug}/records/query", json=q)).json()["total"] == 3

    # The next run retries the failed document (it changed nothing, but it has an error).
    monkeypatch.setattr(extract, "extract_document", real)
    await admin.post(f"/api/orgs/{slug}/record-schemas/{vendor['id']}/extract")
    await _run_jobs()
    detail = (await admin.get(one)).json()
    assert detail["failures"] == [] and detail["stats"]["records"] == 4
    assert detail["stats"]["unchanged"] == 3


@pytest.mark.integration
async def test_latest_only_and_schema_id_keep_collections_apart(app):
    """The same vendor in two clients' collections is two records; latest_only picks the
    newest version per record type, never across collections, and schema_id narrows a
    query to one collection's record type."""
    admin = await _signup(app, f"rl{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Two Clients")
    slug = org["slug"]
    a = _general(org, (await admin.get(f"/api/orgs/{slug}/collections")).json())
    b = (await admin.post(f"/api/orgs/{slug}/collections", json={"name": "Client B"})).json()["id"]
    schemas = {}
    for col in (a, b):
        await _ingest(admin, slug, col, _vendor_files())
        found = (
            await admin.post(f"/api/orgs/{slug}/collections/{col}/record-schemas/suggest")
        ).json()
        schemas[col] = next(s for s in found if s["name"] == "vendor_record")["id"]
        await admin.patch(
            f"/api/orgs/{slug}/record-schemas/{schemas[col]}", json={"status": "active"}
        )
    await _run_jobs()
    q = f"/api/orgs/{slug}/records/query"
    assert (await admin.post(q, json={"schema": "vendor_record"})).json()["total"] == 8
    latest = {"schema": "vendor_record", "latest_only": True}
    assert (await admin.post(q, json=latest)).json()["total"] == 8
    only_b = (await admin.post(q, json={**latest, "schema_id": schemas[b]})).json()
    assert only_b["total"] == 4 and only_b["schemas"] == [schemas[b]]


def test_per_document_types_are_named_by_their_titles_and_keyed_by_a_name():
    """Postmortems with the same "Label: value" lines become one record type named after
    the titles they share, keyed by a field that names each one: never a duration."""
    from app.records.suggest import suggest_from

    incidents = [
        ("Postmortem: Map sync corruption", "2026-04-08", "SEV2", "6 hours", "Aisha Rahman"),
        ("Postmortem: SSO outage", "2026-03-19", "SEV1", "1 hour 25 minutes", "Ines Carvalho"),
        ("Postmortem: DNS outage", "2026-05-21", "SEV2", "38 minutes", "Pedro Alves"),
        ("Postmortem: CI outage", "2026-09-29", "SEV4", "4 hours", "Pedro Alves"),
    ]
    docs = [
        (NS(id=i, title=title), find_structure(
            f"# {title}\n\nDate: {date}\nSeverity: {sev}\nDuration: {took}\n"
            f"Incident commander: {who}\n\nWhat happened.\n"
        ))
        for i, (title, date, sev, took, who) in enumerate(incidents)
    ]  # fmt: skip
    (s,) = suggest_from(docs, NS(name="Engineering"))
    assert (s.name, s.label, s.origin) == ("postmortem", "Postmortem", "field_lines")
    types = {f["name"]: f["type"] for f in s.fields}
    assert types["duration"] == "text" and types["date"] == "date"
    assert s.key_field == "date"  # unique; the commander repeats, a duration measures
