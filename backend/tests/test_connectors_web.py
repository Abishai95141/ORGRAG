"""The website crawler: pages from sitemaps, JavaScript app shells rendered (or a clear
error without a browser), and the renderer's requests held to the same rules as the
crawler's own. No real browser: the renderer is faked, its proxy driven directly."""

import httpx
import pytest

from app.connectors import render
from app.connectors.base import ConnectorError
from app.connectors.web import WebConnector
from app.core import safe_http

DNS = {
    "docs.example": ["93.184.216.34"],
    "cdn.example": ["93.184.216.35"],
    "evil.example": ["10.0.0.7"],
}
SHELL = '<title>App</title><div id="root"></div><script type="module" src="/app.js"></script>'
TEXT = "Our support desk answers within four hours on weekdays. " * 10


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    def resolve(host):
        if host not in DNS:
            raise safe_http.socket.gaierror("unknown")
        return DNS[host]

    monkeypatch.setattr(safe_http, "_resolve", resolve)
    monkeypatch.setattr("app.connectors.web.asyncio.sleep", _no_sleep)


def _site(monkeypatch, pages: dict[str, httpx.Response]):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        return pages.get(request.url.path, httpx.Response(404))

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.connectors.web.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return seen


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html"}, text=body)


def _xml(body: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "application/xml"}, text=body)


async def _crawl(config: dict) -> list:
    async def progress(*_):
        pass

    return [d async for d in WebConnector(config, {}).iter_documents(progress)]


async def test_pages_only_the_sitemap_lists_are_crawled(monkeypatch):
    _site(
        monkeypatch,
        {
            "/robots.txt": httpx.Response(
                200, text="User-agent: *\nSitemap: http://docs.example/index.xml"
            ),
            "/": _html(f"<title>Home</title><p>{TEXT}</p>"),
            "/index.xml": _xml(
                "<sitemapindex><sitemap><loc>http://docs.example/pages.xml</loc></sitemap></sitemapindex>"
            ),
            "/pages.xml": _xml(
                "<urlset><url><loc>http://docs.example/pricing</loc></url>"
                "<url><loc>http://elsewhere.example/off-site</loc></url></urlset>"
            ),
            "/pricing": _html(f"<title>Pricing</title><p>{TEXT}</p>"),
        },
    )
    docs = await _crawl({"url": "http://docs.example/"})
    assert [d.url for d in docs] == ["http://docs.example/", "http://docs.example/pricing"]


async def test_app_shells_without_a_browser_fail_with_what_to_install(monkeypatch):
    monkeypatch.setattr(render, "available", lambda: False)
    _site(monkeypatch, {"/robots.txt": httpx.Response(404), "/": _html(SHELL)})
    with pytest.raises(ConnectorError, match="JavaScript.*browser"):
        await _crawl({"url": "http://docs.example/"})


async def test_app_shells_are_rendered_and_their_links_followed(monkeypatch):
    rendered = []

    class FakeRenderer:
        def __init__(self, client, user_agent):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def render(self, url):
            rendered.append(url)
            if url.endswith("/"):
                return f'<title>App</title><main>{TEXT}<a href="/team">Team</a></main>'
            return f"<title>Team</title><main>{TEXT}</main>"

    monkeypatch.setattr(render, "available", lambda: True)
    monkeypatch.setattr(render, "Renderer", FakeRenderer)
    _site(
        monkeypatch,
        {"/robots.txt": httpx.Response(404), "/": _html(SHELL), "/team": _html(SHELL)},
    )
    docs = await _crawl({"url": "http://docs.example/"})
    assert [d.url for d in docs] == ["http://docs.example/", "http://docs.example/team"]
    assert all(TEXT[:40] in d.data.decode() for d in docs)  # the rendered HTML is indexed
    assert rendered == ["http://docs.example/", "http://docs.example/team"]


def test_a_shell_is_scripts_with_almost_no_text():
    assert render.is_app_shell(SHELL)
    assert not render.is_app_shell(f"<p>{TEXT}</p><script src='/x.js'></script>")
    assert not render.is_app_shell("<p>Short page, no scripts.</p>")


class _Route:
    def __init__(self):
        self.done = None

    async def abort(self):
        self.done = ("abort",)

    async def fulfill(self, status, headers, body):
        self.done = ("fulfill", status, headers, body)


class _Request:
    def __init__(self, url, method="GET", kind="script", headers=None, body=None):
        self.url, self.method, self.resource_type = url, method, kind
        self.headers = headers or {}
        self.post_data_buffer = body


async def test_a_rendered_pages_requests_follow_the_crawlers_rules():
    sent = []

    def handler(request: httpx.Request):
        sent.append((request.method, request.headers["host"], request.url.path,
                     request.headers.get("apikey"), request.content))  # fmt: skip
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "access-control-allow-origin": "*",
                "content-encoding": "identity",
            },  # fmt: skip
            content=b'{"ok": true}',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        renderer = render.Renderer(client, "StrataBot")
        count = [0]
        # An API call with its own headers and body goes out, pinned, and comes back
        # with its CORS headers (hop-by-hop ones dropped).
        api = _Route()
        await renderer.proxy(
            api,
            _Request(
                "http://cdn.example/rest/v1/rpc/pages",
                "POST",
                "fetch",
                {"apikey": "anon", "cookie": "x"},
                b'{"q": 1}',
            ),  # fmt: skip
            count,
        )
        assert api.done[0] == "fulfill" and api.done[3] == b'{"ok": true}'
        assert api.done[2].get("access-control-allow-origin") == "*"
        assert "content-encoding" not in api.done[2]
        assert sent == [("POST", "cdn.example", "/rest/v1/rpc/pages", "anon", b'{"q": 1}')]
        # Internal addresses, other schemes and images are refused without a request.
        for request in (
            _Request("http://evil.example/admin"),
            _Request("file:///etc/passwd", kind="document"),
            _Request("http://cdn.example/logo.png", kind="image"),
            _Request("http://cdn.example/x", "PUT", "fetch"),
        ):
            route = _Route()
            await renderer.proxy(route, request, count)
            assert route.done == ("abort",), request.url
        assert len(sent) == 1


async def _no_sleep(_):
    return None
