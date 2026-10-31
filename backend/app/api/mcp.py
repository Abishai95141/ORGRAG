"""MCP server: Strata's knowledge as tools for Claude, ChatGPT, IDE agents and any other
Model Context Protocol client.

Endpoint `POST /api/mcp` (streamable HTTP, stateless), built on the official MCP Python
SDK (`mcp.server.mcpserver.MCPServer`). Clients authenticate with an org API key as a
bearer token (`Authorization: Bearer strata_sk_…`), verified through the SDK's
`TokenVerifier` hook. Every tool then acts exactly as the REST API does for that key:
as its creator, inside its org, with at most the key's role, so a key never sees a
collection, document or org its role can't. Search results and answers carry
org-labelled citations.

Tools: `search` (no LLM), `read_document`, `list_collections`, and `ask` (the full
agent; spends the org's model budget and appears in the key owner's chat history).
"""

import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.agent.citations import cited_refs
from app.agent.engine import direct_search
from app.agent.tools import paginate
from app.api.chat import DEFAULT_TITLE, begin_answer, wait_for_answer
from app.api.scope import key_collection_ids
from app.core import audit
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.deps import OrgContext, api_key_context, find_api_key
from app.core.errors import ApiError
from app.core.security import API_KEY_PREFIX
from app.models import ApiKey, Collection, Conversation, Document, SearchEvent
from app.records import query as records_query
from app.retrieval.access import visible_collections
from app.schemas.common import MessageCreate

log = logging.getLogger("strata.mcp")

MCP_PATH = "/api/mcp"

INSTRUCTIONS = """Strata searches an organization's internal knowledge base (documents, \
wikis, repositories, Drive, Slack and Notion) with the permissions of the API key you \
were given. Use `search` to find passages and `read_document` to read one in full; \
cite results by title and organization. Use `ask` for a researched, cited answer \
written by Strata's own agent (slower, and it uses the organization's model budget)."""


# ── auth ─────────────────────────────────────────────────────────────────────
class ApiKeyVerifier(TokenVerifier):
    """Accepts live org API keys; anything else gets the SDK's 401."""

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token.startswith(API_KEY_PREFIX):
            return None
        async with SessionLocal() as db:
            key = await find_api_key(db, token)
        if key is None:
            return None
        return AccessToken(
            token=token,
            client_id=f"strata-key:{key.id}",
            scopes=[],
            subject=str(key.user_id),
            claims={"key_id": str(key.id)},
        )


@contextlib.asynccontextmanager
async def _context() -> AsyncIterator[OrgContext]:
    """The calling key's org context, in a session that lives for one tool call."""
    token = get_access_token()
    if token is None or not (token.claims or {}).get("key_id"):
        raise ToolError("Not authenticated")
    async with SessionLocal() as db:
        key = await db.get(ApiKey, uuid.UUID(token.claims["key_id"]))
        ctx = await api_key_context(db, key) if key is not None else None
        if ctx is None:
            raise ToolError("This API key no longer has access to its organization")
        yield ctx


def _ids(values: list[str] | None, name: str) -> list[uuid.UUID] | None:
    if not values:
        return None
    try:
        return [uuid.UUID(v) for v in values]
    except ValueError as e:
        raise ToolError(f"{name} must be collection ids from list_collections") from e


# ── results ──────────────────────────────────────────────────────────────────
class Passage(BaseModel):
    ref: int
    title: str
    organization: str | None
    collection: str | None
    url: str | None
    snippet: str
    document_id: str | None = Field(description="Pass to read_document for the full text")
    origin: str = Field(description="knowledge, web or attachment")


def _passage(ref: dict) -> Passage:
    return Passage(
        ref=ref["ref"],
        title=ref["title"],
        organization=ref.get("org_name"),
        collection=ref.get("collection_name"),
        url=ref.get("url"),
        snippet=ref.get("snippet") or "",
        document_id=str(ref["document_id"]) if ref.get("document_id") else None,
        origin=ref.get("origin") or "knowledge",
    )


class SearchResults(BaseModel):
    results: list[Passage]


class CollectionInfo(BaseModel):
    id: str
    name: str
    description: str | None
    documents: int


class Collections(BaseModel):
    organization: str
    collections: list[CollectionInfo]


class DocumentPage(BaseModel):
    document_id: str
    title: str
    organization: str
    collection: str
    url: str | None
    page: int
    pages: int
    content: str


class AnswerOut(BaseModel):
    answer: str
    status: str = Field(description="complete, error or stopped")
    sources: list[Passage] = Field(description="The sources cited in the answer, by [ref]")
    conversation_id: str = Field(description="Also visible in the key owner's Strata chat")


