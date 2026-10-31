"""Optional vision pass for pages Docling reads weakly (VISION_PARSING).

At indexing time only: a page whose text came out sparse, or that is mostly pictures
(a chart, a diagram, a slide that is a picture, a scan OCR read badly), is rendered
and transcribed to Markdown by a vision model. The transcript replaces Docling's text
for that page; every other page keeps Docling's. Questions later retrieve the stored
chunks like any others, so this costs once per weak page, never per question.

Any failure keeps Docling's text for that page."""

import asyncio
import base64
import logging

from app.core.config import get_settings
from app.llm import pricing
from app.llm.client import get_client

log = logging.getLogger("strata.ingestion")

CONCURRENCY = 4
MAX_TOKENS = 4000

PROMPT = """Transcribe this page into Markdown for a search index.
- Every piece of text on the page, in reading order. Headings as Markdown headings.
- Tables as Markdown tables, every row and column, numbers exactly as printed.
- Charts and graphs: a one-line description, then the plotted values as a Markdown \
table (labels, series, values as shown; mark values you read off the chart rather \
than from a printed label with "≈").
- Diagrams: the boxes and what connects them, as a short list.
- Skip decoration, logos and page furniture (page numbers, repeated footers).
Write only what is on the page. No commentary, no preamble, no code fences."""


def enabled() -> bool:
    s = get_settings()
    return s.vision_parsing and bool(s.anthropic_api_key)


async def _transcribe(png: bytes, model: str) -> tuple[str, float]:
    message = await get_client().messages.create(
        model=model,
        max_tokens=MAX_TOKENS,
        thinking={"type": "disabled"},  # Haiku 5.5: transcription needs no reasoning
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(png).decode(),
                        },
                    },
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
    )
    u = message.usage
    usage = {
        "input": u.input_tokens or 0,
        "output": u.output_tokens or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }
    text = next((b.text for b in message.content if b.type == "text"), "")
    if message.stop_reason == "max_tokens":
        log.warning("Vision transcript of a page was cut at %d tokens", MAX_TOKENS)
    return text.strip(), pricing.cost(model, usage) or 0.0


async def transcribe_pages(renders: dict[int, bytes]) -> tuple[dict[int, str], float]:
    """{page: markdown} for the pages that transcribed, and the total cost."""
    settings = get_settings()
    pages = sorted(renders)[: settings.vision_max_pages]
    sem = asyncio.Semaphore(CONCURRENCY)
    out: dict[int, str] = {}
    cost = 0.0

    async def one(n: int) -> None:
        nonlocal cost
        async with sem:
            try:
                text, spent = await _transcribe(renders[n], settings.vision_model)
            except Exception as exc:  # keep Docling's text for this page
                log.warning("Vision transcript of page %d failed: %s", n, exc)
                return
        cost += spent
        if text:
            out[n] = text

    await asyncio.gather(*(one(n) for n in pages))
    return out, cost
