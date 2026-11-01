"""Typed values from table cells and extracted text, and field types inferred from
samples. A value that doesn't parse as its field's type is kept as text in the
record's provenance but not stored as a typed value, so filters never match on a
misread number or date."""

import re
from datetime import date
from typing import Any

from dateutil import parser as dateparser

from app.records.tables import clean

_MONTH = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\b", re.IGNORECASE
)
_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
# A quantity, not part of an identifier: "INC-2264", "SEV-3", "us-east-1", "/v2" aren't.
_NUMBER = re.compile(
    r"(?<![A-Za-z/\d.,])(?<![A-Za-z]-)[-+]?\$?\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([kmb]n?|%)?"
    r"(?![\w-])",
    re.I,
)
_YES = {"yes", "y", "true"}
_NO = {"no", "n", "false", "none"}
# "Executed — signed May 15, 2024": the part before a dash or bracket is the gist.
_HEAD = re.compile(r"\s+(?:—|–|-{1,2}|\(|;|,\s+(?=signed|dated|since|until|as of))\s*")
MAX_CHOICES = 12


def head(text: str) -> str:
    """The leading phrase of a cell ("Executed" from "Executed — signed May 15, 2024")."""
    return _HEAD.split(clean(text), maxsplit=1)[0].strip(" .:")


def parse_date(text: str, year_hint: int | None = None) -> str | None:
    """ISO date from text with a month name or an ISO date. A date written without a
    year ("Jul 9", in a 2026 incident log) takes `year_hint`; with no hint it doesn't
    parse, rather than getting an invented year."""
    text = clean(text)
    if m := _ISO.search(text):
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return None
    if not _MONTH.search(text):
        return None  # "12/03" is ambiguous; a month name or ISO date isn't
    has_year = _YEAR.search(text) is not None
    if not has_year and year_hint is None:
        return None
    default = _DEFAULT_DAY if has_year else _DEFAULT_DAY.replace(year=year_hint)
    try:
        parsed = dateparser.parse(text, fuzzy=True, default=default)
    except (ValueError, OverflowError):
        return None
    if parsed.year < 1900 or parsed.year > 2200:
        return None
    return parsed.date().isoformat()


_DEFAULT_DAY = dateparser.parse("2000-01-01")  # month-only dates read as the 1st
_YEAR = re.compile(r"\b(19|20|21)\d{2}\b")


_PERSON = re.compile(r"^[A-Z][a-z]+(?:[ '-][A-Z][a-z]+){1,2}$")
_YEAR_ONLY = re.compile(r"(19|20|21)\d{2}")
_TIME_UNIT = re.compile(r"\b(second|sec|minute|min|hour|hr|day|week|month|year)s?\b", re.I)


def is_measure(text: str) -> bool:
    """A quantity with a unit (a duration, an amount of money, a percentage): a value
    that measures something rather than naming it."""
    return bool(_TIME_UNIT.search(text) or re.search(r"[$€£%]", text))


def parse_number(text: str) -> float | None:
    text = clean(text)
    m = _NUMBER.search(text)
    if not m or _MONTH.search(text[: m.start()] + text[m.end() : m.end() + 6]):
        return None  # "May 15" is a date, not 15
    quantities = [x for x in _NUMBER.finditer(text) if not _YEAR_ONLY.fullmatch(x.group(0).strip())]
    if len(quantities) > 1:
        return None  # "1 hour 25 minutes", "$5,000 to $25,000": not one number
    value = float(m.group(1).replace(",", "") + (m.group(2) or ""))
    if text.lstrip().startswith("-") and not m.group(0).startswith("-"):
        value = -value
    suffix = (m.group(3) or "").lower().rstrip("n")
    value *= {"k": 1e3, "m": 1e6, "b": 1e9}.get(suffix, 1)
    return int(value) if value.is_integer() else value


def parse_bool(text: str) -> bool | None:
    word = head(text).lower()
    if word in _YES:
        return True
    if word in _NO:
        return False
    return None


def parse_choice(text: str, options: list[str]) -> str | None:
    gist = head(text).lower()
    for option in options:
        if gist == option.lower():
            return option
    for option in options:  # "Signed" in "Signed and countersigned"
        if gist.startswith(option.lower()):
            return option
    return None


def typed(text: str, field: dict[str, Any], year_hint: int | None = None) -> Any:
    """The value of `text` as `field`'s type, or None when it doesn't parse."""
    kind = field.get("type", "text")
    if kind == "number":
        return parse_number(text)
    if kind == "date":
        return parse_date(text, year_hint)
    if kind == "boolean":
        return parse_bool(text)
    if kind == "choice":
        return parse_choice(text, field.get("options") or [])
    value = clean(text)
    return value or None


def infer_type(samples: list[str]) -> dict[str, Any]:
    """{"type": …, "options"?: […]} from a field's values across records."""
    values = [clean(s) for s in samples if clean(s) and clean(s) not in ("—", "-", "n/a")]
    if not values:
        return {"type": "text"}
    share = lambda parse: sum(parse(v) is not None for v in values) / len(values)  # noqa: E731
    if share(parse_bool) >= 0.9:
        return {"type": "boolean"}
    if share(lambda v: parse_date(v, 2000)) >= 0.8:
        return {"type": "date"}
    heads = [head(v) for v in values]
    numeric = share(parse_number)
    words = sum(len(v.split()) for v in values) / len(values)
    # "47 minutes", "6 hours", "3 days" are durations in different units, not numbers.
    units = {u.lower() for v in values for u in _TIME_UNIT.findall(v)}
    if numeric >= 0.8 and words <= 4 and len(units) < 2:
        return {"type": "number"}
    distinct = sorted(set(heads), key=str.lower)
    # People ("Kenji Watanabe") repeat across a register but aren't a fixed set: as a
    # choice, the next new owner would be dropped.
    if sum(bool(_PERSON.match(h)) for h in heads) >= 0.8 * len(heads):
        return {"type": "text"}
    # A fixed set: some value repeats, and there are few of them for the record count.
    repeats = (
        len(values) >= 4
        and len(distinct) < len(values)
        and len(distinct) <= max(3, len(values) // 2)
    )
    if repeats and len(distinct) <= MAX_CHOICES and all(len(h) <= 40 for h in distinct):
        return {"type": "choice", "options": distinct}
    return {"type": "text"}
