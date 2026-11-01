"""Extract a schema's records from its collection's documents.

Tables and "Label: value" lines are read deterministically (free and exact). Prose
("llm" schemas) goes through LangExtract with Claude Haiku, which aligns every value
to its character offsets; values it can't align are dropped, so every stored value is
citable. In an "llm" schema's documents, a table whose columns name its key field and
another field is read like a register (every row, exactly), and the model reads the
rest: a model reading a register fills a few rows and puts a cell's "Yes" under the
wrong column. Each document is redone only when its content changed (`RecordRun`), and
commits on its own so an interrupted run resumes where it stopped."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Document, Record, RecordRun, RecordSchema
from app.records.tables import Cell, Structure, clean, find_structure, normalize
from app.records.values import typed

log = logging.getLogger("strata.records")

ROW_MATCH = 0.6  # a register table belongs to a schema if it has this share of its fields
_YEAR = re.compile(r"\b(19|20|21)\d{2}\b")


@dataclass
class Extracted:
    key: str
    values: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    method: str | None = None  # how it was read, when not the schema's way


def key_of(text: str) -> str:
    return " ".join(clean(text).lower().split())[:512]


def year_hint(doc: Document) -> int | None:
    """The year a document is about, for dates written without one ("Jul 9")."""
    if m := _YEAR.search(doc.title or ""):
        return int(m.group())
    if doc.source_updated_at:
        return doc.source_updated_at.year
    return None


def _index(schema: RecordSchema) -> dict[str, dict[str, Any]]:
    """Field by every name it's known by: its name, normalised label and aliases."""
    out: dict[str, dict[str, Any]] = {}
    for f in schema.fields:
        for name in [f["name"], f.get("label", ""), *f.get("aliases", [])]:
            if name:
                out.setdefault(normalize(name), f)
    return out


def _record(pairs: list[tuple[dict, Cell]], schema: RecordSchema, year: int | None):
    rec = Extracted(key="")
    for f, cell in pairs:
        rec.provenance[f["name"]] = {"text": cell.text, "start": cell.start, "end": cell.end}
        value = typed(cell.text, f, year)
        if value is not None:
            rec.values[f["name"]] = value
        if f["name"] == schema.key_field:
            rec.key = key_of(cell.text)
    return rec if rec.key else None


def from_structure(doc: Document, s: Structure, schema: RecordSchema) -> list[Extracted]:
    index, year = _index(schema), year_hint(doc)
    out: list[Extracted] = []
    if schema.origin == "fields_table":
        for table in s.tables:
            if not table.vertical:
                continue
            pairs = [
                (index[normalize(r[0].text)], r[1])
                for r in table.rows
                if normalize(r[0].text) in index
            ]
            if rec := _record(pairs, schema, year):
                out.append(rec)
    elif schema.origin == "rows_table":
        wanted = {f["name"] for f in schema.fields}
        for table in s.tables:
            if table.vertical:
                continue
            columns = [index.get(normalize(c.text)) for c in table.header]
            names = {f["name"] for f in columns if f}
            if schema.key_field not in names or len(names) < ROW_MATCH * len(wanted):
                continue
            for row in table.rows:
                pairs = [(f, cell) for f, cell in zip(columns, row, strict=True) if f]
                if rec := _record(pairs, schema, year):
                    out.append(rec)
    elif schema.origin == "field_lines":
        pairs: list[tuple[dict, Cell]] = []
        for line in s.lines:
            f = index.get(normalize(line.label))
            if f is None:
                continue
            if f["name"] == schema.key_field and any(p[0]["name"] == f["name"] for p in pairs):
                if rec := _record(pairs, schema, year):  # the next entity starts
                    out.append(rec)
                pairs = []
            pairs.append((f, line.value))
        if rec := _record(pairs, schema, year):
            out.append(rec)
    return out


# ── tables in an llm schema's documents ───────────────────────────────────
MIN_PROSE = 40  # characters left outside register tables worth a model call
_STOP = {"a", "an", "and", "for", "in", "is", "of", "or", "per", "the", "to"}


def _words(name: str) -> frozenset[str]:
    return frozenset(w for w in normalize(name).split("_") if w and w not in _STOP)


