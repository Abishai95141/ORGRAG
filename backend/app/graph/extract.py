"""Entity and relation extraction with LightRAG's prompts and output parser.

`app/third_party/lightrag` holds LightRAG's JSON extraction prompts and its tolerant
parser, vendored verbatim. `parse_extraction` below ports the record validation of
LightRAG's `_process_json_extraction_result` (operate.py) onto those helpers; the
original is tied to LightRAG's storage and caching, so it isn't vendored as-is.

`extract_document` runs every chunk of a document through the model and stores the
result as per-chunk mentions (see app/models: KGMention), replacing whatever the
document contributed before."""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.client import complete_text
from app.llm.nvidia import NvidiaError
from app.models import Chunk, Document, KGEntity, KGMention, KGRelation
from app.third_party.lightrag import (
    PROMPTS,
    normalize_entity_name,
    normalize_entity_type,
    repair_vlm_json_escape_damage_nested,
    sanitize_and_normalize_extracted_text,
    tolerant_load_json_dict,
)

log = logging.getLogger("strata.graph")

ENTITY_TYPES = (
    "person", "team", "organization", "system", "policy", "project", "role", "event",
    "concept", "other",
)  # fmt: skip
# Replaces LightRAG's general-purpose type list (it documents this override point)
# with types that matter in company knowledge.
ENTITY_TYPES_GUIDANCE = """Classify each entity using one of the following types. \
If no type fits, use `Other`.

- Person: Named individuals (employees, contacts, approvers)
- Team: Teams, departments, committees and boards inside the organization
- Organization: Companies and outside bodies (vendors, business associates, customers, regulators)
- System: Software, services, platforms, tools and databases
- Policy: Policies, standards, agreements, contracts, procedures and runbooks
- Project: Projects, initiatives and programs, including codenames
- Role: Job titles and functions (e.g. Head of Security, charge nurse, incident commander)
- Event: Incidents, meetings, reviews and dated milestones
- Concept: Other important terms, data types and categories"""

# Statuses meaning "this model can't take more right now" (rate limit, quota, credit).
QUOTA_STATUSES = {402, 403, 429}
FALLBACK_COOLDOWN_S = 15 * 60
_primary_paused_until = 0.0

MAX_TOTAL_RECORDS = 40
MAX_ENTITY_RECORDS = 20
NAME_MAX = 256
CHUNK_CONCURRENCY = 2


@dataclass
class ExtractedEntity:
    name: str
    type: str
    description: str


@dataclass
class ExtractedRelation:
    source: str
    target: str
    keywords: str
    description: str


@dataclass
class Extraction:
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)


def build_prompts(chunk_text: str) -> tuple[str, str]:
    params = {
        "entity_types_guidance": ENTITY_TYPES_GUIDANCE,
        "examples": PROMPTS["entity_extraction_json_examples"][0],
        "language": "English",
        "max_total_records": MAX_TOTAL_RECORDS,
        "max_entity_records": MAX_ENTITY_RECORDS,
        "heading_context_block": "",
        "input_text": chunk_text,
    }
    system = PROMPTS["entity_extraction_json_system_prompt"].format(**params)
    user = PROMPTS["entity_extraction_json_user_prompt"].format(**params)
    return system, user


