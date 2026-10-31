"""messages.updated_at: run heartbeat, so interrupted answers can be reaped

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_messages_streaming",
        "messages",
        ["updated_at"],
        postgresql_where=sa.text("status = 'streaming'"),
    )


def downgrade() -> None:
    op.drop_index("ix_messages_streaming", table_name="messages")
    op.drop_column("messages", "updated_at")
