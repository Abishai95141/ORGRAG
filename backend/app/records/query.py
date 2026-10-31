"""Exact queries over structured records: filters, counts and groupings, within the
collections a caller may read. Used by the agent's `query_records` tool and the API.

Filters are validated against each schema's typed fields and turned into
parameterised SQL over the `values` JSONB; nothing the caller sends becomes SQL text.
Schemas with the same name across collections or orgs ("vendor" in two clients) are
queried together, so one question can span them."""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RecordSchema
from app.records.values import parse_bool, parse_choice, parse_date, parse_number

OPS = {
    "text": {"eq", "ne", "contains", "in", "exists", "missing"},
    "choice": {"eq", "ne", "in", "exists", "missing"},
    "number": {"eq", "ne", "lt", "lte", "gt", "gte", "exists", "missing"},
    "date": {"eq", "ne", "lt", "lte", "gt", "gte", "exists", "missing"},
    "boolean": {"eq", "exists", "missing"},
}
_CMP = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
MAX_ROWS = 300


class QueryError(ValueError):
    """A query the schema can't answer (unknown field, wrong operator or value)."""


@dataclass
class Row:
    record_id: uuid.UUID
    schema_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    updated_at: datetime | None
    key: str
    values: dict[str, Any]
    provenance: dict[str, Any]


@dataclass
class Result:
    schemas: list[RecordSchema]
    total: int
    rows: list[Row] = field(default_factory=list)
    groups: list[tuple[Any, int]] = field(default_factory=list)


async def schemas_in_scope(db: AsyncSession, collection_ids: list[uuid.UUID]) -> list[RecordSchema]:
    if not collection_ids:
        return []
    return list(
        (
            await db.execute(
                select(RecordSchema)
                .where(
                    RecordSchema.collection_id.in_(collection_ids),
                    RecordSchema.status == "active",
                )
                .order_by(RecordSchema.name)
            )
        ).scalars()
    )


def _field(schemas: list[RecordSchema], name: str) -> dict[str, Any]:
    for s in schemas:
        for f in s.fields:
            if f["name"] == name:
                return f
    known = sorted({f["name"] for s in schemas for f in s.fields})
    raise QueryError(f"No field '{name}'. Fields: {', '.join(known)}")


def _value(f: dict[str, Any], raw: Any) -> Any:
    kind = f.get("type", "text")
    if kind == "number":
        value = raw if isinstance(raw, int | float) else parse_number(str(raw))
    elif kind == "date":
        value = parse_date(str(raw))
    elif kind == "boolean":
        value = raw if isinstance(raw, bool) else parse_bool(str(raw))
    elif kind == "choice":
        value = parse_choice(str(raw), f.get("options") or [])
        if value is None:
            raise QueryError(f"'{f['name']}' is one of: {', '.join(f.get('options') or [])}")
    else:
        value = " ".join(str(raw).split())
    if value is None or value == "":
        raise QueryError(f"'{raw}' isn't a valid {kind} for '{f['name']}'")
    return value


def _typed_sql(f: dict[str, Any], param: str, alias: str = "r") -> str:
    """SQL for a field's value as its type (field name bound as `:param`); values that
    don't fit the type are NULL, not errors."""
    values = f"{alias}.values"
    path = f"({values}->>:{param})"
    kind = f.get("type", "text")
    if kind == "number":
        return f"(CASE WHEN jsonb_typeof({values}->:{param}) = 'number' THEN {path}::numeric END)"
    if kind == "date":
        return f"(CASE WHEN {path} ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$' THEN {path}::date END)"
    if kind == "boolean":
        return f"(CASE WHEN jsonb_typeof({values}->:{param}) = 'boolean' THEN {path}::boolean END)"
    return f"lower({path})"


