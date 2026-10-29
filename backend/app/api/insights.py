from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import cast, func, select
from sqlalchemy.types import Date

from app.core.deps import OrgContext, admin_ctx
from app.models import (
    Chunk,
    Conversation,
    Document,
    Message,
    MessageCitation,
    SearchEvent,
    Source,
    User,
)
from app.schemas.common import (
    DayCount,
    FeedbackCounts,
    InsightsOut,
    InsightTotals,
    RecentQuestion,
    TopSource,
)

router = APIRouter(prefix="/orgs/{slug}", tags=["insights"])


@router.get("/insights", response_model=InsightsOut)
async def insights(ctx: OrgContext = Depends(admin_ctx), days: int = Query(30, ge=1, le=365)):
    db, org_id = ctx.db, ctx.org_id
    today = datetime.now(UTC).date()
    since_date = today - timedelta(days=days - 1)
    since = datetime.combine(since_date, datetime.min.time(), tzinfo=UTC)
    chat = (
        SearchEvent.org_id == org_id,
        SearchEvent.kind == "chat",
        SearchEvent.created_at >= since,
    )

    async def scalar(stmt) -> int:
        return int((await db.execute(stmt)).scalar_one() or 0)

    totals = InsightTotals(
        questions=await scalar(select(func.count(SearchEvent.id)).where(*chat)),
        conversations=await scalar(
            select(func.count(Conversation.id)).where(
                Conversation.org_id == org_id, Conversation.created_at >= since
            )
        ),
        documents=await scalar(select(func.count(Document.id)).where(Document.org_id == org_id)),
        chunks=await scalar(select(func.count(Chunk.id)).where(Chunk.org_id == org_id)),
        active_users=await scalar(
            select(func.count(func.distinct(SearchEvent.user_id))).where(
                SearchEvent.org_id == org_id, SearchEvent.created_at >= since
            )
        ),
    )

    day = cast(func.timezone("UTC", SearchEvent.created_at), Date)
    per_day = dict((await db.execute(select(day, func.count()).where(*chat).group_by(day))).all())
    questions_per_day = [
        DayCount(date=(d := since_date + timedelta(days=i)).isoformat(), count=per_day.get(d, 0))
        for i in range(days)
    ]

    top = await db.execute(
        select(Source.id, Source.name, Source.kind, func.count().label("n"))
        .join(MessageCitation, MessageCitation.source_id == Source.id)
        .where(MessageCitation.org_id == org_id, MessageCitation.created_at >= since)
        .group_by(Source.id)
        .order_by(func.count().desc())
        .limit(6)
    )
    top_sources = [TopSource(source_id=i, name=n, kind=k, citations=c) for i, n, k, c in top.all()]

    fb = dict(
        (
            await db.execute(
                select(Message.feedback, func.count())
                .join(Conversation, Conversation.id == Message.conversation_id)
                .where(
                    Conversation.org_id == org_id,
                    Message.feedback.is_not(None),
                    Message.created_at >= since,
                )
                .group_by(Message.feedback)
            )
        ).all()
    )

    recent = await db.execute(
        select(
            SearchEvent.query,
            func.coalesce(User.name, "Former member"),
            SearchEvent.created_at,
            SearchEvent.hits,
        )
        .outerjoin(User, User.id == SearchEvent.user_id)
        .where(*chat)
        .order_by(SearchEvent.created_at.desc())
        .limit(10)
    )
    return InsightsOut(
        totals=totals,
        questions_per_day=questions_per_day,
        top_sources=top_sources,
        feedback=FeedbackCounts(up=fb.get("up", 0), down=fb.get("down", 0)),
        recent_questions=[
            RecentQuestion(query=q, user_name=u, created_at=t, hits=h)
            for q, u, t, h in recent.all()
        ],
    )
