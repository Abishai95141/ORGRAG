"""Heading-aware Markdown chunker.

Splits on headings first so a chunk never straddles two sections, then packs
paragraphs into ~`target_words` windows with `overlap_words` of carry-over. Each
chunk records its heading path ("Security policy › Access reviews") which is
indexed with higher weight and shown in citations.

No chunk exceeds `max_chars`: the embedder reads only ~512 tokens, so anything past
that is unsearchable. Oversized paragraphs are split by sentence, then by line (code,
JSON, tables), then by word, and a single giant token is cut by characters."""

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

MAX_CHUNK_CHARS = 2600  # ≈ 512 embedder tokens of English prose
PIECE_WORDS = 200  # target size of the pieces an oversized paragraph is cut into


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


def _fits(text: str, max_words: int, max_chars: int) -> bool:
    return len(text) <= max_chars and len(text.split()) <= max_words


def _pack(parts: list[str], sep: str, max_words: int, max_chars: int) -> list[str]:
    """Greedily join consecutive parts into pieces that fit."""
    out: list[str] = []
    cur = ""
    for part in parts:
        joined = f"{cur}{sep}{part}" if cur else part
        if cur and not _fits(joined, max_words, max_chars):
            out.append(cur)
            cur = part
        else:
            cur = joined
    if cur:
        out.append(cur)
    return out


def _split_oversized(text: str, max_words: int, max_chars: int) -> list[str]:
    """Split text that doesn't fit, coarsest boundary first."""
    if _fits(text, max_words, max_chars):
        return [text]
    for pattern, sep in ((_SENTENCE, " "), (re.compile(r"\n"), "\n")):
        parts = [p for p in pattern.split(text) if p.strip()]
        if len(parts) > 1:
            return [
                piece
                for packed in _pack(parts, sep, max_words, max_chars)
                for piece in _split_oversized(packed, max_words, max_chars)
            ]
    words = text.split()
    if len(words) > 1:
        return [
            piece
            for packed in _pack(words, " ", max_words, max_chars)
            for piece in _split_oversized(packed, max_words, max_chars)
        ]
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def _paragraphs(body: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    out: list[str] = []
    for p in paras:
        if len(p.split()) <= 400 and len(p) <= max_chars:
            out.append(p)
        else:
            # Very long paragraph (PDF page text, minified JSON, a code block…).
            out.extend(_split_oversized(p, PIECE_WORDS, max_chars))
    return out


def chunk_markdown(
    markdown: str,
    *,
    title: str = "",
    target_words: int = 350,
    overlap_words: int = 50,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[ChunkDraft]:
    drafts: list[ChunkDraft] = []
    for path, body in _sections(markdown) or [([], markdown)]:
        # The document title is usually the H1; don't repeat it in every heading path.
        trail = [h for h in path if h.strip().lower() != title.strip().lower()]
        heading = " › ".join(trail)
        window: list[str] = []
        words = chars = 0
        fresh = False  # window holds content not yet emitted (beyond the overlap tail)
        for para in _paragraphs(body, max_chars):
            n = len(para.split())
            if fresh and (words + n > target_words or chars + len(para) + 2 > max_chars):
                text = "\n\n".join(window)
                drafts.append(ChunkDraft(len(drafts), heading, text, words))
                tail = text.split()[-overlap_words:] if overlap_words else []
                carry = " ".join(tail)
                # Carry the overlap only when it leaves room for the next paragraph.
                if not tail or len(carry) + len(para) + 2 > max_chars:
                    carry, tail = "", []
                window = [carry] if carry else []
                words, chars = len(tail), len(carry)
            window.append(para)
            words += n
            chars += len(para) + 2
            fresh = True
        if fresh:
            drafts.append(ChunkDraft(len(drafts), heading, "\n\n".join(window), words))
    return [d for d in drafts if d.content.strip()]


def embedding_text(title: str, heading: str, content: str, context: str = "") -> str:
    """Text actually embedded: title + heading path give each chunk its context, and a
    generated context (Contextual Retrieval) situates it in the document."""
    prefix = title if not heading else f"{title} — {heading}"
    if context:
        prefix = f"{prefix}\n{context}"
    return f"{prefix}\n\n{content}"
