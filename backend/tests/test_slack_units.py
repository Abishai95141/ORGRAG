"""Slack bot pieces that need no database: request signatures, the signed state and
link tokens, and Markdown ↔ Slack text."""

import time
import uuid
from datetime import timedelta

import pytest

from app.slackbot import signing
from app.slackbot.mrkdwn import from_slack, split, to_mrkdwn

SECRET = "8f742231b10e8888abcd99yyyzzz85a5"
BODY = b'{"type":"event_callback","event":{"type":"app_mention","text":"hi"}}'


# ── request signatures ───────────────────────────────────────────────────────
def test_signature_matches_slacks_documented_example():
    # The worked example from Slack's "Verifying requests from Slack" guide.
    body = (
        b"token=xyzz0WbapA4vBCDEFasx0q6G&team_id=T1DC2JH3J&team_domain=testteamnow&"
        b"channel_id=G8PSS9T3V&channel_name=foobar&user_id=U2CERLKJA&user_name=roadrunner&"
        b"command=%2Fwebhook-collect&text=&response_url=https%3A%2F%2Fhooks.slack.com%2F"
        b"commands%2FT1DC2JH3J%2F397700885554%2F96rGlfmibIGlgcZRskXaIFfN&"
        b"trigger_id=398738663015.47445629121.803a0bc887a14d10d2c447fce8b6703c"
    )
    assert signing.signature(SECRET, "1531420618", body) == (
        "v0=a2114d57b48eac39b9ad189dd8316235a7b4a8d21a10bd27519666489c69b503"
    )


def test_valid_signature_passes():
    ts = str(int(time.time()))
    signing.verify_request(SECRET, ts, signing.signature(SECRET, ts, BODY), BODY)


@pytest.mark.parametrize(
    "signed_with, received",
    [("another-secret", BODY), (SECRET, BODY.replace(b"hi", b"ho"))],
    ids=["wrong secret", "tampered body"],
)
def test_bad_signature_is_rejected(signed_with, received):
    ts = str(int(time.time()))
    sent = signing.signature(signed_with, ts, BODY)
    with pytest.raises(signing.InvalidSignature, match="mismatch"):
        signing.verify_request(SECRET, ts, sent, received)


@pytest.mark.parametrize("offset", [-301, 301], ids=["stale", "future"])
def test_timestamp_outside_five_minutes_is_rejected(offset):
    ts = str(int(time.time()) + offset)
    with pytest.raises(signing.InvalidSignature, match="stale"):
        signing.verify_request(SECRET, ts, signing.signature(SECRET, ts, BODY), BODY)


def test_timestamp_inside_tolerance_passes():
    ts = str(int(time.time()) - 290)
    signing.verify_request(SECRET, ts, signing.signature(SECRET, ts, BODY), BODY)


@pytest.mark.parametrize("ts, sent", [(None, "v0=x"), ("123", None), ("soon", "v0=x")])
def test_missing_or_malformed_headers_are_rejected(ts, sent):
    with pytest.raises(signing.InvalidSignature):
        signing.verify_request(SECRET, ts, sent, BODY)


# ── signed tokens ────────────────────────────────────────────────────────────
def test_install_state_round_trip_and_tampering():
    org, user = uuid.uuid4(), uuid.uuid4()
    state = signing.make_state(org, user)
    claim = signing.read_state(state)
    assert claim is not None and (claim.org_id, claim.user_id) == (org, user)
    head, payload, sig = state.split(".")
    assert signing.read_state(f"{head}.{payload}.{sig[::-1]}") is None
    assert signing.read_state("not-a-token") is None


def test_expired_state_is_rejected():
    stale = signing._encode(
        {"org": str(uuid.uuid4()), "sub": str(uuid.uuid4())},
        signing._STATE_AUDIENCE,
        timedelta(seconds=-1),
    )
    assert signing.read_state(stale) is None


def test_tokens_are_not_interchangeable():
    link = signing.make_link_token(uuid.uuid4(), "T1", "U1", "ann")
    assert signing.read_state(link) is None
    assert signing.read_link_token(signing.make_state(uuid.uuid4(), uuid.uuid4())) is None
    from app.core.security import create_session_token, decode_session_token

    session = create_session_token(uuid.uuid4())
    assert signing.read_link_token(session) is None and signing.read_state(session) is None
    # A link (or state) token carries an audience, so it can't pass as a session.
    assert decode_session_token(link) is None
    assert decode_session_token(signing.make_state(uuid.uuid4(), uuid.uuid4())) is None


