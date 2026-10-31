"""knowledge graph: entities, relations and per-chunk mentions

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("graph_hash", sa.String(length=64), nullable=True))
    op.create_table(
        "kg_entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("name_key", sa.String(length=256), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "name_key"),
    )
    op.create_index("ix_kg_entities_org_id", "kg_entities", ["org_id"])
    op.create_table(
        "kg_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["kg_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["kg_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "source_id", "target_id"),
    )
    op.create_index("ix_kg_relations_org_id", "kg_relations", ["org_id"])
    op.create_index("ix_kg_relations_source_id", "kg_relations", ["source_id"])
    op.create_index("ix_kg_relations_target_id", "kg_relations", ["target_id"])
    op.create_table(
        "kg_mentions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("relation_id", sa.Uuid(), nullable=True),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("keywords", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["kg_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["relation_id"], ["kg_relations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "(entity_id IS NULL) <> (relation_id IS NULL)", name="ck_kg_mentions_one_target"
        ),
    )
    for column in ("org_id", "entity_id", "relation_id", "chunk_id", "document_id"):
        op.create_index(f"ix_kg_mentions_{column}", "kg_mentions", [column])
    op.create_index("ix_kg_mentions_scope", "kg_mentions", ["collection_id", "entity_id"])


def downgrade() -> None:
    op.drop_table("kg_mentions")
    op.drop_table("kg_relations")
    op.drop_table("kg_entities")
    op.drop_column("documents", "graph_hash")
