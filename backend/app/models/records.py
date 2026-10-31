"""Structured records (roadmap §4.1): typed rows extracted from a collection's documents,
so "which / how many / list every" questions get exact, cited answers instead of
whatever top-k search happens to return.

A `RecordSchema` describes one kind of record in one collection (a vendor, a contract,
a clinic) as typed fields. It starts as a suggestion derived from the documents'
structure and is used only once an admin activates it. Each `Record` is one entity
found in one document, with typed values and, per field, the exact text and its
character offsets in the document, so every value is citable. `RecordRun` remembers
which document content each schema was extracted from, so only changed documents are
redone."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.entities import Base, _created, _org_fk, _pk, utcnow

FIELD_TYPES = ("text", "number", "date", "boolean", "choice")
SCHEMA_STATUSES = ("suggested", "active", "archived")


class RecordSchema(Base):
    __tablename__ = "record_schemas"
    __table_args__ = (UniqueConstraint("collection_id", "name", name="uq_record_schema_name"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("collections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Machine name used by the agent's query_records tool ("vendor"), and a label.
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # [{"name", "label", "type", "description", "options"?, "aliases"?}]
    fields: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    key_field: Mapped[str] = mapped_column(String(64), nullable=False)
    # How the schema was found: "fields_table" (one Field|Value table per document),
    # "rows_table" (a register: one row per record), "field_lines" or "llm".
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="suggested", nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        UniqueConstraint("schema_id", "document_id", "key", name="uq_record_key"),
        Index("ix_records_values", "values", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    collection_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    schema_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("record_schemas.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # The key field's value, normalised (lowercase, single spaces): one record per
    # entity per document.
    key: Mapped[str] = mapped_column(String(512), nullable=False)
    # {field: typed value}: str, number, ISO date "YYYY-MM-DD", bool, or a choice.
    values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # {field: {"text": as written, "start": int, "end": int}}: offsets into the
    # document's content (the Markdown Strata stores), so each value is citable.
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _created()


class RecordRun(Base):
    """Which version of a document a schema was last extracted from."""

    __tablename__ = "record_runs"

    schema_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("record_schemas.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime] = _created()
