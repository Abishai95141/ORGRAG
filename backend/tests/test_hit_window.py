"""Search-hit windows (audit C1): the hit is always shown in full."""

from app.ingestion.chunker import chunk_markdown
from app.retrieval.hybrid import WINDOW_CHARS, assemble_window


def _long_doc() -> list[str]:
    paras = [
        " ".join(f"p{p}w{w}" for w in range(60)) + f". Fact number {p} is here." for p in range(40)
    ]
    chunks = chunk_markdown("\n\n".join(paras), target_words=350, overlap_words=50)
    assert len(chunks) > 4 and all(1500 < len(c.content) <= 2600 for c in chunks[:-1])
    return [c.content for c in chunks]


def test_realistic_hit_is_complete_and_context_fits():
    chunks = _long_doc()
    for i in range(1, len(chunks) - 1):
        window = assemble_window(chunks[i - 1], chunks[i], chunks[i + 1])
        assert chunks[i] in window, i
        assert len(window) <= WINDOW_CHARS
        before, _, after = window.partition(chunks[i])
        # Context is in reading order and the 50-word overlap isn't shown twice.
        assert before == "" or before.startswith("…")
        overlap = " ".join(chunks[i].split()[:50])
        assert window.count(overlap) == 1


def test_short_chunks_show_full_neighbours_without_duplicates():
    before = "Intro text. The retention period is"
    hit = "The retention period is seven years for payroll records."
    after = "payroll records. Exceptions need legal sign-off."
    window = assemble_window(before, hit, after)
    assert window == "Intro text.\n\n" + hit + "\n\nExceptions need legal sign-off."


def test_edges_and_oversized_hits():
    assert assemble_window(None, "Only chunk.", None) == "Only chunk."
    big = "word " * 1000
    out = assemble_window("prev", big, "next", max_chars=1200)
    assert len(out) <= 1200 and out.endswith("…") and "prev" not in out
