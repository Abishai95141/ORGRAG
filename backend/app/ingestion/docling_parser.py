"""Rich document parsing with Docling: PDF (with OCR and table structure), Word, Excel,
PowerPoint, email and images, to the same Markdown the rest of indexing uses.

It runs once, when a document is indexed; questions only read the stored chunks.

Paginated formats are handled page by page. PDFs and images go through Docling's PDF
pipeline (layout model, TableFormer, OCR for bitmap regions). PowerPoint decks are
converted to PDF with LibreOffice first, so every slide is a page with the same layout
analysis and OCR (slides that are pictures, common in investor and board decks, have
no text for a PowerPoint reader). Word, Excel and email keep Docling's native readers,
which keep their structure exactly.

Each page gets a quality signal from Docling's own output: how much text it yielded,
and how much of it is pictures (charts, diagrams, photos of text). Pages that come out
weak can optionally be transcribed by a vision model (VISION_PARSING; vision.py); off,
indexing uses no LLM at all.

Docling runs in a pool of worker processes (spawn, not fork: fork hangs with torch,
Docling issue #1277). Each worker builds its converter once and is replaced after
DOCLING_TASKS_PER_WORKER documents (Docling has had memory growth, #2209 and #2788).
A conversion past DOCLING_TIMEOUT_SECONDS kills the pool: a process can't be
interrupted from outside, and a hung one would hold a worker forever. Memory is
capped by the container, since RLIMIT_AS breaks torch. OCR is pinned to RapidOCR on
onnxruntime everywhere (left to choose, Docling would use ocrmac on macOS and RapidOCR
on Linux, so dev and production would differ).

Install with the `parsing` extra. Without it, PDF and Word use the light parsers in
`parsers.py` and the other formats aren't accepted."""

import asyncio
import importlib.util
import io
import logging
import multiprocessing
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.ingestion.chunker import page_marker

log = logging.getLogger("strata.ingestion")

# What Docling parses here, by mime, with the extension it detects formats by.
DOCLING_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "message/rfc822": ".eml",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
    # Legacy binary Office: converted with LibreOffice first.
    "application/msword": ".doc",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.ms-excel": ".xls",
}
MIMES_BY_EXT = {ext: mime for mime, ext in DOCLING_TYPES.items()}
OOXML = {m for m, ext in DOCLING_TYPES.items() if ext in (".docx", ".xlsx", ".pptx")}
SLIDES = {".pptx", ".ppt"}  # parsed as PDF pages, through LibreOffice
PAGED = {".pdf", ".png", ".jpg", ".tiff"}

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_PAGES = 500
# Zip-bomb guard for Office files (Docling limits DOCX/XLSX archives itself, but not
# PPTX): total uncompressed size, entry count, and compression ratio.
MAX_UNZIPPED_BYTES = 300 * 1024 * 1024
MAX_ZIP_ENTRIES = 20_000
MAX_ZIP_RATIO = 200
OFFICE_TO_PDF_SECONDS = 180

# A page is weak (worth a vision pass, when that's on) if it yielded little text but
# has pictures, or if pictures cover much of it: a chart, a diagram, a slide that is a
# picture, a scan OCR read badly.
WEAK_PAGE_CHARS = 200
WEAK_PICTURE_SHARE = 0.3
# OCR debris: lowercase words that are stray one- or two-letter fragments, or vowelless
# runs ("snsg"). Measured on real files: clean pages ≤ 0.05 (prose, tables excluded);
# a badly read scan 0.15; garbled slide OCR 0.06–0.12.
WEAK_DEBRIS_SHARE = 0.06
MIN_DEBRIS_WORDS = 30
_VOWEL = re.compile(r"[aeiouy]")
_COMMON_SHORT = frozenset(
    [
        "a",
        "an",
        "as",
        "at",
        "be",
        "by",
        "do",
        "go",
        "he",
        "if",
        "in",
        "is",
        "it",
        "me",
        "my",
        "no",
        "of",
        "on",
        "or",
        "so",
        "to",
        "up",
        "us",
        "we",
        "am",
        "id",
        "ok",
        "vs",
        "i",
    ]
)
VISION_LONG_EDGE = 1568  # px: the size the vision model reads without downscaling


class ParseError(ValueError):
    """The file can't be parsed (unsafe, too large, corrupt, or timed out)."""


@dataclass
class Parsed:
    markdown: str
    title: str | None
    info: dict[str, Any] = field(default_factory=dict)  # stored on the document's meta


def available() -> bool:
    return get_settings().docling != "off" and importlib.util.find_spec("docling") is not None


def legacy_office_available() -> bool:
    """.doc / .ppt / .xls (and slide decks as pages) need LibreOffice on PATH."""
    return available() and shutil.which("soffice") is not None


