"""Turn raw bytes into normalized Markdown. Each parser keeps headings so the
chunker can attach a heading path to every chunk.

`parse_document` is the entry point for indexing: PDF, Office files, email and images
go through Docling (docling_parser.py) when it's installed; everything else, and PDF
and DOCX without Docling, uses the light parsers here."""

import asyncio
import csv
import io
import json
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from app.ingestion.docling_parser import Parsed

SUPPORTED_EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".html": "text/html",
    ".htm": "text/html",
    ".csv": "text/csv",
    ".json": "application/json",
}
# Accepted only when Docling is installed (the `parsing` extra).
RICH_EXTENSIONS = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".eml": "message/rfc822",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}
# Legacy binary Office: also needs LibreOffice, which Docling converts them with.
LEGACY_EXTENSIONS = {
    ".doc": "application/msword",
    ".ppt": "application/vnd.ms-powerpoint",
    ".xls": "application/vnd.ms-excel",
}


class UnsupportedFileType(ValueError):
    pass


def supported_extensions() -> dict[str, str]:
    """Extension → mime for every type this deployment can parse."""
    from app.ingestion import docling_parser

    supported = dict(SUPPORTED_EXTENSIONS)
    if docling_parser.available():
        supported |= RICH_EXTENSIONS
    if docling_parser.legacy_office_available():
        supported |= LEGACY_EXTENSIONS
    return supported


def mime_for(filename: str) -> str:
    ext = PurePosixPath(filename.lower()).suffix
    supported = supported_extensions()
    if ext not in supported:
        raise UnsupportedFileType(f"Unsupported file type: {ext or filename}")
    return supported[ext]


async def parse_document(data: bytes, mime: str) -> "Parsed":
    """Markdown, detected title and parse details, using Docling for the formats it
    handles (and the light parsers otherwise)."""
    from app.ingestion import docling_parser

    if mime in docling_parser.DOCLING_TYPES and docling_parser.available():
        return await docling_parser.convert(data, mime)
    if mime in RICH_EXTENSIONS.values() or mime in LEGACY_EXTENSIONS.values():
        raise UnsupportedFileType(f"Parsing {mime} needs the `parsing` extra (Docling)")
    markdown, title = await asyncio.to_thread(parse, data, mime)  # CPU-bound
    return docling_parser.Parsed(markdown, title)


def parse(data: bytes, mime: str) -> tuple[str, str | None]:
    """Return (markdown, detected_title)."""
    if mime == "application/pdf":
        return _pdf(data), None
    if mime.endswith("wordprocessingml.document"):
        return _docx(data)
    if mime == "text/html":
        return html_to_markdown(data.decode("utf-8", errors="replace"))
    if mime == "text/csv":
        return _csv(data.decode("utf-8", errors="replace")), None
    if mime == "application/json":
        return _json(data.decode("utf-8", errors="replace")), None
    text = data.decode("utf-8", errors="replace")
    return text, _first_heading(text)


def _first_heading(md: str) -> str | None:
    m = re.search(r"^#{1,2}\s+(.+?)\s*$", md, re.MULTILINE)
    return m.group(1).strip() if m else None


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"## Page {i}\n\n{text}")
    return "\n\n".join(pages)


def _docx(data: bytes) -> tuple[str, str | None]:
    import docx

    document = docx.Document(io.BytesIO(data))
    lines: list[str] = []
    title = None
    for para in document.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower() if para.style is not None else ""
        if style == "title":
            title = title or text
            lines.append(f"# {text}")
        elif style.startswith("heading"):
            level = int(style.split()[-1]) if style.split()[-1].isdigit() else 2
            lines.append(f"{'#' * min(level + 1, 6)} {text}")
            title = title or text
        elif "list" in style:
            lines.append(f"- {text}")
        else:
            lines.append(text)
    for table in document.tables:
        rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
        if rows:
            lines.append(_md_table(rows))
    return "\n\n".join(lines), title


def html_to_markdown(html: str) -> tuple[str, str | None]:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else None
    root = soup.find("main") or soup.find("article") or soup.body or soup
    lines: list[str] = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "td", "blockquote"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        name = el.name
        if name in ("h1", "h2", "h3", "h4"):
            lines.append(f"{'#' * int(name[1])} {text}")
        elif name == "li":
            lines.append(f"- {text}")
        elif name == "pre":
            lines.append(f"```\n{el.get_text()}\n```")
        elif name == "blockquote":
            lines.append(f"> {text}")
        else:
            lines.append(text)
    deduped: list[str] = []
    for line in lines:
        if not deduped or deduped[-1] != line:
            deduped.append(line)
    return "\n\n".join(deduped), title


def _md_table(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
    body = ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join([head, sep, *body])


def _csv(text: str) -> str:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return ""
    header, body = rows[0], rows[1:]
    # Render each record as a key/value block so rows chunk and retrieve independently.
    blocks = []
    for i, row in enumerate(body, start=1):
        pairs = [f"- **{h}**: {v}" for h, v in zip(header, row, strict=False) if v.strip()]
        blocks.append(f"### Row {i}\n" + "\n".join(pairs))
    return "\n\n".join(blocks) if blocks else _md_table(rows)


def _json(text: str) -> str:
    try:
        return "```json\n" + json.dumps(json.loads(text), indent=2)[:200_000] + "\n```"
    except json.JSONDecodeError:
        return text
