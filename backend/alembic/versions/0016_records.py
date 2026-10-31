"""Structured records: record_schemas, records, record_runs

Revision ID: 0016
Revises: 0013
Create Date: 2026-11-01 02:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0016"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "record_schemas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "collection_id", sa.Uuid(), sa.ForeignKey("collections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("key_field", sa.String(64), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="suggested"),
        sa.Column("stats", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("collection_id", "name", name="uq_record_schema_name"),
    )  # fmt: skip
    op.create_index("ix_record_schemas_org_id", "record_schemas", ["org_id"])
    op.create_index("ix_record_schemas_collection_id", "record_schemas", ["collection_id"])
    op.create_table(
        "records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column(
            "schema_id", sa.Uuid(), sa.ForeignKey("record_schemas.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(512), nullable=False),
        sa.Column("values", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("schema_id", "document_id", "key", name="uq_record_key"),
    )  # fmt: skip
    for col in ("org_id", "collection_id", "schema_id", "document_id"):
        op.create_index(f"ix_records_{col}", "records", [col])
    op.create_index("ix_records_values", "records", ["values"], postgresql_using="gin")
    op.create_table(
        "record_runs",
        sa.Column(
            "schema_id", sa.Uuid(), sa.ForeignKey("record_schemas.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
    )  # fmt: skip


def downgrade() -> None:
    op.drop_table("record_runs")
    op.drop_table("records")
    op.drop_table("record_schemas")
