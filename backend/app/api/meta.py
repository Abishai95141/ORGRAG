"""Service-level metadata: health and the model catalogue."""

from fastapi import APIRouter, Depends

from app.core.config import get_settings
from app.core.deps import current_user
from app.llm.models import catalogue
from app.retrieval.rerank import get_reranker
from app.schemas.common import HealthOut, ModelOut

router = APIRouter(tags=["meta"])

VERSION = "0.2.0"


@router.get("/health", response_model=HealthOut)
async def health():
    s = get_settings()
    reranker = get_reranker()
    return HealthOut(
        status="ok",
        version=VERSION,
        llm_configured=s.llm_configured,
        llm_providers=s.llm_providers,
        default_model=s.default_model,
        embedding_provider=s.embedding_provider,
        rerank_provider=reranker.name if reranker else None,
        web_search_provider=s.web_search_provider,
    )


@router.get("/models", response_model=list[ModelOut], dependencies=[Depends(current_user)])
async def models():
    """Models an org can choose; `available` is false when that provider has no key."""
    return catalogue()
