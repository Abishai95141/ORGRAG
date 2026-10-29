"""The agentic RAG engine.

One run answers one user message. It plans (deep mode), drives a Claude tool loop
over the scoped knowledge base, streams every step to the client as SSE events
(see docs/API.md), and persists the final assistant message — even if the browser
disconnects mid-answer, because the run executes in its own task and session.

Event flow:  step(plan) → step(search/read/reflect)* + reasoning* + sources* → delta* → done
"""

import asyncio
import copy
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.agent.citations import CitationRegistry, cited_refs, strip_citations
from app.agent.prompts import PLAN_SYSTEM, build_system
from app.agent.tools import TOOL_DEFINITIONS, ToolBox, ToolInputError
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.llm.client import describe_error, get_client, request_extras, structured_call
from app.models import (
    Agent,
    Conversation,
    Message,
    MessageCitation,
    Organization,
    SearchEvent,
)
from app.retrieval.access import RetrievalScope, resolve_scope
from app.retrieval.hybrid import hybrid_search
from app.schemas.common import MessageOut, OrgSettings

log = logging.getLogger("strata.agent")

MAX_TURNS = {"quick": 4, "deep": 10}
HISTORY_MESSAGES = 12
PREAMBLE_CHARS = 160  # text shorter than this before a tool call is treated as reasoning
MAX_REASONING_CHARS = 8000

EventQueue = asyncio.Queue[tuple[str, dict[str, Any]] | None]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def message_payload(message: Message) -> dict[str, Any]:
    return MessageOut.model_validate(message).model_dump(mode="json")


@dataclass
class ChatRequest:
    conversation_id: uuid.UUID
    assistant_message_id: uuid.UUID
    user_id: uuid.UUID
    org_id: uuid.UUID
    question: str
    mode: str
    scope_org_ids: list[uuid.UUID]
    collection_ids: list[uuid.UUID] | None


class Emitter:
    """Owns the trace and pushes typed events onto the SSE queue."""

    def __init__(self, queue: EventQueue):
        self.queue = queue
        self.steps: dict[str, dict[str, Any]] = {}
        self.answer = ""
        self._answer_step: str | None = None

    async def _put(self, event: str, data: dict[str, Any]) -> None:
        # Snapshot: steps keep mutating after they are queued.
        await self.queue.put((event, copy.deepcopy(data)))

    async def step_start(self, kind: str, label: str, detail: dict[str, Any] | None = None) -> str:
        step_id = uuid.uuid4().hex[:12]
        self.steps[step_id] = {
            "id": step_id,
            "kind": kind,
            "label": label,
            "status": "running",
            "detail": detail or {},
            "started_at": _now(),
            "finished_at": None,
        }
        await self._put("step", self.steps[step_id])
        return step_id

    async def step_update(
        self,
        step_id: str,
        *,
        label: str | None = None,
        status: str | None = None,
        **detail: Any,
    ) -> None:
        step = self.steps[step_id]
        if label:
            step["label"] = label
        if status:
            step["status"] = status
            if status in ("done", "error"):
                step["finished_at"] = _now()
        step["detail"].update(detail)
        await self._put("step", step)

    async def reasoning(self, step_id: str, text: str) -> None:
        detail = self.steps[step_id]["detail"]
        current = detail.get("reasoning", "")
        if len(current) < MAX_REASONING_CHARS:
            detail["reasoning"] = current + text
        await self._put("reasoning", {"step_id": step_id, "text": text})

    async def sources(self, sources: list[dict[str, Any]]) -> None:
        if sources:
            await self._put("sources", {"sources": sources})

    async def delta(self, text: str) -> None:
        if not text:
            return
        if self._answer_step is None:
            self._answer_step = await self.step_start("answer", "Writing answer")
        self.answer += text
        await self._put("delta", {"text": text})

    async def finish_answer(self) -> None:
        if self._answer_step:
            await self.step_update(self._answer_step, label="Answer ready", status="done")

    async def fail_running(self) -> None:
        for step in self.steps.values():
            if step["status"] == "running":
                step["status"] = "error"
                step["finished_at"] = _now()

    @property
    def trace(self) -> list[dict[str, Any]]:
        return list(self.steps.values())


