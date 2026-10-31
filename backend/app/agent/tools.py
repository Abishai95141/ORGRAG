"""Tools the agent can call. Every knowledge tool runs inside the request's
RetrievalScope — arguments can only narrow it, never widen it. Web search is only
offered when the org enabled it and a search API key is configured."""

import uuid
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import records_tool
from app.agent.citations import CitationRegistry, Registered, search_result
from app.agent.records_tool import QUERY_RECORDS, RESOLVE_ROLE
from app.graph import query as graph
from app.graph.extract import ENTITY_TYPES
from app.models import ChatAttachment, Document
from app.retrieval.access import RetrievalScope
from app.retrieval.hybrid import Hit, RankingOptions, hit_window, hits_for_chunks, hybrid_search
from app.retrieval.web import WebSearchError, fetch_page, search_web

READ_PAGE_CHARS = 14_000
WEB_RESULTS_PER_QUERY = 5

SEARCH_KNOWLEDGE = {
    "name": "search_knowledge",
    "description": (
        "Hybrid semantic + keyword search over the organization knowledge base in "
        "scope. Returns numbered sources; cite them as [n]. Pass several phrasings "
        "of the same need, or several distinct needs, as separate queries."
    ),
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
}

READ_DOCUMENT = {
    "name": "read_document",
    "description": (
        "Read the full text of a source you already have, by its number: a knowledge "
        "document, an attached file or a web result. Long sources are split into "
        "pages; the reply says how many there are."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ref": {"type": "integer", "description": "Source number."},
            "page": {
                "type": "integer",
                "minimum": 1,
                "description": "Page to read for long sources (default: the most relevant).",
            },
        },
        "required": ["ref"],
    },
}

LIST_COLLECTIONS = {
    "name": "list_collections",
    "description": "List the organizations and collections in scope, with document counts.",
    "input_schema": {"type": "object", "properties": {}},
}

SEARCH_WEB = {
    "name": "search_web",
    "description": (
        "Search the public web. Use it for current events, public facts or anything the "
        "organization's knowledge base can't cover — after checking the knowledge base "
        "when the question is about the organization. Returns numbered sources; cite "
        "them as [n]."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 3,
                "description": "Web search queries.",
            }
        },
        "required": ["queries"],
    },
}

LIST_ENTITIES = {
    "name": "list_entities",
    "description": (
        "List ALL entities of a kind from the knowledge graph built from the org's "
        "documents — people, teams, vendors/organizations, systems, policies, projects, "
        "roles. Unlike search (which returns only the top few passages), this returns the "
        "complete set and its total count, so use it for 'which…' / 'how many…' / 'list "
        "every…' questions. Filter with `contains` (matched against names and what the "
        "documents say about each entity), then check the details before answering."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(ENTITY_TYPES), "description": "Entity type."},
            "contains": {
                "type": "string",
                "description": "Optional text that must appear in the name or description.",
            },
        },
    },
}

EXPLORE_ENTITY = {
    "name": "explore_entity",
    "description": (
        "Look up one entity in the knowledge graph (a person, team, vendor, system, "
        "policy, role, risk tier…) and get what the documents say about it plus every "
        "entity it is connected to. Use it to follow chains (service → owning team → "
        "approver) and to list everything linked to one thing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Entity name."}},
        "required": ["name"],
    },
}

# Kept for callers that want the default knowledge-only tool set.
TOOL_DEFINITIONS: list[dict[str, Any]] = [SEARCH_KNOWLEDGE, READ_DOCUMENT, LIST_COLLECTIONS]


def tool_definitions(
    *, web: bool, graph: bool = False, records: bool = False
) -> list[dict[str, Any]]:
    out = list(TOOL_DEFINITIONS)
    if records:
        out += [QUERY_RECORDS, RESOLVE_ROLE]
    if graph:
        out += [LIST_ENTITIES, EXPLORE_ENTITY]
    if web:
        out.append(SEARCH_WEB)
    return out


def openai_tools(definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Anthropic tool definitions → OpenAI function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": d["input_schema"],
            },
        }
        for d in definitions
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
    # The same passages as citable search_result blocks, for Claude's native citations.
    results: list[dict[str, Any]] = field(default_factory=list)


def _result_title(entry: Registered, hit: Hit) -> str:
    """A search result's title: the document plus what the XML attributes say (titles
    aren't citable, so metadata here can't be cited as evidence)."""
    parts = [f"{hit.title} — {entry.org.name} / {entry.collection.name}"]
    if hit.heading:
        parts.append(f"section: {hit.heading}")
    if hit.page:
        parts.append(f"page {hit.page}")
    if hit.updated_at:
        parts.append(f"updated {hit.updated_at.date().isoformat()}")
    if hit.source_kind == "verified":
        parts.append('verified="true"')
    return "; ".join(parts)