def _condition(f: dict[str, Any], op: str, raw: Any, i: int) -> tuple[str, dict[str, Any]]:
    kind = f.get("type", "text")
    if op not in OPS[kind]:
        raise QueryError(f"'{op}' doesn't apply to {kind} field '{f['name']}'")
    params: dict[str, Any] = {f"f{i}": f["name"]}
    if op == "exists":
        return f"(r.values ? :f{i})", params
    if op == "missing":
        return f"NOT (r.values ? :f{i})", params
    expr = _typed_sql(f, f"f{i}")
    if op == "in":
        values = raw if isinstance(raw, list) else [raw]
        params[f"v{i}"] = [str(_value(f, v)).lower() for v in values]
        return f"{expr} = ANY(:v{i})", params
    value = _value(f, raw)
    if op == "contains":
        params[f"v{i}"] = f"%{str(value).lower().replace('%', r'\%').replace('_', r'\_')}%"
        return f"{expr} LIKE :v{i}", params
    if kind in ("text", "choice"):
        value = str(value).lower()
    elif kind == "date":
        value = date.fromisoformat(value)  # the driver binds dates as dates
    params[f"v{i}"] = value
    return f"{expr} {_CMP[op]} :v{i}", params


async def run(
    db: AsyncSession,
    schemas: list[RecordSchema],
    collection_ids: list[uuid.UUID],
    *,
    filters: list[dict[str, Any]] | None = None,
    mode: str = "list",
    group_by: str | None = None,
    sort: str | None = None,
    descending: bool = False,
    limit: int = 100,
    latest_only: bool = False,
) -> Result:
    """Records of `schemas` in the caller's `collection_ids` matching every filter."""
    if not schemas:
        raise QueryError("No such record type in scope")
    conditions, params = [], {}
    for i, flt in enumerate(filters or []):
        f = _field(schemas, str(flt.get("field", "")))
        sql, p = _condition(f, str(flt.get("op", "eq")), flt.get("value"), i)
        conditions.append(sql)
        params |= p
    params |= {"sids": [s.id for s in schemas], "cids": list(collection_ids)}
    base = (
        "SELECT r.*, d.title, d.source_updated_at AS doc_time FROM records r "
        "JOIN documents d ON d.id = r.document_id AND NOT d.hidden "
        "WHERE r.schema_id IN :sids AND r.collection_id IN :cids"
    )
    if latest_only:  # one record per entity: the one in its most recent document
        base = (
            f"SELECT DISTINCT ON (x.key) x.* FROM ({base}) x "
            "ORDER BY x.key, x.doc_time DESC NULLS LAST, x.created_at DESC"
        )
    where = " AND ".join(conditions) or "TRUE"
    matched = f"SELECT * FROM ({base}) r WHERE {where}"
    expanding = [bindparam("sids", expanding=True), bindparam("cids", expanding=True)]

    total = await db.scalar(
        text(f"SELECT count(*) FROM ({matched}) m").bindparams(*expanding), params
    )
    result = Result(schemas=schemas, total=total or 0)
    if mode == "group":
        g = _field(schemas, group_by or "")
        params["g"] = g["name"]
        rows = await db.execute(
            text(
                f"SELECT m.values->>:g AS value, count(*) AS n FROM ({matched}) m "
                "GROUP BY 1 ORDER BY 2 DESC, 1"
            ).bindparams(*expanding),
            params,
        )
        result.groups = [(r.value, r.n) for r in rows]
        return result

    order = "m.title, m.key"
    if sort:
        s = _field(schemas, sort)
        params["fs"] = s["name"]
        direction = "DESC" if descending else "ASC"
        order = f"{_typed_sql(s, 'fs', alias='m')} {direction} NULLS LAST, m.key"
    params["lim"] = max(1, min(limit, MAX_ROWS))
    rows = await db.execute(
        text(f"SELECT * FROM ({matched}) m ORDER BY {order} LIMIT :lim").bindparams(*expanding),
        params,
    )
    result.rows = [
        Row(
            record_id=r.id,
            schema_id=r.schema_id,
            document_id=r.document_id,
            title=r.title,
            updated_at=r.doc_time,
            key=r.key,
            values=r.values,
            provenance=r.provenance,
        )
        for r in rows
    ]
    return result
