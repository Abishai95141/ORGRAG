"""Encryption at rest for connector secrets (audit S4).

`EncryptedJSON` is a column type: values are encrypted with Fernet (AES-128-CBC +
HMAC-SHA256) on write and decrypted on read, stored as `{"v": 1, "ct": "<token>"}`.

Keys come from `SECRETS_ENCRYPTION_KEYS` (comma-separated Fernet keys; the first
encrypts, all decrypt, so keys can be rotated with `python -m app.core.crypto
rotate`). Without it, a key is derived from `SECRET_KEY`; that key also stays a
decryptor, so setting explicit keys later doesn't strand existing rows.

Rows written before encryption (plain dicts) still read, and are encrypted the next
time they're written (migration 0006 does it for all rows). A value no key can
decrypt reads as `{}` and is logged, so a lost key shows up as "secret not set"
instead of breaking every query that loads the row."""

import base64
import json
import logging
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings

log = logging.getLogger("strata.crypto")
VERSION = 1


def _derived_key(secret_key: str) -> bytes:
    raw = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=b"strata", info=b"connector-secrets-v1"
    ).derive(secret_key.encode())
    return base64.urlsafe_b64encode(raw)


@lru_cache
def _fernet() -> MultiFernet:
    s = get_settings()
    explicit = [k.strip() for k in (s.secrets_encryption_keys or "").split(",") if k.strip()]
    keys = [*explicit, _derived_key(s.secret_key)]
    try:
        return MultiFernet([Fernet(k) for k in keys])
    except ValueError as e:
        raise ValueError(
            "SECRETS_ENCRYPTION_KEYS must be Fernet keys; generate one with "
            "`python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'`"
        ) from e


def is_encrypted(value: Any) -> bool:
    return (
        isinstance(value, dict) and value.get("v") == VERSION and isinstance(value.get("ct"), str)
    )


def encrypt(value: dict[str, Any]) -> dict[str, Any]:
    token = _fernet().encrypt(json.dumps(value).encode())
    return {"v": VERSION, "ct": token.decode()}


def decrypt(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not is_encrypted(value):
        return value  # written before encryption; re-encrypted on the next write
    try:
        return json.loads(_fernet().decrypt(value["ct"].encode()))
    except InvalidToken:
        log.error(
            "Stored secrets can't be decrypted: SECRET_KEY or SECRETS_ENCRYPTION_KEYS changed?"
        )
        return {}


def rotate(value: Any) -> dict[str, Any]:
    """Re-encrypt with the current primary key (plaintext is encrypted)."""
    if is_encrypted(value):
        return {"v": VERSION, "ct": _fernet().rotate(value["ct"].encode()).decode()}
    return encrypt(value or {})


class EncryptedJSON(TypeDecorator):
    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt(value)


# Tables with an EncryptedJSON `secrets` column: connector secrets, Slack bot tokens.
SECRET_TABLES = ("sources", "slack_workspaces")


async def _rotate_all() -> int:
    from sqlalchemy import text

    from app.core.db import SessionLocal

    count = 0
    async with SessionLocal() as db:
        for table in SECRET_TABLES:
            rows = (await db.execute(text(f"SELECT id, secrets FROM {table}"))).all()
            for row in rows:
                await db.execute(
                    text(f"UPDATE {table} SET secrets = CAST(:s AS jsonb) WHERE id = :id"),
                    {"s": json.dumps(rotate(row.secrets)), "id": row.id},
                )
            count += len(rows)
        await db.commit()
    return count


if __name__ == "__main__":
    import asyncio
    import sys

    if sys.argv[1:] != ["rotate"]:
        sys.exit("usage: python -m app.core.crypto rotate")
    count = asyncio.run(_rotate_all())
    print(f"Re-encrypted {count} secret(s) (sources, Slack workspaces) with the primary key")
