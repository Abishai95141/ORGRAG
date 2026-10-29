"""Upload connector: files are written to FILE_STORAGE_DIR/<source_id>/ by the API
and re-read here on every sync."""

from collections.abc import AsyncIterator
from pathlib import Path

from app.connectors.base import Connector, ProgressFn, RawDocument
from app.core.config import get_settings
from app.ingestion.parsers import UnsupportedFileType, mime_for


def storage_dir(source_id: str) -> Path:
    path = Path(get_settings().file_storage_dir) / source_id
    path.mkdir(parents=True, exist_ok=True)
    return path


class UploadConnector(Connector):
    kind = "upload"
    display_name = "File upload"
    description = "Drop in PDFs, Word documents, Markdown, HTML, CSV and text files."
    config_schema = {"type": "object", "properties": {}, "additionalProperties": False}

    async def iter_documents(self, progress: ProgressFn) -> AsyncIterator[RawDocument]:
        folder = storage_dir(self.config["_source_id"])
        files = sorted(p for p in folder.iterdir() if p.is_file())
        for i, path in enumerate(files):
            await progress(i, len(files), path.name)
            try:
                mime = mime_for(path.name)
            except UnsupportedFileType:
                continue
            yield RawDocument(
                external_id=path.name,
                title=path.stem.replace("_", " ").replace("-", " ").strip() or path.name,
                data=path.read_bytes(),
                mime=mime,
                metadata={"filename": path.name},
            )
