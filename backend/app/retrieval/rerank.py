"""Second-stage reranking: a cross-encoder scores (query, passage) pairs for the
hybrid-search candidates, which is far more precise than the first-stage fusion.

Providers: NVIDIA's hosted Nemotron reranker, or a local fastembed cross-encoder.
Scores are raw logits; callers squash them with `sigmoid` before mixing with other
signals."""

import asyncio
import logging
import math
from functools import cached_property, lru_cache
from typing import Protocol

from app.core.config import get_settings
from app.llm import nvidia

log = logging.getLogger("strata.rerank")


class Reranker(Protocol):
    name: str

    async def score(self, query: str, passages: list[str]) -> list[float]: ...


class NvidiaReranker:
    name = "nvidia"

    async def score(self, query: str, passages: list[str]) -> list[float]:
        return await nvidia.rerank(query, passages)


class FastEmbedReranker:
    name = "fastembed"

    def __init__(self, model_name: str):
        self.model_name = model_name

    @cached_property
    def _model(self):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        return TextCrossEncoder(model_name=self.model_name)

    def _score(self, query: str, passages: list[str]) -> list[float]:
        return [float(s) for s in self._model.rerank(query, passages, batch_size=32)]

    async def score(self, query: str, passages: list[str]) -> list[float]:
        return await asyncio.to_thread(self._score, query, passages)


def _fastembed_installed() -> bool:
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return False
    return True


@lru_cache
def get_reranker() -> Reranker | None:
    s = get_settings()
    provider = s.rerank_provider
    if provider == "auto":
        if s.nvidia_api_key:
            provider = "nvidia"
        elif s.embedding_provider == "fastembed" and _fastembed_installed():
            provider = "fastembed"
        else:
            provider = "none"
    if provider == "nvidia":
        if not s.nvidia_api_key:
            log.warning("RERANK_PROVIDER=nvidia needs NVIDIA_API_KEY; reranking is off")
            return None
        return NvidiaReranker()
    if provider == "fastembed":
        return FastEmbedReranker(s.fastembed_rerank_model)
    return None


def sigmoid(x: float) -> float:
    if x == float("-inf"):
        return 0.0
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, x))))