def column_field(label: str, schema: RecordSchema) -> dict[str, Any] | None:
    """The field a column (or a Field | Value row) holds: the one it names (name, label
    or alias), else the only field whose words contain the column's or are contained in
    them ("Notice Period (days)" → notice_days). None when nothing or several match."""
    if exact := _index(schema).get(normalize(label)):
        return exact
    words = _words(label)
    if not words:
        return None
    hits = [
        f
        for f in schema.fields
        if any(
            (w := _words(n)) and (w <= words or words <= w)
            for n in [f["name"], f.get("label", ""), *f.get("aliases", [])]
        )
    ]
    return hits[0] if len(hits) == 1 else None


def _columns(table, schema: RecordSchema) -> list[dict[str, Any] | None]:
    """Each header cell's field; a field claimed by two columns is left to neither."""
    columns = [column_field(c.text, schema) for c in table.header]
    names = [f["name"] for f in columns if f]
    return [f if f and names.count(f["name"]) == 1 else None for f in columns]


def _span(table) -> tuple[int, int]:
    return table.start, table.end


def from_tables(
    doc: Document, s: Structure, schema: RecordSchema
) -> tuple[list[Extracted], list[tuple[int, int]]]:
    """An llm schema's records in register tables: tables whose columns name the key
    field and at least one other field. Returns the records and those tables' spans."""
    year = year_hint(doc)
    out: list[Extracted] = []
    spans: list[tuple[int, int]] = []
    for table in s.tables:
        if table.vertical:
            continue
        columns = _columns(table, schema)
        names = {f["name"] for f in columns if f}
        if schema.key_field not in names or len(names) < 2:
            continue
        for row in table.rows:
            pairs = [(f, cell) for f, cell in zip(columns, row, strict=True) if f]
            if rec := _record(pairs, schema, year):
                rec.method = "rows_table"
                out.append(rec)
        spans.append(_span(table))
    return out, spans


def _cell_fields(s: Structure, schema: RecordSchema, skip: list[tuple[int, int]]):
    """(start, end, field or None) for the value cells of the other tables: what their
    column header (or a Field | Value row's label) says the cell holds."""
    cells = []
    for table in s.tables:
        if _span(table) in skip:
            continue
        if table.vertical:
            cells += [(r[1].start, r[1].end, column_field(r[0].text, schema)) for r in table.rows]
            continue
        columns = [column_field(c.text, schema) for c in table.header]
        cells += [
            (c.start, c.end, f) for row in table.rows for c, f in zip(row, columns, strict=True)
        ]
    return cells


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    """The text with those spans replaced by spaces: offsets stay the document's."""
    for start, end in spans:
        text = text[:start] + re.sub(r"[^\n]", " ", text[start:end]) + text[end:]
    return text


def _merge(rows: list[Extracted], prose: list[Extracted]) -> list[Extracted]:
    """Table rows first; the model's reading of the same record fills in what the
    table doesn't say."""
    by_key = {r.key: r for r in prose}
    for rec in rows:
        if extra := by_key.pop(rec.key, None):
            for name, value in extra.values.items():
                if name not in rec.values:
                    rec.values[name] = value
                    rec.provenance[name] = extra.provenance[name]
    return rows + list(by_key.values())


# ── prose, through LangExtract ────────────────────────────────────────────
def _sample(f: dict[str, Any]) -> tuple[str, str, str]:
    """(sentence, the words a value is extracted from, its normalised value) for the
    worked example. Yes/no and dates are worded the way prose states them, so the
    example shows the words staying verbatim while `value` carries the reading."""
    label = f.get("label", f["name"]).lower()
    kind = f.get("type", "text")
    if kind == "boolean":
        return f"It is confirmed that {label} applies.", f"confirmed that {label} applies", "yes"
    if kind == "date":
        return f"Its {label} falls on 3 March 2026.", "3 March 2026", "2026-03-03"
    if kind == "number":
        return f"Its {label} comes to about 1,250.", "about 1,250", "1250"
    if kind == "choice":
        option = (f.get("options") or ["Approved"])[0]
        return f"Its {label} is {option.lower()}.", option.lower(), option
    words = f"sample {label}"
    return f"Its {label} is {words}.", words, words


