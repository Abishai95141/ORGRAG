import contextlib
import logging
import re
import uuid
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import compile_path

from app.api import (
    agents,
    api_keys,
    attachments,
    auth,
    chat,
    collections,
    documents,
    graph,
    insights,
    mcp,
    meta,
    orgs,
    search,
    sources,
    verified,
)
from app.core import telemetry
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.http import BodyLimit, StripTrailingSlash
from app.schemas.common import ErrorOut

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("strata.api")

DESCRIPTION = """Multi-organization agentic RAG.

**Auth.** Browser sessions use the `strata_session` cookie set by `POST /api/auth/login`.
Programs use an org API key: `Authorization: Bearer strata_sk_…` (create one under
*Settings → API keys*). A key acts as its creator inside its own org, with at most its role.

**Errors** are always `{"detail": str, "code": str, "request_id": str}`; the same id is
returned in the `X-Request-ID` header. **Paginated lists** return
`{"items": [...], "total", "limit", "offset"}`. A key from another org, or a resource
the caller can't see, is a 404 (not 403) so ids can't be probed.
"""


def _operation_id(route: APIRoute) -> str:
    """Stable, readable operation ids for client generators: `<tag>-<function>`."""
    tag = (route.tags[0] if route.tags else "default").replace(" ", "_")
    return f"{tag}-{route.name}"


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    telemetry.setup("strata-api")
    try:
        async with mcp.lifespan():
            yield
    finally:
        telemetry.shutdown()


app = FastAPI(
    lifespan=lifespan,
    title="Strata API",
    version=meta.VERSION,
    description=DESCRIPTION,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    generate_unique_id_function=_operation_id,
)

settings = get_settings()
_REQUEST_ID_OK = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

# Middleware order (outermost first): CORS → slash normalisation → request id →
# MCP endpoint → body limit → app. CORS is outermost so even a 500 carries its headers.
app.add_middleware(BodyLimit)
# /api/mcp goes to the MCP server (its own body limit and errors), inside the request
# id, slash normalisation and CORS layers.
app.add_middleware(mcp.McpEndpoint)


@app.middleware("http")
async def request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id", "")
    request.state.request_id = rid if _REQUEST_ID_OK.match(rid) else uuid.uuid4().hex[:16]
    try:
        response = await call_next(request)
    except Exception:
        # Handled here rather than by Starlette's outermost error middleware so the
        # 500 still passes through CORS.
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _error(request, 500, "Something went wrong on our side", "internal_error")
    response.headers["X-Request-ID"] = request.state.request_id
    return response


app.add_middleware(StripTrailingSlash)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After"],
)

DEFAULT_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "request_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}


def _error(request: Request, status: int, detail: str, code: str, headers=None) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    return JSONResponse(
        {"detail": detail, "code": code, "request_id": rid},
        status_code=status,
        headers={**(headers or {}), **({"X-Request-ID": rid} if rid else {})},
    )


try:  # FastAPI ≥ 0.160 nests included routers; this walks them flat.
    from fastapi.routing import iter_route_contexts as _routes
except ImportError:  # pragma: no cover

    def _routes(routes):
        return routes


@lru_cache(maxsize=256)
def _path_regex(path: str) -> re.Pattern:
    return compile_path(path)[0]


def _allowed_methods(request: Request) -> str:
    """Starlette's 405 names only the first route with a matching path; list them all."""
    path, methods = request.scope["path"], set()
    for route in _routes(request.app.routes):
        if route.path and route.methods and _path_regex(route.path).match(path):
            methods |= route.methods
    return ", ".join(sorted(methods))


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    code = exc.code if isinstance(exc, ApiError) else DEFAULT_CODES.get(exc.status_code, "error")
    headers = dict(exc.headers or {})
    if exc.status_code == 405 and (allowed := _allowed_methods(request)):
        headers["Allow"] = allowed
    return _error(request, exc.status_code, str(exc.detail), code, headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    if first.get("type") == "json_invalid":
        return _error(request, 422, "The request body is not valid JSON", "validation_error")
    field = ".".join(str(p) for p in first.get("loc", [])[1:])
    message = first.get("msg", "Invalid request")
    return _error(request, 422, f"{field}: {message}" if field else message, "validation_error")


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    # Backstop for races the routes don't handle explicitly (e.g. a parent row
    # deleted mid-request): a conflict, not a 500.
    log.info("Integrity error on %s %s: %s", request.method, request.url.path, exc.orig)
    return _error(request, 409, "That conflicts with the current state of the data", "conflict")


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return _error(request, 500, "Something went wrong on our side", "internal_error")


for module in (
    auth,
    orgs,
    collections,
    sources,
    documents,
    graph,
    search,
    agents,
    chat,
    attachments,
    insights,
    api_keys,
    verified,
    meta,
):
    app.include_router(
        module.router,
        prefix="/api",
        responses={"default": {"model": ErrorOut, "description": "Error envelope"}},
    )
