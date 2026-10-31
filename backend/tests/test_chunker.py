import json

import pytest

from app.ingestion.chunker import (
    MAX_CHUNK_CHARS,
    chunk_markdown,
    embedding_text,
    estimate_tokens,
)

DOC = """# Handbook

Intro paragraph about the handbook.

## Deploys

We deploy Monday to Thursday.

### Rollbacks

Rollbacks are always allowed.

## Testing

```python
# not a heading
print("hi")
```

Coverage stays above 80%.
"""


def test_heading_paths_exclude_title_and_nest():
    chunks = chunk_markdown(DOC, title="Handbook")
    headings = [c.heading for c in chunks]
    assert headings == ["", "Deploys", "Deploys › Rollbacks", "Testing"]


def test_code_fence_hash_is_not_a_heading():
    chunks = chunk_markdown(DOC, title="Handbook")
    testing = next(c for c in chunks if c.heading == "Testing")
    assert "# not a heading" in testing.content
    assert "Coverage stays above 80%" in testing.content


def test_small_sections_after_large_ones_are_kept():
    big = " ".join(f"word{i}" for i in range(500))
    md = f"## A\n\n{big}\n\n## B\n\nTiny section."
    chunks = chunk_markdown(md, target_words=200, overlap_words=20)
    assert any(c.heading == "B" and "Tiny section." in c.content for c in chunks)


def test_long_sections_split_with_overlap():
    paras = "\n\n".join(" ".join(f"p{p}w{i}" for i in range(60)) for p in range(10))
    chunks = chunk_markdown(f"## Long\n\n{paras}", target_words=150, overlap_words=20)
    assert len(chunks) >= 4
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        tail = prev.content.split()[-20:]
        assert nxt.content.split()[:20] == tail
    assert all(c.word_count <= 150 + 60 for c in chunks)


def test_ordinals_are_sequential():
    chunks = chunk_markdown(DOC, title="Handbook")
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_embedding_text_and_tokens():
    assert embedding_text("T", "A › B", "body").startswith("T — A › B")
    assert embedding_text("T", "", "body").startswith("T\n\n")
    assert estimate_tokens("one two three") >= 3


# ── hard size cap (audit C2) ─────────────────────────────────────────────────
OVERSIZED = {
    "unpunctuated": " ".join(f"word{i}" for i in range(30_000)),
    "minified_json": json.dumps(
        [{"id": i, "name": f"item {i}", "tags": ["a", "b"]} for i in range(4000)]
    ),
    "one_token": "x" * 50_000,
    "code_block": "```python\n"
    + "\n".join(f"value_{i} = compute({i}, 'abc')" for i in range(3000))
    + "\n```",
    "csv_like": "\n".join(
        f"{i},Vendor {i},owner{i}@example.com,High,2026-01-{i % 28 + 1:02d}" for i in range(2000)
    ),
}


@pytest.mark.parametrize("name", OVERSIZED)
def test_no_chunk_exceeds_the_cap_and_nothing_is_lost(name):
    text = OVERSIZED[name]
    chunks = chunk_markdown(f"# Doc\n\n{text}", title="Doc", overlap_words=0)
    assert len(chunks) > 1
    assert max(len(c.content) for c in chunks) <= MAX_CHUNK_CHARS
    joined = "".join("".join(c.content.split()) for c in chunks)
    assert joined == "".join(text.split())  # every character survives, in order


def test_overlap_never_pushes_a_chunk_over_the_cap():
    text = "\n\n".join(" ".join(["lorem"] * 120) + "." for _ in range(60))
    chunks = chunk_markdown(text, target_words=350, overlap_words=50)
    assert max(len(c.content) for c in chunks) <= MAX_CHUNK_CHARS


def test_normal_prose_chunks_are_unchanged_by_the_cap():
    para = "The access review runs every quarter and covers all production systems. " * 6
    md = "## Reviews\n\n" + "\n\n".join([para] * 10)
    capped = chunk_markdown(md)
    uncapped = chunk_markdown(md, max_chars=10**9)
    assert [c.content for c in capped] == [c.content for c in uncapped]
