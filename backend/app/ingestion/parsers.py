"""Turn raw bytes into normalized Markdown. Each parser keeps headings so the
chunker can attach a heading path to every chunk."""

import csv
import io
import json
import re
from pathlib import PurePosixPath

from bs4 import BeautifulSoup

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


class UnsupportedFileType(ValueError):
    pass


def mime_for(filename: str) -> str:
    ext = PurePosixPath(filename.lower()).suffix
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileType(f"Unsupported file type: {ext or filename}")
    return SUPPORTED_EXTENSIONS[ext]


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
