"""Knowledge gaps (roadmap §6 #2): the questions the org's knowledge base didn't answer
(app/gaps/signals.py), grouped by meaning (app/gaps/cluster.py), with what the coverage
check found missing and the document requests that point at each gap.

"Answered elsewhere": a gap can show that a similar question got a good answer in
another org — only to a viewer who belongs to that org and can read every source the
answer cites (the rule of shared answers), only from orgs that allow cross-org reads,
and never through an API key, which is bound to its own org. Anyone else learns nothing
about the other org: not its questions, its answers or its documents."""

import asyncio
import hashlib
import logging
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.sharing import _missing
from app.core.deps import OrgContext, admin_ctx
from app.core.errors import ApiError
from app.gaps import cluster as clustering
from app.gaps import signals
from app.models import Collection, Document, DocumentRequest, Membership, Organization
from app.retrieval.access import visible_collections
from app.schemas.common import (
    GapCluster,
    GapDocument,
    GapHint,
    GapReasons,
    GapRequestRef,
    GapsOut,
    SharedOrg,
)

log = logging.getLogger("strata.gaps")
router = APIRouter(prefix="/orgs/{slug}", tags=["knowledge gaps"])

MAX_QUESTIONS = 500  # the most recent gap signals of each kind; older ones are left out
MAX_REQUESTS = 300  # open and fulfilled document requests matched to clusters
CROSS_ORG_DAYS = 180  # how far back other orgs' good answers are looked for
MAX_ANSWERS_PER_ORG = 300
MAX_HINTS = 3  # per cluster
MAX_ACCESS_CHECKS = 40  # per request: each one reads the cited documents' permissions
EXAMPLES = 5
MISSING_FACTS = 6


@dataclass
class _Question:
    """One distinct question (case, spacing and end punctuation folded) and every time
    it was asked."""

    text: str
    signals: list[signals.Signal] = field(default_factory=list)

    @property
    def last(self) -> datetime:
        return max(s.at for s in self.signals)


@dataclass
class _Cluster:
    out: GapCluster
    centroid: np.ndarray


async def _embed(texts: list[str]) -> np.ndarray:
    try:
        return await clustering.embed(texts)
    except Exception as exc:  # a hosted embedder being down is a 503, not a 500
        log.exception("Embedding gap questions failed")
        raise ApiError(
            503, "The embedding model is unavailable, so gaps can't be grouped", "embedding_error"
        ) from exc


def _distinct(found: list[signals.Signal]) -> list[_Question]:
    by_key: dict[str, _Question] = {}
    for s in found:  # newest first, so each question keeps its latest phrasing
        by_key.setdefault(signals.text_key(s.text) or s.text, _Question(s.text)).signals.append(s)
    # Most asked first, then most recent: clustering breaks ties by this order.
    return sorted(by_key.values(), key=lambda q: (-len(q.signals), -q.last.timestamp(), q.text))


def _cluster_out(questions: list[_Question], members: list[int], label: int) -> GapCluster:
    group = [questions[i] for i in members]
    every = [s for q in group for s in q.signals]
    reasons = Counter(r for s in every for r in s.reasons)
    facts: Counter[str] = Counter()
    phrasing: dict[str, str] = {}
    for s in every:
        for fact in dict.fromkeys(s.missing):
            facts[fact.casefold()] += 1
            phrasing.setdefault(fact.casefold(), fact)
    examples = [questions[label].text] + [
        q.text for q in sorted(group, key=lambda q: -len(q.signals)) if q is not questions[label]
    ]
    return GapCluster(
        id=hashlib.blake2b(
            signals.text_key(questions[label].text).encode(), digest_size=6
        ).hexdigest(),
        label=questions[label].text,
        count=len(every),
        askers=len({s.user_id for s in every if s.user_id}),
        first_seen=min(s.at for s in every),
        last_seen=max(s.at for s in every),
        reasons=GapReasons(**{r: reasons.get(r, 0) for r in signals.REASONS}),
        examples=examples[:EXAMPLES],
        missing=[phrasing[k] for k, _ in facts.most_common(MISSING_FACTS)],
    )


