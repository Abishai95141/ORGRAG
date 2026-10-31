"""Knowledge-gap signals and clustering, without a database."""

import random

import numpy as np
import pytest

from app.embeddings.hash_provider import HashEmbedder
from app.gaps import cluster
from app.gaps.signals import coverage_missing, excerpt, gap_reasons, text_key

SEARCHED = [{"kind": "search", "label": "Found 0 sources", "detail": {"queries": ["x"]}}]
KNOWLEDGE = {"ref": 1, "origin": "knowledge", "document_id": "d", "title": "Leave policy"}
WEB = {"ref": 2, "origin": "web", "document_id": None, "title": "A blog"}
FILE = {"ref": 3, "origin": "attachment", "document_id": None, "title": "notes.md"}


def _missing_step(*facts):
    return {
        "kind": "reflect",
        "label": "Missing: " + "; ".join(facts),
        "detail": {"missing": facts},
    }


COVERED = {"kind": "reflect", "label": "Sources cover the question", "detail": {}}
THINKING = {"kind": "reflect", "label": "Reasoned", "detail": {"reasoning": "Let me look."}}


def test_text_key_folds_case_spacing_and_end_punctuation():
    assert text_key("How long is  parental leave?") == text_key(" how long is parental LEAVE ")
    assert text_key("Leave?!") == "leave"
    assert text_key("???") == ""


def test_coverage_reads_the_last_check():
    assert coverage_missing([]) == []
    assert coverage_missing([_missing_step("who approves")]) == ["who approves"]
    # A follow-up check that found everything clears the first one's list…
    assert coverage_missing([_missing_step("who approves"), THINKING, COVERED]) == []
    # …thinking steps and a skipped check don't.
    skipped = {"kind": "reflect", "label": "Coverage check skipped", "detail": {}}
    assert coverage_missing([_missing_step("a", "b"), THINKING, skipped]) == ["a", "b"]
    assert coverage_missing([COVERED, _missing_step("later")]) == ["later"]


def _reasons(content, sources=(), researched=True, checks=(), feedback=None):
    return gap_reasons(
        content=content,
        sources=list(sources),
        researched=researched,
        checks=list(checks),
        feedback=feedback,
    )


def test_what_makes_an_answer_a_gap():
    # Searched the knowledge base and cites none of it: an abstention.
    assert _reasons("It isn't in the knowledge base.") == ({"unanswered"}, [])
    assert _reasons("Per a blog post [2].", [WEB]) == ({"unanswered"}, [])
    # Cites the knowledge base: answered (unless downvoted or reported incomplete).
    assert _reasons("Sixteen weeks [1].", [KNOWLEDGE]) == (set(), [])
    assert _reasons("Sixteen weeks [1].", [KNOWLEDGE], feedback="down") == ({"downvoted"}, [])
    assert _reasons("Sixteen weeks [1].", [KNOWLEDGE], feedback="up") == (set(), [])
    reasons, missing = _reasons("Partly [1].", [KNOWLEDGE], checks=[_missing_step("for fathers")])
    assert reasons == {"incomplete"} and missing == ["for fathers"]
    # Small talk (no research) isn't a gap; a listed but uncited source doesn't count.
    assert _reasons("You're welcome!", researched=False) == (set(), [])
    assert _reasons("Not in the sources.", [KNOWLEDGE]) == ({"unanswered"}, [])
    # An answer about an attached file was never the knowledge base's to answer.
    assert _reasons("Your notes say so [3].", [FILE], feedback="down") == (set(), [])


def test_excerpt_drops_markers_and_cuts_on_a_word():
    assert excerpt("Sixteen weeks [1].") == "Sixteen weeks."
    markdown = "## Leave\n\n- Staff get **16 weeks** [1].\n- See [the policy](https://x.test) [2]."
    assert excerpt(markdown) == "Leave Staff get 16 weeks. See the policy."
    assert excerpt("| Plan | Weeks |\n|---|---|\n| A | 16 |") == "Plan Weeks A 16"
    long = excerpt("word " * 200, limit=40)
    assert long.endswith("…") and len(long) <= 41 and "  " not in long


QUESTIONS = [
    "How long is parental leave?",
    "how long is parental leave for fathers?",
    "How long is parental leave here?",
    "Who approves Friday deploys?",
    "Who approves Friday deploys to production?",
    "What is the office wifi password?",
]


