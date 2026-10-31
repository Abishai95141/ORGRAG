"""BM25 index on chunks (pg_textsearch), when the extension is available

A no-op on servers without pg_textsearch installed and preloaded; keyword search
then keeps ts_rank_cd. Installing the extension later: run
`python -m app.retrieval.bm25` to create the index.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-31 22:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    installed = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_textsearch'")
    ).scalar()
    preload = bind.execute(sa.text("SHOW shared_preload_libraries")).scalar() or ""
    if not installed or "pg_textsearch" not in preload:
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")
    # Upstream issue #571: parallel builds can silently omit rows.
    op.execute("SET LOCAL max_parallel_maintenance_workers = 0")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_bm25 ON chunks USING bm25 "
        "((heading || ' ' || content || ' ' || context)) WITH (text_config='english')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_bm25")
