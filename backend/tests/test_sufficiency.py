"""What the sufficient-context check reads: whole results only, exact ones always."""

from app.agent.sufficiency import fit


def test_exact_results_are_kept_whole_and_searches_dropped_oldest_first():
    """A records table found first must not lose its first rows to later searches: the
    check would report the register "cut off" (it did, on the e2e scenario's deep run)."""
    table = "| Vendor | Renewal |\n" + "".join(
        f"| v{i} | 2027-01-{i % 28 + 1:02d} |\n" for i in range(40)
    )
    searches = [
        ("search_knowledge", f'<source ref="{i}">' + "x" * 5000 + "</source>") for i in range(8)
    ]
    text = fit([("query_records", table), *searches], limit=24_000)
    assert table in text  # whole, though it came first
    kept = [body for _, body in searches if body in text]
    assert kept and kept == [body for _, body in searches][-len(kept) :]  # the latest ones
    assert text.count("x") == 5000 * len(kept)  # never part of a result
    assert text.startswith(f"({len(searches) - len(kept)} earlier search result(s) left out")
    assert len(text) <= 24_000 + 100


def test_everything_fits_unchanged_and_one_huge_result_keeps_its_end():
    small = [("search_knowledge", "alpha"), ("query_records", "beta")]
    assert fit(small) == "alpha\n\nbeta"
    huge = "\n".join(f"line {i}" for i in range(5000))
    text = fit([("search_knowledge", huge)], limit=1000)
    assert text.startswith("(the start of this result was left out for length)\nline ")
    assert text.endswith("line 4999") and len(text) < 1100
