"""Slack bot: workspaces (bot token encrypted at rest), org connections, channel →
org scope mappings, Slack user links, thread conversations and event dedupe

Revision ID: 0015
Revises: 0012
Create Date: 2026-11-01 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _workspace_fk() -> sa.Column:
    return sa.Column(
        "workspace_id",
        sa.Uuid(),
        sa.ForeignKey("slack_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "slack_workspaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.String(32), nullable=False, unique=True),
        sa.Column("team_name", sa.String(255), nullable=False),
        sa.Column("bot_user_id", sa.String(32), nullable=False),
        sa.Column("app_id", sa.String(32), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("secrets", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "slack_connections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "org_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        _workspace_fk(),
        sa.Column("connected_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "workspace_id"),
    )
    op.create_index("ix_slack_connections_workspace_id", "slack_connections", ["workspace_id"])
    op.create_table(
        "slack_channels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("channel_id", sa.String(32), nullable=False),
        sa.Column("channel_name", sa.String(255), nullable=True),
        sa.Column("org_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("updated_by_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "channel_id"),
    )
    op.create_table(
        "slack_user_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("slack_user_id", sa.String(32), nullable=False),
        sa.Column("slack_user_name", sa.String(255), nullable=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "slack_user_id"),
    )
    op.create_table(
        "slack_threads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        _workspace_fk(),
        sa.Column("channel_id", sa.String(32), nullable=False),
        sa.Column("thread_ts", sa.String(32), nullable=False),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("last_message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "channel_id", "thread_ts", "user_id"),
    )
    op.create_table(
        "slack_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("slack_events")
    op.drop_table("slack_threads")
    op.drop_table("slack_user_links")
    op.drop_table("slack_channels")
    op.drop_table("slack_connections")
    op.drop_table("slack_workspaces")
