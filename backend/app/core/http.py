"""ASGI middleware for request hygiene: body size caps and trailing slashes."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.errors import ApiError


class BodyLimit:
    """Rejects request bodies over the cap with 413 `request_too_large`.

    Checked against Content-Length up front and against the bytes actually read
    (chunked bodies). The error is raised from `receive`, i.e. while the route parses
    its body, so it goes through the normal error handlers and envelope."""

    def __init__(self, app: ASGIApp):
        self.app = app
        s = get_settings()
        self.multipart_cap = s.max_multipart_mb * 1024 * 1024
        self.json_cap = s.max_json_kb * 1024

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        multipart = headers.get(b"content-type", b"").startswith(b"multipart/")
        cap = self.multipart_cap if multipart else self.json_cap
        declared = headers.get(b"content-length", b"")
        too_large = declared.isdigit() and int(declared) > cap
        received = 0

        def error() -> ApiError:
            mb = cap / 1_048_576
            limit = f"{mb:g} MB" if mb >= 1 else f"{cap // 1024} KB"
            return ApiError(413, f"The request body is larger than {limit}", "request_too_large")

        async def limited() -> Message:
            nonlocal received
            if too_large:
                raise error()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > cap:
                    raise error()
            return message

        await self.app(scope, limited, send)


class StripTrailingSlash:
    """`/api/orgs/acme/` routes like `/api/orgs/acme` instead of redirecting (a 307 to
    the backend's own host breaks behind the web proxy and drops some clients' auth)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and len(path) > 1 and path.endswith("/"):
            scope = {**scope, "path": path.rstrip("/") or "/"}
            if raw := scope.get("raw_path"):
                scope["raw_path"] = raw.rstrip(b"/") or b"/"
        await self.app(scope, receive, send)
