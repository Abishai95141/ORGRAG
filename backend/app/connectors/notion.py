"""Notion connector: indexes pages (and database rows, which are pages) shared with an
internal integration, rendering their blocks to Markdown through the REST API."""

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from app.connectors.base import Connector, ConnectorError, ProgressFn, RawDocument

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"
_MAX_DEPTH = 5
_MAX_TRIES = 5
_HEX_ID = re.compile(r"([0-9a-f]{32})(?:[?#].*)?$")
# Where to look up each kind of parent when checking whether a page sits under a root.
_PARENT_PATHS = {"page_id": "pages", "database_id": "databases", "block_id": "blocks"}
_LIST_TYPES = {"bulleted_list_item", "numbered_list_item", "to_do", "toggle"}
_CONTAINERS = {"column_list", "column", "synced_block", "template"}
# Tests swap in an httpx.MockTransport here.
_transport: httpx.AsyncBaseTransport | None = None


class NotionConnector(Connector):
    kind = "notion"
    display_name = "Notion"
    description = "Index pages and database entries shared with a Notion integration."
    supports_incremental = True
    config_schema = {
        "type": "object",
        "properties": {
            "token": {"type": "string", "title": "Integration secret", "secret": True},
            "root_page_ids": {
                "type": "array",
                "items": {"type": "string"},
                "title": "Root page IDs or links (everything shared with the integration if empty)",
            },
        },
    }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        raw = config.get("root_page_ids") or []
        if isinstance(raw, str):
            raw = re.split(r"[\s,]+", raw)
        if not isinstance(raw, list):
            raise ValueError("Root pages must be a list of page IDs or links")
        roots: list[str] = []
        for item in raw:
            if not str(item).strip():
                continue
            page_id = _normalize_id(str(item))
            if page_id is None:
                raise ValueError(f"Not a Notion page ID or link: {item}")
            if page_id not in roots:
                roots.append(page_id)
        return {"root_page_ids": roots}

    def _client(self) -> httpx.AsyncClient:
        token = str(self.secrets.get("token") or "").strip()
        if not token:
            raise ConnectorError("Add the Notion integration secret to this source")
        return httpx.AsyncClient(
            base_url=_API,
            headers={"Authorization": f"Bearer {token}", "Notion-Version": _VERSION},
            timeout=30,
            transport=_transport,
        )

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        missing_ok: bool = False,
    ) -> dict | None:
        """One API call with Retry-After handling. With `missing_ok`, 400/404 return None
        (the object is gone or isn't shared with the integration)."""
        for attempt in range(_MAX_TRIES):
            resp = await client.request(method, path, json=json, params=params)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry = resp.headers.get("Retry-After", "")
                await asyncio.sleep(
                    min(float(retry), 60.0) if retry.isdigit() else min(2**attempt, 30)
                )
                continue
            if resp.status_code < 400:
                return resp.json()
            if missing_ok and resp.status_code in (400, 404):
                return None
            raise ConnectorError(_error_message(resp))
        raise ConnectorError("Notion kept rate-limiting requests; try again later")

    # ---- discovery --------------------------------------------------------------

    async def _search(
        self, client: httpx.AsyncClient, cutoff: datetime | None, pages_only: bool
    ) -> AsyncIterator[dict]:
        """Everything shared with the integration, most recently edited first. With a
        cutoff, stop at the first result edited before it."""
        body: dict[str, Any] = {
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            "page_size": 100,
        }
        if pages_only:
            body["filter"] = {"property": "object", "value": "page"}
        while True:
            data = await self._request(client, "POST", "/search", json=body) or {}
            for obj in data.get("results", []):
                edited = _parse_time(obj.get("last_edited_time"))
                if cutoff and edited and edited < cutoff:
                    return
                yield obj
            if not data.get("has_more") or not data.get("next_cursor"):
                return
            body["start_cursor"] = data["next_cursor"]

    async def _pages(self, client: httpx.AsyncClient, since: datetime | None) -> list[dict]:
        """Pages to index. With root pages configured, search everything and keep the
        pages whose parent chain reaches a root (sharing a page shares its subtree)."""
        cutoff = _cutoff(since)
        roots = set(self.config.get("root_page_ids") or [])
        if not roots:
            return [p async for p in self._search(client, cutoff, pages_only=True) if _live(p)]
        known: dict[str, dict] = {}
        for root in roots:
            obj = await self._request(client, "GET", f"/pages/{root}", missing_ok=True)
            if obj is None:
                obj = await self._request(client, "GET", f"/databases/{root}", missing_ok=True)
            if obj is None:
                raise ConnectorError(
                    f"Notion page {root} not found. Share it with the integration "
                    "(⋯ menu → Connections) and check the ID."
                )
            known[root] = obj
        candidates: list[dict] = []
        async for obj in self._search(client, cutoff, pages_only=False):
            known[obj["id"]] = obj
            if obj.get("object") == "page" and _live(obj):
                candidates.append(obj)
        verdicts: dict[str, bool] = {}
        return [p for p in candidates if await self._under(client, p, roots, known, verdicts)]

    async def _under(
        self,
        client: httpx.AsyncClient,
        obj: dict,
        roots: set[str],
        known: dict[str, dict],
        verdicts: dict[str, bool],
    ) -> bool:
        chain: list[str] = []
        result = False
        cur: dict | None = obj
        while cur is not None and len(chain) < 64:
            oid = cur["id"]
            if oid in roots:
                result = True
                break
            if oid in verdicts:
                result = verdicts[oid]
                break
            chain.append(oid)
            parent = cur.get("parent") or {}
            kind = parent.get("type")
            pid = parent.get(kind) if kind in _PARENT_PATHS else None
            if not pid:
                break
            if pid not in known:
                found = await self._request(
                    client, "GET", f"/{_PARENT_PATHS[kind]}/{pid}", missing_ok=True
                )
                if found is None:
                    break
                known[pid] = found
            cur = known[pid]
        for oid in chain:
            verdicts[oid] = result
        return result

    # ---- sync -------------------------------------------------------------------

    async def iter_documents(
        self, progress: ProgressFn, since: datetime | None = None
    ) -> AsyncIterator[RawDocument]:
        self.config = self.validate_config(self.config)
        async with self._client() as client:
            await progress(0, None, "Searching Notion")
            pages = await self._pages(client, since)
            for i, page in enumerate(pages):
                title = _title(page)
                await progress(i, len(pages), title)
                parts = [f"# {title}"]
                parent = page.get("parent") or {}
                if parent.get("type") == "database_id" and (props := _properties(page)):
                    parts.append(props)
                blocks = await self._children(client, page["id"])
                if body := await self._render_blocks(client, blocks, 1):
                    parts.append(body)
                metadata: dict[str, Any] = {"parent_type": parent.get("type")}
                if parent.get("type") == "database_id":
                    metadata["database_id"] = parent["database_id"]
                if page.get("created_time"):
                    metadata["created_time"] = page["created_time"]
                yield RawDocument(
                    external_id=page["id"],
                    title=title,
                    data=("\n\n".join(parts) + "\n").encode("utf-8"),
                    mime="text/markdown",
                    url=page.get("url") or _page_url(page["id"]),
                    metadata=metadata,
                    updated_at=_parse_time(page.get("last_edited_time")),
                )
            await progress(len(pages), len(pages), None)

    async def list_ids(self) -> set[str] | None:
        self.config = self.validate_config(self.config)
        async with self._client() as client:
            return {p["id"] for p in await self._pages(client, None)}

    # ---- rendering --------------------------------------------------------------

    async def _children(self, client: httpx.AsyncClient, block_id: str) -> list[dict]:
        blocks: list[dict] = []
        params: dict[str, Any] = {"page_size": 100}
        while True:
            data = await self._request(
                client, "GET", f"/blocks/{block_id}/children", params=params, missing_ok=True
            )
            if data is None:
                return blocks
            blocks.extend(data.get("results", []))
            if not data.get("has_more") or not data.get("next_cursor"):
                return blocks
            params["start_cursor"] = data["next_cursor"]

    async def _render_blocks(
        self, client: httpx.AsyncClient, blocks: list[dict], depth: int
    ) -> str:
        out = ""
        prev_list = False
        for block in blocks:
            text = await self._render_block(client, block, depth)
            if not text:
                continue
            is_list = block.get("type") in _LIST_TYPES
            if out:
                out += "\n" if prev_list and is_list else "\n\n"
            out += text
            prev_list = is_list
        return out

    async def _render_block(self, client: httpx.AsyncClient, block: dict, depth: int) -> str:
        kind = block.get("type", "")
        data = block.get(kind) or {}
        text = _rich(data.get("rich_text") or [])
        if kind == "table":
            rows = await self._children(client, block["id"]) if block.get("has_children") else []
            return _table(rows)
        kids = ""
        nested = kind not in ("child_page", "child_database") and depth < _MAX_DEPTH
        if block.get("has_children") and nested:
            kids = await self._render_blocks(
                client, await self._children(client, block["id"]), depth + 1
            )
        if kind.startswith("heading_"):
            level = int(kind[-1]) + 1  # the page title is the only "#"
            return "\n\n".join(filter(None, [f"{'#' * level} {text}" if text else "", kids]))
        if kind in ("bulleted_list_item", "toggle"):
            return f"- {text}" + _indent(kids, 2)
        if kind == "numbered_list_item":
            return f"1. {text}" + _indent(kids, 3)
        if kind == "to_do":
            return f"- [{'x' if data.get('checked') else ' '}] {text}" + _indent(kids, 2)
        if kind in ("quote", "callout"):
            icon = ((data.get("icon") or {}).get("emoji") or "") if kind == "callout" else ""
            inner = "\n\n".join(filter(None, [f"{icon} {text}".strip(), kids]))
            return "\n".join(f"> {line}" if line else ">" for line in inner.splitlines())
        if kind == "code":
            lang = data.get("language") or ""
            code = _rich(data.get("rich_text") or [], plain=True)
            return f"```{'' if lang == 'plain text' else lang}\n{code}\n```"
        if kind == "equation":
            return f"$${data.get('expression', '')}$$"
        if kind == "divider":
            return "---"
        if kind in ("child_page", "child_database"):
            return f"[{data.get('title') or 'Untitled'}]({_page_url(block['id'])})"
        if kind in ("bookmark", "embed", "link_preview"):
            url = data.get("url") or ""
            caption = _rich(data.get("caption") or [])
            return f"[{caption or url}]({url})" if url else caption
        if kind in ("image", "video", "file", "pdf", "audio"):
            # Notion-hosted file URLs are short-lived signed links; keep only external ones.
            caption = _rich(data.get("caption") or []) or data.get("name") or ""
            if data.get("type") == "external" and (url := (data.get("external") or {}).get("url")):
                return f"[{caption or url}]({url})"
            return f"({kind}: {caption})" if caption else ""
        if kind in _CONTAINERS:
            return kids
        return "\n\n".join(filter(None, [text, kids]))


