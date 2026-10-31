"""The agent's `query_records` tool: exact lookups over structured records, with every
returned record's document registered as a citable source.

Search returns the top few passages, so "which vendors have X" or "how many Y" come
back incomplete; this checks every record of the type in scope and says so."""

import uuid
from typing import Any

from sqlalchemy import select

from app.models import Chunk, RecordSchema
from app.records import query as records_query
from app.retrieval.hybrid import hits_for_chunks

QUERY_RECORDS = {
    "name": "query_records",
    "description": (
        "Exact, complete lookups over structured records extracted from the knowledge base "
        "(registers, inventories, directories, logs, rotas). Use it FIRST for questions "
        "that ask which / how many / list all / every, or that compare a value across many "
        "items: search returns only the top few passages, this checks every record. The "
        "record types in scope and their fields are listed in the system prompt. Filters "
        "are ANDed; use several calls for OR. Dates are YYYY-MM-DD. Returns the matching "
        "records with [n] sources to cite."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "record_type": {"type": "string", "description": "A record type's name."},
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string"},
                        "op": {
                            "type": "string",
                            "enum": sorted(set().union(*records_query.OPS.values())),
                        },
                        "value": {"description": "A value, or a list of values for 'in'."},
                    },
                    "required": ["field", "op"],
                },
            },
            "mode": {
                "type": "string",
                "enum": ["list", "count", "group"],
                "description": "list records (default), count them, or count per value of "
                "group_by.",
            },
            "group_by": {"type": "string"},
            "sort": {"type": "string", "description": "A field to sort the list by."},
            "descending": {"type": "boolean"},
            "latest_only": {
                "type": "boolean",
                "description": "When the same item appears in several versions of a document "
                "(this year's and last year's), keep only the newest version's record.",
            },
            "limit": {"type": "integer", "minimum": 1, "maximum": records_query.MAX_ROWS},
        },
        "required": ["record_type"],
    },
}

MAX_COLUMNS = 8  # fields shown per row (key and filtered fields always included)


def describe(schemas: list[RecordSchema], collections: dict, orgs: dict) -> str:
    """The record types in scope, for the system prompt."""
    lines = []
    for s in schemas:
        col = collections.get(s.collection_id)
        where = f"{orgs[col.org_id].name} › {col.name}" if col is not None else ""
        fields = []
        for f in s.fields:
            kind = f.get("type", "text")
            if kind == "choice":
                kind = "one of: " + ", ".join(f.get("options") or [])
            elif kind == "boolean":
                kind = "yes/no"
            fields.append(f"{f['name']} ({kind}{', key' if f['name'] == s.key_field else ''})")
        count = (s.stats or {}).get("records")
        size = f"; {count} records" if count is not None else ""
        lines.append(f"- {s.name}: {s.label} ({where}{size}). Fields: " + "; ".join(fields))
    return "\n".join(lines)


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, int | float):
        return f"{value:,}"
    return str(value).replace("|", "\\|").replace("\n", " ")


MAX_TALLY_VALUES = 8


def _tallies(rows: list[records_query.Row], fields: list[dict], skip: set) -> list[str]:
    """Exact per-value counts of the listed records' choice and yes/no fields, so a
    breakdown in the answer is counted by code, not by the model."""
    out = []
    seen: set[str] = set()
    for f in fields:
        name = f["name"]
        if name in seen or name in skip or f.get("type") not in ("choice", "boolean"):
            continue
        seen.add(name)
        counts: dict[str, int] = {}
        for row in rows:
            value = row.values.get(name)
            label = "(blank)" if value is None else _show(value)
            counts[label] = counts.get(label, 0) + 1
        if 2 <= len(counts) <= MAX_TALLY_VALUES:
            ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            out.append(f"- {f.get('label', name)}: " + ", ".join(f"{v} {n}" for v, n in ranked))
    return ["", f"Tallies over all {len(rows)} records:", *out] if out else []


async def _source_chunks(
    db, rows: list[records_query.Row], key_fields: dict[uuid.UUID, str]
) -> dict[uuid.UUID, uuid.UUID]:
    """Per document, the chunk where its record's key is written (else its first
    chunk), so a citation opens on the right passage."""
    needle: dict[uuid.UUID, str] = {}
    for r in rows:
        written = (r.provenance.get(key_fields.get(r.schema_id, "")) or {}).get("text") or ""
        needle.setdefault(r.document_id, written)
    chunks = (
        await db.execute(
            select(Chunk.id, Chunk.document_id, Chunk.content)
            .where(Chunk.document_id.in_(list(needle)))
            .order_by(Chunk.document_id, Chunk.ordinal)
        )
    ).all()
    first: dict[uuid.UUID, uuid.UUID] = {}
    found: dict[uuid.UUID, uuid.UUID] = {}
    for chunk_id, doc_id, content in chunks:
        first.setdefault(doc_id, chunk_id)
        if doc_id not in found and needle[doc_id] and needle[doc_id] in content:
            found[doc_id] = chunk_id
    return first | found


