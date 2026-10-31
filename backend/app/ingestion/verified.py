"""Verified answers as documents (roadmap §6 #3).

A verified answer is indexed into its collection like any document, through a hidden
per-collection source of kind `verified`, so it's found by search, respects the
collection's permissions, is cited with its own badge, and reaches MCP clients without
special cases. It carries a ranking boost while valid; once it expires it's hidden from
search until an admin re-verifies it.
"""

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.citations import strip_citations
from app.ingestion.chunker import estimate_tokens
from app.ingestion.pipeline import write_chunks
from app.models import Collection, CollectionAccess, Document, Source, User, VerifiedAnswer

VERIFIED_KIND = "verified"
DEFAULT_TTL_DAYS = 90
BOOST = 2  # ×1.3 ranking while valid (documents take -3..3)


class LeakError(ValueError):
    """Verifying into this collection would show people sources they can't read."""


def is_expired(va: VerifiedAnswer, now: datetime | None = None) -> bool:
    return va.expires_at is not None and va.expires_at <= (now or datetime.now(UTC))


def expiry(days: int | None, now: datetime | None = None) -> datetime | None:
    return None if days is None else (now or datetime.now(UTC)) + timedelta(days=days)


async def verified_source(db: AsyncSession, collection: Collection) -> Source:
    source = (
        await db.execute(
            select(Source).where(
                Source.collection_id == collection.id, Source.kind == VERIFIED_KIND
            )
        )
    ).scalar_one_or_none()
    if source is None:
        source = Source(
            org_id=collection.org_id,
            collection_id=collection.id,
            kind=VERIFIED_KIND,
            name="Verified answers",
            config={},
            status="ready",
        )
        db.add(source)
        await db.flush()
    return source


async def _readers(db: AsyncSession, collection: Collection) -> set[uuid.UUID] | None:
    """Users granted a restricted collection; None means everyone in the org."""
    if collection.visibility != "restricted":
        return None
    rows = await db.execute(
        select(CollectionAccess.user_id).where(CollectionAccess.collection_id == collection.id)
    )
    return set(rows.scalars())


async def check_sources(
    db: AsyncSession, target: Collection, sources: list[dict[str, Any]]
) -> None:
    """Refuse a target collection whose readers could see an answer built from
    documents they can't read: another org's documents, or a restricted collection
    whose readers don't include everyone who can read the target. (Admins read
    everything either way.)"""
    target_readers = await _readers(db, target)
    for s in sources:
        if s.get("origin", "knowledge") != "knowledge" or not s.get("document_id"):
            continue
        if s.get("org_id") and uuid.UUID(str(s["org_id"])) != target.org_id:
            raise LeakError(
                f"This answer cites “{s.get('title')}” from {s.get('org_name') or 'another org'}; "
                "verified answers can only use this organization's documents."
            )
        doc = await db.get(Document, uuid.UUID(str(s["document_id"])))
        if doc is None or doc.collection_id == target.id:
            continue
        cited = await db.get(Collection, doc.collection_id)
        cited_readers = await _readers(db, cited) if cited else None
        if cited_readers is None:
            continue
        if target_readers is None or not target_readers <= cited_readers:
            raise LeakError(
                f"This answer cites “{s.get('title')}” from the restricted collection "
                f"“{cited.name}”. Verify it into that collection, or one only its "
                "readers can see."
            )


def render(va: VerifiedAnswer, *, verifier: str | None, owner: str | None) -> str:
    """The indexed text: question as title, the answer without its [n] markers (they
    pointed at the original conversation's sources), then who vouches for it."""
    answer = strip_citations(va.answer)
    answer = re.sub(r"[ \t]+([.,;:!?])", r"\1", answer).strip()
    stamp = f"Verified answer, reviewed by {verifier or 'an admin'} on {va.verified_at:%B %-d, %Y}"
    if owner:
        stamp += f"; owner: {owner}"
    if va.expires_at:
        stamp += f"; valid until {va.expires_at:%B %-d, %Y}"
    lines = [f"# {va.question.strip()}", "", answer, "", "---", stamp + "."]
    based_on = [s for s in va.sources if s.get("title")]
    if based_on:
        lines += ["", "Based on:"]
        for s in based_on:
            where = " · ".join(x for x in (s.get("org_name"), s.get("collection_name")) if x)
            lines.append(f"- {s['title']}" + (f" ({where})" if where else ""))
    return "\n".join(lines) + "\n"


async def index(db: AsyncSession, va: VerifiedAnswer, collection: Collection) -> Document:
    """Create or refresh the answer's document (moving it if the collection changed)."""
    names = {
        u.id: u.name or u.email
        for u in (
            await db.execute(
                select(User).where(User.id.in_([i for i in (va.owner_id, va.verified_by_id) if i]))
            )
        ).scalars()
    }
    content = render(va, verifier=names.get(va.verified_by_id), owner=names.get(va.owner_id))
    source = await verified_source(db, collection)
    doc = await db.get(Document, va.document_id) if va.document_id else None
    if doc is None:
        doc = Document(
            id=uuid.uuid4(),
            org_id=va.org_id,
            external_id=f"verified:{va.id}",
            mime="text/markdown",
        )
        db.add(doc)
    doc.source_id = source.id
    doc.collection_id = collection.id
    doc.title = va.question.strip()[:512]
    doc.content = content
    doc.content_hash = hashlib.sha256(content.encode()).hexdigest()
    doc.token_count = estimate_tokens(content)
    doc.source_updated_at = va.verified_at
    doc.meta = {"verified_answer_id": str(va.id)}
    doc.boost = BOOST
    doc.hidden = is_expired(va)
    await db.flush()
    va.document_id = doc.id
    await write_chunks(db, doc)
    return doc


async def expire_due(db: AsyncSession, now: datetime | None = None) -> int:
    """Hide the documents of answers whose expiry has passed (worker scheduler)."""
    now = now or datetime.now(UTC)
    expired = select(VerifiedAnswer.document_id).where(VerifiedAnswer.expires_at <= now)
    result = await db.execute(
        update(Document)
        .where(Document.id.in_(expired), ~Document.hidden)
        .values(hidden=True)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount or 0
