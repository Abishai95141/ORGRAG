from abc import ABC, abstractmethod


class Embedder(ABC):
    """Turns text into fixed-size vectors. Documents and queries may be embedded
    differently (asymmetric models prepend instructions to queries)."""

    name: str
    dim: int

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]: ...
