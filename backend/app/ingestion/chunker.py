"""Heading-aware Markdown chunker.

Splits on headings first so a chunk never straddles two sections, then packs
paragraphs into ~`target_words` windows with `overlap_words` of carry-over. Each
chunk records its heading path ("Security policy › Access reviews") which is
indexed with higher weight and shown in citations.

No chunk exceeds `max_chars`: the embedder reads only ~512 tokens, so anything past
that is unsearchable. Oversized paragraphs are split by sentence, then by line (code,
JSON), then by word, and a single giant token is cut by characters.

A large Markdown table (a spreadsheet, a PDF table) becomes passages of its own: row
groups under the section heading, each repeating the header row so every passage says
what its columns are. Small tables stay inline with the prose around them. Tables lose
their cell padding first; a row too long for a passage is split, never cut.

A CSV's rows (`### Row N` sections, each a list of `**Column**: value` lines) are packed
whole into passages of consecutive rows, each row under its number, so a passage stands
alone. Passages are sized by an estimate of the embedder's tokens rather than characters:
rows of names and codes run about 2.5 characters a token, half what `max_chars` assumes
of prose, so a passage of `max_chars` would be embedded only halfway. A row too big to
share a passage is chunked like any other section.

Parsers that know page numbers (Docling) mark page starts with `<!-- page N -->`; each
chunk records the page it starts on, and the markers aren't indexed."""

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

MAX_CHUNK_CHARS = 2600  # ≈ 512 embedder tokens of English prose
PIECE_WORDS = 200  # target size of the pieces an oversized paragraph is cut into
OWN_PASSAGE_TABLE_CHARS = 1200  # tables larger than this get passages of their own
# Estimated embedder tokens per passage of CSV rows: 512, less room for the title and
# heading in front of it and the estimate's error.
ROW_PASSAGE_TOKENS = 400
_PAGE = re.compile(r"^<!-- page (\d+) -->$")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{3,}")
_PADDING = re.compile(r"(?<=\S) {2,}")
_ROW = re.compile(r"^Row (\d+)$")
_PIECE = re.compile(r"[^\W_]+|[^\w\s]|_")


@dataclass
class ChunkDraft:
    ordinal: int
    heading: str
    content: str
    word_count: int
    page: int | None = None  # the page the chunk starts on, when the format has pages


def page_marker(n: int) -> str:
    return f"<!-- page {n} -->"


def row_heading(n: int) -> str:
    return f"### Row {n}"


