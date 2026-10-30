import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import (
    agents,
    api_keys,
    attachments,
    auth,
    chat,
    collections,
    documents,
    insights,
    meta,
    orgs,
    search,
    sources,
)
from app.core.config import get_settings
from app.core.errors import ApiError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("strata.api")

DESCRIPTION = """Multi-organization agentic RAG.

**Auth.** Browser sessions use the `strata_session` cookie set by `POST /api/auth/login`.
Programs use an org API key: `Authorization: Bearer strata_sk_…` (create one under
*Settings → API keys*). A key acts as its creator inside its own org, with at most its role.

**Errors** are always `{"detail": str, "code": str, "request_id": str}`; the same id is
returned in the `X-Request-ID` header. **Paginated lists** return
`{"items": [...], "total", "limit", "offset"}`.
"""

app = FastAPI(
    title="Strata API",
    version=meta.VERSION,
    description=DESCRIPTION,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After"],
)


@app.middleware("http")
async def request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
    request.state.request_id = rid[:64]
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def _error(request: Request, status: int, detail: str, code: str, headers=None) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    return JSONResponse(
        {"detail": detail, "code": code, "request_id": rid},
        status_code=status,
        headers={**(headers or {}), **({"X-Request-ID": rid} if rid else {})},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    code = (
        exc.code
        if isinstance(exc, ApiError)
        else {
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
            429: "rate_limited",
        }.get(exc.status_code, "error")
    )
    return _error(request, exc.status_code, str(exc.detail), code, exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:])
    message = first.get("msg", "Invalid request")
    return _error(request, 422, f"{field}: {message}" if field else message, "validation_error")


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
    search,
    agents,
    chat,
    attachments,
    insights,
    api_keys,
    meta,
):
    app.include_router(module.router, prefix="/api")
