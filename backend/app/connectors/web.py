"""Same-domain breadth-first crawler that honours robots.txt."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.connectors.base import Connector, ConnectorError, ProgressFn, RawDocument

USER_AGENT = "StrataBot/0.1 (+https://github.com/strata-rag)"
_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip", ".gz", ".mp4", ".css", ".js", ".ico")


class WebConnector(Connector):
    kind = "web"
    display_name = "Website"
    description = "Crawl a documentation site or wiki, staying on the same domain."
    config_schema = {
        "type": "object",
        "required": ["url"],
        "properties": {
            "url": {"type": "string", "format": "uri", "title": "Start URL"},
            "max_pages": {"type": "integer", "minimum": 1, "maximum": 2000, "default": 100},
            "max_depth": {"type": "integer", "minimum": 0, "maximum": 10, "default": 3},
            "include_paths": {
                "type": "array",
                "items": {"type": "string"},
                "title": "Only follow paths starting with",
            },
        },
    }

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        url = str(config.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Start URL must be an http(s) URL")
        include = [p for p in (config.get("include_paths") or []) if isinstance(p, str) and p]
        return {
            "url": url,
            "max_pages": max(1, min(int(config.get("max_pages") or 100), 2000)),
            "max_depth": max(0, min(int(config.get("max_depth") or 3), 10)),
            "include_paths": include,
        }

    def _allowed(self, url: str, host: str, include: list[str]) -> bool:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or p.netloc != host:
            return False
        if p.path.lower().endswith(_SKIP_EXT):
            return False
        return not include or any(p.path.startswith(prefix) for prefix in include)

    async def _robots(self, client: httpx.AsyncClient, root: str) -> RobotFileParser:
        rp = RobotFileParser()
        try:
            resp = await client.get(urljoin(root, "/robots.txt"))
            rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
        except httpx.HTTPError:
            rp.parse([])
        return rp

    async def iter_documents(self, progress: ProgressFn) -> AsyncIterator[RawDocument]:
        cfg = self.validate_config(self.config)
        start = cfg["url"]
        host = urlparse(start).netloc
        seen: set[str] = {start}
        queue: list[tuple[str, int]] = [(start, 0)]
        fetched = 0
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True
        ) as client:
            robots = await self._robots(client, start)
            while queue and fetched < cfg["max_pages"]:
                url, depth = queue.pop(0)
                if not robots.can_fetch(USER_AGENT, url):
                    continue
                try:
                    resp = await client.get(url)
                except httpx.HTTPError:
                    continue
                if resp.status_code != 200 or "text/html" not in resp.headers.get(
                    "content-type", ""
                ):
                    if fetched == 0 and url == start:
                        raise ConnectorError(f"Could not fetch {start} (HTTP {resp.status_code})")
                    continue
                fetched += 1
                await progress(fetched, cfg["max_pages"], url)
                html = resp.text
                if depth < cfg["max_depth"]:
                    soup = BeautifulSoup(html, "lxml")
                    for a in soup.find_all("a", href=True):
                        link = urldefrag(urljoin(str(resp.url), a["href"]))[0]
                        if link not in seen and self._allowed(link, host, cfg["include_paths"]):
                            seen.add(link)
                            queue.append((link, depth + 1))
                title_tag = BeautifulSoup(html[:20000], "lxml").title
                yield RawDocument(
                    external_id=str(resp.url),
                    title=(title_tag.get_text(strip=True) if title_tag else "") or str(resp.url),
                    data=html.encode("utf-8"),
                    mime="text/html",
                    url=str(resp.url),
                )
                delay = robots.crawl_delay(USER_AGENT)
                await asyncio.sleep(min(float(delay), 5.0) if delay else 0.2)
