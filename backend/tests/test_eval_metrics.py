"""The eval's grounding metrics and statistics (no model, no database)."""

import pytest

from eval import grounding
from eval.scoring import forbidden_hits, judge_json, parse_verdict
from eval.stats import (
    cohen_kappa,
    compare_repeats,
    detection_rates,
    noise_floor,
    paired_bootstrap,
    repeat_variance,
    split_half,
    verdict,
)

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


def test_a_short_judge_verdict_is_refused_not_padded():
    # A judge that lost count must be asked again: padding failed correct answers.
    reply = {"facts": [True, True], "abstained": False, "contradiction": False}
    with pytest.raises(ValueError):
        parse_verdict(reply, 3)
    assert parse_verdict(reply, 2)["facts"] == [True, True]


def test_judge_json_takes_the_verdict_from_a_reply_with_extra_text():
    assert judge_json('{"facts": [true]}\n\nNote: fact 0 is implied.')["facts"] == [True]
    assert judge_json('{"facts": [true]}\n{"facts": [false]}')["facts"] == [False]
    assert judge_json('Here: {"x": 1} then {"facts": []}')["facts"] == []
    with pytest.raises(ValueError):
        judge_json("no json here")


def test_forbidden_hits_ignore_case_and_spacing():
    assert forbidden_hits("It costs  £199,000 a year", ["199,000", "204,000"]) == ["199,000"]
    assert forbidden_hits("Nothing to see", ["199,000"]) == []


FLIPPER = {"always": [1, 1, 1, 1], "never": [0, 0, 0, 0], "flips": [1, 0, 1, 0]}


def test_only_questions_that_flip_add_to_the_noise():
    assert repeat_variance({"a": [1, 1, 1], "b": [0, 0, 0]}) == 0
    assert repeat_variance({"flips": [1, 0, 1, 0]}) == pytest.approx(1 / 3)
    assert noise_floor({"a": [1, 1], "b": [0, 0]}) == 0


def test_noise_floor_shrinks_with_more_repeats_and_with_more_steady_questions():
    many = {f"q{i}": [1, 0, 1, 0] for i in range(100)}
    assert noise_floor(many, k=16) == pytest.approx(noise_floor(many, k=4) / 2, abs=1e-3)
    half_steady = {**{f"q{i}": [1, 0, 1, 0] for i in range(50)},
                   **{f"s{i}": [1, 1, 1, 1] for i in range(50)}}  # fmt: skip
    assert noise_floor(half_steady) < noise_floor(many)


def test_noise_floor_of_two_sets_uses_each_sets_own_repeats():
    a = {"x": [1, 0, 1, 0], "y": [1, 1, 1, 1]}
    assert noise_floor(a, {"x": [1, 1, 1, 1], "y": [0, 0, 0, 0]}) < noise_floor(a, a)


def test_split_half_compares_every_split_once():
    runs = {"q1": [1, 1, 0, 0], "q2": [1, 1, 0, 0]}
    result = split_half(runs)
    assert result["splits"] == 3  # C(4,2) / 2
    assert result["max"] == 1.0  # first two repeats against the last two
    assert split_half({"q": [1]})["splits"] == 0


def test_compare_repeats_needs_both_tests_to_call_a_difference():
    base = {f"q{i}": [0, 0, 0] for i in range(40)}
    better = {f"q{i}": [1, 1, 1] if i < 30 else [0, 0, 0] for i in range(40)}
    assert compare_repeats(base, better)["verdict"] == "better"
    assert compare_repeats(better, base)["verdict"] == "worse"
    same = {f"q{i}": [1, 0, 1] for i in range(40)}
    assert compare_repeats(same, same)["verdict"] == "noise"
    # One question moved completely: past the noise, but nothing says it generalises.
    one = {**base, "q0": [1, 1, 1]}
    assert compare_repeats(base, one)["verdict"] == "unclear"


def test_detection_rates_rise_with_the_gain_and_stay_low_for_the_same_system():
    runs = {f"q{i}": [1, 0, 1, 0, 1, 0] for i in range(60)}
    runs |= {f"s{i}": [0, 0, 0, 0, 0, 0] for i in range(60)}
    rates = detection_rates(runs, [0, 0.25], repeats=3, trials=40)
    assert rates[0] < 0.2
    assert rates[0.25] > 0.9


def test_verdict_rules():
    assert verdict(0.02, 0.01, 0.03, 0.05) == "noise"
    assert verdict(0.06, 0.01, 0.11, 0.05) == "better"
    assert verdict(-0.06, -0.11, -0.01, 0.05) == "worse"
    assert verdict(0.06, -0.01, 0.13, 0.05) == "unclear"