def _channel_only(gap: GapCluster) -> bool:
    """Every question in it was asked in a Slack channel that left out restricted
    collections the asker can read: maybe not missing knowledge at all."""
    return gap.reasons.restricted_in_channel == gap.count


async def _link_requests(ctx: OrgContext, clusters: list[_Cluster], threshold: float) -> None:
    """Attach each open or fulfilled document request to the gap its question (or title)
    is most similar to, if it's similar enough."""
    rows = list(
        (
            await ctx.db.execute(
                select(DocumentRequest)
                .where(
                    DocumentRequest.org_id == ctx.org_id,
                    DocumentRequest.status.in_(("pending", "fulfilled")),
                )
                .order_by(DocumentRequest.created_at.desc())
                .limit(MAX_REQUESTS)
            )
        ).scalars()
    )
    if not rows or not clusters:
        return
    vectors = await _embed([(r.question or r.title).strip() for r in rows])
    sims = vectors @ np.stack([c.centroid for c in clusters]).T
    for req, row in zip(rows, sims, strict=True):
        best = int(np.argmax(row))
        if row[best] < threshold:
            continue
        gap = clusters[best].out
        gap.requests.append(
            GapRequestRef(
                id=req.id,
                title=req.title,
                status=req.status,
                document_id=req.document_id,
                created_at=req.created_at,
                decided_at=req.decided_at,
            )
        )
        if req.status == "fulfilled" and req.decided_at and req.decided_at >= gap.last_seen:
            gap.addressed = True


class _Elsewhere:
    """Finds good answers in the viewer's other orgs and decides, per answer, whether
    the viewer may see it."""

    def __init__(self, ctx: OrgContext):
        self.ctx = ctx
        self.orgs: dict[uuid.UUID, tuple[Organization, str]] = {}
        self.visible: dict[uuid.UUID, set[uuid.UUID]] = {}

    async def load(self) -> list[signals.Answered]:
        db = self.ctx.db
        rows = await db.execute(
            select(Organization, Membership.role)
            .join(Membership, Membership.org_id == Organization.id)
            .where(Membership.user_id == self.ctx.user.id, Organization.id != self.ctx.org_id)
            .order_by(Organization.name)
        )
        for org, role in rows.all():
            # The same opt-out as cross-org search (app.retrieval.access.resolve_scope).
            if (org.settings or {}).get("allow_cross_org", True):
                self.orgs[org.id] = (org, role)
        since = datetime.now(UTC) - timedelta(days=CROSS_ORG_DAYS)
        found: dict[tuple, signals.Answered] = {}
        for org_id in self.orgs:
            answers = await signals.verified_answers(db, org_id, MAX_ANSWERS_PER_ORG)
            answers += await signals.good_answers(
                db, org_id, since, MAX_ANSWERS_PER_ORG, self.ctx.user.id
            )
            for a in answers:  # one per org, kind and question; the newest wins
                kind = "verified" if a.verified_answer_id else "answer"
                found.setdefault((org_id, kind, signals.text_key(a.question)), a)
        return list(found.values())

    async def hint(self, answer: signals.Answered) -> GapHint | None:
        """The hint for `answer`, or None if the viewer may not see it (or it points at
        nothing outside this org)."""
        db, viewer = self.ctx.db, self.ctx.user
        org, role = self.orgs[answer.org_id]
        if answer.collection_id is not None:  # a verified answer: readable where it's filed
            if org.id not in self.visible:
                cols = await visible_collections(db, org.id, viewer.id, role)
                self.visible[org.id] = {c.id for c in cols}
            if answer.collection_id not in self.visible[org.id]:
                return None
        author = answer.author_id or uuid.uuid4()  # no author: attachments are unreadable
        if await _missing(db, viewer, author, answer.cited):
            return None
        documents: list[GapDocument] = []
        for s in answer.cited:
            if (s.get("origin") or "knowledge") != "knowledge" or not s.get("document_id"):
                continue
            doc = await db.get(Document, uuid.UUID(str(s["document_id"])))
            if doc is None or doc.org_id == self.ctx.org_id:
                continue
            if any(d.id == doc.id for d in documents):
                continue
            home = await db.get(Organization, doc.org_id)
            collection = await db.get(Collection, doc.collection_id)
            documents.append(
                GapDocument(
                    id=doc.id,
                    title=doc.title,
                    org_slug=home.slug,
                    collection_name=collection.name if collection else None,
                    source_id=doc.source_id,
                )
            )
        if not documents and answer.verified_answer_id is None:
            return None
        return GapHint(
            kind="verified" if answer.verified_answer_id else "answer",
            org=SharedOrg(name=org.name, slug=org.slug, hue=org.hue),
            question=answer.question,
            answer=signals.excerpt(answer.answer),
            similarity=0.0,
            answered_at=answer.at,
            verified_answer_id=answer.verified_answer_id,
            documents=documents,
        )