def _examples(schema: RecordSchema):
    """One worked example built from the schema itself, so any schema can be extracted
    without hand-written examples; every extraction's words occur verbatim in its text."""
    import langextract as lx

    key = "Example Holdings Ltd"
    others = [(f, *_sample(f)) for f in schema.fields if f["name"] != schema.key_field]
    text = f"{key} is one of the entries. " + " ".join(sentence for _, sentence, _, _ in others)
    extractions = [
        lx.data.Extraction(
            extraction_class=schema.key_field,
            extraction_text=key,
            attributes={"record": key, "value": key},
        )
    ] + [
        lx.data.Extraction(
            extraction_class=f["name"],
            extraction_text=words,
            attributes={"record": key, "value": value},
        )
        for f, _, words, value in others
    ]
    return [lx.data.ExampleData(text=text, extractions=extractions)]


def _prompt(schema: RecordSchema) -> str:
    key = next((f for f in schema.fields if f["name"] == schema.key_field), schema.fields[0])
    fields = "; ".join(
        f"{f['name']} ({f.get('label', f['name'])}"
        + (f": {f['description']}" if f.get("description") else "")
        + (f"; one of {', '.join(f['options'])}" if f.get("options") else "")
        + ")"
        for f in schema.fields
    )
    return (
        f"Extract every {schema.label} described in the text. For each one, extract these "
        f"fields where the text states them: {fields}. Use the exact words from the text "
        f"for every extraction; don't paraphrase. Give every extraction two attributes: "
        f"record = the {key.get('label', key['name'])} of the {schema.label} it belongs to, "
        f"and value = what those words state, normalised: yes or no for yes/no fields, "
        f"exactly one of the options for fields that have options, YYYY-MM-DD for dates, "
        f"a plain number for numbers, and for text the value alone (a name, not the "
        f"sentence around it). Leave a field out when the text doesn't state it."
    )


def _from_prose_sync(
    doc: Document,
    schema: RecordSchema,
    model,
    text: str | None = None,
    structure: Structure | None = None,
    skip: list[tuple[int, int]] | None = None,
) -> list[Extracted]:
    """`text` is the document with its register tables (`skip`) blanked out."""
    import langextract as lx

    # A value the model places in a table cell must be what the cell's column holds.
    cells = _cell_fields(structure or find_structure(doc.content), schema, skip or [])
    result = lx.extract(
        text_or_documents=text if text is not None else doc.content,
        prompt_description=_prompt(schema),
        examples=_examples(schema),
        model=model,
        max_char_buffer=2500,
        max_workers=model.max_workers,
        show_progress=False,
    )
    by_name = {f["name"]: f for f in schema.fields}
    year = year_hint(doc)
    records: dict[str, Extracted] = {}
    current = None
    for e in sorted(
        (e for e in result.extractions if e.char_interval is not None),
        key=lambda e: e.char_interval.start_pos or 0,
    ):
        f = by_name.get(e.extraction_class)
        start, end = e.char_interval.start_pos, e.char_interval.end_pos
        if f is None or start is None or end is None:
            continue
        held = next((col for lo, hi, col in cells if lo <= start and end <= hi), f)
        if held is not None and held["name"] != f["name"]:
            continue  # "Yes" under Customer Data Access isn't auto-renewal
        owner = key_of((e.attributes or {}).get("record") or "") or current
        if e.extraction_class == schema.key_field:
            owner = key_of(e.extraction_text)
            current = owner
        if not owner:
            continue
        rec = records.setdefault(owner, Extracted(key=owner))
        reading = (e.attributes or {}).get("value")
        reading = reading.strip() if isinstance(reading, str) else ""
        kind = f.get("type", "text")
        text = doc.content[start:end]
        if kind == "text" and reading and reading != text:
            # "Tom Okafor owns the record" → "Tom Okafor", when the reading is written
            # inside the span: value and offsets narrow to those exact words.
            at = text.lower().find(reading.lower())
            if at >= 0:
                start, end = start + at, start + at + len(reading)
                text = doc.content[start:end]
        rec.provenance.setdefault(f["name"], {"text": text, "start": start, "end": end})
        # The words themselves first; prose rarely says "Yes" or an option verbatim
        # ("handles protected health information"), so a yes/no, choice, date or number
        # falls back to the model's reading of those words (the span stays the evidence).
        value = typed(text, f, year)
        if value is None and kind != "text" and reading:
            value = typed(reading, f, year)
        if value is not None:
            rec.values.setdefault(f["name"], value)
    return list(records.values())


def _model():
    from app.records.langextract_claude import ClaudeLanguageModel

    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError("Prose extraction needs ANTHROPIC_API_KEY")
    return ClaudeLanguageModel(settings.record_model, settings.anthropic_api_key)


