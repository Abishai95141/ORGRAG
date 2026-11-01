"""Connector interface. A connector streams `RawDocument`s; the ingestion pipeline
handles parsing, hashing, chunking, embedding and pruning. One async generator covers
full syncs and incremental ones (`since` set), and `list_ids` lists what still exists
upstream so documents deleted there can be removed."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar


@dataclass
class RawDocument:
    external_id: str
    title: str
    data: bytes
    mime: str
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # When the document last changed in the source system (drives recency bias).
    updated_at: datetime | None = None


ProgressFn = Callable[[int, int | None, str | None], Any]


class ConnectorError(RuntimeError):
    """A problem the user has to fix (bad credentials, missing access). Not retried."""


class Connector(ABC):
    kind: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str]
    # JSON schema for the "Add source" form. Properties marked `"secret": True` are
    # moved into `Source.secrets` by the API and never returned to clients.
    config_schema: ClassVar[dict[str, Any]]
    # True when `iter_documents(since=...)` yields only documents changed after `since`.
    supports_incremental: ClassVar[bool] = False
    # True when `check()` can list what the credentials reach (the add-source picker).
    can_check: ClassVar[bool] = False

    def __init__(self, config: dict[str, Any], secrets: dict[str, Any]):
        self.config = config
        self.secrets = secrets
        # Filled in during `iter_documents`, read by the pipeline before pruning:
        # ids that exist upstream but couldn't be fetched this run (kept, not deleted)...
        self.skipped: set[str] = set()
        # ...and False when the listing was cut short (size caps, truncated trees),
        # in which case nothing is pruned.
        self.complete = True

    def skip(self, external_id: str) -> None:
        """Record an item that exists upstream but couldn't be fetched (rate limit,
        timeout, server error). Its indexed copy is kept rather than pruned."""
        self.skipped.add(external_id)

    @classmethod
    def secret_fields(cls) -> list[str]:
        props = cls.config_schema.get("properties", {})
        return [name for name, spec in props.items() if spec.get("secret")]

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        """Return a normalized config or raise ValueError with a readable message."""
        return config

    @abstractmethod
    def iter_documents(
        self, progress: ProgressFn, since: datetime | None = None
    ) -> AsyncIterator[RawDocument]:
        """Yield documents. `since=None` means a full sync; otherwise connectors with
        `supports_incremental` may yield only documents changed after it."""

    async def check(self) -> dict[str, Any]:
        """What these credentials can reach, for the add-source form to pick from:
        `{identity, field, items: [{id, name, kind, detail?, ready, note?}], warnings}`
        (`field` is the config field picked ids go into). ConnectorError when the
        credentials can't be used, with what to fix."""
        raise NotImplementedError

    async def list_ids(self) -> set[str] | None:
        """Every external id that currently exists upstream, without fetching content.
        Return None when the connector can't list cheaply; the pipeline then prunes
        only after full syncs."""
        return None
