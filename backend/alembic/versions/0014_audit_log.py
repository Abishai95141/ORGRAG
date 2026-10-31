"""audit_access (who read an org's data) and audit_events (admin actions)

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-01 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _actor() -> list[sa.Column]:
    return [
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("actor_name", sa.String(120), nullable=False),
        sa.Column("actor_email", sa.String(320), nullable=False),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("api_key_name", sa.String(120), nullable=True),
    ]


def _org() -> sa.Column:
    return sa.Column(
        "org_id",
        sa.Uuid(),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "audit_access",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _org(),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("channel", sa.String(8), nullable=False),
        *_actor(),
        sa.Column("origin_org_id", sa.Uuid(), nullable=True),
        sa.Column("query", sa.Text(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("documents", postgresql.JSONB(), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("cited_count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_audit_access_org_created", "audit_access", ["org_id", "created_at", "id"])
    op.create_index(
        "ix_audit_access_org_actor", "audit_access", ["org_id", "actor_user_id", "created_at"]
    )
    op.create_index(
        "ix_audit_access_org_key",
        "audit_access",
        ["org_id", "api_key_id", "created_at"],
        postgresql_where=sa.text("api_key_id IS NOT NULL"),
    )
    op.create_index(
        "ix_audit_access_documents",
        "audit_access",
        ["documents"],
        postgresql_using="gin",
        postgresql_ops={"documents": "jsonb_path_ops"},
    )
    op.create_index(
        "ix_audit_access_created_brin", "audit_access", ["created_at"], postgresql_using="brin"
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _org(),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("channel", sa.String(8), nullable=False),
        *_actor(),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("target_label", sa.String(512), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_audit_events_org_created", "audit_events", ["org_id", "created_at", "id"])
    op.create_index(
        "ix_audit_events_org_action", "audit_events", ["org_id", "action", "created_at"]
    )
    op.create_index(
        "ix_audit_events_created_brin", "audit_events", ["created_at"], postgresql_using="brin"
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("audit_access")
