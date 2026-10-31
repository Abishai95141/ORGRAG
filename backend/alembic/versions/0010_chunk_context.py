"""chunks.context (Contextual Retrieval) and documents.context_hash

A chunk's context is a sentence or two situating it in its document. It's embedded
with the chunk and indexed for keyword search at weight C (below heading A and
content B), but never shown or cited.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-31 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TSV_WITH_CONTEXT = (
    "setweight(to_tsvector('english', coalesce(heading, '')), 'A') || "
    "setweight(to_tsvector('english', content), 'B') || "
    "setweight(to_tsvector('english', coalesce(context, '')), 'C')"
)
TSV = (
    "setweight(to_tsvector('english', coalesce(heading, '')), 'A') || "
    "setweight(to_tsvector('english', content), 'B')"
)


def _replace_tsv(expression: str) -> None:
    op.drop_index("ix_chunks_tsv", table_name="chunks", postgresql_using="gin")
    op.drop_column("chunks", "tsv")
    op.add_column(
        "chunks",
        sa.Column(
            "tsv",
            sa.dialects.postgresql.TSVECTOR(),
            sa.Computed(expression, persisted=True),
            nullable=True,
        ),
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], unique=False, postgresql_using="gin")


def upgrade() -> None:
    op.add_column("chunks", sa.Column("context", sa.Text(), server_default="", nullable=False))
    # "<prompt version>:<content_hash>" the contexts were generated from (None = none yet).
    op.add_column("documents", sa.Column("context_hash", sa.String(length=96), nullable=True))
    _replace_tsv(TSV_WITH_CONTEXT)


def downgrade() -> None:
    _replace_tsv(TSV)
    op.drop_column("documents", "context_hash")
    op.drop_column("chunks", "context")
