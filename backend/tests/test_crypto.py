"""Connector secrets are encrypted at rest (audit S4)."""

import uuid

import pytest
from cryptography.fernet import Fernet

from app.core import crypto
from app.core.config import get_settings


@pytest.fixture
def keys(monkeypatch):
    def use(value: str | None):
        monkeypatch.setattr(get_settings(), "secrets_encryption_keys", value)
        crypto._fernet.cache_clear()

    yield use
    crypto._fernet.cache_clear()


def test_round_trip_and_ciphertext_hides_the_value(keys):
    keys(None)
    stored = crypto.encrypt({"token": "xoxb-secret"})
    assert crypto.is_encrypted(stored) and "xoxb" not in stored["ct"]
    assert crypto.decrypt(stored) == {"token": "xoxb-secret"}


def test_plaintext_rows_from_before_encryption_still_read(keys):
    keys(None)
    assert crypto.decrypt({"token": "old"}) == {"token": "old"}
    assert crypto.decrypt(None) == {}


def test_keys_can_be_introduced_and_rotated(keys):
    keys(None)
    derived = crypto.encrypt({"k": 1})  # written before explicit keys existed
    first, second = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    keys(first)
    assert crypto.decrypt(derived) == {"k": 1}  # the derived key stays a decryptor
    under_first = crypto.rotate(derived)
    keys(f"{second},{first}")
    assert crypto.decrypt(under_first) == {"k": 1}
    rotated = crypto.rotate(under_first)
    keys(second)  # retire the old key
    assert crypto.decrypt(rotated) == {"k": 1}


def test_unreadable_secrets_degrade_to_unset(keys, monkeypatch):
    keys(Fernet.generate_key().decode())
    stored = crypto.encrypt({"token": "x"})
    keys(Fernet.generate_key().decode())
    monkeypatch.setattr(get_settings(), "secret_key", "a-different-secret-key-" + "x" * 20)
    crypto._fernet.cache_clear()
    assert crypto.decrypt(stored) == {}


def test_bad_key_configuration_is_explained(keys):
    with pytest.raises(ValueError, match="Fernet"):
        keys("not-a-key")
        crypto.encrypt({})


@pytest.mark.integration
async def test_source_secrets_are_ciphertext_in_the_database(app):
    from sqlalchemy import text

    from app.core.db import SessionLocal
    from app.models import Source
    from tests.test_api_integration import _org, _signup

    admin = await _signup(app, "crypt")
    slug = (await _org(admin, "Crypt Co"))["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    r = await admin.post(
        f"/api/orgs/{slug}/sources",
        json={
            "kind": "slack",
            "name": "Slack",
            "collection_id": cols[0]["id"],
            "config": {"token": "xoxb-very-secret", "channels": ["general"]},
        },
    )
    assert r.status_code == 201 and r.json()["config"]["token_set"] is True
    source_id = uuid.UUID(r.json()["id"])
    async with SessionLocal() as db:
        raw = (
            await db.execute(
                text("SELECT secrets::text FROM sources WHERE id = :i"), {"i": source_id}
            )
        ).scalar_one()
        assert "xoxb" not in raw and '"ct"' in raw
        assert (await db.get(Source, source_id)).secrets == {"token": "xoxb-very-secret"}

    # Updating one secret keeps the others and stays encrypted.
    r = await admin.patch(
        f"/api/orgs/{slug}/sources/{source_id}", json={"config": {"token": "xoxb-rotated"}}
    )
    assert r.status_code == 200, r.text
    async with SessionLocal() as db:
        assert (await db.get(Source, source_id)).secrets == {"token": "xoxb-rotated"}
