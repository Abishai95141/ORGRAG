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
