"""Web search for the agent's `search_web` tool, through an official search API
(Tavily or Brave — whichever key is configured), plus a guarded page fetcher for
reading a result in full.

The fetcher only follows public http(s) addresses: every hop's host is resolved and
private, loopback, link-local and reserved ranges are refused, so a model-chosen
URL can't reach internal services."""

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import get_settings
from app.ingestion.parsers import parse

USER_AGENT = "StrataBot/0.1 (+https://github.com/Abishai95141/ORGRAG)"
MAX_PAGE_BYTES = 3_000_000
MAX_REDIRECTS = 4


class WebSearchError(RuntimeError):
    pass


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str


async def search_web(query: str, max_results: int = 5) -> list[WebResult]:
    s = get_settings()
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as client:
        if s.tavily_api_key:
            r = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {s.tavily_api_key}"},
                json={"query": query, "max_results": max_results, "search_depth": "basic"},
            )
            if r.status_code != 200:
                raise WebSearchError(f"Tavily returned HTTP {r.status_code}")
            return [
                WebResult(i.get("title") or i["url"], i["url"], i.get("content") or "")
                for i in r.json().get("results", [])
                if i.get("url")
            ][:max_results]
        if s.brave_api_key:
            r = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={"X-Subscription-Token": s.brave_api_key, "Accept": "application/json"},
                params={"q": query, "count": max_results},
            )
            if r.status_code != 200:
                raise WebSearchError(f"Brave Search returned HTTP {r.status_code}")
            return [
                WebResult(i.get("title") or i["url"], i["url"], i.get("description") or "")
                for i in (r.json().get("web") or {}).get("results", [])
                if i.get("url")
            ][:max_results]
    raise WebSearchError("Web search isn't configured: set TAVILY_API_KEY or BRAVE_API_KEY")


def _is_public_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            return False
    return True


async def fetch_page(url: str) -> tuple[str | None, str]:
    """(detected title, Markdown) for a public web page."""
    async with httpx.AsyncClient(
        timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise WebSearchError("Only public http(s) pages can be read")
            if not await asyncio.to_thread(_is_public_host, parsed.hostname):
                raise WebSearchError("That address isn't a public web page")
            async with client.stream("GET", url) as r:
                if r.is_redirect and r.headers.get("location"):
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    raise WebSearchError(f"The page returned HTTP {r.status_code}")
                kind = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind not in ("text/html", "application/xhtml+xml", "text/plain"):
                    raise WebSearchError(f"Can't read {kind or 'this'} content")
                data = bytearray()
                async for chunk in r.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_PAGE_BYTES:
                        break
            mime = "text/plain" if kind == "text/plain" else "text/html"
            markdown, title = await asyncio.to_thread(parse, bytes(data), mime)
            return title, markdown
    raise WebSearchError("Too many redirects")