# ── server ───────────────────────────────────────────────────────────────────
server = MCPServer(
    name="Strata",
    title="Strata knowledge base",
    instructions=INSTRUCTIONS,
    token_verifier=ApiKeyVerifier(),
    # Strata is the resource server only: keys are issued in Settings → API keys, so
    # no OAuth endpoints or protected-resource metadata are served.
    auth=AuthSettings(issuer_url=get_settings().public_url, resource_server_url=None),
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)


@server.tool(annotations=READ_ONLY)
async def search(
    query: Annotated[str, Field(min_length=1, max_length=500)],
    collection_ids: list[str] | None = None,
    limit: Annotated[int, Field(ge=1, le=20)] = 8,
) -> SearchResults:
    """Search the organization's knowledge base (hybrid keyword + semantic search with
    reranking, no LLM). Returns the best passage per document with its organization,
    collection and link. Optionally restrict to collection ids from list_collections."""
    started = time.perf_counter()
    async with _context() as ctx:
        scoped = await key_collection_ids(ctx, _ids(collection_ids, "collection_ids"))
        found = await direct_search(
            ctx.db,
            user_id=ctx.user.id,
            org=ctx.org,
            org_slugs=[ctx.org.slug],
            collection_ids=scoped,
            query=query,
            limit=limit,
        )
        refs = [r.to_source_ref() for r in found]
        audit.record_reads(
            ctx.db, audit.Actor.of(ctx), "search", refs, query=query, origin_org_id=ctx.org_id
        )
        ctx.db.add(
            SearchEvent(
                org_id=ctx.org_id,
                user_id=ctx.user.id,
                kind="search",
                query=query,
                latency_ms=int((time.perf_counter() - started) * 1000),
                hits=len(found),
            )
        )
        await ctx.db.commit()
    return SearchResults(results=[_passage(r) for r in refs])


@server.tool(annotations=READ_ONLY)
async def read_document(
    document_id: str, page: Annotated[int | None, Field(ge=1)] = None
) -> DocumentPage:
    """Read a document found by search, as Markdown. Long documents come in pages of
    about 14,000 characters; ask for the next page until page == pages."""
    try:
        doc_id = uuid.UUID(document_id)
    except ValueError as e:
        raise ToolError("document_id must be a document_id from search") from e
    async with _context() as ctx:
        visible = set(await key_collection_ids(ctx, None))
        doc = await ctx.db.get(Document, doc_id)
        # Hidden, other-org and out-of-role documents are indistinguishable from missing.
        if doc is None or doc.hidden or doc.collection_id not in visible:
            raise ToolError("No such document")
        collection = await ctx.db.get(Collection, doc.collection_id)
        window, page_no, pages = paginate(doc.content, page)
        audit.record_reads(
            ctx.db,
            audit.Actor.of(ctx),
            "read",
            [audit.document_ref(doc, collection)],
            origin_org_id=ctx.org_id,
        )
        await ctx.db.commit()
        return DocumentPage(
            document_id=str(doc.id),
            title=doc.title,
            organization=ctx.org.name,
            collection=collection.name if collection else "",
            url=doc.url,
            page=page_no,
            pages=pages,
            content=window,
        )


@server.tool(annotations=READ_ONLY)
async def list_collections() -> Collections:
    """The collections this key can read, with how many documents each holds."""
    async with _context() as ctx:
        cols = await visible_collections(ctx.db, ctx.org_id, ctx.user.id, ctx.role)
        counts = dict(
            (
                await ctx.db.execute(
                    select(Document.collection_id, func.count())
                    .where(Document.collection_id.in_([c.id for c in cols]), ~Document.hidden)
                    .group_by(Document.collection_id)
                )
            ).all()
        )
        return Collections(
            organization=ctx.org.name,
            collections=[
                CollectionInfo(
                    id=str(c.id),
                    name=c.name,
                    description=c.description,
                    documents=counts.get(c.id, 0),
                )
                for c in sorted(cols, key=lambda c: c.name.lower())
            ],
        )


class RecordType(BaseModel):
    name: str = Field(description="Pass as record_type to query_records")
    label: str
    collection: str
    records: int | None
    fields: list[dict] = Field(description="name, label, type and, for choices, options")


class RecordTypes(BaseModel):
    record_types: list[RecordType]


class RecordRow(BaseModel):
    key: str
    values: dict
    document_id: str = Field(description="Pass to read_document to see the source")
    document_title: str


class RecordResults(BaseModel):
    total: int = Field(description="Every matching record in scope, not a top-k sample")
    rows: list[RecordRow]
    groups: list[dict] = Field(description="For mode=group: value and count")


