"""Voyage AI embeddings over HTTP. Set EMBEDDING_MODEL (e.g. voyage-3.5-lite) and the
matching EMBEDDING_DIM before running the first migration."""

import httpx

from app.embeddings.base import Embedder

_URL = "https://api.voyageai.com/v1/embeddings"


class VoyageEmbedder(Embedder):
    name = "voyage"

    def __init__(self, api_key: str, model_name: str, dim: int):
        self.api_key = api_key
        self.model_name = model_name
        self.dim = dim

    async def _call(self, texts: list[str], input_type: str) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60) as client:
            for i in range(0, len(texts), 64):
                resp = await client.post(
                    _URL,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "input": texts[i : i + 64],
                        "model": self.model_name,
                        "input_type": input_type,
                        "output_dimension": self.dim,
                    },
                )
                resp.raise_for_status()
                out.extend(item["embedding"] for item in resp.json()["data"])
        return out

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._call(texts, "document")

    async def embed_query(self, text: str) -> list[float]:
        return (await self._call([text], "query"))[0]
