import uuid

from app.retrieval.hybrid import Hit, diversify, rrf_fuse, snippet


def _ids(n):
    return [uuid.uuid4() for _ in range(n)]


def test_rrf_rewards_agreement_between_lists():
    a, b, c, d = _ids(4)
    scores = rrf_fuse([[a, b, c], [b, d, a]])
    ranked = sorted(scores, key=scores.get, reverse=True)
    # b is 2nd and 1st, a is 1st and 3rd → b wins; both beat items in one list.
    assert ranked[:2] == [b, a]
    assert scores[c] < scores[a] and scores[d] < scores[a]


def test_rrf_score_formula():
    a = uuid.uuid4()
    assert abs(rrf_fuse([[a]], k=60)[a] - 1 / 61) < 1e-12


def _hit(doc):
    return Hit(
        uuid.uuid4(),
        doc,
        0,
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        "upload",
        "t",
        None,
        "",
        "c",
        1.0,
    )


def test_diversify_caps_chunks_per_document():
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    hits = [_hit(d1), _hit(d1), _hit(d1), _hit(d2)]
    out = diversify(hits, limit=10, per_doc=2)
    assert [h.document_id for h in out] == [d1, d1, d2]
    assert len(diversify(hits, limit=1)) == 1


def test_snippet_truncates_on_word_boundary():
    text = "alpha beta gamma " * 50
    s = snippet(text, 40)
    assert s.endswith("…") and len(s) <= 41 and "  " not in s
