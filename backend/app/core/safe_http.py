"""Outbound fetches of user- or model-chosen URLs (website crawler, agent page reads).

Every hop is checked before it is requested:
- only http(s) on the default ports (or an explicit port on a public host);
- the host is resolved once and *every* address must be globally routable — private,
  loopback, link-local, CGNAT, multicast and reserved ranges are refused;
- the request connects to the address that was checked (Host header and TLS SNI keep
  the real name, so certificates are still verified), so a second DNS answer can't
  swap in an internal address between the check and the connect (DNS rebinding);
- redirects are followed by hand, each one re-checked, up to `max_redirects`;
- bodies are read as a stream and cut at `max_bytes`.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx


class UnsafeURL(ValueError):
    """The URL isn't a public http(s) address (or a redirect led somewhere that isn't)."""


@dataclass
class Fetched:
    url: str  # final URL after redirects
    status: int
    content_type: str  # lower-cased media type without parameters
    body: bytes
    truncated: bool
    charset: str | None = None
    headers: dict[str, str] = field(default_factory=dict)  # the final response's

    @property
    def text(self) -> str:
        """Body decoded with the declared charset (UTF-8 when absent or unknown)."""
        try:
            return self.body.decode(self.charset or "utf-8", errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")


def _resolve(host: str) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)]


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip.split("%")[0])
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


async def public_address(host: str) -> str:
    """The address to connect to for `host`, or UnsafeURL when any of its addresses
    is not public (a name that resolves to both is treated as internal)."""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        ips = [str(literal)]
    except ValueError:
        try:
            ips = await asyncio.to_thread(_resolve, host)
        except (socket.gaierror, UnicodeError) as e:
            raise UnsafeURL(f"Can't resolve {host}") from e
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        raise UnsafeURL("That address isn't a public web page")
    # IPv4 first: containers often have no IPv6 route even when DNS returns AAAA.
    return sorted(ips, key=lambda ip: ":" in ip)[0]


def check_url(url: str) -> tuple[str, str, int | None]:
    """(scheme, hostname, explicit port) for an http(s) URL, else UnsafeURL."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeURL("Only public http(s) pages can be read")
    if parts.username or parts.password:
        raise UnsafeURL("URLs with credentials aren't allowed")
    try:
        port = parts.port
    except ValueError as e:
        raise UnsafeURL("Invalid port") from e
    return parts.scheme, parts.hostname, port


async def _pinned_request(
    client: httpx.AsyncClient,
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
) -> httpx.Request:
    scheme, host, port = check_url(url)
    ip = await public_address(host)
    netloc = f"[{ip}]" if ":" in ip else ip
    if port:
        netloc += f":{port}"
    parts = urlsplit(url)
    target = urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    host_header = host if not port else f"{host}:{port}"
    return client.build_request(
        method,
        target,
        headers={**(headers or {}), "Host": host_header},
        content=content,
        extensions={"sni_hostname": host},
    )


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    max_redirects: int = 4,
    follow: Callable[[str], bool] | None = None,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    content: bytes | None = None,
) -> Fetched:
    """`url` with the checks above (GET unless `method` says otherwise; `headers` and
    `content` are sent as given). `follow(next_url)` can refuse a redirect (the 3xx
    response is then returned as is); only GETs follow redirects. The client must not
    follow redirects itself."""
    for _ in range(max_redirects + 1):
        request = await _pinned_request(client, url, method, headers, content)
        response = await client.send(request, stream=True)
        try:
            location = response.headers.get("location")
            if method == "GET" and response.is_redirect and location:
                nxt = urljoin(url, location)
                if follow is None or follow(nxt):
                    url = nxt
                    continue
            kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
            charset = response.charset_encoding
            body = bytearray()
            truncated = False
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    del body[max_bytes:]
                    truncated = True
                    break
            return Fetched(
                url, response.status_code, kind, bytes(body), truncated, charset,
                dict(response.headers),
            )  # fmt: skip
        finally:
            await response.aclose()
    raise UnsafeURL("Too many redirects")
