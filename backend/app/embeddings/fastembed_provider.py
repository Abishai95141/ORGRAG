"""Local ONNX embeddings via fastembed (default: BAAI/bge-small-en-v1.5, 384 dims)."""

import asyncio
from functools import cached_property

from app.embeddings.base import Embedder


class FastEmbedEmbedder(Embedder):
    name = "fastembed"

    def __init__(self, model_name: str, dim: int):
        self.model_name = model_name
        self.dim = dim

    @cached_property
    def _model(self):
        from fastembed import TextEmbedding

        return TextEmbedding(model_name=self.model_name)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts, batch_size=32)]

    def _embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed, texts)

    async def embed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self._embed_query, text)