# ── storing ───────────────────────────────────────────────────────────────
async def extract_document(db: AsyncSession, schema: RecordSchema, doc: Document) -> RecordRun:
    """Replace one document's records for a schema. Caller commits."""
    cost = None
    if schema.origin == "llm":
        structure = find_structure(doc.content)
        found, tables = from_tables(doc, structure, schema)
        rest = _blank(doc.content, tables)
        if len(re.sub(r"<!--.*?-->|\s", "", rest)) >= MIN_PROSE:
            model = _model()
            try:
                prose = await asyncio.to_thread(
                    _from_prose_sync, doc, schema, model, rest, structure, tables
                )
            finally:
                cost = round(model.cost_usd, 6)
            found = _merge(found, prose)
        method = "llm"
    else:
        found = from_structure(doc, find_structure(doc.content), schema)
        method = schema.origin
    seen: dict[str, int] = {}
    await db.execute(
        delete(Record).where(Record.schema_id == schema.id, Record.document_id == doc.id)
    )
    for rec in found:
        n = seen[rec.key] = seen.get(rec.key, 0) + 1
        db.add(
            Record(
                org_id=doc.org_id,
                collection_id=doc.collection_id,
                schema_id=schema.id,
                document_id=doc.id,
                # A register can repeat a key; the column holds 512 characters.
                key=rec.key[:512] if n == 1 else f"{rec.key[:500]} #{n}",
                values=rec.values,
                provenance=rec.provenance,
                method=rec.method or method,
            )
        )
    run = await db.get(RecordRun, (schema.id, doc.id))
    if run is None:
        run = RecordRun(schema_id=schema.id, document_id=doc.id)
        db.add(run)
    run.content_hash, run.method, run.records = doc.content_hash, method, len(found)
    run.cost_usd, run.error, run.extracted_at = cost, None, datetime.now(UTC)
    return run


async def extract_schema(db: AsyncSession, schema: RecordSchema, *, force: bool = False) -> dict:
    """Extract every changed document of the schema's collection, committing each. A
    failed document is recorded and skipped; the rest still run."""
    # Plain values only: a rollback after a failure expires every loaded object.
    schema_id, schema_name, origin = schema.id, schema.name, schema.origin
    docs = (
        await db.execute(
            select(Document.id, Document.title, Document.content_hash)
            .where(Document.collection_id == schema.collection_id, Document.hidden.is_(False))
            .order_by(Document.title)
        )
    ).all()
    previous = {
        doc_id: (content_hash, error)
        for doc_id, content_hash, error in (
            await db.execute(
                select(RecordRun.document_id, RecordRun.content_hash, RecordRun.error).where(
                    RecordRun.schema_id == schema_id
                )
            )
        ).all()
    }
    done = failed = skipped = 0
    cost = 0.0
    for doc_id, title, content_hash in docs:
        before = previous.get(doc_id)
        if not force and before is not None and before[0] == content_hash and not before[1]:
            skipped += 1
            continue
        try:
            schema = await db.get(RecordSchema, schema_id, populate_existing=True)
            doc = await db.get(Document, doc_id, populate_existing=True)
            run = await extract_document(db, schema, doc)
            cost += run.cost_usd or 0.0
            await db.commit()
            done += 1
        except Exception as exc:  # one bad document doesn't stop the schema
            await db.rollback()
            failed += 1
            log.warning("Extracting %s from %s failed: %s", schema_name, title, exc)
            run = await db.get(RecordRun, (schema_id, doc_id), populate_existing=True)
            if run is None:  # recorded, so the failure shows and the next run retries it
                run = RecordRun(schema_id=schema_id, document_id=doc_id, content_hash="")
                db.add(run)
            run.method, run.records = origin, 0
            run.error, run.extracted_at = str(exc)[:500], datetime.now(UTC)
            await db.commit()
    # Documents removed from the collection take their records with them (cascade).
    total = await db.scalar(
        select(func.count()).select_from(Record).where(Record.schema_id == schema_id)
    )
    schema = await db.get(RecordSchema, schema_id, populate_existing=True)
    schema.extracted_at = datetime.now(UTC)
    schema.stats = {
        **(schema.stats or {}),
        "records": total or 0,
        "documents": len(docs),
        "extracted": done,
        "unchanged": skipped,
        "failed": failed,
        "cost_usd": round(cost, 6),
    }
    await db.commit()
    return schema.stats
