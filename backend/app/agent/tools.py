"""Tools the agent can call. Every tool runs inside the request's RetrievalScope —
arguments can only narrow it, never widen it."""

import uuid
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.citations import CitationRegistry, Registered
from app.models import Document
from app.retrieval.access import RetrievalScope
from app.retrieval.hybrid import Hit, hybrid_search, neighbour_text

READ_MAX_CHARS = 14_000

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "search_knowledge",
        "description": (
            "Hybrid semantic + keyword search over the organization knowledge base in "
            "scope. Returns numbered sources; cite them as [n]. Pass several phrasings "
            "of the same need, or several distinct needs, as separate queries."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 4,
                    "description": "Short search queries (3-10 words each).",
                },
                "collections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional collection names to restrict the search to.",
                },
                "orgs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional organization names or slugs to restrict to.",
                },
            },
            "required": ["queries"],
        },
    },
    {
        "name": "read_document",
        "description": (
            "Read the full text of a source you already found, by its number. Use when "
            "a snippet is relevant but you need the surrounding detail."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "integer", "description": "Source number."}},
            "required": ["ref"],
        },
    },
    {
        "name": "list_collections",
        "description": "List the organizations and collections in scope, with document counts.",
        "eager_input_streaming": True,
        "input_schema": {"type": "object", "properties": {}},
    },
]


class ToolInputError(ValueError):
    pass


@dataclass
class SearchOutcome:
    text: str
    queries: list[str]
    refs: list[int]
    new_sources: list[Registered] = field(default_factory=list)
    hits: int = 0


@dataclass
class ToolBox:
    db: AsyncSession
    scope: RetrievalScope
    registry: CitationRegistry
    top_k: int = 8

    # ── validation ────────────────────────────────────────────────────────
    @staticmethod
    def _str_list(value: Any, name: str, *, required: bool = False) -> list[str]:
        if value is None and not required:
            return []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ToolInputError(f"'{name}' must be a list of strings")
        cleaned = [v.strip() for v in value if v.strip()]
        if required and not cleaned:
            raise ToolInputError(f"'{name}' must contain at least one non-empty string")
        return cleaned

    def _narrowed_collections(self, collections: list[str], orgs: list[str]) -> list[uuid.UUID]:
        ids = self.scope.collections
        if orgs:
            wanted = {o.lower() for o in orgs}
            org_ids = {
                oid
                for oid, org in self.scope.orgs.items()
                if org.slug.lower() in wanted or org.name.lower() in wanted
            }
            if org_ids:
                ids = {k: v for k, v in ids.items() if v.org_id in org_ids}
        if collections:
            wanted = {c.lower() for c in collections}
            narrowed = {k: v for k, v in ids.items() if v.name.lower() in wanted}
            if narrowed:
                ids = narrowed
        return list(ids)

    # ── tools ─────────────────────────────────────────────────────────────
    async def search(self, args: dict[str, Any]) -> SearchOutcome:
        queries = self._str_list(args.get("queries"), "queries", required=True)[:4]
        collection_ids = self._narrowed_collections(
            self._str_list(args.get("collections"), "collections"),
            self._str_list(args.get("orgs"), "orgs"),
        )
        hits = await hybrid_search(self.db, queries, collection_ids, limit=self.top_k)
        return await self.render_hits(queries, hits)

    async def render_hits(self, queries: list[str], hits: list[Hit]) -> SearchOutcome:
        if not hits:
            return SearchOutcome(
                text="No matching sources found. Try different wording or broader terms.",
                queries=queries,
                refs=[],
            )
        blocks, refs, new = [], [], []
        for hit in hits:
            entry, is_new = self.registry.register(
                hit, self.scope.orgs[hit.org_id], self.scope.collections[hit.collection_id]
            )
            if is_new:
                new.append(entry)
            if entry.ref not in refs:
                refs.append(entry.ref)
            section = await neighbour_text(self.db, hit)
            heading = f" section={quoteattr(hit.heading)}" if hit.heading else ""
            blocks.append(
                f'<source ref="{entry.ref}" org={quoteattr(entry.org.name)} '
                f"collection={quoteattr(entry.collection.name)} "
                f"title={quoteattr(hit.title)}{heading}>\n{escape(section[:2400])}\n</source>"
            )
        return SearchOutcome(
            text="\n\n".join(blocks), queries=queries, refs=refs, new_sources=new, hits=len(hits)
        )

    async def read(self, args: dict[str, Any]) -> tuple[str, Registered]:
        ref = args.get("ref")
        if isinstance(ref, str) and ref.strip().isdigit():
            ref = int(ref)
        if not isinstance(ref, int):
            raise ToolInputError("'ref' must be an integer source number")
        entry = self.registry.get(ref)
        if entry is None:
            raise ToolInputError(f"There is no source [{ref}] yet. Search first.")
        doc = await self.db.get(Document, entry.hit.document_id)
        if doc is None or doc.collection_id not in self.scope.collections:
            raise ToolInputError(f"Source [{ref}] is no longer available.")
        body = doc.content
        if len(body) > READ_MAX_CHARS:
            # Centre the window on the chunk that was found.
            anchor = body.find(entry.hit.content[:120])
            start = max(0, (anchor if anchor >= 0 else 0) - READ_MAX_CHARS // 3)
            body = ("…" if start else "") + body[start : start + READ_MAX_CHARS] + "…"
        text = (
            f'<document ref="{ref}" title={quoteattr(doc.title)} '
            f"org={quoteattr(entry.org.name)}>\n{escape(body)}\n</document>"
        )
        return text, entry

    async def list_collections(self) -> str:
        if not self.scope.collections:
            return "No collections are available in this scope."
        counts = dict(
            (
                await self.db.execute(
                    select(Document.collection_id, func.count(Document.id))
                    .where(Document.collection_id.in_(self.scope.collection_ids))
                    .group_by(Document.collection_id)
                )
            ).all()
        )
        lines = []
        for org in self.scope.orgs.values():
            lines.append(f"{org.name} (slug: {org.slug})")
            for col in self.scope.collections.values():
                if col.org_id == org.id:
                    desc = f" — {col.description}" if col.description else ""
                    lines.append(f"  • {col.name}: {counts.get(col.id, 0)} documents{desc}")
        return "\n".join(lines)
