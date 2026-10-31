"""chunks.page: the page a chunk starts on, for formats that have pages (Docling)

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chunks", sa.Column("page", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("chunks", "page")
