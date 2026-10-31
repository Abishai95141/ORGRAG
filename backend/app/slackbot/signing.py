"""Slack request signatures, and the two signed tokens the bot hands out.

Slack signs every request it sends us: `X-Slack-Signature` is `v0=` + hex HMAC-SHA256
of `v0:{X-Slack-Request-Timestamp}:{raw body}` with the app's signing secret.
Requests older than five minutes are refused, so a captured one can't be replayed.

The OAuth `state` (bound to the org and the admin who clicked "Connect") and the
account-link token (bound to one Slack user in one workspace) are short-lived JWTs
signed with SECRET_KEY, each with its own audience so one can't stand in for the
other or for a session."""

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings

TOLERANCE_SECONDS = 5 * 60
STATE_TTL = timedelta(minutes=10)
LINK_TTL = timedelta(hours=1)
_STATE_AUDIENCE = "strata:slack-install"
_LINK_AUDIENCE = "strata:slack-link"


class InvalidSignature(Exception):
    pass


def signature(secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verify_request(
    secret: str, timestamp: str | None, sent: str | None, body: bytes, now: float | None = None
) -> None:
    """Raise InvalidSignature unless the request is signed with `secret` and fresh."""
    if not timestamp or not sent:
        raise InvalidSignature("missing signature headers")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise InvalidSignature("bad timestamp") from exc
    if abs((time.time() if now is None else now) - ts) > TOLERANCE_SECONDS:
        raise InvalidSignature("stale timestamp")
    if not hmac.compare_digest(signature(secret, timestamp, body), sent):
        raise InvalidSignature("signature mismatch")


# ── signed tokens ────────────────────────────────────────────────────────────
def _encode(payload: dict, audience: str, ttl: timedelta) -> str:
    now = datetime.now(UTC)
    claims = {**payload, "aud": audience, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, get_settings().secret_key, algorithm="HS256")


def _decode(token: str, audience: str, *, verify_exp: bool = True) -> dict | None:
    try:
        return jwt.decode(
            token,
            get_settings().secret_key,
            algorithms=["HS256"],
            audience=audience,
            options={"verify_exp": verify_exp, "require": ["exp", "aud"]},
        )
    except jwt.PyJWTError:
        return None


@dataclass
class InstallState:
    org_id: uuid.UUID
    user_id: uuid.UUID


def make_state(org_id: uuid.UUID, user_id: uuid.UUID) -> str:
    return _encode({"org": str(org_id), "sub": str(user_id)}, _STATE_AUDIENCE, STATE_TTL)


def read_state(token: str) -> InstallState | None:
    """The org and admin an OAuth round trip started for; None if forged or expired."""
    claims = _decode(token, _STATE_AUDIENCE)
    try:
        return InstallState(uuid.UUID(claims["org"]), uuid.UUID(claims["sub"])) if claims else None
    except (KeyError, ValueError):
        return None


@dataclass
class LinkClaim:
    workspace_id: uuid.UUID
    team_id: str
    slack_user_id: str
    slack_user_name: str | None
    expired: bool
    slack_email: str | None = None  # the Slack account's own email, as Slack reports it
    jti: str | None = None  # one use per link


def make_link_token(
    workspace_id: uuid.UUID,
    team_id: str,
    slack_user_id: str,
    slack_user_name: str | None,
    slack_email: str | None = None,
) -> str:
    payload = {
        "w": str(workspace_id),
        "t": team_id,
        "u": slack_user_id,
        "n": slack_user_name,
        "e": slack_email,
        "j": uuid.uuid4().hex,
    }
    return _encode(payload, _LINK_AUDIENCE, LINK_TTL)


def read_link_token(token: str) -> LinkClaim | None:
    """The Slack user a link is for. Expired links still read (so the page can say it
    expired) but come back with `expired=True`; forged ones are None."""
    claims = _decode(token, _LINK_AUDIENCE, verify_exp=False)
    if claims is None:
        return None
    try:
        return LinkClaim(
            workspace_id=uuid.UUID(claims["w"]),
            team_id=str(claims["t"]),
            slack_user_id=str(claims["u"]),
            slack_user_name=claims.get("n"),
            expired=float(claims["exp"]) <= time.time(),
            slack_email=claims.get("e"),
            jti=claims.get("j"),
        )
    except (KeyError, ValueError, TypeError):
        return None
