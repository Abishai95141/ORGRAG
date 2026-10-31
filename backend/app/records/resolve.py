"""Who holds a role, and what a name or code stands for, across every active record
type in scope (roadmap §4 item 3, `resolve_role`).

Answers to multi-hop questions stall on a role: "the change approver of the owning team",
"the Head of Security". Registers already hold the answer, as a column named for the role
(a directory's "Change approver") or as a value that names it ("Tomasz Wierzbicki (Head of
Security)"). This looks for both in one pass, optionally narrowed to the rows that mention
a team, system or code (`of`: "PLAT", "Platform", "Driftwood"), so the agent can take the
next hop with an exact, cited lookup instead of another search."""

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RecordSchema
from app.records import query
from app.records.tables import normalize

MAX_MATCHES = 20


@dataclass
class Match:
    row: query.Row
    schema: RecordSchema
    field: str  # the field naming the role, or holding the value that names it
    value: Any  # that field's value in this row: the role holder, or the matching text
    by: str  # "column" (a field named for the role) or "value" (a value naming the role)


def role_fields(schema: RecordSchema, role: str) -> list[dict]:
    """Fields named for the role: every word of the role in the field's name, label or
    one of its aliases ("change approver" → change_approver)."""
    words = set(normalize(role).split("_")) - {"", "the", "of", "a", "an"}
    if not words:
        return []
    out = []
    for f in schema.fields:
        names = [f["name"], normalize(f.get("label", "")), *map(normalize, f.get("aliases", []))]
        if any(words <= set(n.split("_")) for n in names):
            out.append(f)
    return out


def _like(value: str) -> str:
    return "%" + value.lower().replace("%", r"\%").replace("_", r"\_") + "%"


async def resolve(
    db: AsyncSession,
    schemas: list[RecordSchema],
    collection_ids: list[uuid.UUID],
    role: str,
    of: str | None = None,
    limit: int = MAX_MATCHES,
) -> list[Match]:
    role, of = role.strip(), (of or "").strip() or None
    if not role or not schemas:
        return []
    by_id = {s.id: s for s in schemas}
    params: dict[str, Any] = {
        "sids": [s.id for s in schemas],
        "cids": list(collection_ids),
        "lim": limit,
    }
    # A row "mentions" `of` when any of its values contains it (codes and names alike:
    # PLAT and Platform sit in the same directory row).
    mentions = (
        "EXISTS (SELECT 1 FROM jsonb_each_text(m.values) kv WHERE lower(kv.value) LIKE :of)"
        if of
        else "TRUE"
    )
    if of:
        params["of"] = _like(of)
    matches: list[Match] = []

    # 1. Columns named for the role: the holder is that column's value in matching rows.
    for schema in schemas:
        for f in role_fields(schema, role):
            rows = await db.execute(
                text(
                    f"SELECT * FROM ({query.SCOPED}) m WHERE m.schema_id = :sid "
                    f"AND m.values ? :field AND {mentions} ORDER BY m.title, m.key LIMIT :lim"
                ).bindparams(*query.EXPANDING),
                {**params, "sid": schema.id, "field": f["name"]},
            )
            for r in rows:
                holder = r.values[f["name"]]
                matches.append(Match(query.row_of(r), schema, f["name"], holder, "column"))

    # 2. Values that name the role ("Tomasz Wierzbicki (Head of Security)"), as whole words
    # ("lead" doesn't match "leadership"). Edges are any non-word character or the end of
    # the value, so roles that start or end in punctuation ("C++ lead", "SRE (primary)")
    # match too.
    params["role"] = r"(^|[^[:alnum:]_])" + re.escape(role.lower()) + r"($|[^[:alnum:]_])"
    rows = await db.execute(
        text(
            f"SELECT m.*, kv.key AS matched_field, kv.value AS matched_value "
            f"FROM ({query.SCOPED}) m, jsonb_each_text(m.values) kv "
            f"WHERE lower(kv.value) ~ :role AND {mentions} ORDER BY m.title, m.key LIMIT :lim"
        ).bindparams(*query.EXPANDING),
        params,
    )
    seen = {(m.row.record_id, m.field) for m in matches}
    for r in rows:
        if (r.id, r.matched_field) not in seen:
            schema = by_id[r.schema_id]
            matches.append(
                Match(query.row_of(r), schema, r.matched_field, r.matched_value, "value")
            )
    return matches[:limit]