def parse_extraction(raw: str) -> Extraction:
    """Validate a model's JSON extraction the way LightRAG does: tolerant JSON recovery,
    name/type/description sanitising, dropping empty or self-referencing records."""
    out = Extraction()
    parsed = tolerant_load_json_dict(raw)
    if not parsed:
        return out
    parsed = repair_vlm_json_escape_damage_nested(parsed, context="extraction")
    seen: set[str] = set()
    entities = parsed.get("entities") if isinstance(parsed.get("entities"), list) else []
    for item in entities:
        if not isinstance(item, dict):
            continue
        name = normalize_entity_name(str(item.get("name", "")))[:NAME_MAX]
        etype = normalize_entity_type(
            sanitize_and_normalize_extracted_text(
                str(item.get("type", "")), remove_inner_quotes=True
            ),
            f"entity {name!r}",
        )
        description = sanitize_and_normalize_extracted_text(str(item.get("description", "")))
        if not name or etype is None or not description:
            continue
        if etype not in ENTITY_TYPES:
            etype = "other"
        if name.lower() not in seen:
            seen.add(name.lower())
            out.entities.append(ExtractedEntity(name, etype, description))
    relations = parsed.get("relationships") if isinstance(parsed.get("relationships"), list) else []
    for item in relations:
        if not isinstance(item, dict):
            continue
        source = normalize_entity_name(str(item.get("source", "")))[:NAME_MAX]
        target = normalize_entity_name(str(item.get("target", "")))[:NAME_MAX]
        description = sanitize_and_normalize_extracted_text(str(item.get("description", "")))
        if not source or not target or source.lower() == target.lower() or not description:
            continue
        keywords = sanitize_and_normalize_extracted_text(
            str(item.get("keywords", "")), remove_inner_quotes=True
        ).replace("，", ",")
        out.relations.append(ExtractedRelation(source, target, keywords, description))
    return out


async def _complete(model: str | None, system: str, user: str) -> str:
    """Call the extraction model; when it's out of quota (after the client's own retries),
    switch to GRAPH_EXTRACTION_FALLBACK_MODEL for a cooldown period before trying the
    primary again — so a free tier is used up first and paid credits only cover the rest."""
    global _primary_paused_until
    fallback = get_settings().graph_extraction_fallback_model
    if fallback and fallback != model and time.monotonic() < _primary_paused_until:
        return await complete_text(model=fallback, system=system, prompt=user, max_tokens=6000)
    try:
        return await complete_text(model=model, system=system, prompt=user, max_tokens=6000)
    except NvidiaError as exc:
        if not fallback or fallback == model or exc.status not in QUOTA_STATUSES:
            raise
        _primary_paused_until = time.monotonic() + FALLBACK_COOLDOWN_S
        log.warning(
            "Graph extraction: %s unavailable (%s); using %s for %d min",
            model, exc.status, fallback, FALLBACK_COOLDOWN_S // 60,
        )  # fmt: skip
        return await complete_text(model=fallback, system=system, prompt=user, max_tokens=6000)


async def extract_chunk(model: str | None, chunk: Chunk, title: str) -> Extraction:
    context = f"Document: {title}\n" + (f"Section: {chunk.heading}\n" if chunk.heading else "")
    system, user = build_prompts(context + "\n" + chunk.content)
    return parse_extraction(await _complete(model, system, user))


async def _entity_ids(
    db: AsyncSession, org_id: uuid.UUID, wanted: dict[str, tuple[str, str]]
) -> dict[str, uuid.UUID]:
    """name_key → id, creating entities that don't exist yet (first type wins)."""
    if not wanted:
        return {}
    await db.execute(
        insert(KGEntity)
        .values(
            [
                {"id": uuid.uuid4(), "org_id": org_id, "name": name, "name_key": key, "type": etype}
                for key, (name, etype) in wanted.items()
            ]
        )
        .on_conflict_do_nothing(index_elements=["org_id", "name_key"])
    )
    rows = await db.execute(
        select(KGEntity.name_key, KGEntity.id).where(
            KGEntity.org_id == org_id, KGEntity.name_key.in_(list(wanted))
        )
    )
    return dict(rows.all())


