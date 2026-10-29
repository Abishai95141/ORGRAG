"""Connector interface. A connector streams `RawDocument`s; the ingestion pipeline
handles parsing, hashing, chunking and embedding. Mirrors the role of Onyx's
`connectors/interfaces.py` with a single async generator instead of
Load/Poll/Checkpoint variants."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar


@dataclass
class RawDocument:
    external_id: str
    title: str
    data: bytes
    mime: str
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


ProgressFn = Callable[[int, int | None, str | None], Any]


class ConnectorError(RuntimeError):
    pass


class Connector(ABC):
    kind: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str]
    config_schema: ClassVar[dict[str, Any]]

    def __init__(self, config: dict[str, Any], secrets: dict[str, Any]):
        self.config = config
        self.secrets = secrets

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Return a normalized config or raise ValueError with a readable message."""
        return config

    @abstractmethod
    def iter_documents(self, progress: ProgressFn) -> AsyncIterator[RawDocument]: ...
