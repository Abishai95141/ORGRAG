"""Find the date a document says it was issued or took effect.

Uploaded files carry no trustworthy "last modified" time — every upload would look
brand new — so recency ranking needs the date the document states about itself:
"**Effective:** September 15, 2026", "Date: 2026-10-19", "Annual leave policy
(effective October 1, 2026)". Only the title and the opening lines are read, so a
date mentioned in passing deep in the body (a deadline, a renewal) isn't mistaken
for the document's own date."""

import re
from datetime import UTC, datetime

HEADER_CHARS = 900

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
_MONTH = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_DATE_PATTERNS = [
    re.compile(r"(?P<y>20\d{2})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"),
    re.compile(rf"{_MONTH}\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>20\d{{2}})", re.I),
    re.compile(rf"(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH},?\s+(?P<y>20\d{{2}})", re.I),
    re.compile(rf"{_MONTH}\s+(?P<y>20\d{{2}})", re.I),
]
# Labels that state the document's own date, strongest first.
_LABELS = re.compile(
    r"\b(effective(?:\s+date)?|last\s+(?:updated|revised|reviewed)|updated|revised|"
    r"approved|issued|published|date)\b\s*[:*_ )(-]*",
    re.I,
)
_RANK = {"effective": 0, "last": 1, "updated": 1, "revised": 1, "approved": 2, "issued": 2}


def _parse(match: re.Match) -> datetime | None:
    groups = match.groupdict()
    month_text = next((g for g in match.groups() if g and g.lower().rstrip(".") in _MONTHS), None)
    month = _MONTHS[month_text.lower().rstrip(".")] if month_text else int(groups.get("m") or 0)
    day = int(groups.get("d") or 1)
    try:
        return datetime(int(groups["y"]), month, day, tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def _first_date(text: str) -> datetime | None:
    found = []
    for pattern in _DATE_PATTERNS:
        for m in pattern.finditer(text):
            if (date := _parse(m)) is not None:
                found.append((m.start(), date))
        if found:  # most specific pattern that matched wins
            break
    return min(found)[1] if found else None


def document_date(markdown: str, title: str | None = None) -> datetime | None:
    """The document's stated date, or None. Prefers labelled dates in the header
    (effective > updated > approved/issued > other), then a date in the title.

    A label only counts at the start of a metadata line or of a "·"-separated
    segment ("**Version:** 2.0 · **Effective:** September 15, 2026"). Table rows
    are skipped: there "updated October 2024" or "Renewal date" describe something
    else, not the document."""
    header = markdown[:HEADER_CHARS]
    labelled: list[tuple[int, datetime]] = []
    for line in header.splitlines():
        if line.lstrip().startswith("|"):
            continue
        for segment in line.split("·"):
            m = _LABELS.match(segment.lstrip(" \t*_->#"))
            if m is None:
                continue
            tail = segment.lstrip(" \t*_->#")[m.end() : m.end() + 40]
            if (date := _first_date(tail)) is not None:
                word = m.group(1).split()[0].lower()
                labelled.append((_RANK.get(word, 3), date))
    if labelled:
        best = min(rank for rank, _ in labelled)
        return max(date for rank, date in labelled if rank == best)
    for text in (title or "", header.split("\n", 1)[0]):
        if (date := _first_date(text)) is not None:
            return date
    return None
