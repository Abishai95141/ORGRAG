"""GitHub connector: indexes Markdown/text files from a repository via the REST API."""

import base64
import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from app.connectors.base import Connector, ConnectorError, ProgressFn, RawDocument
from app.ingestion.parsers import SUPPORTED_EXTENSIONS

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".mdx"}
_API = "https://api.github.com"


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

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "StrataBot"}
        if token := self.secrets.get("token"):
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def iter_documents(
        self, progress: ProgressFn, since: datetime | None = None
    ) -> AsyncIterator[RawDocument]:
        cfg = self.validate_config(self.config)
        repo = cfg["repo"]
        async with httpx.AsyncClient(base_url=_API, headers=self._headers(), timeout=30) as gh:
            meta = await gh.get(f"/repos/{repo}")
            if meta.status_code == 404:
                raise ConnectorError(f"Repository {repo} not found (or token lacks access)")
            if meta.status_code >= 400:
                raise ConnectorError(f"GitHub API error {meta.status_code}: {meta.text[:200]}")
            branch = cfg.get("branch") or meta.json()["default_branch"]
            tree = await gh.get(f"/repos/{repo}/git/trees/{branch}", params={"recursive": "1"})
            if tree.status_code >= 400:
                raise ConnectorError(f"Could not read branch {branch}: HTTP {tree.status_code}")
            files = [
                item
                for item in tree.json().get("tree", [])
                if item["type"] == "blob"
                and item.get("size", 0) < 1_000_000
                and _ext(item["path"]) in _TEXT_EXT | set(SUPPORTED_EXTENSIONS)
                and (not cfg["paths"] or any(item["path"].startswith(p) for p in cfg["paths"]))
            ][:1000]
            for i, item in enumerate(files):
                await progress(i, len(files), item["path"])
                blob = await gh.get(f"/repos/{repo}/git/blobs/{item['sha']}")
                if blob.status_code != 200:
                    continue
                data = base64.b64decode(blob.json()["content"])
                ext = _ext(item["path"])
                mime = SUPPORTED_EXTENSIONS.get(ext, "text/markdown")
                yield RawDocument(
                    external_id=item["path"],
                    title=f"{repo}: {item['path']}",
                    data=data,
                    mime=mime,
                    url=f"https://github.com/{repo}/blob/{branch}/{item['path']}",
                    metadata={"repo": repo, "path": item["path"], "branch": branch},
                )


def _ext(path: str) -> str:
    dot = path.rfind(".")
    return path[dot:].lower() if dot != -1 else ""