def wordpieces(text: str) -> int:
    """Roughly the embedder's token count: one per punctuation mark, one per word and one
    more per further four characters of it. Within about 10% of bge-small's count on
    rows of names and codes; somewhat high on prose."""
    return sum(1 + (len(p) - 1) // 4 for p in _PIECE.findall(text))


def _is_table(para: str) -> bool:
    lines = para.splitlines()
    return len(lines) >= 2 and all(line.lstrip().startswith("|") for line in lines)


def _squeeze_table(table: str) -> str:
    """A Markdown table without its alignment padding. Docling pads every cell to its
    column's widest, so one long cell can make every line, rule included, thousands of
    characters of spaces and dashes."""
    lines = table.splitlines()
    for i, line in enumerate(lines):
        if i == 1 and _TABLE_RULE.match(line.strip()):
            lines[i] = re.sub(r"-{3,}", "---", line.replace(" ", ""))
        else:
            lines[i] = _PADDING.sub(" ", line)
    return "\n".join(lines)


def table_passages(table: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Row groups of a Markdown table, each under the table's header (and rule). A header
    over a third of a passage is a row Docling took for one, not column names, so it
    leads the first passage only. A row too long for a passage is split like prose."""
    lines = table.splitlines()
    head_len = 2 if len(lines) > 1 and _TABLE_RULE.match(lines[1].strip()) else 1
    head, rows = "\n".join(lines[:head_len]), lines[head_len:]
    if len(head) > max_chars // 3:
        head, rows = "", lines
    room = max_chars - len(head) - 1 if head else max_chars
    groups: list[list[str]] = [[]]
    size = 0
    for row in rows:
        for piece in [row] if len(row) <= room else _split_oversized(row, PIECE_WORDS, room):
            if groups[-1] and size + len(piece) + 1 > room:
                groups.append([])
                size = 0
            groups[-1].append(piece)
            size += len(piece) + 1
    return ["\n".join([head, *g] if head else g) for g in groups]


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


Rows = list[tuple[str, str]]  # (row number, its lines)


def _row_runs(
    sections: list[tuple[list[str], str]], max_chars: int
) -> list[tuple[list[str], str, Rows]]:
    """Sections as (path, body, []), except that consecutive rows small enough to share a
    passage become one (parent path, "", rows)."""
    out: list[tuple[list[str], str, Rows]] = []
    for path, body in sections:
        m = _ROW.match(path[-1]) if path else None
        block = f"Row {m.group(1)}\n{body}" if m else ""
        if (
            not m
            or "<!-- page " in body
            or len(block) > max_chars
            or wordpieces(block) > ROW_PASSAGE_TOKENS
        ):
            out.append((path, body, []))
        elif out and out[-1][2] and out[-1][0] == path[:-1]:
            out[-1][2].append((m.group(1), body))
        else:
            out.append((path[:-1], "", [(m.group(1), body)]))
    # A row alone stays the section it was.
    return [
        (path + [f"Row {rows[0][0]}"], rows[0][1], []) if len(rows) == 1 else (path, body, rows)
        for path, body, rows in out
    ]


def _row_passages(rows: Rows, max_chars: int) -> list[tuple[str, str]]:
    """(heading, text) passages of consecutive whole rows, each row under its number."""
    groups: list[Rows] = [[]]
    tokens = chars = 0
    for n, body in rows:
        block = f"Row {n}\n{body}"
        size = wordpieces(block)
        if groups[-1] and (
            tokens + size > ROW_PASSAGE_TOKENS or chars + len(block) + 2 > max_chars
        ):
            groups.append([])
            tokens = chars = 0
        groups[-1].append((n, body))
        tokens += size
        chars += len(block) + 2
    return [
        (f"Row {g[0][0]}", g[0][1])
        if len(g) == 1
        else (f"Rows {g[0][0]}–{g[-1][0]}", "\n\n".join(f"Row {n}\n{body}" for n, body in g))
        for g in groups
    ]


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
        if _is_table(p):
            out.append(_squeeze_table(p))  # placed whole by chunk_markdown
        elif _PAGE.match(p) or (len(p.split()) <= 400 and len(p) <= max_chars):
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
    page: int | None = None  # current page, from Docling's markers
    for path, body, rows in _row_runs(_sections(markdown) or [([], markdown)], max_chars):
        # The document title is usually the H1; don't repeat it in every heading path.
        trail = [h for h in path if h.strip().lower() != title.strip().lower()]
        heading = " › ".join(trail)
        if rows:
            for label, text in _row_passages(rows, max_chars):
                head = " › ".join([*trail, label])
                drafts.append(ChunkDraft(len(drafts), head, text, len(text.split()), page))
            continue
        window: list[str] = []
        words = chars = 0
        fresh = False  # window holds content not yet emitted (beyond the overlap tail)
        start_page = page
        for para in _paragraphs(body, max_chars):
            if m := _PAGE.match(para):
                page = int(m.group(1))
                if not fresh:
                    start_page = page
                continue
            if _is_table(para) and (len(para) > OWN_PASSAGE_TABLE_CHARS or len(para) > max_chars):
                if fresh:  # the prose so far, then the table on its own
                    text = "\n\n".join(window)
                    drafts.append(ChunkDraft(len(drafts), heading, text, words, start_page))
                for piece in table_passages(para, max_chars):
                    drafts.append(ChunkDraft(len(drafts), heading, piece, len(piece.split()), page))
                window, words, chars, fresh, start_page = [], 0, 0, False, page
                continue
            n = len(para.split())
            if fresh and (words + n > target_words or chars + len(para) + 2 > max_chars):
                text = "\n\n".join(window)
                drafts.append(ChunkDraft(len(drafts), heading, text, words, start_page))
                start_page = page
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
            drafts.append(ChunkDraft(len(drafts), heading, "\n\n".join(window), words, start_page))
    kept = [d for d in drafts if d.content.strip()]
    for i, d in enumerate(kept):
        d.ordinal = i
    return kept


def embedding_text(title: str, heading: str, content: str, context: str = "") -> str:
    """Text actually embedded: title + heading path give each chunk its context, and a
    generated context (Contextual Retrieval) situates it in the document."""
    prefix = title if not heading else f"{title} — {heading}"
    if context:
        prefix = f"{prefix}\n{context}"
    return f"{prefix}\n\n{content}"
