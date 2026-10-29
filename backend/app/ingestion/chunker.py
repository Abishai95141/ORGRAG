"""Heading-aware Markdown chunker.

Splits on headings first so a chunk never straddles two sections, then packs
paragraphs into ~`target_words` windows with `overlap_words` of carry-over. Each
chunk records its heading path ("Security policy › Access reviews") which is
indexed with higher weight and shown in citations."""

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass
class ChunkDraft:
    ordinal: int
    heading: str
    content: str
    word_count: int


def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * 1.33) + 1


def _sections(markdown: str) -> list[tuple[list[str], str]]:
    path: list[tuple[int, str]] = []
    sections: list[tuple[list[str], str]] = []
    buf: list[str] = []
    in_fence = False

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append(([h for _, h in path], body))
        buf.clear()

    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            buf.append(line)
            continue
        m = None if in_fence else _HEADING.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            path = [(lvl, h) for lvl, h in path if lvl < level]
            path.append((level, title))
        else:
            buf.append(line)
    flush()
    return sections


def _paragraphs(body: str) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    out: list[str] = []
    for p in paras:
        words = p.split()
        if len(words) <= 400:
            out.append(p)
            continue
        # Very long paragraph (e.g. PDF page text): split on sentence boundaries.
        sentences = re.split(r"(?<=[.!?])\s+", p)
        cur: list[str] = []
        for s in sentences:
            cur.append(s)
            if sum(len(x.split()) for x in cur) >= 200:
                out.append(" ".join(cur))
                cur = []
        if cur:
            out.append(" ".join(cur))
    return out


def chunk_markdown(
    markdown: str, *, title: str = "", target_words: int = 350, overlap_words: int = 50
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for path, body in _sections(markdown) or [([], markdown)]:
        # The document title is usually the H1; don't repeat it in every heading path.
        trail = [h for h in path if h.strip().lower() != title.strip().lower()]
        heading = " › ".join(trail)
        window: list[str] = []
        words = 0
        fresh = False  # window holds content not yet emitted (beyond the overlap tail)
        for para in _paragraphs(body):
            n = len(para.split())
            if fresh and words + n > target_words:
                text = "\n\n".join(window)
                drafts.append(ChunkDraft(len(drafts), heading, text, words))
                tail = text.split()[-overlap_words:] if overlap_words else []
                window = [" ".join(tail)] if tail else []
                words = len(tail)
            window.append(para)
            words += n
            fresh = True
        if fresh:
            drafts.append(ChunkDraft(len(drafts), heading, "\n\n".join(window), words))
    return [d for d in drafts if d.content.strip()]


def embedding_text(title: str, heading: str, content: str) -> str:
    """Text actually embedded: title + heading path give each chunk its context."""
    prefix = title if not heading else f"{title} — {heading}"
    return f"{prefix}\n\n{content}"
