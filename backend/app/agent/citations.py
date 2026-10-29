"""Per-answer citation registry: each document gets one stable number across every
search and read step of a single answer."""

import re
import uuid
from dataclasses import dataclass, field

from app.models import Collection, Organization
from app.retrieval.hybrid import Hit, snippet

_CITE = re.compile(r"\[(\d{1,3})\]")


@dataclass
class Registered:
    ref: int
    hit: Hit
    org: Organization
    collection: Collection
    # Snapshot taken at registration so serialization never touches the ORM again
    # (safe after a session rollback or close).
    source: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.source = self._build()

    def to_source_ref(self) -> dict:
        return dict(self.source)

    def _build(self) -> dict:
        return {
            "ref": self.ref,
            "document_id": str(self.hit.document_id),
            "chunk_id": str(self.hit.chunk_id),
            "title": self.hit.title,
            "url": self.hit.url,
            "snippet": snippet(self.hit.content),
            "org_id": str(self.org.id),
            "org_slug": self.org.slug,
            "org_name": self.org.name,
            "org_hue": self.org.hue,
            "collection_name": self.collection.name,
            "source_kind": self.hit.source_kind,
            "score": self.hit.score,
        }


class CitationRegistry:
    def __init__(self) -> None:
        self._by_doc: dict[uuid.UUID, Registered] = {}
        self._by_ref: dict[int, Registered] = {}

    def register(
        self, hit: Hit, org: Organization, collection: Collection
    ) -> tuple[Registered, bool]:
        existing = self._by_doc.get(hit.document_id)
        if existing:
            return existing, False
        entry = Registered(len(self._by_ref) + 1, hit, org, collection)
        self._by_doc[hit.document_id] = entry
        self._by_ref[entry.ref] = entry
        return entry, True

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