class AgentRun:
    def __init__(self, req: ChatRequest, queue: EventQueue):
        self.req = req
        self.em = Emitter(queue)
        self.registry = CitationRegistry()
        self.retrieval_ms = 0

    # ── setup ─────────────────────────────────────────────────────────────
    async def _load(self, db) -> None:
        self.org = await db.get(Organization, self.req.org_id)
        self.conversation = await db.get(Conversation, self.req.conversation_id)
        self.settings = OrgSettings(**(self.org.settings or {}))
        self.agent = (
            await db.get(Agent, self.conversation.agent_id) if self.conversation.agent_id else None
        )
        scope = await resolve_scope(
            db,
            user_id=self.req.user_id,
            primary_org=self.org,
            org_ids=self.req.scope_org_ids,
            collection_ids=self.req.collection_ids,
        )
        if self.agent and self.agent.collection_ids and not self.req.collection_ids:
            keep = set(self.agent.collection_ids)
            scope = RetrievalScope(
                orgs=scope.orgs,
                collections={
                    k: v
                    for k, v in scope.collections.items()
                    if v.org_id != self.org.id or k in keep
                },
            )
        self.scope = scope
        self.tools = ToolBox(db, scope, self.registry, top_k=self.settings.retrieval_top_k)

    async def _history(self, db) -> list[dict[str, Any]]:
        rows = (
            (
                await db.execute(
                    select(Message)
                    .where(
                        Message.conversation_id == self.req.conversation_id,
                        Message.id != self.req.assistant_message_id,
                        Message.status == "complete",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(HISTORY_MESSAGES + 1)
                )
            )
            .scalars()
            .all()
        )
        # Drop the current question (newest user row); it is sent separately.
        rows = list(reversed(rows))
        if rows and rows[-1].role == "user":
            rows = rows[:-1]
        history: list[dict[str, Any]] = []
        for m in rows:
            text = m.content if m.role == "user" else strip_citations(m.content)
            if not text.strip():
                continue
            if history and history[-1]["role"] == m.role:
                history[-1]["content"] += "\n\n" + text
            else:
                history.append({"role": m.role, "content": text})
        while history and history[0]["role"] != "user":
            history.pop(0)
        if history and history[-1]["role"] == "user":
            history.pop()
        return history

    def _collections_note(self) -> str:
        lines = []
        for org in self.scope.orgs.values():
            names = [c.name for c in self.scope.collections.values() if c.org_id == org.id]
            lines.append(f"- {org.name}: {', '.join(names) if names else '(no collections)'}")
        return "Collections available:\n" + "\n".join(lines)

    # ── main entry ────────────────────────────────────────────────────────
    async def execute(self) -> None:
        async with SessionLocal() as db:
            try:
                await self._load(db)
                if not self.scope.collections:
                    await self.em.delta(
                        "There's no knowledge I can search in this scope yet. Add a source "
                        "under **Sources**, or ask an admin for access to a collection."
                    )
                elif get_settings().llm_configured:
                    await self._run_llm(db)
                else:
                    await self._run_retrieval_only(db)
                await self.em.finish_answer()
                message = await self._persist(db, status="complete")
                await self.em.queue.put(("done", {"message": message_payload(message)}))
            except Exception as exc:
                log.exception("Agent run failed")
                detail, code = describe_error(exc)
                await self.em.fail_running()
                try:
                    async with SessionLocal() as clean_db:
                        await self._persist(clean_db, status="error", error=detail)
                except Exception:
                    log.exception("Could not persist failed message")
                await self.em.queue.put(("error", {"detail": detail, "code": code}))
            finally:
                await self.em.queue.put(None)

    # ── retrieval helpers ────────────────────────────────────────────────
    async def _search(self, step_id: str, args: dict[str, Any]):
        started = time.perf_counter()
        outcome = await self.tools.search(args)
        self.retrieval_ms += int((time.perf_counter() - started) * 1000)
        await self.em.sources([s.to_source_ref() for s in outcome.new_sources])
        n = len(outcome.refs)
        await self.em.step_update(
            step_id,
            label=f"Found {n} source{'s' if n != 1 else ''}" if n else "No matches",
            status="done",
            queries=outcome.queries,
            refs=outcome.refs,
        )
        return outcome

    async def _run_retrieval_only(self, db) -> None:
        step = await self.em.step_start(
            "search", "Searching knowledge", {"queries": [self.req.question]}
        )
        outcome = await self._search(step, {"queries": [self.req.question]})
        if not outcome.refs:
            await self.em.delta(
                "I couldn't find anything relevant in the knowledge base for that question."
            )
            return
        await self.em.delta(
            "**Answer generation is off** — no `ANTHROPIC_API_KEY` is configured, so here are "
            "the most relevant passages Strata found:\n\n"
        )
        for ref in outcome.refs[:6]:
            entry = self.registry.get(ref)
            source = entry.to_source_ref()
            await self.em.delta(f"- **{source['title']}** — {source['snippet']} [{ref}]\n")

    async def _plan(self) -> list[dict[str, str]]:
        step = await self.em.step_start("plan", "Planning research")
        schema = {
            "type": "object",
            "properties": {
                "sub_questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["question", "rationale"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["sub_questions"],
            "additionalProperties": False,
        }
        try:
            result = await structured_call(
                model=self.settings.model,
                system=PLAN_SYSTEM,
                prompt=f"{self._collections_note()}\n\nQuestion: {self.req.question}",
                schema=schema,
                effort="low",
            )
            plan = [
                {"question": p["question"].strip(), "rationale": p["rationale"].strip()}
                for p in result.get("sub_questions", [])
                if p.get("question", "").strip()
            ][:5]
        except Exception as exc:
            log.warning("Planning failed, continuing without a plan: %s", exc)
            plan = []
        if not plan:
            plan = [{"question": self.req.question, "rationale": "Answer the question directly."}]
        await self.em.step_update(
            step,
            label=f"Planned {len(plan)} sub-question{'s' if len(plan) != 1 else ''}",
            status="done",
            plan=plan,
        )
        return plan

    # ── the Claude tool loop ──────────────────────────────────────────────
    async def _run_llm(self, db) -> None:
        deep = self.req.mode == "deep"
        client = get_client()
        system = build_system(
            org_names=[o.name for o in self.scope.orgs.values()],
            collections_note=self._collections_note(),
            agent_instructions=self.agent.instructions if self.agent else None,
            deep=deep,
        )
        user_content = self.req.question
        if deep:
            plan = await self._plan()
            bullets = "\n".join(f"{i}. {p['question']}" for i, p in enumerate(plan, 1))
            user_content = f"{self.req.question}\n\n<research_plan>\n{bullets}\n</research_plan>"
        messages: list[dict[str, Any]] = [
            *await self._history(db),
            {"role": "user", "content": user_content},
        ]
        effort = self.settings.deep_effort if deep else self.settings.quick_effort
        max_turns = MAX_TURNS["deep" if deep else "quick"]
        json_retries = 0
        turn = 0

        while turn < max_turns:
            last_turn = turn == max_turns - 1
            kwargs: dict[str, Any] = {
                "model": self.settings.model,
                "max_tokens": 32000,
                "system": system,
                "messages": messages,
                "tools": TOOL_DEFINITIONS,
                "thinking": {"type": "adaptive", "display": "summarized"},
                "output_config": {"effort": effort},
                **request_extras(),
            }
            if last_turn:
                # Research budget spent: the model must answer with what it has.
                kwargs["tool_choice"] = {"type": "none"}

            reflect_step: str | None = None
            pending_text = ""
            streaming_answer = False
            try:
                async with client.beta.messages.stream(**kwargs) as stream:
                    async for event in stream:
                        if event.type == "thinking" and event.thinking:
                            if reflect_step is None:
                                reflect_step = await self.em.step_start("reflect", "Thinking")
                            await self.em.reasoning(reflect_step, event.thinking)
                        elif event.type == "text":
                            if streaming_answer:
                                await self.em.delta(event.text)
                            else:
                                pending_text += event.text
                                if len(pending_text) > PREAMBLE_CHARS:
                                    streaming_answer = True
                                    await self.em.delta(pending_text)
                                    pending_text = ""
                        elif (
                            event.type == "content_block_start"
                            and event.content_block.type == "tool_use"
                            and pending_text
                        ):
                            # A short preamble before a tool call is narration, not answer.
                            reflect_step = reflect_step or await self.em.step_start(
                                "reflect", "Thinking"
                            )
                            await self.em.reasoning(reflect_step, pending_text)
                            pending_text = ""
                    final = await stream.get_final_message()
            except ValueError:
                # Tool input JSON the SDK could not parse at all: re-issue the turn (bounded).
                json_retries += 1
                if json_retries > 2:
                    raise
                continue
            json_retries = 0
            turn += 1

            tool_uses = [b for b in final.content if b.type == "tool_use"]
            if pending_text:
                if tool_uses and not streaming_answer:
                    reflect_step = reflect_step or await self.em.step_start("reflect", "Thinking")
                    await self.em.reasoning(reflect_step, pending_text)
                else:
                    await self.em.delta(pending_text)
            if reflect_step:
                await self.em.step_update(reflect_step, label="Reasoned", status="done")

            messages.append({"role": "assistant", "content": final.content})

            if final.stop_reason == "pause_turn":
                continue
            if final.stop_reason == "refusal":
                if not self.em.answer:
                    await self.em.delta("I can't help with that request.")
                break
            if not tool_uses:
                break
            if final.stop_reason == "max_tokens":
                # A truncated tool input parses as a partial object; never run it.
                break

            results = [await self._run_tool(block) for block in tool_uses]
            messages.append({"role": "user", "content": results})

        if not self.em.answer.strip():
            await self.em.delta(
                "I wasn't able to put together an answer from the knowledge base. "
                "Try rephrasing the question or narrowing it to a collection."
            )

    async def _run_tool(self, block) -> dict[str, Any]:
        args = block.input if isinstance(block.input, dict) else {}
        try:
            if block.name == "search_knowledge":
                step = await self.em.step_start(
                    "search", "Searching knowledge", {"queries": args.get("queries") or []}
                )
                try:
                    outcome = await self._search(step, args)
                except ToolInputError as exc:
                    await self.em.step_update(step, label="Search failed", status="error")
                    raise exc
                content = outcome.text
            elif block.name == "read_document":
                step = await self.em.step_start("read", "Reading document")
                try:
                    content, entry = await self.tools.read(args)
                except ToolInputError as exc:
                    await self.em.step_update(step, label="Couldn't read document", status="error")
                    raise exc
                await self.em.step_update(
                    step,
                    label=f"Read “{entry.hit.title}”",
                    status="done",
                    document_title=entry.hit.title,
                    refs=[entry.ref],
                )
            elif block.name == "list_collections":
                step = await self.em.step_start("search", "Listing collections")
                content = await self.tools.list_collections()
                await self.em.step_update(step, label="Listed collections", status="done")
            else:
                raise ToolInputError(f"Unknown tool {block.name}")
        except ToolInputError as exc:
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "is_error": True,
                "content": str(exc),
            }
        return {"type": "tool_result", "tool_use_id": block.id, "content": content}

    # ── persistence ───────────────────────────────────────────────────────
    async def _persist(self, db, *, status: str, error: str | None = None) -> Message:
        message = await db.get(Message, self.req.assistant_message_id)
        content = self.em.answer
        if error and not content.strip():
            content = error
        message.content = content
        message.sources = [r.to_source_ref() for r in self.registry.all()]
        message.trace = self.em.trace
        message.status = status

        cited = [r for r in cited_refs(content) if self.registry.get(r)]
        for ref in cited:
            entry = self.registry.get(ref)
            db.add(
                MessageCitation(
                    message_id=message.id,
                    document_id=entry.hit.document_id,
                    org_id=entry.hit.org_id,
                    source_id=entry.hit.source_id,
                )
            )
        if self.registry or status == "complete":
            db.add(
                SearchEvent(
                    org_id=self.req.org_id,
                    user_id=self.req.user_id,
                    kind="chat",
                    query=self.req.question[:2000],
                    latency_ms=self.retrieval_ms,
                    hits=len(self.registry),
                )
            )
        conversation = await db.get(Conversation, self.req.conversation_id)
        if conversation:
            conversation.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(message)
        return message


async def run_chat(req: ChatRequest, queue: EventQueue) -> None:
    await AgentRun(req, queue).execute()


async def direct_search(db, *, user_id, org, org_slugs, collection_ids, query, limit):
    """LLM-free search used by POST /search."""
    scope = await resolve_scope(
        db, user_id=user_id, primary_org=org, org_slugs=org_slugs, collection_ids=collection_ids
    )
    hits = await hybrid_search(db, [query], scope.collection_ids, limit=limit, per_doc=1)
    registry = CitationRegistry()
    for h in hits:  # one result per document, best chunk first
        registry.register(h, scope.orgs[h.org_id], scope.collections[h.collection_id])
    return registry.all()
