"""Web search for the agent's `search_web` tool, through an official search API
(Tavily or Brave — whichever key is configured), plus a guarded page fetcher for
reading a result in full.

The fetcher only follows public http(s) addresses (see `app.core.safe_http`), so a
model-chosen URL can't reach internal services."""

import asyncio
from dataclasses import dataclass

import httpx

from app.core.config import get_settings
from app.core.safe_http import UnsafeURL, check_url, fetch, public_address
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


async def _tavily_extract(url: str, key: str) -> str | None:
    """Page text through Tavily's extract API: sites that block plain fetchers (403s are
    common) still come back, and Tavily does the fetching. None when it can't."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.tavily.com/extract",
                headers={"Authorization": f"Bearer {key}"},
                json={"urls": [url]},
            )
        if r.status_code != 200:
            return None
        results = r.json().get("results") or []
        content = (results[0].get("raw_content") or "").strip() if results else ""
        return content[:MAX_PAGE_BYTES] or None
    except (httpx.HTTPError, ValueError):
        return None


async def fetch_page(url: str) -> tuple[str | None, str]:
    """(detected title, Markdown) for a public web page. Uses Tavily's extractor when a
    Tavily key is set (after the same public-address check), else fetches directly."""
    try:
        _, host, _ = check_url(url)
        await public_address(host)
    except UnsafeURL as e:
        raise WebSearchError(str(e)) from e
    key = get_settings().tavily_api_key
    if key and (content := await _tavily_extract(url, key)):
        return None, content
    return await _fetch_direct(url)


async def _fetch_direct(url: str) -> tuple[str | None, str]:
    try:
        async with httpx.AsyncClient(
            timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        ) as client:
            page = await fetch(client, url, max_bytes=MAX_PAGE_BYTES, max_redirects=MAX_REDIRECTS)
    except UnsafeURL as e:
        raise WebSearchError(str(e)) from e
    except httpx.HTTPError as e:
        raise WebSearchError(f"Couldn't fetch the page ({type(e).__name__})") from e
    if page.status != 200:
        raise WebSearchError(f"The page returned HTTP {page.status}")
    if page.content_type not in ("text/html", "application/xhtml+xml", "text/plain"):
        raise WebSearchError(f"Can't read {page.content_type or 'this'} content")
    mime = "text/plain" if page.content_type == "text/plain" else "text/html"
    markdown, title = await asyncio.to_thread(parse, page.text.encode("utf-8"), mime)
    return title, markdown
