import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select

from app.core import audit
from app.core.deps import OrgContext, admin_ctx, org_context
from app.core.errors import bad_request, not_found
from app.models import Agent, Collection
from app.schemas.common import AgentCreate, AgentOut, AgentPatch

router = APIRouter(prefix="/orgs/{slug}/agents", tags=["agents"])


async def _check_collections(ctx: OrgContext, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if not ids:
        return []
    found = set(
        (
            await ctx.db.execute(
                select(Collection.id).where(Collection.org_id == ctx.org_id, Collection.id.in_(ids))
            )
        ).scalars()
    )
    if set(ids) - found:
        raise bad_request("Some collections don't belong to this organization", "bad_collections")
    return list(dict.fromkeys(ids))


def _prompts(prompts: list[str]) -> list[str]:
    return [p.strip() for p in prompts if p.strip()][:6]


async def _get(ctx: OrgContext, agent_id: uuid.UUID) -> Agent:
    agent = await ctx.db.get(Agent, agent_id)
    if agent is None or agent.org_id != ctx.org_id:
        raise not_found("Agent")
    return agent


@router.get("", response_model=list[AgentOut])
async def list_agents(ctx: OrgContext = Depends(org_context)):
    rows = await ctx.db.execute(
        select(Agent).where(Agent.org_id == ctx.org_id).order_by(Agent.created_at)
    )
    return list(rows.scalars())


# What an agent's change records: its scope and behaviour, not its starter prompts.
AUDITED = ("name", "mode", "instructions", "collection_ids")


def _plain(values: dict) -> dict:
    return {k: [str(i) for i in v] if isinstance(v, list) else v for k, v in values.items()}


@router.post("", response_model=AgentOut, status_code=201)
async def create_agent(body: AgentCreate, ctx: OrgContext = Depends(admin_ctx)):
    agent = Agent(
        org_id=ctx.org_id,
        name=body.name.strip(),
        description=body.description,
        instructions=body.instructions,
        mode=body.mode,
        glyph=body.glyph,
        collection_ids=await _check_collections(ctx, body.collection_ids),
        starter_prompts=_prompts(body.starter_prompts),
        created_by=ctx.user.id,
    )
    ctx.db.add(agent)
    await ctx.db.flush()
    audit.record_event(
        ctx, "agent.created", "agent", agent.id, agent.name,
        collections=len(agent.collection_ids or []),
    )  # fmt: skip
    await ctx.db.commit()
    return agent


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: uuid.UUID, body: AgentPatch, ctx: OrgContext = Depends(admin_ctx)):
    agent = await _get(ctx, agent_id)
    before = {k: getattr(agent, k) for k in AUDITED}
    data = body.model_dump(exclude_unset=True)
    if "collection_ids" in data:
        data["collection_ids"] = await _check_collections(ctx, data["collection_ids"] or [])
    if "starter_prompts" in data:
        data["starter_prompts"] = _prompts(data["starter_prompts"] or [])
    for key, value in data.items():
        if value is not None or key == "description":
            setattr(agent, key, value)
    after = {k: getattr(agent, k) for k in AUDITED}
    if changed := audit.changes(_plain(before), _plain(after)):
        audit.record_event(ctx, "agent.updated", "agent", agent.id, agent.name, **changed)
    await ctx.db.commit()
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: uuid.UUID, ctx: OrgContext = Depends(admin_ctx)):
    agent = await _get(ctx, agent_id)
    audit.record_event(ctx, "agent.deleted", "agent", agent.id, agent.name)
    await ctx.db.delete(agent)
    await ctx.db.commit()
    return Response(status_code=204)
