import time

from fastapi import APIRouter, Depends

from app.agent.engine import direct_search
from app.core.deps import OrgContext, org_context
from app.models import SearchEvent
from app.schemas.common import SearchIn, SearchOut, SourceRef

router = APIRouter(prefix="/orgs/{slug}", tags=["search"])


@router.post("/search", response_model=SearchOut)
async def search(body: SearchIn, ctx: OrgContext = Depends(org_context)):
    started = time.perf_counter()
    results = await direct_search(
        ctx.db,
        user_id=ctx.user.id,
        org=ctx.org,
        org_slugs=body.org_slugs or [ctx.org.slug],
        collection_ids=body.collection_ids,
        query=body.query,
        limit=body.limit,
    )
    latency = int((time.perf_counter() - started) * 1000)
    ctx.db.add(
        SearchEvent(
            org_id=ctx.org_id,
            user_id=ctx.user.id,
            kind="search",
            query=body.query,
            latency_ms=latency,
            hits=len(results),
        )
    )
    await ctx.db.commit()
    return SearchOut(results=[SourceRef(**r.to_source_ref()) for r in results], latency_ms=latency)
