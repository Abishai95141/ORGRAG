"""The outbound URL guard (audit S1/S2): every hop public, pinned to the checked IP."""

import httpx
import pytest

from app.connectors.base import ConnectorError
from app.connectors.web import WebConnector
from app.core import safe_http
from app.core.safe_http import UnsafeURL, fetch

DNS = {
    "docs.example": ["93.184.216.34"],
    "evil.example": ["10.0.0.7"],
    "mixed.example": ["93.184.216.34", "127.0.0.1"],
    "meta.example": ["169.254.169.254"],
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    def resolve(host):
        if host not in DNS:
            raise safe_http.socket.gaierror("unknown")
        return DNS[host]

    monkeypatch.setattr(safe_http, "_resolve", resolve)


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def test_request_is_pinned_to_the_checked_address():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["host"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>hi</p>")

    async with client_for(handler) as c:
        page = await fetch(c, "https://docs.example/guide?x=1", max_bytes=1000)
    assert seen == {
        "url": "https://93.184.216.34/guide?x=1",
        "host": "docs.example",
        "sni": "docs.example",
    }
    assert page.url == "https://docs.example/guide?x=1" and page.text == "<p>hi</p>"


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.example/",
        "http://mixed.example/",  # one internal answer is enough to refuse
        "http://meta.example/latest/meta-data",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:10.0.0.1]/",
        "http://100.64.0.1/",  # CGNAT
        "http://nowhere.example/",
        "file:///etc/passwd",
        "http://user:pw@docs.example/",
    ],
)
async def test_non_public_targets_are_refused(url):
    async with client_for(lambda r: httpx.Response(200)) as c:
        with pytest.raises(UnsafeURL):
            await fetch(c, url, max_bytes=1000)


async def test_redirects_are_rechecked_on_every_hop():
    def handler(request: httpx.Request):
        if request.headers["host"] == "docs.example":
            return httpx.Response(302, headers={"location": "http://evil.example/admin"})
        raise AssertionError("an internal address was contacted")

    async with client_for(handler) as c:
        with pytest.raises(UnsafeURL):
            await fetch(c, "http://docs.example/", max_bytes=1000)


async def test_redirect_loops_and_oversized_bodies_are_bounded():
    loop = client_for(lambda r: httpx.Response(301, headers={"location": "/again"}))
    async with loop as c:
        with pytest.raises(UnsafeURL, match="redirects"):
            await fetch(c, "http://docs.example/", max_bytes=1000, max_redirects=3)
    big = client_for(lambda r: httpx.Response(200, content=b"x" * 5000))
    async with big as c:
        page = await fetch(c, "http://docs.example/", max_bytes=1000)
    assert len(page.body) == 1000 and page.truncated


async def test_declared_charset_is_used_for_text():
    body = "café".encode("latin-1")
    handler = lambda r: httpx.Response(  # noqa: E731
        200, headers={"content-type": "text/html; charset=iso-8859-1"}, content=body
    )
    async with client_for(handler) as c:
        page = await fetch(c, "http://docs.example/", max_bytes=1000)
    assert page.text == "café"


@pytest.mark.parametrize(
    "url", ["http://localhost:8000/", "http://10.1.2.3/", "http://svc.internal/", "http://box/"]
)
def test_crawler_config_rejects_obvious_internal_hosts(url):
    with pytest.raises(ValueError, match="public"):
        WebConnector.validate_config({"url": url})


async def test_crawler_refuses_a_start_url_that_resolves_internally():
    connector = WebConnector({"url": "http://evil.example/"}, {})

    async def progress(*_):
        pass

    with pytest.raises(ConnectorError, match="public"):
        async for _ in connector.iter_documents(progress):
            pass


async def test_crawl_follows_the_site_and_skips_redirects_into_the_network(monkeypatch):
    contacted = []

    def handler(request: httpx.Request):
        contacted.append(f"{request.headers['host']}{request.url.path}")
        path = request.url.path
        if request.headers["host"] != "docs.example":
            raise AssertionError("left the public site")
        if path == "/robots.txt":
            return httpx.Response(404)
        if path == "/":
            return httpx.Response(301, headers={"location": "/home"})
        if path == "/home":
            html = '<title>Home</title><a href="/a">a</a><a href="/sneaky">s</a>'
            return httpx.Response(200, headers={"content-type": "text/html"}, text=html)
        if path == "/sneaky":
            return httpx.Response(302, headers={"location": "http://meta.example/latest"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>A</p>")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.connectors.web.httpx.AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    monkeypatch.setattr("app.connectors.web.asyncio.sleep", _no_sleep)

    async def progress(*_):
        pass

    connector = WebConnector({"url": "http://docs.example/"}, {})
    docs = [d async for d in connector.iter_documents(progress)]
    assert [d.url for d in docs] == ["http://docs.example/home", "http://docs.example/a"]
    assert docs[0].title == "Home"
    assert not any(c.startswith("meta.example") for c in contacted)


async def _no_sleep(_):
    return None
