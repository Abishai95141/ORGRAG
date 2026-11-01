import uuid
from types import SimpleNamespace

from app.agent.citations import CitationRegistry, cited_refs, strip_citations
from app.retrieval.hybrid import Hit
from app.schemas.common import SourceRef

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
    assert set(ref) == set(SourceRef.model_fields)
    SourceRef(**ref)
    assert ref["origin"] == "knowledge"
    assert ref["org_slug"] == "acme" and ref["title"] == "Handbook"


def test_web_and_attachment_sources_share_numbering():
    reg = CitationRegistry()
    doc, _ = reg.register(_hit(uuid.uuid4()), ORG, COL)
    web, new = reg.register_web("https://example.com/a/", "Example", "Public page text")
    again, new_again = reg.register_web("https://example.com/a", "Example", "dup")
    attachment = SimpleNamespace(id=uuid.uuid4(), filename="brief.pdf", content="Brief text")
    att, _ = reg.register_attachment(attachment)
    assert (doc.ref, web.ref, att.ref) == (1, 2, 3)
    assert new and not new_again and again is web
    for entry in (web, att):
        SourceRef(**entry.to_source_ref())
    assert web.to_source_ref()["origin"] == "web" and web.to_source_ref()["org_id"] is None
    assert att.to_source_ref()["attachment_id"] == str(attachment.id)


def test_cited_refs_and_strip():
    text = "Deploys stop on Friday [2]. Rollbacks anytime [1][2]. Not a cite [x]."
    assert cited_refs(text) == [2, 1]
    assert strip_citations(text) == "Deploys stop on Friday . Rollbacks anytime . Not a cite [x]."


def test_earlier_answers_keep_their_research_and_sources():
    """In the history an earlier answer loses its [n] (this turn numbers sources afresh)
    but says what research it rests on and what it cited, so the model doesn't take it
    for an unsourced claim to re-audit."""
    from app.agent.engine import _earlier_answer

    sources = [
        {"ref": 1, "title": "Travel policy"},
        {"ref": 2, "title": "Team directory"},
        {"ref": 3, "title": "Retrieved, never cited"},
    ]
    trace = [
        {"kind": "plan", "label": "Planned 2 sub-questions", "status": "done"},
        {"kind": "records", "label": "Found 1 match", "status": "done"},
        {"kind": "scan", "label": "Found 5 of 16", "status": "done"},
        {"kind": "search", "label": "Searching", "status": "error"},
    ]
    m = SimpleNamespace(
        content="Amara approves it [2][1]. Her email is listed [2].", sources=sources, trace=trace
    )
    text = _earlier_answer(m)
    assert "[1]" not in text and "[2]" not in text
    assert text.endswith(
        "(Research behind this answer: records: Found 1 match; scan: Found 5 of 16. "
        "Sources it cited: Team directory; Travel policy.)"
    )
    assert "Retrieved, never cited" not in text and "Planned" not in text
    plain = SimpleNamespace(content="Hello! How can I help?", sources=[], trace=[])
    assert _earlier_answer(plain) == "Hello! How can I help?"
