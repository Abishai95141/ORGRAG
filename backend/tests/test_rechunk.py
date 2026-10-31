"""Re-chunking documents indexed before the size cap (audit C2)."""

import pytest
from sqlalchemy import func, select, update

from tests.test_api_integration import _general, _ingest, _org, _signup


@pytest.mark.integration
async def test_rechunk_rebuilds_only_oversized_documents(app, monkeypatch, capsys):
    from app.core.db import SessionLocal
    from app.ingestion import rechunk
    from app.ingestion.chunker import MAX_CHUNK_CHARS
    from app.models import Chunk, Document

    admin = await _signup(app, "rc")
    org = await _org(admin, "Rechunk Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    blob = " ".join(f"token{i}" for i in range(5000))
    await _ingest(
        admin, slug, _general(org, cols), {"big.md": f"# Big\n\n{blob}", "ok.md": "# Ok\n\nFine."}
    )
    async with SessionLocal() as db:
        big = (await db.execute(select(Document).where(Document.title == "Big"))).scalar_one()
        # Simulate the pre-cap index: one giant chunk holding the whole document.
        vector = (
            await db.execute(select(Chunk.embedding).where(Chunk.document_id == big.id).limit(1))
        ).scalar_one()
        await db.execute(Chunk.__table__.delete().where(Chunk.document_id == big.id))
        await db.execute(
            Chunk.__table__.insert().values(
                org_id=big.org_id, document_id=big.id, collection_id=big.collection_id,
                ordinal=0, heading="", content=blob, token_count=6000, embedding=vector,
            )
        )  # fmt: skip
        await db.execute(update(Document).where(Document.id == big.id).values(graph_hash="x"))
        await db.commit()

    await rechunk.main(apply=False)
    assert "1 document(s)" in capsys.readouterr().out
    await rechunk.main(apply=True)

    async with SessionLocal() as db:
        longest = (
            await db.execute(
                select(func.max(func.length(Chunk.content))).where(Chunk.document_id == big.id)
            )
        ).scalar()
        count = (await db.execute(select(func.count()).where(Chunk.document_id == big.id))).scalar()
        doc = await db.get(Document, big.id)
    assert longest <= MAX_CHUNK_CHARS and count > 10 and doc.graph_hash is None
    await rechunk.main(apply=False)
    assert "0 document(s)" in capsys.readouterr().out
