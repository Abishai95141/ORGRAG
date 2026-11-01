"""GitHub connector: indexes Markdown, text and parseable files from a repository.

It downloads the repository as one archive instead of calling the REST API per file:
a public repository costs no API request at all (GitHub allows 60 unauthenticated API
requests an hour per IP address, shared by everyone behind the same address), and a
private one, with a token, costs one."""

import asyncio
import re
import tarfile
import tempfile
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.connectors.base import Connector, ConnectorError, ProgressFn, RawDocument
from app.ingestion.parsers import supported_extensions

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SHA = re.compile(r"\b[0-9a-f]{40}\b")
_TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".mdx"}
# Logos, badges and screenshots: OCR would index noise.
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".tif", ".tiff", ".webp", ".ico"}
_API = "https://api.github.com"
_WEB = "https://github.com"
MAX_FILES = 1000
MAX_FILE_BYTES = 1_000_000
MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
# Tests swap in an httpx.MockTransport here.
_transport: httpx.AsyncBaseTransport | None = None


@dataclass
class _File:
    path: str
    data: bytes
    modified: datetime


class GitHubConnector(Connector):
    kind = "github"
    display_name = "GitHub repository"
    description = "Index READMEs, docs folders and other Markdown from a repository."
    config_schema = {
        "type": "object",
        "required": ["repo"],
        "properties": {
            "repo": {"type": "string", "title": "Repository", "pattern": "owner/name"},
            "branch": {"type": "string", "title": "Branch (default branch if empty)"},
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "title": "Only include paths starting with",
            },
            "token": {"type": "string", "title": "Access token (private repos)", "secret": True},
        },
    }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        repo = str(config.get("repo") or "").strip().removeprefix("https://github.com/")
        repo = repo.removesuffix(".git").strip("/")
        if not _REPO.match(repo):
            raise ValueError("Repository must look like owner/name")
        paths = [p.strip("/") for p in (config.get("paths") or []) if isinstance(p, str) and p]
        out: dict[str, Any] = {"repo": repo, "paths": paths}
        if config.get("branch"):
            out["branch"] = str(config["branch"]).strip()
        return out

    def _archive_url(self, repo: str, ref: str) -> str:
        if self.secrets.get("token"):  # one API request; redirects to a signed download
            return f"{_API}/repos/{repo}/tarball/{ref}"
        return f"{_WEB}/{repo}/archive/{ref}.tar.gz"  # not the API: no rate limit

    async def _download(self, repo: str, ref: str, into) -> str | None:
        """Stream the archive into `into`; return the commit SHA when the redirect
        names it. Raises ConnectorError with what the user can do about a failure."""
        headers = {"User-Agent": "StrataBot", "Accept": "application/vnd.github+json"}
        if token := self.secrets.get("token"):
            headers["Authorization"] = f"Bearer {token}"
        async with (
            httpx.AsyncClient(
                headers=headers, timeout=120, follow_redirects=True, transport=_transport
            ) as gh,
            gh.stream("GET", self._archive_url(repo, ref)) as resp,
        ):
            if resp.status_code != 200:
                await resp.aread()
                raise ConnectorError(_download_error(repo, ref, resp, bool(token)))
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise ConnectorError(
                        f"{repo} is larger than {MAX_ARCHIVE_BYTES // 2**20} MB to "
                        "download; choose a branch or paths with less in them"
                    )
                into.write(chunk)
            # The redirect to the download names the commit (github.com → codeload).
            for hop in [*resp.history, resp]:
                if match := _SHA.search(str(hop.url)):
                    return match.group()
        return None

    def _read(self, archive, paths: list[str]) -> tuple[list[_File], str | None, bool]:
        """The eligible files in the archive (a .tar.gz with one top-level folder), the
        commit GitHub recorded in it, and whether the listing is complete."""
        supported = supported_extensions()
        files: list[_File] = []
        complete = True
        with tarfile.open(fileobj=archive, mode="r:gz") as tar:
            commit = tar.pax_headers.get("comment") if tar.pax_headers else None
            for member in tar:
                if not member.isfile() or "/" not in member.name:
                    continue
                path = member.name.split("/", 1)[1]
                if _ext(path) in _IMAGE_EXT or _ext(path) not in _TEXT_EXT | set(supported):
                    continue
                if paths and not any(path.startswith(p) for p in paths):
                    continue
                if member.size >= MAX_FILE_BYTES:
                    continue
                if len(files) >= MAX_FILES:
                    complete = False  # capped: don't prune what wasn't read
                    break
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                modified = datetime.fromtimestamp(member.mtime, UTC)
                files.append(_File(path, handle.read(), modified))
        return files, commit or None, complete

    async def iter_documents(
        self, progress: ProgressFn, since: datetime | None = None
    ) -> AsyncIterator[RawDocument]:
        cfg = self.validate_config(self.config)
        repo = cfg["repo"]
        ref = cfg.get("branch") or "HEAD"
        await progress(0, None, f"Downloading {repo}")
        with tempfile.SpooledTemporaryFile(max_size=32 * 2**20) as archive:
            commit = await self._download(repo, ref, archive)
            archive.seek(0)
            try:
                files, recorded, complete = await asyncio.to_thread(
                    self._read, archive, cfg["paths"]
                )
            except (tarfile.TarError, EOFError, OSError) as exc:
                raise ConnectorError(
                    f"GitHub sent an archive of {repo} that can't be read"
                ) from exc
        commit = commit or recorded
        self.complete = complete
        supported = supported_extensions()
        # Links pin the commit indexed, so a citation shows the text that was read.
        at = commit or ref
        for i, f in enumerate(files):
            await progress(i, len(files), f.path)
            yield RawDocument(
                external_id=f.path,
                title=f"{repo}: {f.path}",
                data=f.data,
                mime=supported.get(_ext(f.path), "text/markdown"),
                url=f"{_WEB}/{repo}/blob/{at}/{f.path}",
                metadata={
                    "repo": repo,
                    "path": f.path,
                    "branch": cfg.get("branch"),
                    "commit": commit,
                },  # fmt: skip
                updated_at=f.modified,
            )


def _download_error(repo: str, ref: str, resp: httpx.Response, has_token: bool) -> str:
    status = resp.status_code
    if status == 404:
        if has_token:
            return (
                f"{repo} (ref {ref}) wasn't found with this token: check the name and "
                "branch, and that the token can read the repository (Contents: read)"
            )
        return (
            f"{repo} (ref {ref}) wasn't found. If the repository is private, add an access "
            "token that can read it (a fine-grained token with Contents: read)"
        )
    if status == 401:
        return "GitHub rejected the access token: it may have expired or been revoked"
    if status in (403, 429) and not has_token:
        return f"GitHub refused to send {repo} (HTTP {status}); the next sync will retry"
    if status in (403, 429):
        reset = resp.headers.get("x-ratelimit-reset")
        when = (
            f" until {datetime.fromtimestamp(int(reset), UTC):%H:%M} UTC"
            if reset and reset.isdigit()
            else ""
        )
        return f"GitHub is rate-limiting this token{when}; the next sync will retry"
    return f"GitHub returned HTTP {status} for {repo}; the next sync will retry"


def _ext(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:].lower() if dot != -1 else ""
