"""Suggest record schemas for a collection from its documents' own structure; an admin
activates the ones worth keeping.

- A "Field | Value" table in each of several documents (one vendor per document) →
  one schema, one record per document ("fields_table").
- A register table with a header row → one schema per distinct header, one record per
  row ("rows_table"). The same header in several documents (last year's and this
  year's SLA tiers) is one schema; each record keeps its document.
- The same "Label: value" lines in several documents → one schema ("field_lines").
- Prose only: optionally, a model proposes a schema from sample documents ("llm"),
  extracted later with LangExtract.
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Collection, Document, RecordSchema
from app.records.tables import Structure, Table, clean, find_structure, normalize
from app.records.values import infer_type

MIN_ROWS = 3  # a register needs at least this many rows to be worth a schema
MIN_DOCS = 2  # per-document tables and field lines must recur across documents
FIELD_SHARE = 0.4  # a label must appear in this share of documents to be a field
MAX_SCHEMAS = 25
_VERSION = re.compile(r"\b(v\d+|q[1-4]|(19|20)\d{2}(-\d{2})?|jan|feb|mar|apr|may|jun|jul|aug|"
                      r"sep|oct|nov|dec)\b", re.I)  # fmt: skip
_GENERIC_HEADINGS = {"", "log", "table", "summary", "overview", "details", "notes", "list"}


@dataclass
class Suggestion:
    name: str
    label: str
    origin: str
    fields: list[dict[str, Any]]
    key_field: str
    documents: int
    records: int
    description: str = ""
    samples: list[dict[str, str]] = field(default_factory=list)


def _slug(text: str) -> str:
    text = _VERSION.sub(" ", clean(text).split(":")[0])
    return normalize(text)[:48] or "record"


def _label(source: str) -> str:
    """A readable label without dates or versions: "On-call rota Nov–Dec 2026" →
    "On-call rota"."""
    text = clean(source).split(":")[0]
    text = re.sub(r"\s+", " ", _VERSION.sub("", text)).strip(" -–—(),")
    return text or clean(source)


def _fields(labels_values: dict[str, list[str]], labels: dict[str, str]) -> list[dict]:
    out = []
    for name, values in labels_values.items():
        out.append({"name": name, "label": labels[name], "description": "", **infer_type(values)})
    return out


def _key_field(fields: list[dict], rows: list[dict[str, str]], titles: list[str]) -> str:
    """The field naming the entity: the one whose values appear in document titles, else
    the first text field whose values are (nearly) unique."""
    names = [f["name"] for f in fields]
    for name in names:
        hits = sum(
            1 for row, title in zip(rows, titles, strict=False)
            if row.get(name) and clean(row[name]).split(",")[0].lower() in title.lower()
        )  # fmt: skip
        if rows and hits / len(rows) >= 0.6:
            return name
    for f in fields:
        values = [clean(r.get(f["name"], "")) for r in rows if r.get(f["name"])]
        if f["type"] in ("text", "number") and values and len(set(values)) >= 0.9 * len(values):
            return f["name"]
    return names[0]


def _rows_of(table: Table) -> list[dict[str, str]]:
    names = [normalize(c.text) for c in table.header]
    return [{n: c.text for n, c in zip(names, row, strict=True)} for row in table.rows]


def _vertical_rows(table: Table) -> dict[str, str]:
    return {normalize(r[0].text): r[1].text for r in table.rows if normalize(r[0].text)}


def suggest_from(
    documents: list[tuple[Document, Structure]], collection: Collection
) -> list[Suggestion]:
    out: list[Suggestion] = []
    titles = {d.id: d.title for d, _ in documents}

    # One record per document: a Field | Value table.
    per_doc = [(d, t) for d, s in documents for t in s.tables[:1] if t.vertical]
    if len(per_doc) >= MIN_DOCS:
        labels: dict[str, str] = {}
        values: dict[str, list[str]] = defaultdict(list)
        rows, row_titles = [], []
        for doc, table in per_doc:
            row = _vertical_rows(table)
            for r in table.rows:
                labels.setdefault(normalize(r[0].text), clean(r[0].text))
            for name, value in row.items():
                values[name].append(value)
            rows.append(row)
            row_titles.append(doc.title)
        keep = {n: v for n, v in values.items() if len(v) >= max(MIN_DOCS, FIELD_SHARE * len(rows))}
        fields = _fields(keep, labels)
        prefix = Counter(clean(d.title).split(":")[0] for d, _ in per_doc).most_common(1)[0][0]
        name = _slug(prefix) if ":" in per_doc[0][0].title else _slug(collection.name)
        out.append(
            Suggestion(
                name=name,
                label=clean(prefix) if ":" in per_doc[0][0].title else collection.name,
                origin="fields_table",
                fields=fields,
                key_field=_key_field(fields, rows, row_titles),
                documents=len(per_doc),
                records=len(per_doc),
                description=f"One per document with a Field | Value table in {collection.name}.",
                samples=rows[:3],
            )
        )

    # One record per row: register tables, grouped by header.
    by_doc = {d.id: s.tables for d, s in documents}
    groups: dict[tuple[str, ...], list[tuple[Document, Table]]] = defaultdict(list)
    for doc, structure in documents:
        for table in structure.tables:
            names = tuple(normalize(c.text) for c in table.header)
            if table.vertical or len(names) < 2 or not all(names) or len(set(names)) < len(names):
                continue  # a comparison table ("| | Before | After |") isn't a register
            groups[names].append((doc, table))
    for names, members in groups.items():
        rows = [row for _, table in members for row in _rows_of(table)]
        if len(rows) < MIN_ROWS:
            continue
        labels = {normalize(c.text): clean(c.text) for c in members[0][1].header}
        values = {n: [r[n] for r in rows] for n in names}
        fields = _fields(values, labels)
        doc, table = members[0]
        # Named after the document ("Team directory"), plus the section when the
        # document has several registers ("API rate limits Endpoints").
        registers = sum(1 for t in by_doc[doc.id] if not t.vertical and len(t.rows) >= MIN_ROWS)
        heading = "" if table.heading.lower() in _GENERIC_HEADINGS else table.heading
        source = doc.title if registers <= 1 or not heading else f"{doc.title} {heading}"
        out.append(
            Suggestion(
                name=_slug(source),
                label=_label(source),
                origin="rows_table",
                fields=fields,
                key_field=_key_field(fields, rows, [titles[d.id] for d, _ in members]),
                documents=len({d.id for d, _ in members}),
                records=len(rows),
                description=f"Rows of the table under “{table.heading or doc.title}” in "
                f"{len({d.id for d, _ in members})} document(s).",
                samples=rows[:3],
            )
        )

    # One record per document: the same "Label: value" lines across documents.
    line_docs = [(d, s) for d, s in documents if len(s.lines) >= 3 and not s.tables]
    if len(line_docs) >= MIN_DOCS:
        counts = Counter(normalize(line.label) for _, s in line_docs for line in s.lines)
        common = {n for n, c in counts.items() if c >= max(MIN_DOCS, FIELD_SHARE * len(line_docs))}
        if len(common) >= 3:
            labels, values = {}, defaultdict(list)
            rows = []
            for _, s in line_docs:
                row = {}
                for line in s.lines:
                    n = normalize(line.label)
                    if n in common and n not in row:
                        labels.setdefault(n, line.label)
                        row[n] = line.value.text
                        values[n].append(line.value.text)
                rows.append(row)
            fields = _fields(values, labels)
            out.append(
                Suggestion(
                    name=_slug(collection.name),
                    label=collection.name,
                    origin="field_lines",
                    fields=fields,
                    key_field=_key_field(fields, rows, [d.title for d, _ in line_docs]),
                    documents=len(line_docs),
                    records=len(line_docs),
                    description=f"“Label: value” lines shared by documents in {collection.name}.",
                    samples=rows[:3],
                )
            )

    # Unique names within the collection.
    seen: Counter[str] = Counter()
    for s in out:
        seen[s.name] += 1
        if seen[s.name] > 1:
            s.name = f"{s.name}_{seen[s.name]}"
    return sorted(out, key=lambda s: -s.records)[:MAX_SCHEMAS]


async def suggest(db: AsyncSession, collection: Collection, user_id=None) -> list[RecordSchema]:
    """Suggestions for a collection, saved as `suggested` schemas (an active or archived
    schema of the same name is left as the admin set it)."""
    docs = list(
        (
            await db.execute(
                select(Document).where(
                    Document.collection_id == collection.id, Document.hidden.is_(False)
                )
            )
        ).scalars()
    )
    found = suggest_from([(d, find_structure(d.content)) for d in docs], collection)
    existing = {
        s.name: s
        for s in (
            await db.execute(
                select(RecordSchema).where(RecordSchema.collection_id == collection.id)
            )
        ).scalars()
    }
    out = []
    for s in found:
        schema = existing.get(s.name)
        if schema is not None and schema.status != "suggested":
            out.append(schema)
            continue
        if schema is None:
            schema = RecordSchema(
                org_id=collection.org_id,
                collection_id=collection.id,
                name=s.name,
                created_by_id=user_id,
            )
            db.add(schema)
        schema.label = s.label[:120]
        schema.description = s.description
        schema.fields = s.fields
        schema.key_field = s.key_field
        schema.origin = s.origin
        schema.status = "suggested"
        schema.stats = {"documents": s.documents, "records": s.records, "samples": s.samples}
        out.append(schema)
    await db.flush()
    return out
