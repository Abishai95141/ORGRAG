"""Markdown ↔ Slack text.

Answers are Markdown with `[n]` citations; Slack speaks mrkdwn: `*bold*`, `_italic_`,
`~strike~`, `<url|label>` links, no headings and no tables, and `&`, `<`, `>` must be
escaped. `to_mrkdwn` converts (citations become links to their source), and `split`
cuts the result into messages within Slack's size limit without breaking a code
block. `from_slack` turns an incoming message back into a plain question."""

import re
from collections.abc import Callable

# Slack truncates `text` at 40k characters but recommends ~4k per message.
MAX_CHARS = 3500
TABLE_CODE_WIDTH = 72  # wider tables become bullet lines instead of a code block

_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(.*)$")
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")
_RULE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_CITE = re.compile(r"\[(\d{1,3})\]")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
_ITALIC = re.compile(r"(?<![*\w])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![*\w])")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_KEPT = re.compile("\x00(\\d+)\x00")
_PLAIN_MARKS = re.compile(r"\*\*|__|~~|`")

LinkFor = Callable[[int], str | None]


def escape(text: str) -> str:
    """Slack's three control characters (the only escaping mrkdwn needs)."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def link(url: str, label: str) -> str:
    """`<url|label>`; the label is escaped and can't contain the `|` separator."""
    return f"<{escape(url)}|{escape(label).replace('|', '/')}>"


def _no_links(_: int) -> None:
    return None


def inline(text: str, link_for: LinkFor = _no_links) -> str:
    """One line of Markdown → mrkdwn. Code spans, links and citations are set aside
    first so the emphasis rules can't reach inside them."""
    kept: list[str] = []

    def keep(value: str) -> str:
        kept.append(value)
        return f"\x00{len(kept) - 1}\x00"

    text = _CODE_SPAN.sub(lambda m: keep(f"`{escape(m.group(2))}`"), text)
    text = _LINK.sub(lambda m: keep(link(m.group(2), m.group(1))), text)

    def cite(m: re.Match) -> str:
        url = link_for(int(m.group(1)))
        return keep(link(url, m.group(0))) if url else m.group(0)

    text = escape(_CITE.sub(cite, text))
    text = _BOLD.sub(lambda m: "\x01" + (m.group(1) or m.group(2)) + "\x01", text)
    text = _ITALIC.sub(lambda m: f"_{m.group(1)}_", text)
    text = _STRIKE.sub(lambda m: f"~{m.group(1)}~", text)
    text = text.replace("\x01", "*")
    return _KEPT.sub(lambda m: kept[int(m.group(1))], text)


def _cells(row: str) -> list[str]:
    row = row.strip()
    row = row[1:] if row.startswith("|") else row
    row = row[:-1] if row.endswith("|") and not row.endswith("\\|") else row
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]


def _plain(cell: str) -> str:
    return _PLAIN_MARKS.sub("", _LINK.sub(r"\1", cell))