def check_archive(data: bytes) -> None:
    """Refuse Office files whose zip would expand to something dangerous."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
    except zipfile.BadZipFile as exc:
        raise ParseError("Not a valid Office file") from exc
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ParseError("The file has too many parts to open safely")
    total = sum(i.file_size for i in infos)
    packed = sum(i.compress_size for i in infos) or 1
    if total > MAX_UNZIPPED_BYTES or total / packed > MAX_ZIP_RATIO:
        raise ParseError("The file expands to too much data to open safely")


# ── worker side ───────────────────────────────────────────────────────────
_converter = None


def _init_worker(threads: int, artifacts_path: str | None) -> None:
    os.environ["OMP_NUM_THREADS"] = str(threads)  # before torch is imported
    if artifacts_path:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    global _converter
    _converter = _build_converter(artifacts_path)


def _build_converter(artifacts_path: str | None):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        RapidOcrOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

    options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
        document_timeout=get_settings().docling_timeout_seconds * 0.8,
        ocr_options=RapidOcrOptions(backend="onnxruntime"),
        artifacts_path=artifacts_path,
    )
    options.table_structure_options.mode = TableFormerMode.ACCURATE
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
        }
    )
    converter.initialize_pipeline(InputFormat.PDF)
    return converter


def _office_to_pdf(data: bytes, ext: str) -> bytes:
    """Convert an Office file to PDF with LibreOffice, in a private profile (instances
    sharing one profile block each other)."""
    soffice = shutil.which("soffice")
    if soffice is None:
        raise ParseError("Slide decks need LibreOffice to be parsed as pages")
    with tempfile.TemporaryDirectory(prefix="strata-office-") as tmp:
        src = Path(tmp) / f"input{ext}"
        src.write_bytes(data)
        subprocess.run(
            [
                soffice,
                f"-env:UserInstallation=file://{tmp}/profile",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                tmp,
                str(src),
            ],
            check=True,
            capture_output=True,
            timeout=OFFICE_TO_PDF_SECONDS,
        )
        return (Path(tmp) / "input.pdf").read_bytes()


def _page_signals(doc) -> dict[int, dict[str, float]]:
    """Per page: characters of text Docling found, and the share covered by pictures."""
    from docling_core.types.doc import PictureItem, TableItem

    signals = {n: {"chars": 0, "pictures": 0, "picture_share": 0.0} for n in doc.pages}
    prose: dict[int, list[str]] = {n: [] for n in doc.pages}
    for item, _ in doc.iterate_items():
        for prov in getattr(item, "prov", None) or []:
            page = signals.get(prov.page_no)
            if page is None:
                continue
            if isinstance(item, PictureItem):
                size = doc.pages[prov.page_no].size
                area = abs((prov.bbox.r - prov.bbox.l) * (prov.bbox.t - prov.bbox.b))
                page["pictures"] += 1
                page["picture_share"] += area / max(size.width * size.height, 1.0)
            elif isinstance(item, TableItem):
                page["chars"] += sum(len(c.text) for c in item.data.table_cells)
            else:
                text = getattr(item, "text", "") or ""
                page["chars"] += len(text)
                prose[prov.page_no].append(text)
    for n, page in signals.items():
        page["picture_share"] = min(round(page["picture_share"], 3), 1.0)
        page["debris"] = debris_share(" ".join(prose[n]))
    return signals


def debris_share(text: str) -> float:
    """The share of lowercase words that look like OCR debris (0 when too few words
    to tell). Words with digits ("mtCO2e") are skipped: units and codes aren't debris."""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", text) if w.isalpha() and w.islower()]
    if len(words) < MIN_DEBRIS_WORDS:
        return 0.0
    bad = sum(
        1
        for w in words
        if (len(w) <= 2 and w not in _COMMON_SHORT) or (len(w) >= 3 and not _VOWEL.search(w))
    )
    return round(bad / len(words), 3)


def _is_weak(page: dict[str, float]) -> bool:
    sparse = page["chars"] < WEAK_PAGE_CHARS and page["pictures"] > 0
    pictorial = page["picture_share"] >= WEAK_PICTURE_SHARE
    return sparse or pictorial or page.get("debris", 0.0) >= WEAK_DEBRIS_SHARE


def _render(pdf: bytes, pages: list[int]) -> dict[int, bytes]:
    """PNG renders of the given (1-based) pages, long edge ≈ VISION_LONG_EDGE."""
    import pypdfium2

    out: dict[int, bytes] = {}
    document = pypdfium2.PdfDocument(pdf)
    for n in pages:
        page = document[n - 1]
        width, height = page.get_size()
        image = page.render(scale=VISION_LONG_EDGE / max(width, height, 1.0)).to_pil()
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "PNG", optimize=True)
        out[n] = buf.getvalue()
    return out


