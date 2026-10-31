"""encrypt connector secrets at rest (sources.secrets)

Rewrites every row through app.core.crypto: plaintext dicts become
{"v": 1, "ct": <Fernet token>}. Downgrade decrypts them back.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-31 14:00:00.000000
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.core import crypto

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rewrite(transform) -> None:
    conn = op.get_bind()
    for row in conn.execute(sa.text("SELECT id, secrets FROM sources")).all():
        conn.execute(
            sa.text("UPDATE sources SET secrets = CAST(:s AS jsonb) WHERE id = :id"),
            {"s": json.dumps(transform(row.secrets)), "id": row.id},
        )


def upgrade() -> None:
    _rewrite(lambda v: v if crypto.is_encrypted(v) else crypto.encrypt(v or {}))


def downgrade() -> None:
    _rewrite(crypto.decrypt)
