"""SQLAlchemy models. Every tenant-owned row carries `org_id` (indexed)."""

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import get_settings
from app.core.db import Base

ROLES = ("owner", "admin", "member", "viewer")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


def _org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_hue: Mapped[int] = mapped_column(Integer, default=210, nullable=False)
    created_at: Mapped[datetime] = _created()


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _pk()
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    hue: Mapped[int] = mapped_column(Integer, default=24, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = _created()


class Membership(Base):
    __tablename__ = "memberships"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    joined_at: Mapped[datetime] = _created()


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created()
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Collection(Base):
    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[str] = mapped_column(String(16), default="org", nullable=False)
    glyph: Mapped[str] = mapped_column(String(32), default="stack", nullable=False)
    created_at: Mapped[datetime] = _created()


class CollectionAccess(Base):
    __tablename__ = "collection_access"

    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("collections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    secrets: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="idle", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    sync_interval_minutes: Mapped[int | None] = mapped_column(Integer)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("collections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(128), default="text/markdown", nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # When the document last changed in its source system (recency bias); falls back
    # to updated_at when the connector doesn't know.
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Admin curation: boost -3..3 scales ranking; hidden documents never surface.
    boost: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    hidden: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Denormalized vote totals from document_feedback, read on every search.
    feedback_up: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    feedback_down: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # content_hash the knowledge graph was last extracted from (None = not yet).
    graph_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class DocumentFeedback(Base):
    """One vote per person per document: was this source useful for the answer?"""

    __tablename__ = "document_feedback"
    __table_args__ = (UniqueConstraint("document_id", "user_id"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="SET NULL")
    )
    vote: Mapped[str] = mapped_column(String(8), nullable=False)
    created_at: Mapped[datetime] = _created()


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_document_ordinal", "document_id", "ordinal"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(get_settings().embedding_dim), nullable=False
    )
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(heading, '')), 'A') || "
            "setweight(to_tsvector('english', content), 'B')",
            persisted=True,
        ),
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    instructions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    mode: Mapped[str] = mapped_column(String(8), default="quick", nullable=False)
    glyph: Mapped[str] = mapped_column(String(32), default="compass", nullable=False)
    collection_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), default=list, nullable=False
    )
    starter_prompts: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created()


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_user_org", "user_id", "org_id", "updated_at"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("agents.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), default="New conversation", nullable=False)
    scope_org_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(Uuid), default=list, nullable=False
    )
    mode: Mapped[str] = mapped_column(String(8), default="quick", nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation", "conversation_id", "created_at"),)

    id: Mapped[uuid.UUID] = _pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )
    trace: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    feedback: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="complete", nullable=False)
    created_at: Mapped[datetime] = _created()


class MessageCitation(Base):
    """One row per document cited by an assistant message — powers Insights."""

    __tablename__ = "message_citations"

    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = _org_fk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = _created()


class SearchEvent(Base):
    __tablename__ = "search_events"
    __table_args__ = (Index("ix_search_events_org_created", "org_id", "created_at"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(8), default="chat", nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = _created()


class ChatAttachment(Base):
    """A file attached to a conversation. Parsed once to Markdown and read straight
    into the model's context; it is never indexed into the org's knowledge base."""

    __tablename__ = "chat_attachments"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = _created()


class ApiKey(Base):
    """Programmatic access to one org, acting as the key's creator with at most `role`.
    Only a SHA-256 of the key is stored; the plaintext is shown once at creation."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class KGEntity(Base):
    """A named thing (person, team, system, vendor, policy…) in one org's graph.

    Holds no description of its own: what is known about it lives in KGMention rows
    tied to source chunks, and is assembled per request from the chunks the asker can
    see — so a restricted collection never leaks through the graph."""

    __tablename__ = "kg_entities"
    __table_args__ = (UniqueConstraint("org_id", "name_key"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    name_key: Mapped[str] = mapped_column(String(256), nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = _created()


class KGRelation(Base):
    __tablename__ = "kg_relations"
    __table_args__ = (UniqueConstraint("org_id", "source_id", "target_id"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("kg_entities.id", ondelete="CASCADE"), index=True, nullable=False
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("kg_entities.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_at: Mapped[datetime] = _created()


class KGMention(Base):
    """One chunk's evidence for an entity or a relation: the extracted description and
    keywords. Deleted with the chunk, so re-indexing or deleting a document removes
    what it contributed to the graph."""

    __tablename__ = "kg_mentions"
    __table_args__ = (Index("ix_kg_mentions_scope", "collection_id", "entity_id"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = _org_fk()
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("kg_entities.id", ondelete="CASCADE"), index=True
    )
    relation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("kg_relations.id", ondelete="CASCADE"), index=True
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chunks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[str] = mapped_column(Text, default="", nullable=False)
