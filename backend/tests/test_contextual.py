"""Contextual Retrieval: generated chunk contexts are indexed but never shown."""

import uuid
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import select

from tests.test_api_integration import _general, _ingest, _org, _signup

pytestmark = pytest.mark.integration

SECTION = " ".join(["The quarterly review covers spending, hiring and roadmap risk."] * 40)
LONG_DOC = (
    f"# Annual plan\n\n## Budget\n\n{SECTION}\n\n## Hiring\n\n{SECTION}\n\n## Risks\n\n{SECTION}"
)


class FakeAnthropic:
    def __init__(self):
        self.requests: list[dict] = []
        self.messages = NS(create=self._create)

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        n = len(self.requests)
        return NS(
            content=[NS(type="text", text=f"Part {n} of the zebracorn annual plan.")],
            usage=NS(
                input_tokens=20,
                output_tokens=8,
                cache_read_input_tokens=0 if n == 1 else 900,
                cache_creation_input_tokens=900 if n == 1 else 0,
            ),
        )


async def test_contexts_are_generated_indexed_and_never_shown(app, monkeypatch):
    from app.core.config import get_settings
    from app.core.db import SessionLocal
    from app.ingestion import contextual
    from app.models import Chunk, Document

    fake = FakeAnthropic()
    monkeypatch.setattr(get_settings(), "contextual_retrieval", True)
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-test")
    monkeypatch.setattr(contextual, "get_client", lambda: fake)

    admin = await _signup(app, f"cx{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Context Co")
    cols = (await admin.get(f"/api/orgs/{org['slug']}/collections")).json()
    files = {"plan.md": LONG_DOC, "note.md": "# Parking\n\nVisitors park on level two."}
    await _ingest(admin, org["slug"], _general(org, cols), files)  # sync, then contextualize

    async with SessionLocal() as db:
        docs = {
            d.title: d
            for d in (
                await db.execute(select(Document).where(Document.org_id == uuid.UUID(org["id"])))
            ).scalars()
        }
        plan, note = docs["Annual plan"], docs["Parking"]
        chunks = list(
            (await db.execute(select(Chunk).where(Chunk.document_id == plan.id))).scalars()
        )
        note_chunks = list(
            (await db.execute(select(Chunk).where(Chunk.document_id == note.id))).scalars()
        )
    assert len(chunks) > 1 and all("zebracorn" in c.context for c in chunks)
    assert plan.context_hash == contextual.context_hash(plan)
    # A single-chunk document is its own context: no call, but marked done.
    assert note_chunks[0].context == "" and note.context_hash == contextual.context_hash(note)
    assert len(fake.requests) == len(chunks)

    # The document is cached in the first block; the chunk comes second; no thinking.
    blocks = fake.requests[0]["messages"][0]["content"]
    assert blocks[0]["cache_control"] == {"type": "ephemeral"} and "<document>" in blocks[0]["text"]
    assert "<chunk>" in blocks[1]["text"] and fake.requests[0]["thinking"] == {"type": "disabled"}

    # The context is keyword-searchable, but results show only the chunk text.
    body = {"query": "zebracorn"}
    results = (await admin.post(f"/api/orgs/{org['slug']}/search", json=body)).json()["results"]
    assert results and results[0]["title"] == "Annual plan"
    assert all("zebracorn" not in r["snippet"] for r in results)


async def test_rewritten_chunks_are_contextualized_again(app, monkeypatch):
    from app.core.db import SessionLocal
    from app.ingestion import contextual
    from app.ingestion.pipeline import write_chunks
    from app.models import Document

    fake = FakeAnthropic()
    monkeypatch.setattr(contextual, "get_client", lambda: fake)
    admin = await _signup(app, f"cy{uuid.uuid4().hex[:6]}")
    org = await _org(admin, "Context Two")
    cols = (await admin.get(f"/api/orgs/{org['slug']}/collections")).json()
    source = await _ingest(admin, org["slug"], _general(org, cols), {"plan.md": LONG_DOC})

    async with SessionLocal() as db:
        stats = await contextual.contextualize_source(db, uuid.UUID(source["id"]))
        assert stats["documents"] == 1 and stats["cost_usd"] > 0
        # Nothing pending: a second run makes no calls.
        before = len(fake.requests)
        assert (await contextual.contextualize_source(db, uuid.UUID(source["id"])))[
            "documents"
        ] == 0
        assert len(fake.requests) == before
        # Re-chunking drops the contexts, so the document is pending again.
        doc = (
            await db.execute(select(Document).where(Document.source_id == uuid.UUID(source["id"])))
        ).scalar_one()
        await write_chunks(db, doc)
        await db.commit()
        assert doc.context_hash is None
        assert (await contextual.contextualize_source(db, uuid.UUID(source["id"])))[
            "documents"
        ] == 1