async def _vectors(texts):
    vecs = np.array(await HashEmbedder(384).embed_documents(texts), dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def _partition(texts, groups):
    return sorted(sorted(texts[i] for i in g) for g in groups)


async def test_similar_questions_cluster_together_in_any_order():
    vectors = await _vectors(QUESTIONS)
    groups = cluster.average_linkage(vectors, np.ones(len(QUESTIONS)), 0.5)
    expected = sorted(
        [sorted(QUESTIONS[:3]), sorted(QUESTIONS[3:5]), [QUESTIONS[5]]],
    )
    assert _partition(QUESTIONS, groups) == expected
    for seed in range(5):
        shuffled = QUESTIONS[:]
        random.Random(seed).shuffle(shuffled)
        again = cluster.average_linkage(await _vectors(shuffled), np.ones(len(shuffled)), 0.5)
        assert _partition(shuffled, again) == expected
    # A higher bar splits the deploy pair; nothing reaches a perfect match.
    strict = cluster.average_linkage(vectors, np.ones(len(QUESTIONS)), 0.95)
    assert len(strict) == len(QUESTIONS)
    assert cluster.average_linkage(vectors[:0], np.ones(0), 0.5) == []


async def test_the_label_is_the_most_central_question_and_repeats_weigh():
    texts = QUESTIONS[:3]
    vectors = await _vectors(texts)
    members = [0, 1, 2]
    # "…parental leave?" is the closest to both others.
    assert cluster.most_central(members, vectors, np.ones(3)) == 0
    # Asked by many people, a less central phrasing becomes the label.
    assert cluster.most_central(members, vectors, np.array([1.0, 6.0, 1.0])) == 1
    # Average linkage with weights equals clustering every repeat.
    repeated = np.repeat(vectors, [1, 6, 1], axis=0)
    flat = cluster.average_linkage(repeated, np.ones(8), 0.5)
    assert [len(g) for g in flat] == [8]


async def test_the_centroid_scores_average_similarity():
    vectors = await _vectors(QUESTIONS[:3])
    weights = np.array([2.0, 1.0, 1.0])
    other = (await _vectors([QUESTIONS[3]]))[0]
    expected = float(np.average(vectors @ other, weights=weights))
    assert abs(float(cluster.centroid([0, 1, 2], vectors, weights) @ other) - expected) < 1e-6


async def test_embeddings_are_cached_per_model_and_text(monkeypatch):
    calls: list[list[str]] = []

    class Counting(HashEmbedder):
        name = "counting"

        async def embed_documents(self, texts):
            calls.append(list(texts))
            return await super().embed_documents(texts)

    monkeypatch.setattr(cluster, "get_embedder", lambda: Counting(64))
    monkeypatch.setattr(cluster, "_cache", type(cluster._cache)())
    first = await cluster.embed(["a question", "another one", "a question"])
    assert first.shape == (3, 64) and calls == [["a question", "another one"]]
    assert np.allclose(np.linalg.norm(first, axis=1), 1.0)
    second = await cluster.embed(["another one", "something new"])
    assert calls[-1] == ["something new"] and np.allclose(second[0], first[1])
    monkeypatch.setattr(cluster, "CACHE_SIZE", 2)
    await cluster.embed(["x", "y", "z"])
    assert len(cluster._cache) == 2
    assert (await cluster.embed([])).shape == (0, 64)


def test_the_threshold_follows_the_embedder(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "gap_similarity", None)
    monkeypatch.setattr(settings, "embedding_provider", "voyage")
    assert cluster.similarity_threshold() == 0.7
    monkeypatch.setattr(settings, "gap_similarity", 0.62)
    assert cluster.similarity_threshold() == 0.62


@pytest.mark.parametrize(
    ("answer", "missing"),
    [
        ("The knowledge base doesn't contain a drone delivery policy. Closest: [1].", True),
        ("## Short answer\n\n**The knowledge base has no Q4 2026 ARR figure.** It has Q3.", True),
        ("I couldn't find a travel policy for contractors in the documents [2].", True),
        ("There is no documented root cause for INC-2351 yet.", True),
        ("INC-2351 has no documented root cause yet [1].", True),
        ("Parental leave is 16 weeks [1]. The policy doesn't cover contractors.", False),
        ("Staff get 16 weeks of leave, and it can't be split [1].", False),
    ],
)
def test_an_opening_that_says_the_answer_is_not_there(answer, missing):
    from app.gaps.signals import gap_reasons, says_not_found

    assert says_not_found(answer) is missing and not says_not_found("")
    cited = [{"ref": 1, "origin": "knowledge"}, {"ref": 2, "origin": "knowledge"}]
    reasons, _ = gap_reasons(
        content=answer, sources=cited, researched=True, checks=None, feedback=None
    )
    assert ("unanswered" in reasons) is missing
