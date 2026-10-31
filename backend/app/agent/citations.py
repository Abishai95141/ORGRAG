"""Per-answer citation registry: each source gets one stable number across every
search and read step of a single answer. Sources are knowledge-base documents,
web pages, or files attached to the conversation."""

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.models import ChatAttachment, Collection, Organization
from app.retrieval.hybrid import Hit, snippet

_CITE = re.compile(r"\[(\d{1,3})\]")


@dataclass
class Registered:
    ref: int
    origin: str  # "knowledge" | "web" | "attachment"
    hit: Hit | None = None
    org: Organization | None = None
    collection: Collection | None = None
    attachment_id: uuid.UUID | None = None
    title: str = ""
    url: str | None = None
    # Snapshot taken at registration so serialization never touches the ORM again
    # (safe after a session rollback or close).
    source: dict[str, Any] = field(default_factory=dict)

    def to_source_ref(self) -> dict[str, Any]:
        return dict(self.source)


def _knowledge_ref(ref: int, hit: Hit, org: Organization, collection: Collection) -> dict:
    return {
        "ref": ref,
        "origin": "knowledge",
        "document_id": str(hit.document_id),
        "chunk_id": str(hit.chunk_id),
        "attachment_id": None,
        "title": hit.title,
        "url": hit.url,
        "snippet": snippet(hit.content),
        "org_id": str(org.id),
        "org_slug": org.slug,
        "org_name": org.name,
        "org_hue": org.hue,
        "collection_name": collection.name,
        "source_kind": hit.source_kind,
        "score": hit.score,
        "updated_at": hit.updated_at.isoformat() if hit.updated_at else None,
    }


def _bare_ref(ref: int, origin: str, title: str, url: str | None, text: str) -> dict:
    return {
        "ref": ref,
        "origin": origin,
        "document_id": None,
        "chunk_id": None,
        "attachment_id": None,
        "title": title,
        "url": url,
        "snippet": snippet(text),
        "org_id": None,
        "org_slug": None,
        "org_name": None,
        "org_hue": None,
        "collection_name": None,
        "source_kind": None,
        "score": 1.0,
        "updated_at": None,
    }


class CitationRegistry:
    def __init__(self) -> None:
        self._by_key: dict[Any, Registered] = {}
        self._by_ref: dict[int, Registered] = {}

    def _add(self, key: Any, entry: Registered) -> Registered:
        self._by_key[key] = entry
        self._by_ref[entry.ref] = entry
        return entry

    def register(
        self, hit: Hit, org: Organization, collection: Collection
    ) -> tuple[Registered, bool]:
        existing = self._by_key.get(hit.document_id)
        if existing:
            return existing, False
        ref = len(self._by_ref) + 1
        entry = Registered(
            ref,
            "knowledge",
            hit=hit,
            org=org,
            collection=collection,
            title=hit.title,
            url=hit.url,
            source=_knowledge_ref(ref, hit, org, collection),
        )
        return self._add(hit.document_id, entry), True

    def register_web(self, url: str, title: str, text: str) -> tuple[Registered, bool]:
        key = ("web", url.rstrip("/"))
        existing = self._by_key.get(key)
        if existing:
            return existing, False
        ref = len(self._by_ref) + 1
        entry = Registered(
            ref, "web", title=title, url=url, source=_bare_ref(ref, "web", title, url, text)
        )
        return self._add(key, entry), True

    def register_attachment(self, attachment: ChatAttachment) -> tuple[Registered, bool]:
        key = ("attachment", attachment.id)
        existing = self._by_key.get(key)
        if existing:
            return existing, False
        ref = len(self._by_ref) + 1
        source = _bare_ref(ref, "attachment", attachment.filename, None, attachment.content)
        source["attachment_id"] = str(attachment.id)
        entry = Registered(
            ref,
            "attachment",
            attachment_id=attachment.id,
            title=attachment.filename,
            source=source,
        )
        return self._add(key, entry), True

    def get(self, ref: int) -> Registered | None:
        return self._by_ref.get(ref)

    def all(self) -> list[Registered]:
        return list(self._by_ref.values())

    def __len__(self) -> int:
        return len(self._by_ref)


def cited_refs(text: str) -> list[int]:
    seen: list[int] = []
    for m in _CITE.finditer(text):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def strip_citations(text: str) -> str:
    return _CITE.sub("", text)