def _normalize_id(value: str) -> str | None:
    """A page id in any form Notion shows it (dashed, undashed, inside a page link)."""
    compact = value.strip().lower().replace("-", "")
    m = _HEX_ID.search(compact)
    if m is None:
        return None
    h = m.group(1)
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _page_url(page_id: str) -> str:
    return f"https://www.notion.so/{page_id.replace('-', '')}"


def _cutoff(since: datetime | None) -> datetime | None:
    """Notion rounds last_edited_time down to the minute, so compare at that grain;
    pages edited in the same minute as `since` are re-fetched rather than missed."""
    if since is None:
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return since.astimezone(UTC).replace(second=0, microsecond=0)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _live(page: dict) -> bool:
    return not page.get("archived") and not page.get("in_trash")


def _rich(items: list[dict], plain: bool = False) -> str:
    out: list[str] = []
    for item in items:
        text = item.get("plain_text") or ""
        core = text.strip()
        if plain or not core:
            out.append(text)
            continue
        ann = item.get("annotations") or {}
        if ann.get("code"):
            core = f"`{core}`"
        if ann.get("bold"):
            core = f"**{core}**"
        if ann.get("italic"):
            core = f"*{core}*"
        if ann.get("strikethrough"):
            core = f"~~{core}~~"
        if href := item.get("href"):
            core = f"[{core}]({'https://www.notion.so' + href if href.startswith('/') else href})"
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        out.append(f"{lead}{core}{trail}")
    return "".join(out)


