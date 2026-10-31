import asyncio
import math
import uuid
from datetime import UTC, datetime

from app.core import audit
from app.core.deps import role_at_least
from app.core.security import (
    create_session_token,
    decode_session_token,
    hash_password,
    verify_password,
)
from app.core.slugs import is_valid_slug, next_available, slugify
from app.embeddings.hash_provider import HashEmbedder


def test_slugify():
    assert slugify("Northwind Labs, Inc.") == "northwind-labs-inc"
    assert slugify("Café Ñoño") == "cafe-nono"
    assert slugify("!!!") == "org"


def test_slug_validation_and_uniqueness():
    assert is_valid_slug("acme-co") and not is_valid_slug("-acme") and not is_valid_slug("api")
    assert next_available("acme", {"acme", "acme-2"}) == "acme-3"
    assert next_available("admin", set()) == "admin-org"


def test_role_ordering():
    assert role_at_least("owner", "admin")
    assert role_at_least("admin", "admin")
    assert not role_at_least("member", "admin")
    assert not role_at_least("viewer", "member")
    assert not role_at_least("stranger", "viewer")


def test_password_and_session_tokens():
    import uuid

    h = hash_password("correct horse")
    assert verify_password("correct horse", h) and not verify_password("wrong", h)
    uid = uuid.uuid4()
    assert decode_session_token(create_session_token(uid)) == uid
    assert decode_session_token("garbage") is None


def test_hash_embedder_is_deterministic_and_normalized():
    emb = HashEmbedder(384)
    a = asyncio.run(emb.embed_query("friday deploy policy"))
    b = asyncio.run(emb.embed_query("friday deploy policy"))
    assert a == b and len(a) == 384
    assert math.isclose(sum(x * x for x in a), 1.0, rel_tol=1e-6)

    def cos(x, y):
        return sum(i * j for i, j in zip(x, y, strict=True))

    near = asyncio.run(emb.embed_query("deploy policy on friday"))
    far = asyncio.run(emb.embed_query("patient intake vitals"))
    assert cos(a, near) > cos(a, far)


def test_audit_details_never_hold_credentials():
    when = datetime(2026, 11, 1, tzinfo=UTC)
    uid = uuid.uuid4()
    cleaned = audit._clean(
        {
            "config": {"repo": "acme/handbook", "token": "ghp_x", "client_secret": "s"},
            "password": "hunter2",
            "service_account_json": "{}",
            "note": "my key is strata_sk_abc",
            "owner": uid,
            "expires_at": when,
            "files": ("a.md", "b.md"),
        }
    )
    assert cleaned == {
        "config": {"repo": "acme/handbook"},
        "note": audit.REDACTED,
        "owner": str(uid),
        "expires_at": when.isoformat(),
        "files": ["a.md", "b.md"],
    }
    assert audit.changes({"a": 1, "b": 2}, {"a": 1, "b": 3}) == {"b": {"from": 2, "to": 3}}