def _table(rows: list[list[str]], link_for: LinkFor) -> list[str]:
    """A table as a monospaced code block when it fits, else one bullet line per row
    (where links and citations still work)."""
    header, body = rows[0], rows[1:]
    width = len(header)
    rows = [(r + [""] * width)[:width] for r in rows]
    plain = [[_plain(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in plain) for i in range(width)]
    if sum(widths) + 3 * (width - 1) <= TABLE_CODE_WIDTH:
        lines = [" | ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip() for r in plain]
        lines.insert(1, "-+-".join("-" * w for w in widths))
        return ["```", *(escape(line) for line in lines), "```"]
    out = []
    for r in rows[1:] if body else rows:
        parts = [
            f"*{inline(_plain(h))}:* {inline(c, link_for)}" if h else inline(c, link_for)
            for h, c in zip(rows[0], r, strict=True)
            if c
        ]
        out.append("• " + " · ".join(parts))
    return out


def to_mrkdwn(markdown: str, link_for: LinkFor = _no_links) -> str:
    lines = markdown.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            out.append("```")  # Slack shows a language tag as text, so drop it
            in_fence = not in_fence
        elif in_fence:
            out.append(escape(line))
        elif (
            "|" in line
            and i + 1 < len(lines)
            and _TABLE_SEP.match(lines[i + 1])
            and "|" in lines[i + 1]
        ):
            rows = [_cells(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            out += _table(rows, link_for)
            continue
        elif m := _HEADING.match(line):
            out.append(f"*{inline(_PLAIN_MARKS.sub('', m.group(1)), link_for)}*")
        elif _RULE.match(line):
            out.append("───")
        elif m := _BULLET.match(line):
            depth = min(len(m.group(1).expandtabs(4)) // 2, 3)
            out.append("    " * depth + ("◦ " if depth else "• ") + inline(m.group(2), link_for))
        elif m := _ORDERED.match(line):
            depth = min(len(m.group(1).expandtabs(4)) // 2, 3)
            out.append("    " * depth + f"{m.group(2)}. " + inline(m.group(3), link_for))
        elif m := _QUOTE.match(line):
            out.append("> " + inline(m.group(1), link_for))
        else:
            out.append(inline(line.rstrip(), link_for))
        i += 1
    if in_fence:
        out.append("```")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def split(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Cut `text` into messages of at most `limit` characters, preferring paragraph
    breaks. A code block that has to be cut is closed and reopened."""
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    in_fence = False

    def flush(cut: int | None = None) -> None:
        nonlocal current, size
        head, tail = (current, []) if cut is None else (current[:cut], current[cut:])
        open_fence = sum(1 for ln in head if _FENCE.match(ln)) % 2 == 1
        if open_fence:
            head = [*head, "```"]
        if body := "\n".join(head).strip("\n"):
            chunks.append(body)
        current = (["```"] if open_fence else []) + tail
        size = sum(len(ln) + 1 for ln in current)

    for raw in text.split("\n"):
        # A single line longer than a message is cut into pieces.
        pieces = [raw[k : k + limit - 8] for k in range(0, len(raw), limit - 8)] or [""]
        for line in pieces:
            if size + len(line) + 1 > limit - 4 and current:
                blank = max((k for k, ln in enumerate(current) if not ln.strip()), default=0)
                # Cut at the last paragraph break if it isn't too far back (and isn't
                # inside a code block); otherwise right here.
                usable = blank > len(current) // 2 and not in_fence
                flush(blank if usable else None)
            current.append(line)
            size += len(line) + 1
            if _FENCE.match(line):
                in_fence = not in_fence
    flush()
    return chunks or [""]


# ── incoming ─────────────────────────────────────────────────────────────────
_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|([^>]*))?>")
_CHANNEL = re.compile(r"<#([A-Z0-9]+)(?:\|([^>]*))?>")
_SPECIAL = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")
_SUBTEAM = re.compile(r"<!subteam\^[A-Z0-9]+(?:\|([^>]*))?>")
_URL = re.compile(r"<((?:https?|mailto):[^|>]+)(?:\|([^>]*))?>")


def from_slack(text: str, bot_user_id: str | None = None) -> str:
    """A Slack message → the plain question: the bot's own mention removed, other
    mentions, channels and links made readable, entities decoded."""

    def mention(m: re.Match) -> str:
        return "" if m.group(1) == bot_user_id else "@" + (m.group(2) or m.group(1))

    def url(m: re.Match) -> str:
        target, label = m.group(1), m.group(2)
        if not label or label in target:
            return label or target
        return f"{label} ({target})"

    text = _MENTION.sub(mention, text or "")
    text = _CHANNEL.sub(lambda m: "#" + (m.group(2) or m.group(1)), text)
    text = _SPECIAL.sub(lambda m: "@" + m.group(1), text)
    text = _SUBTEAM.sub(lambda m: m.group(1) or "@group", text)
    text = _URL.sub(url, text)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return re.sub(r"[ \t]+", " ", text).strip()
