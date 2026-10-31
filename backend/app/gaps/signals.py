"""Gap signals, read from what chat and search already store.

A complete answer is a gap when it is
- *unanswered*: the agent searched or read the knowledge base but cites none of it (it
  abstained, said the information isn't there, or answered from the web alone);
- *incomplete*: the sufficient-context check (app.agent.engine `_coverage_note`) still
  reported facts missing at the end of the run;
- *downvoted*: the asker gave it a thumbs-down.
A direct search (`POST /search`, MCP `search`) with zero hits is a gap too
(*no_results*). Chat questions are read from their answers, not from `search_events`, so
each counts once. An answer that cites a file attached to the conversation was about
that file, not the knowledge base, and is never a gap.

A *good* answer (for "answered in another org") is the opposite: complete, cites the
knowledge base, not downvoted, nothing reported missing."""

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, exists, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.agent.citations import cited_refs
from app.models import Conversation, Message, MessageCitation, SearchEvent, VerifiedAnswer

REASONS = ("unanswered", "incomplete", "downvoted", "no_results")
# What the engine labels a coverage check that found everything (engine._coverage_note);
# tests/test_gaps.py runs the real check so a renamed label fails there.
COVERED_LABEL = "Sources cover the question"

# Only the trace steps read here leave the database: traces carry up to 8000 characters
# of reasoning per step, which gap detection never needs.
RESEARCHED = '$[*] ? (@.kind == "search" || @.kind == "read")'
REPORTED_MISSING = '$[*] ? (@.kind == "reflect" && exists(@.detail.missing))'
COVERAGE_CHECKS = (
    '$[*] ? (@.kind == "reflect" && (exists(@.detail.missing) || @.label == "'
    + COVERED_LABEL
    + '"))'
)


def text_key(text: str) -> str:
    """Case, whitespace and end punctuation folded: "How long is leave?" and
    "how long is  leave" are the same question."""
    return re.sub(r"\s+", " ", text).strip().rstrip("?.!…").strip().casefold()


def coverage_missing(checks: list[dict[str, Any]] | None) -> list[str]:
    """What the run's last sufficient-context check still reported missing: a later
    check that found everything clears an earlier one's list."""
    missing: list[str] = []
    for step in checks or []:
        if step.get("kind") != "reflect":
            continue
        reported = (step.get("detail") or {}).get("missing")
        if reported:
            missing = [str(m).strip() for m in reported if str(m).strip()]
        elif step.get("label") == COVERED_LABEL:
            missing = []
    return missing


