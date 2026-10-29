from functools import lru_cache

from app.core.config import get_settings
from app.embeddings.base import Embedder


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "hash":
        from app.embeddings.hash_provider import HashEmbedder

        return HashEmbedder(s.embedding_dim)
    if s.embedding_provider == "voyage":
        if not s.voyage_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=voyage requires VOYAGE_API_KEY")
        from app.embeddings.voyage_provider import VoyageEmbedder

        return VoyageEmbedder(s.voyage_api_key, s.embedding_model, s.embedding_dim)
    from app.embeddings.fastembed_provider import FastEmbedEmbedder

    return FastEmbedEmbedder(s.embedding_model, s.embedding_dim)
