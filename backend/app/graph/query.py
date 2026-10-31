"""Read the knowledge graph within a retrieval scope.

Everything is filtered through mentions whose chunk sits in a collection the asker
can see: an entity known only from a restricted document doesn't exist for someone
without access, and descriptions are assembled only from visible chunks."""

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

LIST_LIMIT = 300
DESCRIPTION_CHARS = 400
RELATION_LIMIT = 80


@dataclass
class EntityRow:
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    type: str
    description: str
    chunk_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class RelationRow:
    other_id: uuid.UUID
    other_name: str
    other_type: str
    keywords: str
    description: str
    chunk_ids: list[uuid.UUID] = field(default_factory=list)


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


_LIST_SQL = """
    SELECT e.id, e.org_id, e.name, e.type,
           string_agg(DISTINCT m.description, ' | ') AS description,
           array_agg(DISTINCT m.chunk_id) AS chunk_ids
    FROM kg_entities e
    JOIN kg_mentions m ON m.entity_id = e.id AND m.collection_id IN :cids
    WHERE {where}
    GROUP BY e.id
    {having}
    ORDER BY e.name
    LIMIT :lim
"""


async def list_entities(
    db: AsyncSession,
    collection_ids: list[uuid.UUID],
    *,
    entity_type: str | None = None,
    contains: str | None = None,
    limit: int = LIST_LIMIT,
) -> tuple[list[EntityRow], int]:
    """Entities visible in scope, optionally of one type and/or whose name or visible
    description contains `contains`. Returns (rows, total matching)."""
    if not collection_ids:
        return [], 0
    where, having, params = ["TRUE"], "", {"cids": collection_ids, "lim": limit}
    if entity_type:
        where.append("e.type = :etype")
        params["etype"] = entity_type
    if contains:
        # Facts often sit on relations ("Basalt is in the High risk tier"), so match the
        # entity's visible relation descriptions and keywords too.
        having = (
            "HAVING e.name ILIKE :q ESCAPE '\\' "
            "OR bool_or(m.description ILIKE :q ESCAPE '\\') "
            "OR EXISTS (SELECT 1 FROM kg_relations r "
            "JOIN kg_mentions rm ON rm.relation_id = r.id AND rm.collection_id IN :cids "
            "WHERE (r.source_id = e.id OR r.target_id = e.id) "
            "AND (rm.description ILIKE :q ESCAPE '\\' OR rm.keywords ILIKE :q ESCAPE '\\'))"
        )
        params["q"] = _like(contains)
    sql = _LIST_SQL.format(where=" AND ".join(where), having=having)
    stmt = text(sql).bindparams(bindparam("cids", expanding=True))
    rows = (await db.execute(stmt, params)).all()
    count_sql = text(f"SELECT count(*) FROM ({sql.replace('LIMIT :lim', '')}) t").bindparams(
        bindparam("cids", expanding=True)
    )
    total = (await db.execute(count_sql, {k: v for k, v in params.items() if k != "lim"})).scalar()
    return [
        EntityRow(r.id, r.org_id, r.name, r.type, r.description[:DESCRIPTION_CHARS], r.chunk_ids)
        for r in rows
    ], total or 0


_TOKEN = re.compile(r"[a-z0-9]+")


def name_tokens(name: str) -> frozenset[str]:
    """Order-insensitive word set: "High Risk Tier" and "Risk Tier High" are the same
    entity under two spellings, a common extraction artefact."""
    return frozenset(_TOKEN.findall(name.lower()))