def _indent(text: str, width: int) -> str:
    if not text:
        return ""
    pad = " " * width
    return "\n" + "\n".join(pad + line if line else "" for line in text.splitlines())


def _table(rows: list[dict]) -> str:
    cells = [
        [
            _rich(cell).replace("|", "\\|").replace("\n", " ")
            for cell in (row.get("table_row") or {}).get("cells", [])
        ]
        for row in rows
        if row.get("type") == "table_row"
    ]
    if not cells:
        return ""
    width = max(len(r) for r in cells)
    cells = [r + [""] * (width - len(r)) for r in cells]
    lines = ["| " + " | ".join(cells[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in cells[1:]]
    return "\n".join(lines)


def _error_message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        body = {}
    code = body.get("code") if isinstance(body, dict) else None
    message = (body.get("message") if isinstance(body, dict) else None) or resp.text[:200]
    if resp.status_code == 401:
        return "Notion rejected the integration secret (unauthorized)"
    if resp.status_code == 403:
        return f"The Notion integration lacks a capability it needs: {message}"
    if resp.status_code == 404:
        return f"{message}. Share the page with the integration (⋯ menu → Connections)."
    return f"Notion API error (HTTP {resp.status_code}, {code or 'unknown'}): {message}"


def _title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        title = _rich(prop.get("title") or [], plain=True).strip()
        if prop.get("type") == "title" and title:
            return title
    return "Untitled"


def _properties(page: dict) -> str:
    """Database row properties as a "key: value" list (title excluded, empties skipped)."""
    lines = []
    for name, prop in (page.get("properties") or {}).items():
        if prop.get("type") == "title":
            continue
        if value := _prop_value(prop):
            lines.append(f"- **{name}**: {value}")
    return "\n".join(lines)


def _prop_value(prop: dict) -> str:
    kind = prop.get("type")
    value = prop.get(kind) if kind else None
    if value is None or value == [] or value == "":
        return ""
    if kind in ("title", "rich_text"):
        return _rich(value, plain=True).strip()
    if kind in ("select", "status"):
        return value.get("name") or ""
    if kind == "multi_select":
        return ", ".join(o.get("name", "") for o in value)
    if kind == "date":
        return value.get("start", "") + (f" → {value['end']}" if value.get("end") else "")
    if kind == "people":
        return ", ".join(p.get("name") or p.get("id", "") for p in value)
    if kind in ("created_by", "last_edited_by"):
        return value.get("name") or ""
    if kind == "checkbox":
        return "Yes" if value else "No"
    if kind == "files":
        return ", ".join(f.get("name", "") for f in value)
    if kind == "unique_id":
        prefix = value.get("prefix")
        return f"{prefix}-{value.get('number')}" if prefix else str(value.get("number", ""))
    if kind in ("formula", "rollup"):
        inner_kind = value.get("type")
        inner = value.get(inner_kind)
        if inner_kind == "array":
            return ", ".join(filter(None, (_prop_value(v) for v in inner or [])))
        if inner_kind == "date":
            return _prop_value({"type": "date", "date": inner})
        return "" if inner is None else str(inner)
    if kind == "relation":
        return ""  # bare page ids are noise without the linked titles
    return str(value) if isinstance(value, str | int | float) else ""
