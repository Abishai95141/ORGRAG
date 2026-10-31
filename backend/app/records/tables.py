"""Find the structure records are made from, in a document's Markdown, with exact
character offsets: Markdown tables (a register with a header row, or a per-document
"Field | Value" table) and "Label: value" lines."""

import re
from dataclasses import dataclass, field

_RULE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FIELD_LINE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?(?P<label>[A-Z][A-Za-z0-9 /&()'.-]{1,48}?)(?:\*\*)?[ \t]*:"
    r"(?:\*\*)?[ \t]+(?P<value>\S.{0,300}?)[ \t]*$"
)
# Header words of a per-document attribute table ("| Field | Value |").
_VERTICAL_HEADERS = {
    ("field", "value"),
    ("field", "details"),
    ("attribute", "value"),
    ("property", "value"),
    ("item", "value"),
    ("key", "value"),
    ("field", "entry"),
}


@dataclass
class Cell:
    text: str  # as written, trimmed
    start: int  # offsets of `text` in the document
    end: int


@dataclass
class Table:
    header: list[Cell]
    rows: list[list[Cell]]
    heading: str  # the nearest section heading above it ("" at the top)
    start: int

    @property
    def labels(self) -> list[str]:
        return [clean(c.text) for c in self.header]

    @property
    def vertical(self) -> bool:
        """A Field | Value table describing one thing."""
        if len(self.header) != 2:
            return False
        words = tuple(normalize(c.text).replace("_", " ") for c in self.header)
        return words in _VERTICAL_HEADERS


@dataclass
class FieldLine:
    label: str
    value: Cell
    heading: str


@dataclass
class Structure:
    tables: list[Table] = field(default_factory=list)
    lines: list[FieldLine] = field(default_factory=list)


def clean(text: str) -> str:
    """Cell text without Markdown emphasis or code marks, single-spaced."""
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", text)
    return " ".join(text.split()).strip()


def normalize(label: str) -> str:
    """A label as a field name: "Data Processing Agreement (DPA)" → "data_processing_
    agreement_dpa"."""
    return re.sub(r"[^a-z0-9]+", "_", clean(label).lower()).strip("_")[:64]


def _cells(line: str, offset: int) -> list[Cell]:
    """Split a table row into cells with their offsets (escaped pipes stay in cells)."""
    body_start = line.find("|") + 1
    body_end = line.rstrip().rfind("|")
    if body_end < body_start:
        body_end = len(line.rstrip())
    cells: list[Cell] = []
    pos = body_start
    for part in re.split(r"(?<!\\)\|", line[body_start:body_end]):
        lead = len(part) - len(part.lstrip())
        text = part.strip()
        start = offset + pos + lead
        cells.append(Cell(text, start, start + len(text)))
        pos += len(part) + 1
    return cells


def find_structure(content: str) -> Structure:
    """Tables and field lines in a document, outside code fences."""
    out = Structure()
    heading = ""
    lines = content.split("\n")
    offsets = []
    pos = 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    i, in_fence = 0, False
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            i += 1
            continue
        if in_fence:
            i += 1
            continue
        if m := _HEADING.match(line):
            heading = clean(m.group(2))
            i += 1
            continue
        if (
            line.lstrip().startswith("|")
            and i + 1 < len(lines)
            and _RULE.match(lines[i + 1].strip())
        ):
            header = _cells(line, offsets[i])
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                cells = _cells(lines[j], offsets[j])
                if len(cells) == len(header):
                    rows.append(cells)
                j += 1
            out.tables.append(Table(header, rows, heading, offsets[i]))
            i = j
            continue
        if (m := _FIELD_LINE.match(line)) and not line.lstrip().startswith("|"):
            value = m.group("value").strip()
            if value.endswith("**"):
                value = value[:-2].rstrip()
            start = offsets[i] + m.start("value")
            out.lines.append(
                FieldLine(clean(m.group("label")), Cell(value, start, start + len(value)), heading)
            )
        i += 1
    return out