async def find_entity(
    db: AsyncSession, collection_ids: list[uuid.UUID], name: str
) -> list[EntityRow]:
    """Visible entities matching a name. Every word of the query must appear in the
    name; spelling variants with exactly the query's words come first (and are merged
    by callers), then the shortest other matches."""
    words = sorted(name_tokens(name))
    if not collection_ids or not words:
        return []
    conditions = " AND ".join(f"e.name ILIKE :w{i}" for i in range(len(words)))
    stmt = text(
        f"""
        SELECT e.id, e.org_id, e.name, e.type,
               string_agg(DISTINCT m.description, ' | ') AS description,
               array_agg(DISTINCT m.chunk_id) AS chunk_ids
        FROM kg_entities e
        JOIN kg_mentions m ON m.entity_id = e.id AND m.collection_id IN :cids
        WHERE {conditions}
        GROUP BY e.id
        ORDER BY length(e.name)
        LIMIT 12
        """
    ).bindparams(bindparam("cids", expanding=True))
    params = {"cids": collection_ids, **{f"w{i}": _like(w) for i, w in enumerate(words)}}
    rows = [
        EntityRow(r.id, r.org_id, r.name, r.type, r.description[:DESCRIPTION_CHARS], r.chunk_ids)
        for r in (await db.execute(stmt, params)).all()
    ]
    want = name_tokens(name)
    exact = [r for r in rows if name_tokens(r.name) == want]
    return exact + [r for r in rows if name_tokens(r.name) != want][:3]


async def relations_of(
    db: AsyncSession, collection_ids: list[uuid.UUID], entity_ids: list[uuid.UUID] | uuid.UUID
) -> tuple[list[RelationRow], int]:
    """Visible relations of an entity (or of several spellings of one), with the entity
    on the other end."""
    ids = [entity_ids] if isinstance(entity_ids, uuid.UUID) else list(entity_ids)
    stmt = text(
        """
        SELECT o.id AS other_id, o.name AS other_name, o.type AS other_type,
               string_agg(DISTINCT m.keywords, ', ') AS keywords,
               string_agg(DISTINCT m.description, ' | ') AS description,
               array_agg(DISTINCT m.chunk_id) AS chunk_ids,
               count(*) OVER () AS total
        FROM kg_relations r
        JOIN kg_mentions m ON m.relation_id = r.id AND m.collection_id IN :cids
        JOIN kg_entities o ON o.id = CASE WHEN r.source_id IN :eids THEN r.target_id
                                          ELSE r.source_id END
        WHERE (r.source_id IN :eids OR r.target_id IN :eids) AND o.id NOT IN :eids
        GROUP BY o.id
        ORDER BY o.name
        LIMIT :lim
        """
    ).bindparams(bindparam("cids", expanding=True), bindparam("eids", expanding=True))
    rows = (
        await db.execute(stmt, {"cids": collection_ids, "eids": ids, "lim": RELATION_LIMIT})
    ).all()
    total = rows[0].total if rows else 0
    return [
        RelationRow(
            r.other_id, r.other_name, r.other_type, r.keywords or "",
            r.description[:DESCRIPTION_CHARS], r.chunk_ids,
        )
        for r in rows
    ], total  # fmt: skip


async def linked_names(
    db: AsyncSession, collection_ids: list[uuid.UUID], entity_ids: list[uuid.UUID], per: int = 8
) -> dict[uuid.UUID, list[str]]:
    """For each entity, the names of up to `per` visible neighbours — enough for a list to
    show the facts that live on relations ("linked: Risk Tier High, Tom Okafor")."""
    if not entity_ids:
        return {}
    stmt = text(
        """
        SELECT e_id, other FROM (
          SELECT x.e_id, o.name AS other,
                 row_number() OVER (PARTITION BY x.e_id ORDER BY o.name) AS n
          FROM (
            SELECT DISTINCT r.id AS rid,
                   CASE WHEN r.source_id IN :eids THEN r.source_id ELSE r.target_id END AS e_id,
                   CASE WHEN r.source_id IN :eids THEN r.target_id ELSE r.source_id END AS o_id
            FROM kg_relations r
            JOIN kg_mentions m ON m.relation_id = r.id AND m.collection_id IN :cids
            WHERE r.source_id IN :eids OR r.target_id IN :eids
          ) x JOIN kg_entities o ON o.id = x.o_id
        ) t WHERE n <= :per
        """
    ).bindparams(bindparam("cids", expanding=True), bindparam("eids", expanding=True))
    out: dict[uuid.UUID, list[str]] = {}
    for row in (
        await db.execute(stmt, {"cids": collection_ids, "eids": list(entity_ids), "per": per})
    ).all():
        out.setdefault(row.e_id, []).append(row.other)
    return out