@server.tool(annotations=READ_ONLY)
async def list_record_types() -> RecordTypes:
    """Structured record types this key can query (registers, inventories, directories
    read into typed rows), with their fields."""
    async with _context() as ctx:
        visible = await key_collection_ids(ctx, None)
        schemas = await records_query.schemas_in_scope(ctx.db, visible)
        names = dict(
            (await ctx.db.execute(select(Collection.id, Collection.name).where(
                Collection.id.in_({s.collection_id for s in schemas})
            ))).all()
        )  # fmt: skip
        return RecordTypes(
            record_types=[
                RecordType(
                    name=s.name,
                    label=s.label,
                    collection=names.get(s.collection_id, ""),
                    records=(s.stats or {}).get("records"),
                    fields=[
                        {k: f[k] for k in ("name", "label", "type", "options") if f.get(k)}
                        for f in s.fields
                    ],
                )
                for s in schemas
            ]
        )


@server.tool(annotations=READ_ONLY)
async def query_records(
    record_type: str,
    filters: list[dict] | None = None,
    mode: Literal["list", "count", "group"] = "list",
    group_by: str | None = None,
    sort: str | None = None,
    descending: bool = False,
    limit: Annotated[int, Field(ge=1, le=300)] = 100,
    latest_only: bool = False,
) -> RecordResults:
    """Exact, complete lookups over a record type from list_record_types: filters
    ({field, op, value}; op is eq, ne, contains, in, lt, lte, gt, gte, exists or
    missing; dates YYYY-MM-DD), counts, or counts per value of group_by. Use it for
    which / how many / list-all questions, where search returns only a sample."""
    async with _context() as ctx:
        visible = await key_collection_ids(ctx, None)
        schemas = [
            s
            for s in await records_query.schemas_in_scope(ctx.db, visible)
            if s.name == record_type
        ]
        if not schemas:
            raise ToolError(f"No record type '{record_type}'. See list_record_types.")
        try:
            result = await records_query.run(
                ctx.db,
                schemas,
                visible,
                filters=filters or [],
                mode=mode,
                group_by=group_by,
                sort=sort,
                descending=descending,
                limit=limit,
                latest_only=latest_only,
            )
        except records_query.QueryError as e:
            raise ToolError(str(e)) from e
        return RecordResults(
            total=result.total,
            rows=[
                RecordRow(
                    key=r.key,
                    values=r.values,
                    document_id=str(r.document_id),
                    document_title=r.title,
                )
                for r in result.rows
            ],
            groups=[{"value": v, "count": n} for v, n in result.groups],
        )


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, openWorldHint=True))
async def ask(
    question: Annotated[str, Field(min_length=1, max_length=20000)],
    mode: Literal["quick", "deep"] = "quick",
    collection_ids: list[str] | None = None,
) -> AnswerOut:
    """Get a researched answer from Strata's agent, with [n] citations resolved in
    `sources`. "quick" takes seconds; "deep" plans sub-questions and reads more, and
    can take minutes. Uses the organization's model budget; the conversation is saved
    to the key owner's chat history."""
    async with _context() as ctx:
        conv = Conversation(
            org_id=ctx.org_id,
            user_id=ctx.user.id,
            title=DEFAULT_TITLE,
            mode=mode,
            scope_org_ids=[ctx.org_id],
        )
        ctx.db.add(conv)
        await ctx.db.flush()
        body = MessageCreate(
            content=question, mode=mode, collection_ids=_ids(collection_ids, "collection_ids")
        )
        queue, _ = await begin_answer(ctx, conv, body)
        conversation_id = str(conv.id)
    try:
        message = (await wait_for_answer(queue))["message"]
    except ApiError as e:
        raise ToolError(e.detail) from e
    cited = set(cited_refs(message["content"]))
    return AnswerOut(
        answer=message["content"],
        status=message["status"],
        sources=[_passage(s) for s in message["sources"] if s["ref"] in cited],
        conversation_id=conversation_id,
    )


# ── ASGI wiring ──────────────────────────────────────────────────────────────
class McpEndpoint:
    """Serves `MCP_PATH` from the MCP app and everything else from the API. A plain
    middleware rather than a Mount: a mount wants `/api/mcp/`, which the trailing-slash
    normaliser would undo. The SDK's session manager runs once per app instance, so
    `lifespan` builds a fresh one each time the API starts."""

    app: ASGIApp | None = None

    def __init__(self, app: ASGIApp):
        self.api = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == MCP_PATH:
            if McpEndpoint.app is None:  # outside the app's error handlers, so answer here
                body = {"detail": "The MCP server isn't running", "code": "mcp_unavailable"}
                await JSONResponse(body, status_code=503)(scope, receive, send)
                return
            await McpEndpoint.app(scope, receive, send)
            return
        await self.api(scope, receive, send)


@contextlib.asynccontextmanager
async def lifespan() -> AsyncIterator[None]:
    McpEndpoint.app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        # DNS-rebinding protection guards local servers that trust the browser; this
        # endpoint accepts only bearer API keys (never cookies), which a rebinding page
        # doesn't have, and it sits behind arbitrary hostnames and proxies.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    try:
        async with server.session_manager.run():
            yield
    finally:
        McpEndpoint.app = None