async def _relation_ids(
    db: AsyncSession, org_id: uuid.UUID, pairs: set[tuple[uuid.UUID, uuid.UUID]]
) -> dict[tuple[uuid.UUID, uuid.UUID], uuid.UUID]:
    if not pairs:
        return {}
    await db.execute(
        insert(KGRelation)
        .values(
            [
                {"id": uuid.uuid4(), "org_id": org_id, "source_id": a, "target_id": b}
                for a, b in pairs
            ]
        )
        .on_conflict_do_nothing(index_elements=["org_id", "source_id", "target_id"])
    )
    rows = await db.execute(
        select(KGRelation.source_id, KGRelation.target_id, KGRelation.id).where(
            KGRelation.org_id == org_id, KGRelation.source_id.in_({a for a, _ in pairs})
        )
    )
    return {(a, b): rid for a, b, rid in rows.all() if (a, b) in pairs}


def _key(name: str) -> str:
    return " ".join(name.lower().split())[:NAME_MAX]


async def extract_document(db: AsyncSession, doc: Document, model: str | None) -> dict[str, int]:
    """(Re)build what one document contributes to the graph. Caller commits."""
    chunks = list(
        (
            await db.execute(
                select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.ordinal)
            )
        ).scalars()
    )
    sem = asyncio.Semaphore(CHUNK_CONCURRENCY)

    async def run(chunk: Chunk) -> Extraction:
        async with sem:
            try:
                return await extract_chunk(model, chunk, doc.title)
            except Exception as exc:  # one failed chunk must not lose the document
                log.warning("Extraction failed for chunk %s of %s: %s", chunk.ordinal, doc.id, exc)
                raise

    results = await asyncio.gather(*(run(c) for c in chunks))

    await db.execute(delete(KGMention).where(KGMention.document_id == doc.id))
    wanted: dict[str, tuple[str, str]] = {}
    for result in results:
        for e in result.entities:
            wanted.setdefault(_key(e.name), (e.name, e.type))
        for r in result.relations:  # endpoints the model didn't list as entities
            wanted.setdefault(_key(r.source), (r.source, "other"))
            wanted.setdefault(_key(r.target), (r.target, "other"))
    ids = await _entity_ids(db, doc.org_id, wanted)

    pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for result in results:
        for r in result.relations:
            # Relations are undirected (as in LightRAG): store each pair once, ordered.
            a, b = sorted((ids[_key(r.source)], ids[_key(r.target)]), key=str)
            pairs.add((a, b))
    rel_ids = await _relation_ids(db, doc.org_id, pairs)

    mentions = []
    for chunk, result in zip(chunks, results, strict=True):
        # Every row carries every column: a multi-row INSERT needs one shape.
        base = {"org_id": doc.org_id, "chunk_id": chunk.id, "document_id": doc.id,
                "collection_id": doc.collection_id, "entity_id": None, "relation_id": None,
                "keywords": ""}  # fmt: skip
        for e in result.entities:
            mentions.append({**base, "entity_id": ids[_key(e.name)], "description": e.description})
        for r in result.relations:
            a, b = sorted((ids[_key(r.source)], ids[_key(r.target)]), key=str)
            mentions.append(
                {**base, "relation_id": rel_ids[(a, b)], "description": r.description,
                 "keywords": r.keywords}
            )  # fmt: skip
    if mentions:
        await db.execute(insert(KGMention).values([{"id": uuid.uuid4(), **m} for m in mentions]))
    doc.graph_hash = doc.content_hash
    return {
        "chunks": len(chunks),
        "entities": sum(len(r.entities) for r in results),
        "relations": sum(len(r.relations) for r in results),
    }


async def prune_orphans(db: AsyncSession, org_id: uuid.UUID) -> None:
    """Entities and relations no longer backed by any chunk."""
    await db.execute(
        text(
            "DELETE FROM kg_relations r WHERE r.org_id = :org AND NOT EXISTS "
            "(SELECT 1 FROM kg_mentions m WHERE m.relation_id = r.id)"
        ),
        {"org": org_id},
    )
    await db.execute(
        text(
            "DELETE FROM kg_entities e WHERE e.org_id = :org AND NOT EXISTS "
            "(SELECT 1 FROM kg_mentions m WHERE m.entity_id = e.id)"
        ),
        {"org": org_id},
    )