async def run(toolbox, schemas: list[RecordSchema], args: dict[str, Any]):
    """Run a query for the agent: (text for the model, refs, newly registered sources)."""
    from app.agent.tools import SearchOutcome, ToolInputError

    name = args.get("record_type")
    if not isinstance(name, str) or not name:
        raise ToolInputError("'record_type' is required")
    matching = [s for s in schemas if s.name == name]
    if not matching:
        known = ", ".join(sorted({s.name for s in schemas})) or "none"
        raise ToolInputError(f"No record type '{name}'. In scope: {known}")
    filters = args.get("filters") or []
    if not isinstance(filters, list) or not all(isinstance(f, dict) for f in filters):
        raise ToolInputError("'filters' must be a list of {field, op, value}")
    mode = args.get("mode") or "list"
    if mode not in ("list", "count", "group"):
        raise ToolInputError("'mode' must be list, count or group")
    limit = args.get("limit") or (40 if mode == "count" else 100)
    if not isinstance(limit, int):
        raise ToolInputError("'limit' must be an integer")
    try:
        result = await records_query.run(
            toolbox.db,
            matching,
            toolbox.scope.collection_ids,
            filters=filters,
            mode=mode,
            group_by=args.get("group_by"),
            sort=args.get("sort"),
            descending=bool(args.get("descending")),
            limit=limit,
            latest_only=bool(args.get("latest_only")),
        )
    except records_query.QueryError as exc:
        raise ToolInputError(str(exc)) from exc

    label = matching[0].label
    described = records_query.describe(filters) or "no filters"
    head = (
        f"query_records {name} ({label}), {described}: {result.total} matching record(s). "
        "Exact: every record of this type in scope was checked."
    )
    if mode == "group":
        lines = [head, "", f"| {args.get('group_by')} | records |", "|---|---|"]
        lines += [f"| {_show(v) if v is not None else '(blank)'} | {n} |" for v, n in result.groups]
        return SearchOutcome(text="\n".join(lines), queries=[described], refs=[], hits=result.total)

    new, refs, ref_of_doc = [], [], {}
    key_fields = {s.id: s.key_field for s in matching}
    chunk_of = await _source_chunks(toolbox.db, result.rows, key_fields) if result.rows else {}
    hits = {h.document_id: h for h in await hits_for_chunks(toolbox.db, list(chunk_of.values()))}
    for row in result.rows:
        hit = hits.get(row.document_id)
        if hit is None or hit.collection_id not in toolbox.scope.collections:
            continue
        entry, is_new = toolbox.registry.register(
            hit, toolbox.scope.orgs[hit.org_id], toolbox.scope.collections[hit.collection_id]
        )
        if is_new:
            new.append(entry)
        ref_of_doc[row.document_id] = entry.ref
        if entry.ref not in refs:
            refs.append(entry.ref)

    fields = [f for s in matching for f in s.fields]
    names: list[str] = []
    for f in [
        matching[0].key_field,
        *(x.get("field") for x in filters),
        *(f["name"] for f in fields),
    ]:
        if f and f not in names and any(x["name"] == f for x in fields):
            names.append(f)
    names = names[:MAX_COLUMNS]
    labels = {f["name"]: f.get("label", f["name"]) for f in fields}
    lines = [head]
    if result.rows:
        lines += [
            "",
            "| " + " | ".join(labels[n] for n in names) + " | Source |",
            "|" + "---|" * (len(names) + 1),
        ]
        for row in result.rows:
            cells = [_show(row.values.get(n, "")) for n in names]
            ref = ref_of_doc.get(row.document_id)
            lines.append("| " + " | ".join(cells) + f" | {f'[{ref}]' if ref else ''} |")
        if result.total > len(result.rows):
            lines.append(
                f"\nShowing {len(result.rows)} of {result.total}. Narrow the filters, or use "
                "mode=count or mode=group for totals."
            )
        else:
            lines += _tallies(result.rows, fields, {f.get("field") for f in filters})
    return SearchOutcome(
        text="\n".join(lines), queries=[described], refs=refs, new_sources=new, hits=result.total
    )