def _image_png(data: bytes) -> bytes:
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.thumbnail((VISION_LONG_EDGE, VISION_LONG_EDGE))
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _convert(data: bytes, ext: str, render_weak: bool) -> dict[str, Any]:
    """In a worker: bytes → per-page Markdown and signals (paginated formats) or one
    Markdown (others), plus renders of weak pages when asked."""
    from docling.datamodel.base_models import DocumentStream
    from docling_core.types.doc import DocItemLabel

    source_ext = ext
    if ext in SLIDES and shutil.which("soffice"):  # else Docling's PowerPoint reader
        data, ext = _office_to_pdf(data, ext), ".pdf"
    result = _converter.convert(
        DocumentStream(name=f"file{ext}", stream=io.BytesIO(data)),
        max_num_pages=MAX_PAGES,
        max_file_size=MAX_FILE_BYTES,
    )
    doc = result.document
    title = next(
        (item.text for item, _ in doc.iterate_items() if item.label == DocItemLabel.TITLE),
        None,
    )
    if ext not in PAGED:
        return {"title": title, "markdown": doc.export_to_markdown(), "pages": None}

    signals = _page_signals(doc)
    numbers = sorted(signals)
    pages = [doc.export_to_markdown(page_no=n) for n in numbers]
    weak = [n for n in numbers if _is_weak(signals[n])]
    renders: dict[int, bytes] = {}
    if render_weak and weak:
        renders = _render(data, weak) if ext == ".pdf" else {1: _image_png(data)}
    return {
        "title": title,
        "pages": pages,
        "numbers": numbers,
        "weak": weak,
        "renders": renders,
        "slides": source_ext in SLIDES,
    }


# ── parent side ───────────────────────────────────────────────────────────
class _Pool:
    def __init__(self) -> None:
        self._executor: ProcessPoolExecutor | None = None

    def executor(self) -> ProcessPoolExecutor:
        if self._executor is None:
            s = get_settings()
            self._executor = ProcessPoolExecutor(
                max_workers=s.docling_workers,
                mp_context=multiprocessing.get_context("spawn"),
                initializer=_init_worker,
                initargs=(s.docling_threads, s.docling_artifacts_path),
                max_tasks_per_child=s.docling_tasks_per_worker,
            )
        return self._executor

    def kill(self) -> None:
        """Stop every worker now (a hung conversion can't be interrupted otherwise)."""
        executor, self._executor = self._executor, None
        if executor is None:
            return
        for process in list(getattr(executor, "_processes", {}).values()):
            process.kill()
        executor.shutdown(wait=False, cancel_futures=True)


_pool = _Pool()


async def convert(data: bytes, mime: str) -> Parsed:
    """Parse a file with Docling in the worker pool (and, if VISION_PARSING is on, its
    weak pages with a vision model). Raises ParseError."""
    from app.ingestion import vision

    if len(data) > MAX_FILE_BYTES:
        raise ParseError("The file is larger than 50 MB")
    if mime in OOXML:
        check_archive(data)
    ext = DOCLING_TYPES[mime]
    use_vision = vision.enabled()
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_pool.executor(), _convert, data, ext, use_vision)
    try:
        out = await asyncio.wait_for(future, timeout=get_settings().docling_timeout_seconds)
    except TimeoutError as exc:
        log.warning("Docling timed out on a %s file; restarting its workers", mime)
        _pool.kill()
        raise ParseError("Parsing took too long") from exc
    except BrokenProcessPool as exc:  # a worker died (out of memory, crash)
        _pool.kill()
        raise ParseError("The parser crashed on this file") from exc
    except ParseError:
        raise
    except Exception as exc:  # Docling's or LibreOffice's own errors
        raise ParseError(f"Couldn't parse the file: {exc}") from exc

    info: dict[str, Any] = {"parser": "docling"}
    if out["pages"] is None:
        return Parsed(_tidy(out["markdown"]), out["title"], info)

    pages = dict(zip(out["numbers"], out["pages"], strict=True))
    info |= {"pages": len(pages), "weak_pages": out["weak"]}
    if out["renders"]:
        transcribed, cost = await vision.transcribe_pages(out["renders"])
        for n, markdown in transcribed.items():
            pages[n] = markdown
        info |= {"vision_pages": sorted(transcribed), "vision_cost_usd": round(cost, 5)}
    markdown = "\n\n".join(
        f"{page_marker(n)}\n\n{_tidy(text)}" for n, text in sorted(pages.items()) if text.strip()
    )
    return Parsed(markdown, out["title"], info)


def _tidy(markdown: str) -> str:
    # Docling marks images with a comment; there's nothing to index in them.
    return re.sub(r"^<!-- image -->\s*$", "", markdown, flags=re.MULTILINE).strip()


def shutdown() -> None:
    _pool.kill()