def paginate(body: str, page: int | None, anchor: str | None = None) -> tuple[str, int, int]:
    """(window, page number, page count). Without a page, centre on `anchor`."""
    pages = max(1, -(-len(body) // READ_PAGE_CHARS))
    if pages == 1:
        return body, 1, 1
    if page is None:
        idx = body.find(anchor[:120]) if anchor else -1
        page = max(0, idx) // READ_PAGE_CHARS + 1
    page = max(1, min(page, pages))
    start = (page - 1) * READ_PAGE_CHARS
    window = body[start : start + READ_PAGE_CHARS]
    return ("…" if start else "") + window + ("…" if page < pages else ""), page, pages


@dataclass
class ToolBox:
    db: AsyncSession | None  # a short session bound for each tool call by the engine
    scope: RetrievalScope
    registry: CitationRegistry
    top_k: int = 8
    ranking: RankingOptions = field(default_factory=RankingOptions)
    attachments: dict[uuid.UUID, ChatAttachment] = field(default_factory=dict)
    graph: bool = False
    # Active record schemas in scope, for query_records (app/agent/records_tool.py).
    record_schemas: list[Any] = field(default_factory=list)
    _web_pages: dict[int, tuple[str | None, str]] = field(default_factory=dict)

    # ── validation ────────────────────────────────────────────────────────
    @staticmethod
    def _str_list(value: Any, name: str, *, required: bool = False) -> list[str]:
        if value is None and not required:
            return []
        if isinstance(value, str):
            # Models sometimes send one query as a bare string (seen live with Haiku on
            # search_web); treat it as a one-item list rather than failing the search.
            value = [value]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ToolInputError(f"'{name}' must be a list of strings")
        cleaned = [v.strip() for v in value if v.strip()]
        if required and not cleaned:
            raise ToolInputError(f"'{name}' must contain at least one non-empty string")
        return cleaned

    @staticmethod
    def _int(value: Any, name: str, *, required: bool = False) -> int | None:
        if value is None and not required:
            return None
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolInputError(f"'{name}' must be an integer")
        return value

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

    # ── knowledge ─────────────────────────────────────────────────────────
    async def search(self, args: dict[str, Any]) -> SearchOutcome:
        queries = self._str_list(args.get("queries"), "queries", required=True)[:4]
        collection_ids = self._narrowed_collections(
            self._str_list(args.get("collections"), "collections"),
            self._str_list(args.get("orgs"), "orgs"),
        )
        hits = await hybrid_search(
            self.db, queries, collection_ids, limit=self.top_k, ranking=self.ranking
        )
        return await self.render_hits(queries, hits)

    async def render_hits(self, queries: list[str], hits: list[Hit]) -> SearchOutcome:
        if not hits:
            return SearchOutcome(
                text="No matching sources found. Try different wording or broader terms.",
                queries=queries,
                refs=[],
            )
        blocks, refs, new, results = [], [], [], []
        for hit in hits:
            entry, is_new = self.registry.register(
                hit, self.scope.orgs[hit.org_id], self.scope.collections[hit.collection_id]
            )
            if is_new:
                new.append(entry)
            if entry.ref not in refs:
                refs.append(entry.ref)
            section = await hit_window(self.db, hit)
            heading = f" section={quoteattr(hit.heading)}" if hit.heading else ""
            updated = f' updated="{hit.updated_at.date().isoformat()}"' if hit.updated_at else ""
            updated += f' page="{hit.page}"' if hit.page else ""
            verified = ' verified="true"' if hit.source_kind == "verified" else ""
            blocks.append(
                f'<source ref="{entry.ref}" org={quoteattr(entry.org.name)} '
                f"collection={quoteattr(entry.collection.name)} "
                f"title={quoteattr(hit.title)}{heading}{updated}{verified}>\n"
                f"{escape(section)}\n</source>"
            )
            results.append(search_result(entry.ref, _result_title(entry, hit), section))
        note = self._capped_note(len(hits))
        if note and results:  # a tool result of search results holds only search results
            results[-1]["content"].append({"type": "text", "text": note.strip()})
        return SearchOutcome(
            text="\n\n".join(blocks) + note,
            queries=queries,
            refs=refs,
            new_sources=new,
            hits=len(hits),
            results=results,
        )

    async def query_records(self, args: dict[str, Any]) -> SearchOutcome:
        return await records_tool.run(self, self.record_schemas, args)

    async def resolve_role(self, args: dict[str, Any]) -> SearchOutcome:
        return await records_tool.run_resolve(self, self.record_schemas, args)

    async def list_collections(self) -> str:
        if not self.scope.collections:
            return "No collections are available in this scope."
        counts = dict(
            (
                await self.db.execute(
                    select(Document.collection_id, func.count(Document.id))
                    .where(
                        Document.collection_id.in_(self.scope.collection_ids),
                        Document.hidden.is_(False),
                    )
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

    def _capped_note(self, hits: int) -> str:
        """Search results are capped; say so where the model reads them, and point to
        the complete-set tools, rather than relying on the system prompt alone."""
        if not self.graph or hits < self.top_k:
            return ""
        return (
            f"\n\n(Search returns at most {self.top_k} passages, so this may not be "
            "everything. For lists or counts, also check list_entities or explore_entity.)"
        )

    # ── knowledge graph ───────────────────────────────────────────────────
    async def _refs_for(self, chunk_ids: list[uuid.UUID], new: list[Registered]) -> list[int]:
        """Register the documents behind graph facts as citable sources."""
        refs: list[int] = []
        for hit in await hits_for_chunks(self.db, chunk_ids[:3]):
            if hit.collection_id not in self.scope.collections:
                continue
            entry, is_new = self.registry.register(
                hit, self.scope.orgs[hit.org_id], self.scope.collections[hit.collection_id]
            )
            if is_new:
                new.append(entry)
            if entry.ref not in refs:
                refs.append(entry.ref)
        return refs

    def _org_label(self, org_id: uuid.UUID) -> str:
        return f" ({self.scope.orgs[org_id].name})" if len(self.scope.orgs) > 1 else ""

    async def list_entities(self, args: dict[str, Any]) -> SearchOutcome:
        etype = args.get("type")
        if etype is not None and etype not in ENTITY_TYPES:
            raise ToolInputError(f"'type' must be one of: {', '.join(ENTITY_TYPES)}")
        contains = args.get("contains")
        if contains is not None and not isinstance(contains, str):
            raise ToolInputError("'contains' must be a string")
        rows, total = await graph.list_entities(
            self.db, self.scope.collection_ids, entity_type=etype, contains=contains or None
        )
        label = " ".join(
            x
            for x in (
                f"type={etype}" if etype else "",
                f"contains={contains!r}" if contains else "",
            )
            if x
        )
        if not rows:
            return SearchOutcome(
                text=f"No entities match ({label or 'all'}).", queries=[label], refs=[]
            )
        new: list[Registered] = []
        lines, all_refs = [], []
        # Facts often live on relations (owner, risk tier, agreement status), so show
        # each entity's neighbours too: one call is then enough to filter or count.
        links = await graph.linked_names(self.db, self.scope.collection_ids, [r.id for r in rows])
        for row in rows:
            refs = await self._refs_for(row.chunk_ids, new)
            all_refs += [r for r in refs if r not in all_refs]
            cite = "".join(f"[{r}]" for r in refs)
            linked = f" | linked: {'; '.join(links[row.id])}" if links.get(row.id) else ""
            lines.append(
                f"- {row.name} [{row.type}]{self._org_label(row.org_id)} {cite}: "
                f"{row.description[:240]}{linked}"
            )
        more = f" Showing the first {len(rows)}." if total > len(rows) else ""
        # The graph is extracted automatically and can miss items: say so, so the model
        # cross-checks instead of treating it as ground truth (measured: claiming
        # completeness made a stronger model stop searching too early).
        header = (
            f"{total} entities in the knowledge graph match ({label or 'all types'}).{more} "
            "The graph is extracted automatically and may miss items; for an exact list or "
            "count, cross-check with search_knowledge."
        )
        return SearchOutcome(
            text=header + "\n" + "\n".join(lines),
            queries=[label or "all entities"],
            refs=all_refs,
            new_sources=new,
            hits=total,
        )

    async def explore_entity(self, args: dict[str, Any]) -> SearchOutcome:
        name = args.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ToolInputError("'name' must be a non-empty string")
        matches = await graph.find_entity(self.db, self.scope.collection_ids, name)
        if not matches:
            return SearchOutcome(
                text=f"No entity named {name!r} in the knowledge graph. Try search_knowledge.",
                queries=[name],
                refs=[],
            )
        new: list[Registered] = []
        blocks, all_refs = [], []
        # Spellings with exactly the query's words ("Risk Tier High" / "High Risk Tier")
        # are one entity: merge their relations.
        want = graph.name_tokens(name)
        same = [m for m in matches if graph.name_tokens(m.name) == want]
        for group in [same] if same else [[m] for m in matches[:2]]:
            entity = group[0]
            refs = await self._refs_for([c for g in group for c in g.chunk_ids], new)
            all_refs += [r for r in refs if r not in all_refs]
            relations, total = await graph.relations_of(
                self.db, self.scope.collection_ids, [g.id for g in group]
            )
            lines = [
                f"{entity.name} [{entity.type}]{self._org_label(entity.org_id)} "
                f"{''.join(f'[{r}]' for r in refs)}: {entity.description}",
                f"Connected to {total} entities:",
            ]
            for rel in relations:
                rrefs = await self._refs_for(rel.chunk_ids, new)
                all_refs += [r for r in rrefs if r not in all_refs]
                lines.append(
                    f"- {rel.other_name} [{rel.other_type}] {''.join(f'[{r}]' for r in rrefs)}: "
                    f"{rel.description}"
                )
            if total > len(relations):
                lines.append(f"(first {len(relations)} of {total} shown)")
            blocks.append("\n".join(lines))
        return SearchOutcome(
            text="\n\n".join(blocks), queries=[name], refs=all_refs, new_sources=new,
            hits=len(all_refs),
        )  # fmt: skip

    # ── web ───────────────────────────────────────────────────────────────
    async def search_web(self, args: dict[str, Any]) -> SearchOutcome:
        queries = self._str_list(args.get("queries"), "queries", required=True)[:3]
        blocks, refs, new = [], [], []
        try:
            for query in queries:
                for result in await search_web(query, WEB_RESULTS_PER_QUERY):
                    entry, is_new = self.registry.register_web(
                        result.url, result.title, result.snippet
                    )
                    if is_new:
                        new.append(entry)
                    if entry.ref in refs:
                        continue
                    refs.append(entry.ref)
                    blocks.append(
                        f'<source ref="{entry.ref}" origin="web" title={quoteattr(result.title)} '
                        f"url={quoteattr(result.url)}>\n{escape(result.snippet[:1500])}\n</source>"
                    )
        except WebSearchError as exc:
            raise ToolInputError(str(exc)) from exc
        text = "\n\n".join(blocks) if blocks else "The web search returned no results."
        return SearchOutcome(text=text, queries=queries, refs=refs, new_sources=new, hits=len(refs))

    # ── reading ───────────────────────────────────────────────────────────
    async def read(self, args: dict[str, Any]) -> tuple[str, Registered, dict[str, Any]]:
        """The source's text (one page of it if long): as XML-tagged text, and as a
        citable search_result block for Claude."""
        ref = self._int(args.get("ref"), "ref", required=True)
        page = self._int(args.get("page"), "page")
        entry = self.registry.get(ref)
        if entry is None:
            raise ToolInputError(f"There is no source [{ref}] yet. Search first.")

        if entry.origin == "attachment":
            attachment = self.attachments.get(entry.attachment_id)
            if attachment is None:
                raise ToolInputError(f"Source [{ref}] is no longer available.")
            title, body, anchor = attachment.filename, attachment.content, None
            extra = ' origin="attachment"'
        elif entry.origin == "web":
            if ref not in self._web_pages:
                try:
                    self._web_pages[ref] = await fetch_page(entry.url or "")
                except WebSearchError as exc:
                    raise ToolInputError(f"Couldn't read [{ref}]: {exc}") from exc
                except Exception as exc:
                    raise ToolInputError(f"Couldn't read [{ref}]: the page didn't load") from exc
            detected, body = self._web_pages[ref]
            # Open on the part of the page the search snippet came from.
            title, anchor = detected or entry.title, entry.source.get("snippet", "").rstrip("…")
            extra = f' origin="web" url={quoteattr(entry.url or "")}'
        else:
            doc = await self.db.get(Document, entry.hit.document_id)
            if doc is None or doc.hidden or doc.collection_id not in self.scope.collections:
                raise ToolInputError(f"Source [{ref}] is no longer available.")
            title, body, anchor = doc.title, doc.content, entry.hit.content
            extra = f" org={quoteattr(entry.org.name)}"

        window, page_no, pages = paginate(body, page, anchor)
        paging = f' page="{page_no}" pages="{pages}"' if pages > 1 else ""
        text = (
            f'<document ref="{ref}" title={quoteattr(title)}{extra}{paging}>\n'
            f"{escape(window)}\n</document>"
        )
        label = title + (f" — {entry.org.name}" if entry.origin == "knowledge" else "")
        label += f" (page {page_no} of {pages})" if pages > 1 else ""
        return text, entry, search_result(ref, label, window)