def cited_sources(content: str, sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    cited = set(cited_refs(content))
    return [s for s in sources or [] if s.get("ref") in cited]


def _origin(source: dict[str, Any]) -> str:
    return source.get("origin") or "knowledge"


def gap_reasons(
    *,
    content: str,
    sources: list[dict[str, Any]] | None,
    researched: bool,
    checks: list[dict[str, Any]] | None,
    feedback: str | None,
) -> tuple[set[str], list[str]]:
    """Why a complete answer is a gap (empty: it isn't), and the facts the coverage
    check reported missing."""
    origins = {_origin(s) for s in cited_sources(content, sources)}
    if "attachment" in origins:
        return set(), []
    reasons: set[str] = set()
    if researched and "knowledge" not in origins:
        reasons.add("unanswered")
    missing = coverage_missing(checks)
    if missing:
        reasons.add("incomplete")
    if feedback == "down":
        reasons.add("downvoted")
    return reasons, missing


_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Markdown that means nothing in a one-paragraph excerpt: emphasis, code ticks, table
# pipes and rules, and heading, quote and list markers at the start of a line.
_MARKUP = re.compile(r"\*{1,3}|`+|\||:?-{3,}:?|^\s*(?:#{1,6}|>|[-+]|\d+\.)\s+", re.MULTILINE)


def excerpt(answer: str, limit: int = 400) -> str:
    """The start of an answer as plain prose: citation markers and Markdown removed."""
    text = _MARKUP.sub(" ", _LINK.sub(r"\1", re.sub(r"\s*\[\d{1,3}\]", "", answer)))
    text = re.sub(r"\s+([.,;:!?)])", r"\1", re.sub(r"\s+", " ", text)).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


@dataclass
class Signal:
    """One question that went unanswered."""

    text: str
    user_id: uuid.UUID | None
    at: datetime
    reasons: set[str]
    missing: list[str] = field(default_factory=list)


@dataclass
class Answered:
    """A good answer, a candidate for "answered in another org"."""

    org_id: uuid.UUID
    question: str
    answer: str
    at: datetime
    cited: list[dict[str, Any]]  # every source it cites (the viewer must read them all)
    author_id: uuid.UUID | None = None
    verified_answer_id: uuid.UUID | None = None
    collection_id: uuid.UUID | None = None  # verified answers: the collection it's in


def _path(path: str):
    return literal(path, JSONPATH)


def _asked():
    """The user message an assistant message answers: the last one before it."""
    asked = aliased(Message)
    return (
        select(asked.content)
        .where(
            asked.conversation_id == Message.conversation_id,
            asked.role == "user",
            asked.created_at <= Message.created_at,
        )
        .order_by(asked.created_at.desc())
        .limit(1)
        .correlate(Message)
        .scalar_subquery()
    )


def _checks():
    return func.jsonb_path_query_array(Message.trace, _path(COVERAGE_CHECKS), type_=JSONB)


async def answer_signals(
    db: AsyncSession, org_id: uuid.UUID, since: datetime, limit: int
) -> list[Signal]:
    """Answers in the org's conversations that were gaps, newest first. The SQL narrows
    to candidates (thumbs-down; researched with no knowledge citation; missing facts
    reported); `gap_reasons` decides."""
    researched = func.jsonb_path_exists(Message.trace, _path(RESEARCHED))
    uncited = ~exists().where(MessageCitation.message_id == Message.id)
    rows = await db.execute(
        select(
            Message.content,
            Message.sources,
            Message.feedback,
            Message.created_at,
            Conversation.user_id,
            researched.label("researched"),
            _checks().label("checks"),
            _asked().label("question"),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.org_id == org_id,
            Message.role == "assistant",
            Message.status == "complete",
            Message.created_at >= since,
            or_(
                Message.feedback == "down",
                and_(uncited, researched),
                func.jsonb_path_exists(Message.trace, _path(REPORTED_MISSING)),
            ),
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    out: list[Signal] = []
    for content, sources, feedback, at, user_id, did_research, checks, question in rows.all():
        reasons, missing = gap_reasons(
            content=content,
            sources=sources,
            researched=bool(did_research),
            checks=checks,
            feedback=feedback,
        )
        if reasons and question and question.strip():
            out.append(Signal(question.strip(), user_id, at, reasons, missing))
    return out


async def search_signals(
    db: AsyncSession, org_id: uuid.UUID, since: datetime, limit: int
) -> list[Signal]:
    """Direct searches in the org that found nothing, newest first."""
    rows = await db.execute(
        select(SearchEvent.query, SearchEvent.user_id, SearchEvent.created_at)
        .where(
            SearchEvent.org_id == org_id,
            SearchEvent.kind != "chat",
            SearchEvent.hits == 0,
            SearchEvent.created_at >= since,
        )
        .order_by(SearchEvent.created_at.desc())
        .limit(limit)
    )
    return [
        Signal(query.strip(), user_id, at, {"no_results"})
        for query, user_id, at in rows.all()
        if query.strip()
    ]


async def good_answers(
    db: AsyncSession, org_id: uuid.UUID, since: datetime, limit: int
) -> list[Answered]:
    """Good answers in the org's conversations, newest first."""
    rows = await db.execute(
        select(
            Message.content,
            Message.sources,
            Message.created_at,
            Conversation.user_id,
            _checks().label("checks"),
            _asked().label("question"),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.org_id == org_id,
            Message.role == "assistant",
            Message.status == "complete",
            Message.created_at >= since,
            or_(Message.feedback.is_(None), Message.feedback != "down"),
            exists().where(MessageCitation.message_id == Message.id),
        )
        .order_by(Message.created_at.desc())
        .limit(limit)
    )
    out: list[Answered] = []
    for content, sources, at, author_id, checks, question in rows.all():
        cited = cited_sources(content, sources)
        knowledge = [s for s in cited if _origin(s) == "knowledge"]
        if not knowledge or coverage_missing(checks) or not (question and question.strip()):
            continue
        out.append(Answered(org_id, question.strip(), content, at, cited, author_id=author_id))
    return out


async def verified_answers(db: AsyncSession, org_id: uuid.UUID, limit: int) -> list[Answered]:
    """The org's verified answers that haven't expired, newest verification first."""
    now = datetime.now(UTC)
    rows = await db.execute(
        select(VerifiedAnswer)
        .where(
            VerifiedAnswer.org_id == org_id,
            or_(VerifiedAnswer.expires_at.is_(None), VerifiedAnswer.expires_at > now),
        )
        .order_by(VerifiedAnswer.verified_at.desc())
        .limit(limit)
    )
    return [
        Answered(
            org_id,
            va.question,
            va.answer,
            va.verified_at,
            list(va.sources or []),
            verified_answer_id=va.id,
            collection_id=va.collection_id,
        )
        for va in rows.scalars()
    ]
