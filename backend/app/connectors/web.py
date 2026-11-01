"""Same-domain breadth-first crawler that honours robots.txt.

Pages are found from links and from the site's sitemap (robots.txt `Sitemap:` lines,
else /sitemap.xml), so routes a JavaScript app only links to from its scripts are
found too. A page that is an app shell (scripts, almost no text) is rendered in
headless Chromium when the `browser` extra is installed (`app.connectors.render`).

Every request (robots.txt, sitemaps, pages, redirects, and a rendered page's own
requests) goes through `app.core.safe_http`, so a start URL or a redirect can't point
the crawler at internal addresses."""

import asyncio
import contextlib
import ipaddress
import logging
import re
from collections.abc import AsyncIterator
from datetime import datetime
from html import unescape as html_unescape
from typing import Any
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.connectors import render
from app.connectors.base import Connector, ConnectorError, ProgressFn, RawDocument
from app.core.safe_http import UnsafeURL, fetch

log = logging.getLogger("strata.web")

USER_AGENT = "StrataBot/0.1 (+https://github.com/strata-rag)"
MAX_PAGE_BYTES = 5_000_000
_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".zip", ".gz", ".mp4", ".css", ".js", ".ico")
MAX_SITEMAPS = 5  # sitemap files read per crawl (an index counts, and each it lists)
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)


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
        if not _looks_public(parsed.hostname or ""):
            raise ValueError("Start URL must be a public web address")
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
            page = await fetch(client, urljoin(root, "/robots.txt"), max_bytes=500_000)
            ok = page.status == 200
            rp.parse(page.body.decode("utf-8", "replace").splitlines() if ok else [])
        except (httpx.HTTPError, UnsafeURL):
            rp.parse([])
        return rp

    async def _sitemap_urls(
        self, client: httpx.AsyncClient, robots: RobotFileParser, root: str
    ) -> list[str]:
        """Page URLs from the site's sitemaps (an index's children included)."""
        todo = list(robots.site_maps() or []) or [urljoin(root, "/sitemap.xml")]
        pages: list[str] = []
        read = 0
        while todo and read < MAX_SITEMAPS:
            url = todo.pop(0)
            read += 1
            try:
                got = await fetch(client, url, max_bytes=MAX_PAGE_BYTES)
            except (httpx.HTTPError, UnsafeURL):
                continue
            if got.status != 200:
                continue
            locs = [html_unescape(u) for u in _LOC.findall(got.text)]
            if "<sitemapindex" in got.text[:2000].lower():
                todo.extend(locs)
            else:
                pages.extend(locs)
        return pages

    async def iter_documents(
        self, progress: ProgressFn, since: datetime | None = None
    ) -> AsyncIterator[RawDocument]:
        cfg = self.validate_config(self.config)
        start = cfg["url"]
        host = urlparse(start).netloc
        seen: set[str] = {start}
        queue: list[tuple[str, int]] = [(start, 0)]
        fetched = with_text = shells = 0
        sitemap_read = False
        renderer: render.Renderer | None = None
        rendering = render.available()
        async with contextlib.AsyncExitStack() as stack:
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=False
                )
            )
            robots = await self._robots(client, start)
            while queue and fetched < cfg["max_pages"]:
                url, depth = queue.pop(0)
                if not robots.can_fetch(USER_AGENT, url):
                    continue
                try:
                    page = await fetch(client, url, max_bytes=MAX_PAGE_BYTES)
                except UnsafeURL as e:
                    if url == start:
                        raise ConnectorError(f"Can't crawl {start}: {e}") from e
                    continue
                except httpx.HTTPError:
                    self.skip(url)  # timeout or connection error: keep what we had
                    continue
                if page.status in (429,) or page.status >= 500:
                    self.skip(url)
                    continue
                if page.status != 200 or page.content_type not in (
                    "text/html",
                    "application/xhtml+xml",
                ):
                    if fetched == 0 and url == start:
                        raise ConnectorError(f"Could not fetch {start} (HTTP {page.status})")
                    continue
                if url == start:
                    # A start URL that redirects (http → https, apex → www) defines the site.
                    host = urlparse(page.url).netloc
                fetched += 1
                await progress(fetched, cfg["max_pages"], page.url)
                html = page.text
                if render.is_app_shell(html):
                    shells += 1
                    if rendering and renderer is None:
                        try:
                            renderer = await stack.enter_async_context(
                                render.Renderer(client, USER_AGENT)
                            )
                        except Exception as exc:  # no browser installed, or it won't start
                            log.warning("Headless browser unavailable: %s", exc)
                            rendering = False
                    if renderer is not None:
                        html = await renderer.render(page.url) or html
                if len(render.visible_text(html)) >= render.SHELL_TEXT_CHARS:
                    with_text += 1
                soup = BeautifulSoup(html, "lxml")
                if depth < cfg["max_depth"]:
                    for a in soup.find_all("a", href=True):
                        link = urldefrag(urljoin(page.url, a["href"]))[0]
                        if link not in seen and self._allowed(link, host, cfg["include_paths"]):
                            seen.add(link)
                            queue.append((link, depth + 1))
                title_tag = soup.title
                yield RawDocument(
                    external_id=page.url,
                    title=(title_tag.get_text(strip=True) if title_tag else "") or page.url,
                    data=html.encode("utf-8"),
                    mime="text/html",
                    url=page.url,
                )
                if not sitemap_read:  # once the start page has settled the host
                    sitemap_read = True
                    for link in await self._sitemap_urls(client, robots, page.url):
                        link = urldefrag(link)[0]
                        if link not in seen and self._allowed(link, host, cfg["include_paths"]):
                            seen.add(link)
                            queue.append((link, 1))
                delay = robots.crawl_delay(USER_AGENT)
                await asyncio.sleep(min(float(delay), 5.0) if delay else 0.2)
            if queue:
                self.complete = False  # stopped at max_pages: unvisited pages aren't gone
        if fetched and not with_text and shells:
            raise ConnectorError(
                f"{start} builds its pages with JavaScript, so there was no text to "
                "index. Install the server's `browser` extra (headless Chromium) to "
                "render them: `uv sync --extra browser`, then `playwright install chromium`"
                if not rendering
                else f"{start} builds its pages with JavaScript, and rendering them "
                "produced no text either"
            )


def _looks_public(host: str) -> bool:
    """Cheap syntactic check for the config form (DNS is checked again on every fetch)."""
    host = host.strip("[]").lower()
    if host in ("localhost", "") or host.endswith((".localhost", ".local", ".internal")):
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return addr.is_global and not addr.is_multicast