def test_link_token_reads_and_reports_expiry():
    workspace = uuid.uuid4()
    claim = signing.read_link_token(signing.make_link_token(workspace, "T1", "U1", "ann"))
    assert claim is not None and claim.workspace_id == workspace
    assert (claim.team_id, claim.slack_user_id, claim.slack_user_name) == ("T1", "U1", "ann")
    assert claim.expired is False
    old = signing._encode(
        {"w": str(workspace), "t": "T1", "u": "U1", "n": None},
        signing._LINK_AUDIENCE,
        timedelta(seconds=-5),
    )
    expired = signing.read_link_token(old)
    assert expired is not None and expired.expired is True


# ── Markdown → mrkdwn ────────────────────────────────────────────────────────
def _links(n: int) -> str | None:
    return f"https://strata.test/a/tok?source={n}" if n <= 2 else None


def test_inline_formatting_links_and_escaping():
    out = to_mrkdwn(
        "Staff get **16 weeks** of *paid* leave, ~~not 12~~. See [the handbook]"
        "(https://x.com/h?a=1&b=2) & ask HR <today>.",
        _links,
    )
    assert out == (
        "Staff get *16 weeks* of _paid_ leave, ~not 12~. See "
        "<https://x.com/h?a=1&amp;b=2|the handbook> &amp; ask HR &lt;today&gt;."
    )


def test_citations_become_numbered_source_links():
    out = to_mrkdwn("Leave is 16 weeks [1][2], or 4 for partners [3].", _links)
    assert "<https://strata.test/a/tok?source=1|[1]>" in out
    assert "<https://strata.test/a/tok?source=2|[2]>" in out
    assert out.endswith("partners [3].")  # no link for an unknown source: left as is


def test_block_structure():
    md = "## Leave\n\n- one\n  - nested\n1. first\n> quote\n\n---\n```python\nx = a < b\n```"
    lines = to_mrkdwn(md).split("\n")
    assert lines[0] == "*Leave*"
    assert "• one" in lines and "    ◦ nested" in lines and "1. first" in lines
    assert "> quote" in lines and "───" in lines
    # Slack would print a language tag as text; code is escaped but not formatted.
    assert lines[-3:] == ["```", "x = a &lt; b", "```"]


def test_code_spans_are_not_formatted():
    assert to_mrkdwn("Run `make **all** <x>` now") == "Run `make **all** &lt;x&gt;` now"


def test_narrow_table_becomes_a_code_block():
    md = "| Plan | Weeks |\n|---|---:|\n| Primary | 16 [1] |\n| Partner | 4 |"
    assert to_mrkdwn(md, _links).split("\n") == [
        "```",
        "Plan    | Weeks",
        "--------+-------",
        "Primary | 16 [1]",
        "Partner | 4",
        "```",
    ]


def test_wide_table_becomes_bullet_lines_with_links():
    md = f"| Name | Notes |\n|---|---|\n| {'a' * 40} | {'b' * 40} [1] |"
    out = to_mrkdwn(md, _links)
    assert out == (
        f"• *Name:* {'a' * 40} · *Notes:* {'b' * 40} <https://strata.test/a/tok?source=1|[1]>"
    )


def test_split_keeps_messages_under_the_limit_and_code_blocks_whole():
    paragraphs = "\n\n".join(f"Paragraph {i} " + "w" * 60 for i in range(30))
    code = "```\n" + "\n".join(f"line {i}" for i in range(60)) + "\n```"
    chunks = split(paragraphs + "\n\n" + code, limit=500)
    assert len(chunks) > 3 and all(len(c) <= 500 for c in chunks)
    for chunk in chunks:  # every chunk opens and closes its own fences
        assert chunk.count("```") % 2 == 0
    assert "\n".join(chunks).count("line 59") == 1
    assert chunks[0].startswith("Paragraph 0") and not chunks[1].startswith("\n")


def test_split_cuts_an_overlong_line():
    chunks = split("x" * 1200, limit=500)
    assert all(len(c) <= 500 for c in chunks) and "".join(chunks) == "x" * 1200


def test_from_slack_cleans_the_question():
    text = (
        "<@UBOT> what did <@U2|sam> post in <#C1|general> about "
        "<https://wiki.test/a|the wiki> &amp; <https://x.test>? <!here>"
    )
    assert from_slack(text, "UBOT") == (
        "what did @sam post in #general about the wiki (https://wiki.test/a) & "
        "https://x.test? @here"
    )
    assert from_slack("<@UBOT>   ", "UBOT") == ""