async def _answered_elsewhere(ctx: OrgContext, clusters: list[_Cluster], threshold: float):
    if ctx.api_key is not None or not clusters:
        return  # a key stays inside its org
    elsewhere = _Elsewhere(ctx)
    answers = await elsewhere.load()
    if not answers:
        return
    vectors = await _embed([a.question for a in answers])
    sims = vectors @ np.stack([c.centroid for c in clusters]).T  # answers × clusters
    decided: dict[int, GapHint | None] = {}
    for col, gap in enumerate(clusters):
        for k in np.argsort(-sims[:, col], kind="stable"):
            k = int(k)
            if sims[k, col] < threshold or len(gap.out.answered_elsewhere) >= MAX_HINTS:
                break
            if k not in decided:
                if len(decided) >= MAX_ACCESS_CHECKS:
                    break
                decided[k] = await elsewhere.hint(answers[k])
            if (found := decided[k]) is not None:
                similarity = round(float(sims[k, col]), 3)
                gap.out.answered_elsewhere.append(
                    found.model_copy(update={"similarity": similarity})
                )


@router.get("/gaps", response_model=GapsOut)
async def knowledge_gaps(
    ctx: OrgContext = Depends(admin_ctx),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=100, description="Most clusters returned"),
):
    """Clusters of questions the knowledge base didn't answer in the last `days` days
    (calendar days, UTC, as Insights counts them), biggest first; gaps asked only in
    Slack channels that left out restricted collections come after the rest, and gaps a
    fulfilled document request has addressed since go last."""
    since_date = datetime.now(UTC).date() - timedelta(days=days - 1)
    since = datetime.combine(since_date, datetime.min.time(), tzinfo=UTC)
    answers, capped = await signals.answer_signals(ctx.db, ctx.org_id, since, MAX_QUESTIONS)
    searches = await signals.search_signals(ctx.db, ctx.org_id, since, MAX_QUESTIONS)
    found = sorted(answers + searches, key=lambda s: s.at, reverse=True)
    # Capped when either query hit its limit (answers are counted before filtering).
    truncated = capped or len(searches) == MAX_QUESTIONS or len(found) > MAX_QUESTIONS
    found = found[:MAX_QUESTIONS]

    threshold = clustering.similarity_threshold()
    questions = _distinct(found)
    vectors = await _embed([q.text for q in questions])
    weights = np.array([len(q.signals) for q in questions], dtype=np.float64)
    groups = await asyncio.to_thread(clustering.average_linkage, vectors, weights, threshold)
    clusters = [
        _Cluster(
            _cluster_out(questions, g, clustering.most_central(g, vectors, weights)),
            clustering.centroid(g, vectors, weights),
        )
        for g in groups
    ]
    await _link_requests(ctx, clusters, threshold)
    clusters.sort(
        key=lambda c: (
            c.out.addressed,
            _channel_only(c.out),
            -c.out.count,
            -c.out.askers,
            -c.out.last_seen.timestamp(),
        )
    )
    shown = clusters[:limit]
    await _answered_elsewhere(ctx, shown, threshold)
    return GapsOut(
        days=days,
        since=since,
        questions=len(found),
        truncated=truncated,
        similarity=threshold,
        total_clusters=len(clusters),
        clusters=[c.out for c in shown],
    )
