"""The eval's grounding metrics and statistics (no model, no database)."""

import pytest

from eval import grounding
from eval.stats import cohen_kappa, paired_bootstrap

ANSWER = """Here's what I found.

Friday deploys need approval from the on-call engineering manager [1]. Rollbacks \
are always allowed. [2]

- Hotels in London are capped at $350 a night [3][4].
- Elsewhere the cap is $250 [3].

| City | Cap |
|---|---|
| London | $350 [3] |

```
not a statement [9]
```
## Sources
"""


def test_statements_split_sentences_items_and_rows_with_their_citations():
    items = grounding.statements(ANSWER)
    assert [(s.text, s.refs) for s in items] == [
        ("Here's what I found.", []),
        ("Friday deploys need approval from the on-call engineering manager.", [1]),
        # A citation after the full stop stays with its sentence.
        ("Rollbacks are always allowed.", [2]),
        ("Hotels in London are capped at $350 a night.", [3, 4]),
        ("Elsewhere the cap is $250.", [3]),
        ("London | $350", [3]),  # table header and rule are skipped, as is code
    ]


def test_score_counts_claims_support_and_citations():
    items = grounding.statements(ANSWER)
    verdict = {
        "statements": [
            {"i": 1, "claim": False, "supported": True, "cited_support": True},
            {"i": 2, "claim": True, "supported": True, "cited_support": True,
             "citations": {"1": True}},
            {"i": 3, "claim": True, "supported": True, "cited_support": False,
             "citations": {"2": False}},
            {"i": 4, "claim": True, "supported": True, "cited_support": True,
             "citations": {"3": True, "4": False}},
            {"i": 5, "claim": True, "supported": False, "cited_support": False,
             "citations": {"3": False}},
            # statement 6 missing from the reply: counted as an unsupported claim
        ]
    }  # fmt: skip
    g = grounding.score(items, verdict, sources={1, 2, 3})  # [4] isn't a source
    assert (g.statements, g.claims, g.supported, g.cited_supported) == (6, 5, 3, 2)
    assert (g.citations, g.good_citations, g.dangling) == (6, 2, [4])
    assert g.faithfulness == pytest.approx(0.6)
    assert g.citation_recall == pytest.approx(0.4)
    assert g.citation_precision == pytest.approx(2 / 6)
    assert grounding.aggregate([{"grounding": g.as_dict()}, {"grounding": None}]) == {
        "faithfulness": 0.6,
        "cite_recall": 0.4,
        "cite_precision": 0.333,
    }


def test_an_uncited_claim_is_never_citation_supported():
    items = grounding.statements("Rollbacks need no approval.")
    g = grounding.score(
        items, {"statements": [{"i": 1, "claim": True, "supported": True, "cited_support": True}]},
        sources={1},
    )  # fmt: skip
    assert (g.supported, g.cited_supported, g.citation_precision) == (1, 0, None)


def test_prompt_puts_cited_passages_first_and_respects_the_budget(monkeypatch):
    monkeypatch.setattr(grounding, "MAX_PASSAGE_CHARS", 1000)
    passages = {1: "a" * 600, 2: "b" * 600, 3: "c" * 600}
    block = grounding.passage_block(passages, cited={3})
    assert block.startswith("[3]\n" + "c" * 600)
    assert "[1]\n" + "a" * 100 in block and "[2]" not in block
    items = grounding.statements("Caps are $350 [3].")
    prompt = grounding.build_prompt("Hotel cap?", "Caps are $350 [3].", items, passages)
    assert "1. Caps are $350.  (cites [3])" in prompt


def test_judge_json_is_parsed_from_fenced_or_chatty_replies():
    assert grounding.parse_json('```json\n{"statements": []}\n```') == {"statements": []}
    assert grounding.parse_json('Sure! {"statements": [{"i": 1}]} Hope that helps.') == {
        "statements": [{"i": 1}]
    }
    with pytest.raises(ValueError):
        grounding.parse_json("no json here")


def test_paired_bootstrap_separates_real_gains_from_noise():
    a = [0.0] * 30 + [1.0] * 70
    better = [1.0] * 100
    res = paired_bootstrap(a, better, rounds=2000)
    assert res["diff"] == pytest.approx(0.3) and res["lo"] > 0 and res["p_not_better"] == 0
    flip = [1.0, 0.0] * 50
    same = paired_bootstrap(flip, flip[1:] + flip[:1], rounds=2000)  # same rate, reshuffled
    assert same["lo"] < 0 < same["hi"]
    with pytest.raises(ValueError):
        paired_bootstrap([1.0], [])


def test_cohen_kappa():
    assert cohen_kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert cohen_kappa([True, True, False, False], [True, False, True, False]) == 0.0
    # 90% raw agreement on a skewed sample is only moderate agreement beyond chance.
    judge = [True] * 9 + [False]
    human = [True] * 8 + [False, False]
    assert cohen_kappa(judge, human) == pytest.approx(0.615, abs=1e-3)
    assert cohen_kappa([True, True], [True, True]) is None
    assert cohen_kappa([], []) is None