# ── Claude's native citations (search_result blocks) ──────────────────────
# A source's ref travels in the search result's `source`, so each citation Claude
# returns maps straight back to a [n] marker.
SOURCE_PREFIX = "strata://source/"
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[\"'(\[]?[A-Z0-9])")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}")
_MIN_PROSE_CHARS = 12  # shorter prose ("Note:") joins the next block
# A "sentence" ending in one of these was split at an abbreviation, not a full stop.
_ABBREVIATION = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Mon|Tue|Wed|Thu|Fri|Sat|Sun"
    r"|Mr|Mrs|Ms|Dr|St|No|vs|etc|Inc|Ltd|Co|approx|e\.g|i\.e)\.$"
)


def citable_blocks(text: str) -> list[str]:
    """Split a passage into the units Claude cites: a sentence, a list item or a table
    row. Claude cites whole blocks, so smaller blocks mean citations that point at the
    claim itself. A heading, or a prose fragment too short to be a claim, joins the
    block after it (unless that's a table row); table header rules are dropped."""
    units: list[tuple[str, str]] = []  # (kind, text)
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            prose = " ".join(paragraph)
            for sentence in (s.strip() for s in _SENTENCE_END.split(prose)):
                if not sentence:
                    continue
                if units and units[-1][0] == "prose" and _ABBREVIATION.search(units[-1][1]):
                    units[-1] = ("prose", f"{units[-1][1]} {sentence}")
                else:
                    units.append(("prose", sentence))
            paragraph.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif _TABLE_RULE.match(stripped):
            continue
        elif stripped.startswith("|"):
            flush()
            units.append(("row", stripped))
        elif stripped.startswith("#"):
            flush()
            units.append(("heading", stripped))
        elif _LIST_ITEM.match(stripped):
            flush()
            units.append(("item", stripped))
        else:
            paragraph.append(stripped)
    flush()

    blocks: list[str] = []
    carry = ""
    for kind, unit in units:
        if carry and kind == "row":
            blocks.append(carry)
            carry = ""
        unit = f"{carry} {unit}" if carry else unit
        carry = ""
        if kind == "heading" or (kind == "prose" and len(unit) < _MIN_PROSE_CHARS):
            carry = unit
        else:
            blocks.append(unit)
    if carry:
        blocks.append(carry)
    return blocks


def search_result(ref: int, title: str, text: str, note: str = "") -> dict[str, Any]:
    """One source as a citable search_result block for Claude. `title` carries the
    metadata the XML attributes carried (org, collection, section, dates): titles
    aren't citable, so it can't end up cited as evidence."""
    blocks = citable_blocks(text) or [text.strip() or "(empty)"]
    if note:
        blocks.append(note)
    return {
        "type": "search_result",
        "source": f"{SOURCE_PREFIX}{ref}",
        "title": f"[{ref}] {title}",
        "content": [{"type": "text", "text": b} for b in blocks],
        "citations": {"enabled": True},
    }


def ref_of(source: str | None) -> int | None:
    """The ref a cited search result stands for (None if it isn't one of ours)."""
    if source and source.startswith(SOURCE_PREFIX):
        tail = source[len(SOURCE_PREFIX) :]
        return int(tail) if tail.isdigit() else None
    return None


def citation_markers(text: str, citations: list[Any] | None) -> str:
    """The [n] markers to append after a text block Claude cited, skipping refs the
    block already names (the model sometimes writes [n] itself too)."""
    refs: list[int] = []
    for c in citations or []:
        source = c.get("source") if isinstance(c, dict) else getattr(c, "source", None)
        ref = ref_of(source)
        if ref is not None and ref not in refs and f"[{ref}]" not in text:
            refs.append(ref)
    if not refs:
        return ""
    lead = "" if not text or text[-1].isspace() else " "
    return lead + "".join(f"[{r}]" for r in refs)


_REPEATED = re.compile(r"\[(\d{1,3})\](?:\s*\[\1\])+")
# "[1-6]": search result 1, block 6, the model's own notation for a search-result cite.
_RESULT_BLOCK = re.compile(r"\[(\d{1,3})-\d{1,3}\]")


def collapse_repeated_markers(text: str) -> str:
    """Tidy markers the model typed beside native citations: '[1-6]' → '[1]' (a block
    of source 1), then '[2] [2]' → '[2]'."""
    return _REPEATED.sub(r"[\1]", _RESULT_BLOCK.sub(r"[\1]", text))
