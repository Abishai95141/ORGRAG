from app.ingestion.chunker import chunk_markdown, embedding_text, estimate_tokens

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
