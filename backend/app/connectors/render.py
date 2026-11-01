"""Render pages that build themselves with JavaScript, for the web crawler.

Many sites (React, Vue, Svelte apps) send an empty shell and fetch their text in the
browser, so a plain fetch indexes nothing. With the `browser` extra installed, those
pages are rendered in headless Chromium (Playwright).

The browser makes no network connection of its own: every request a page issues goes
through `app.core.safe_http` (public addresses only, pinned to the checked address,
size-capped) and the response is handed back to it. A page can't reach an internal
address, another scheme, or anything the crawler itself couldn't fetch."""

import contextlib
import importlib.util
import logging

import httpx
from bs4 import BeautifulSoup

from app.core.safe_http import UnsafeURL, fetch

log = logging.getLogger("strata.render")

SHELL_TEXT_CHARS = 200  # less visible text than this, with scripts, is an app shell
MAX_REQUEST_BYTES = 10_000_000
MAX_REQUESTS = 150  # per page
NAVIGATION_MS = 20_000
SETTLE_MS = 8_000  # wait for the page's own requests to finish
# What a text-only render doesn't need. Stylesheets it does: Vite apps wait for a
# route's CSS to load before rendering it (blocked, the page stays blank).
_SKIP_TYPES = {"image", "media", "font", "texttrack", "manifest"}
_DROP_HEADERS = {"host", "connection", "content-length", "accept-encoding", "cookie"}
_HOP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}


def available() -> bool:
    return importlib.util.find_spec("playwright") is not None


def visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    body = soup.body or soup
    return " ".join(body.get_text(" ").split())


def is_app_shell(html: str) -> bool:
    """Little text but scripts to run: the content arrives once JavaScript runs."""
    soup = BeautifulSoup(html, "lxml")
    has_scripts = bool(soup.find("script", src=True)) or bool(soup.find("script", type="module"))
    return has_scripts and len(visible_text(html)) < SHELL_TEXT_CHARS


class Renderer:
    """One headless browser for a crawl. `async with Renderer(client) as r:` then
    `await r.render(url)` returns the page's HTML once its scripts have run."""

    def __init__(self, client: httpx.AsyncClient, user_agent: str):
        self.client = client
        self.user_agent = user_agent
        self._pw = None
        self._browser = None
        self._context = None

    async def __aenter__(self) -> "Renderer":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        try:
            self._browser = await self._pw.chromium.launch(headless=True)
        except Exception:
            await self._pw.stop()
            raise
        self._context = await self._browser.new_context(
            user_agent=self.user_agent, service_workers="block", java_script_enabled=True
        )
        return self

    async def __aexit__(self, *exc) -> None:
        for closer in (self._context, self._browser):
            if closer is not None:
                with contextlib.suppress(Exception):  # already gone
                    await closer.close()
        if self._pw is not None:
            await self._pw.stop()

    async def proxy(self, route, request, counter: list[int]) -> None:
        """Answer one of the page's requests through safe_http, or refuse it.
        `counter` holds the page's request count so far."""
        counter[0] += 1
        if (
            request.resource_type in _SKIP_TYPES
            or request.method not in ("GET", "POST", "HEAD", "OPTIONS")
            or counter[0] > MAX_REQUESTS
        ):
            await route.abort()
            return
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _DROP_HEADERS}
        try:
            got = await fetch(
                self.client,
                request.url,
                max_bytes=MAX_REQUEST_BYTES,
                method=request.method,
                headers=headers,
                content=request.post_data_buffer,
            )
        except (UnsafeURL, httpx.HTTPError) as exc:
            log.debug("Render blocked %s: %s", request.url, exc)
            await route.abort()
            return
        await route.fulfill(
            status=got.status,
            headers={k: v for k, v in got.headers.items() if k.lower() not in _HOP_HEADERS},
            body=got.body,
        )

    async def render(self, url: str) -> str | None:
        """The rendered HTML, or None when the page couldn't be rendered."""
        page = await self._context.new_page()
        counter = [0]
        try:
            await page.route("**/*", lambda route, request: self.proxy(route, request, counter))
            await page.goto(url, wait_until="domcontentloaded", timeout=NAVIGATION_MS)
            # A page that keeps polling never goes idle: take what has rendered.
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
            return await page.content()
        except Exception as exc:
            log.warning("Couldn't render %s: %s", url, exc)
            return None
        finally:
            await page.close()
