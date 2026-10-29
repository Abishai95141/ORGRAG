import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import agents, auth, chat, collections, insights, orgs, search, sources
from app.core.config import get_settings
from app.core.errors import ApiError
from app.schemas.common import HealthOut

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Strata API", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json"
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    code = (
        exc.code
        if isinstance(exc, ApiError)
        else {
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            405: "method_not_allowed",
        }.get(exc.status_code, "error")
    )
    return JSONResponse({"detail": exc.detail, "code": code}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:])
    message = first.get("msg", "Invalid request")
    return JSONResponse(
        {"detail": f"{field}: {message}" if field else message, "code": "validation_error"},
        status_code=422,
    )


for module in (auth, orgs, collections, sources, search, agents, chat, insights):
    app.include_router(module.router, prefix="/api")


@app.get("/api/health", response_model=HealthOut, tags=["health"])
async def health():
    return HealthOut(
        status="ok",
        llm_configured=settings.llm_configured,
        embedding_provider=settings.embedding_provider,
    )
