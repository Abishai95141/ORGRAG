"""Contextual Retrieval: a short, model-written context for each chunk, saying where it
sits in its document ("This section of Halcyon's PTO policy covers carry-over…").

Following Anthropic's recipe, the context is prepended to the chunk for embedding and
added to the keyword index; answers and citations still use the chunk text alone.
The document goes in a cached first block and the chunk in a second, so a document's
chunks share one cache write. Documents with a single chunk are skipped: that chunk
is the whole document, so there's nothing to situate it in.

Indexing is two-phase: a document is searchable as soon as it's chunked, and the
`contextualize` job adds contexts and re-embeds afterwards."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import CONTEXT_VERSION, get_settings
from app.embeddings import get_embedder
from app.ingestion.chunker import embedding_text, estimate_tokens
from app.llm import pricing
from app.llm.client import get_client
from app.models import Chunk, Document

log = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 200
CONCURRENCY = 8
# Haiku 5.5 bills prompts over 100K tokens at 5×; long documents get a window instead.
MAX_DOCUMENT_TOKENS = 60_000
NEIGHBOURS = 6  # chunks on each side of the target in a windowed document

DOCUMENT_PROMPT = "<document>\n<title>{title}</title>\n{content}\n</document>"
CHUNK_PROMPT = (
    "Here is the chunk we want to situate within the whole document\n"
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Please give a short succinct context to situate this chunk within the overall "
    "document for the purposes of improving search retrieval of the chunk. Answer only "
    "with the succinct context and nothing else."
)


@dataclass
class Usage:
    calls: int = 0
    tokens: dict[str, int] = field(
        default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    )
    cost_usd: float = 0.0

    def add(self, model: str, u: Any) -> None:
        call = {
            "input": u.input_tokens or 0,
            "output": u.output_tokens or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        self.calls += 1
        for k, v in call.items():
            self.tokens[k] += v
        self.cost_usd += pricing.cost(model, call) or 0.0


def context_hash(doc: Document) -> str:
    return f"{CONTEXT_VERSION}:{doc.content_hash}"


def _document_text(doc: Document, chunks: list[Chunk], i: int) -> str:
    """The whole document, or for a very long one, its outline plus the chunks around
    chunk i (kept under MAX_DOCUMENT_TOKENS)."""
    if estimate_tokens(doc.content) <= MAX_DOCUMENT_TOKENS:
        return DOCUMENT_PROMPT.format(title=doc.title, content=doc.content)
    outline = "\n".join(dict.fromkeys(c.heading for c in chunks if c.heading))
    near = chunks[max(0, i - NEIGHBOURS) : i + NEIGHBOURS + 1]
    body = "\n\n".join(f"## {c.heading}\n{c.content}" if c.heading else c.content for c in near)
    text = f"Outline:\n{outline}\n\nExcerpt around the chunk:\n{body}"
    return DOCUMENT_PROMPT.format(title=doc.title, content=text[: MAX_DOCUMENT_TOKENS * 4])


async def _situate(model: str, document: str, chunk: Chunk, usage: Usage) -> str:
    message = await get_client().messages.create(
        model=model,
        max_tokens=MAX_CONTEXT_TOKENS,
        thinking={"type": "disabled"},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": document,
                        "cache_control": {"type": "ephemeral"},
                    },
                    {"type": "text", "text": CHUNK_PROMPT.format(chunk=chunk.content)},
                ],
            }
        ],
    )
    usage.add(model, message.usage)
    text = next((b.text for b in message.content if b.type == "text"), "")
    return " ".join(text.split())


async def contextualize_document(
    db: AsyncSession, doc: Document, model: str | None = None, usage: Usage | None = None
) -> int:
    """Write contexts for a document's chunks and re-embed them. Returns the number of
    chunks contextualized (0 for a single-chunk document). Caller commits."""
    model = model or get_settings().context_model
    usage = usage or Usage()
    chunks = list(
        (
            await db.execute(
                select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.ordinal)
            )
        ).scalars()
    )
    if len(chunks) <= 1:
        for c in chunks:
            c.context = ""
        doc.context_hash = context_hash(doc)
        return 0

    contexts: list[str] = [""] * len(chunks)
    # The first call writes the document to the cache; the rest then read it.
    contexts[0] = await _situate(model, _document_text(doc, chunks, 0), chunks[0], usage)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(i: int) -> None:
        async with sem:
            contexts[i] = await _situate(model, _document_text(doc, chunks, i), chunks[i], usage)

    await asyncio.gather(*(one(i) for i in range(1, len(chunks))))

    vectors = await get_embedder().embed_documents(
        [
            embedding_text(doc.title, c.heading, c.content, ctx)
            for c, ctx in zip(chunks, contexts, strict=True)
        ]
    )
    for c, ctx, vec in zip(chunks, contexts, vectors, strict=True):
        c.context = ctx
        c.embedding = vec
    doc.context_hash = context_hash(doc)
    return len(chunks)


def pending_documents(source_id) -> Any:
    """Documents of a source whose contexts are missing or stale."""
    return (
        select(Document)
        .where(
            Document.source_id == source_id,
            or_(
                Document.context_hash.is_(None),
                Document.context_hash != CONTEXT_VERSION + ":" + Document.content_hash,
            ),
        )
        .order_by(Document.title)
    )


async def contextualize_source(db: AsyncSession, source_id, model: str | None = None) -> dict:
    """Contextualize every pending document of a source, committing each one, so an
    interrupted run resumes where it stopped."""
    usage = Usage()
    docs = list((await db.execute(pending_documents(source_id))).scalars())
    done = failed = 0
    for doc in docs:
        try:
            await contextualize_document(db, doc, model, usage)
            await db.commit()
            done += 1
        except Exception as exc:
            await db.rollback()
            failed += 1
            log.warning("Contextualizing %s failed: %s", doc.title, exc)
    return {
        "documents": done,
        "failed": failed,
        "calls": usage.calls,
        **usage.tokens,
        "cost_usd": round(usage.cost_usd, 4),
    }
