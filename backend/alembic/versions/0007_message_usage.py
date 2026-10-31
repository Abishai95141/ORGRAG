"""messages.usage: model tokens spent per answer, so spend can be summed per org

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-31 19:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("usage", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "usage")
