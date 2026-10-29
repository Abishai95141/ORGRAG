import uuid
from types import SimpleNamespace

from app.agent.citations import CitationRegistry, cited_refs, strip_citations
from app.retrieval.hybrid import Hit

ORG = SimpleNamespace(id=uuid.uuid4(), slug="acme", name="Acme", hue=10)
COL = SimpleNamespace(id=uuid.uuid4(), name="General")


def _hit(doc, title="Doc"):
    return Hit(
        uuid.uuid4(),
        doc,
        0,
        ORG.id,
        COL.id,
        uuid.uuid4(),
        "upload",
        title,
        None,
        "",
        "Some content here.",
        0.9,
    )


def test_same_document_keeps_its_number():
    reg = CitationRegistry()
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    first, new1 = reg.register(_hit(d1), ORG, COL)
    second, new2 = reg.register(_hit(d2), ORG, COL)
    again, new3 = reg.register(_hit(d1), ORG, COL)
    assert (first.ref, second.ref, again.ref) == (1, 2, 1)
    assert (new1, new2, new3) == (True, True, False)
    assert len(reg) == 2 and reg.get(2) is second and reg.get(9) is None


def test_source_ref_shape_matches_contract():
    reg = CitationRegistry()
    entry, _ = reg.register(_hit(uuid.uuid4(), "Handbook"), ORG, COL)
    ref = entry.to_source_ref()
    assert set(ref) == {
        "ref",
        "document_id",
        "chunk_id",
        "title",
        "url",
        "snippet",
        "org_id",
        "org_slug",
        "org_name",
        "org_hue",
        "collection_name",
        "source_kind",
        "score",
    }
    assert ref["org_slug"] == "acme" and ref["title"] == "Handbook"


def test_cited_refs_and_strip():
    text = "Deploys stop on Friday [2]. Rollbacks anytime [1][2]. Not a cite [x]."
    assert cited_refs(text) == [2, 1]
    assert strip_citations(text) == "Deploys stop on Friday . Rollbacks anytime . Not a cite [x]."
