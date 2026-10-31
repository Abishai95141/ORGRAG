"""The agentic RAG engine.

One run answers one user message. It plans (deep mode), drives a tool loop over the
scoped knowledge base (plus attached files, and the web when enabled) on the org's
model — Claude through the Anthropic API or an open model through NVIDIA's API —
streams every step to the client as SSE events (see docs/API.md), and persists the
final assistant message — even if the browser disconnects mid-answer, because the
run executes in its own task and session.

Event flow:  step(plan) → step(search/read/reflect)* + reasoning* + sources* → delta* → done
"""

import asyncio
import contextlib
import contextvars
import copy
import dataclasses
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from sqlalchemy import select

from app.agent import records_tool, runs, sufficiency
from app.agent.citations import (
    CitationRegistry,
    citation_markers,
    cited_refs,
    collapse_repeated_markers,
    strip_citations,
)
from app.agent.prompts import PLAN_SYSTEM, build_system
from app.agent.sanitize import strip_images
from app.agent.tools import (
    READ_PAGE_CHARS,
    ToolBox,
    ToolInputError,
    openai_tools,
    tool_definitions,
)
from app.core import audit, telemetry
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.llm import nvidia, pricing
from app.llm.client import describe_error, get_client, request_extras, structured_call
from app.llm.models import resolve_model
from app.models import (
    Agent,
    ChatAttachment,
    Conversation,
    KGMention,
    Message,
    MessageCitation,
    Organization,
    SearchEvent,
)
from app.records import query as records_query
from app.retrieval.access import RetrievalScope, resolve_scope
from app.retrieval.hybrid import RankingOptions, hybrid_search
from app.schemas.common import MessageOut, OrgSettings

log = logging.getLogger("strata.agent")

MAX_TURNS = {"quick": 4, "deep": 10}
HISTORY_MESSAGES = 12
PREAMBLE_CHARS = 160  # text shorter than this before a tool call is treated as reasoning
# A tool result: text, or (Claude path) a list of search_result blocks.
ToolContent = str | list[dict[str, Any]]
MAX_PARALLEL_TOOLS = 4  # tool calls from one turn running at once (each holds a DB session)
# Tools whose results are evidence for the sufficient-context check.
EVIDENCE_TOOLS = frozenset({"search_knowledge", "read_document", "search_web"})
MAX_COVERAGE_CHECKS = 2  # after the first research round, and after a follow-up

# The toolbox (with its DB session) of the tool call running in the current task.
_bound_tools: contextvars.ContextVar[ToolBox | None] = contextvars.ContextVar(
    "bound_tools", default=None
)
MAX_REASONING_CHARS = 8000
INLINE_ATTACHMENT_CHARS = 40_000  # across all files attached to one message
NVIDIA_MAX_TOKENS = {"quick": 8192, "deep": 16384}
ANSWER_NOW = (
    "Write the final answer now, using only the sources above and citing them as [n]. "
    "If they don't answer the question, say so."
)

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
    attachment_ids: list[uuid.UUID] = field(default_factory=list)
    web_search: bool | None = None


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
        self.usage: dict[str, dict[str, int]] = {}  # per model: input/output tokens, calls
        # Claude path: search and read results go back as citable search_result blocks,
        # and Claude's citations become [n] markers (CLAUDE_CITATIONS).
        self.native_citations = False
        self.coverage_checks = 0
        self.status = "streaming"

    def _add_usage(self, model: str, usage: dict[str, int] | None) -> None:
        totals = self.usage.setdefault(model, {"input": 0, "output": 0, "calls": 0})
        totals["calls"] += 1
        for key, value in (usage or {}).items():
            totals[key] = totals.get(key, 0) + value
        if (spent := pricing.cost(model, usage)) is not None:  # priced per call
            totals["cost_usd"] = round(totals.get("cost_usd", 0) + spent, 6)

    def _trace_attrs(self) -> dict[str, Any]:
        r = self.req
        attrs = {
            "langfuse.trace.name": "chat",
            "langfuse.user.id": str(r.user_id),
            "langfuse.session.id": str(r.conversation_id),
            "langfuse.trace.metadata.mode": r.mode,
            "langfuse.trace.metadata.message_id": str(r.assistant_message_id),
            "langfuse.trace.metadata.scope_orgs": len(r.scope_org_ids or []),
            **telemetry.org_attrs(r.org_id),
        }
        if telemetry.capture_content():
            attrs["langfuse.observation.input"] = r.question
        return attrs

    def _trace_result(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "strata.status": self.status,
            "langfuse.trace.metadata.status": self.status,
            "strata.sources": len(self.registry),
            "strata.retrieval_ms": self.retrieval_ms,
            "strata.answer_chars": len(self.em.answer),
            # [n] markers that match no source: an unsupported claim (audit C3).
            "strata.unknown_refs": sum(
                1 for r in cited_refs(self.em.answer) if self.registry.get(r) is None
            ),
            "strata.native_citations": self.native_citations,
            "gen_ai.usage.input_tokens": sum(u["input"] for u in self.usage.values()),
            "gen_ai.usage.output_tokens": sum(u["output"] for u in self.usage.values()),
        }
        if org := getattr(self, "org", None):
            attrs["langfuse.trace.metadata.org"] = org.slug
        if telemetry.capture_content():
            attrs["langfuse.observation.output"] = self.em.answer
        return attrs

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
        attachments = (
            (
                await db.execute(
                    select(ChatAttachment)
                    .where(
                        ChatAttachment.conversation_id == self.req.conversation_id,
                        ChatAttachment.user_id == self.req.user_id,
                    )
                    .order_by(ChatAttachment.created_at)
                )
            )
            .scalars()
            .all()
        )
        self.attachments = {a.id: a for a in attachments}
        wanted_web = (
            self.req.web_search if self.req.web_search is not None else self.settings.web_search
        )
        self.web_enabled = bool(wanted_web and get_settings().web_search_provider)
        # Graph tools only when enabled and something in scope has been extracted.
        self.graph_enabled = bool(
            self.settings.knowledge_graph
            and self.scope.collections
            and (
                await db.execute(
                    select(KGMention.id)
                    .where(KGMention.collection_id.in_(self.scope.collection_ids))
                    .limit(1)
                )
            ).first()
        )
        # Structured records: query_records when an active schema covers the scope.
        self.record_schemas = await records_query.schemas_in_scope(db, self.scope.collection_ids)
        self._toolbox = ToolBox(
            None,  # bound per tool call by _tool_session
            scope,
            self.registry,
            top_k=self.settings.retrieval_top_k,
            ranking=RankingOptions(
                rerank=self.settings.rerank, recency_bias=self.settings.recency_bias
            ),
            attachments=self.attachments,
            graph=self.graph_enabled,
            record_schemas=self.record_schemas,
        )

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
        # Its own trace: the answer can outlive the request that started it.
        with telemetry.span("chat", "agent", root=True, **self._trace_attrs()) as root:
            try:
                await self._lifecycle()
            finally:
                telemetry.set_attrs(root, **self._trace_result())
                if self.status == "error":  # failures are caught and saved, not raised
                    telemetry.fail(root, "answer failed")

    async def _lifecycle(self) -> None:
        beat = asyncio.create_task(runs.heartbeat(self.req.assistant_message_id))
        try:
            async with asyncio.timeout(get_settings().chat_run_timeout_seconds):
                await self._execute()
        except asyncio.CancelledError:
            # Stopped by the user (runs.cancel): keep what was written so far.
            await self.em.fail_running()
            payload: dict[str, Any] = {}
            try:
                async with SessionLocal() as clean_db:
                    message = await self._persist(clean_db, status="stopped")
                    payload = {"message": message_payload(message)}
            except Exception:
                log.exception("Could not persist stopped message")
            await self.em.queue.put(("stopped", payload))
            await self.em.queue.put(None)
            raise
        except TimeoutError:
            await self._fail(TimeoutError())
            await self.em.queue.put(None)
        finally:
            beat.cancel()

    async def _fail(self, exc: BaseException) -> None:
        detail, code = describe_error(exc)
        await self.em.fail_running()
        try:
            async with SessionLocal() as clean_db:
                await self._persist(clean_db, status="error", error=detail)
        except Exception:
            log.exception("Could not persist failed message")
        await self.em.queue.put(("error", {"detail": detail, "code": code}))

    async def _execute(self) -> None:
        # No database connection is held while the model generates (audit P2): loading,
        # each tool call and the final save each use their own short session.
        try:
            llm = get_settings().llm_configured
            async with SessionLocal() as db:
                await self._load(db)
                history = await self._history(db) if llm else []
            if not self.scope.collections and not self.attachments and not self.web_enabled:
                await self.em.delta(
                    "There's no knowledge I can search in this scope yet. Add a source "
                    "under **Sources**, or ask an admin for access to a collection."
                )
            elif llm:
                await self._run_llm(history)
            elif self.scope.collections:
                await self._run_retrieval_only()
            else:
                await self.em.delta(
                    "**Answer generation is off** — set `NVIDIA_API_KEY` or "
                    "`ANTHROPIC_API_KEY` so Strata can read attached files and the web."
                )
            await self.em.finish_answer()
            async with SessionLocal() as db:
                message = await self._persist(db, status="complete")
                await self.em.queue.put(("done", {"message": message_payload(message)}))
        except Exception as exc:
            log.exception("Agent run failed")
            await self._fail(exc)
        await self.em.queue.put(None)

    @property
    def tools(self) -> ToolBox:
        """The toolbox bound to the tool call running in this task."""
        return _bound_tools.get() or self._toolbox

    @contextlib.asynccontextmanager
    async def _tool_session(self):
        """A session for one tool call. Calls from one model turn run concurrently, each
        in its own task with its own session; they share the citation registry."""
        async with SessionLocal() as db:
            token = _bound_tools.set(dataclasses.replace(self._toolbox, db=db))
            try:
                yield
            finally:
                _bound_tools.reset(token)

    async def _run_tools(
        self, calls: list[tuple[str, dict[str, Any]]]
    ) -> list[tuple[ToolContent, bool]]:
        """Run one turn's tool calls concurrently (at most MAX_PARALLEL_TOOLS at once);
        results come back in call order."""
        if len(calls) == 1:
            return [await self._run_tool(*calls[0])]
        sem = asyncio.Semaphore(MAX_PARALLEL_TOOLS)

        async def one(name: str, args: dict[str, Any]) -> tuple[ToolContent, bool]:
            async with sem:
                return await self._run_tool(name, args)

        return list(await asyncio.gather(*(one(n, a) for n, a in calls)))

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

    async def _run_retrieval_only(self) -> None:
        step = await self.em.step_start(
            "search", "Searching knowledge", {"queries": [self.req.question]}
        )
        async with self._tool_session():
            outcome = await self._search(step, {"queries": [self.req.question]})
        if not outcome.refs:
            await self.em.delta(
                "I couldn't find anything relevant in the knowledge base for that question."
            )
            return
        await self.em.delta(
            "**Answer generation is off** — no LLM API key (`NVIDIA_API_KEY` or "
            "`ANTHROPIC_API_KEY`) is configured, so here are the most relevant passages "
            "Strata found:\n\n"
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
            with telemetry.span(
                "plan", "generation", **{"gen_ai.request.model": self.settings.model}
            ):
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

    # ── the tool loop ─────────────────────────────────────────────────────
    def _attachment_context(self) -> str:
        """Registers every file in the conversation as a citable source. Files attached
        to this message are inlined (up to a budget); earlier ones are listed so the
        model can read them on demand."""
        if not self.attachments:
            return ""
        budget = INLINE_ATTACHMENT_CHARS
        blocks, earlier = [], []
        new_ids = set(self.req.attachment_ids)
        for attachment in self.attachments.values():
            entry, _ = self.registry.register_attachment(attachment)
            if attachment.id not in new_ids:
                earlier.append(f"[{entry.ref}] {attachment.filename}")
                continue
            body = attachment.content
            take = min(len(body), max(0, budget), READ_PAGE_CHARS)
            budget -= take
            more = (
                f"\n… (truncated — use read_document with ref {entry.ref} and a page number "
                "to read the rest)"
                if take < len(body)
                else ""
            )
            # Escaped like tool results, so a file can't close the block and speak as
            # the user (audit S3).
            blocks.append(
                f'<attachment ref="{entry.ref}" filename={quoteattr(attachment.filename)}>\n'
                f"{escape(body[:take])}{more}\n</attachment>"
            )
        parts = []
        if blocks:
            parts.append("Files attached to this message (cite them as [n]):\n" + "\n".join(blocks))
        if earlier:
            parts.append(
                "Files attached earlier in this conversation (read with read_document): "
                + ", ".join(earlier)
            )
        return "\n\n".join(parts)

    async def _run_llm(self, history: list[dict[str, Any]]) -> None:
        deep = self.req.mode == "deep"
        provider, model = resolve_model(self.settings.model)
        self.native_citations = (
            provider not in nvidia.OPENAI_COMPATIBLE and get_settings().claude_citations
        )
        system = build_system(
            org_names=[o.name for o in self.scope.orgs.values()],
            collections_note=self._collections_note(),
            agent_instructions=self.agent.instructions if self.agent else None,
            deep=deep,
            web=self.web_enabled,
            graph=self.graph_enabled,
            native_citations=self.native_citations,
            records=records_tool.describe(
                self.record_schemas, self.scope.collections, self.scope.orgs
            ),
        )
        user_content = self.req.question
        attachment_context = self._attachment_context()
        await self.em.sources([r.to_source_ref() for r in self.registry.all()])
        if deep:
            plan = await self._plan()
            bullets = "\n".join(f"{i}. {p['question']}" for i, p in enumerate(plan, 1))
            user_content = f"{self.req.question}\n\n<research_plan>\n{bullets}\n</research_plan>"
        if attachment_context:
            user_content = f"{attachment_context}\n\n{user_content}"
        definitions = tool_definitions(
            web=self.web_enabled, graph=self.graph_enabled, records=bool(self.record_schemas)
        )
        if provider in nvidia.OPENAI_COMPATIBLE:
            self.provider = provider
            await self._nvidia_loop(model, system, history, user_content, definitions, deep)
        else:
            await self._claude_loop(model, system, history, user_content, definitions, deep)
        if not self.em.answer.strip():
            await self.em.delta(
                "I wasn't able to put together an answer from the knowledge base. "
                "Try rephrasing the question or narrowing it to a collection."
            )

    async def _claude_loop(self, model, system, history, user_content, definitions, deep) -> None:
        client = get_client()
        messages: list[dict[str, Any]] = [*history, {"role": "user", "content": user_content}]
        effort = self.settings.deep_effort if deep else self.settings.quick_effort
        max_turns = MAX_TURNS["deep" if deep else "quick"]
        json_retries = 0
        turn = 0
        evidence: list[str] = []  # search and read results so far, for the coverage check

        caching = get_settings().prompt_caching
        system_blocks: str | list[dict[str, Any]] = system
        if caching:
            # A breakpoint on the system prompt caches tools + system for every turn and
            # every question with the same scope; the top-level one caches the loop so
            # far (each turn re-reads the previous turns' tool results from cache).
            system_blocks = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]

        while turn < max_turns:
            last_turn = turn == max_turns - 1
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": 32000,
                "system": system_blocks,
                "messages": messages,
                "tools": definitions,
                "thinking": {"type": "adaptive", "display": "summarized"},
                "output_config": {"effort": effort},
                **request_extras(),
            }
            if caching:
                kwargs["cache_control"] = {"type": "ephemeral"}
            if last_turn:
                # Research budget spent: the model must answer with what it has. (Changing
                # tool_choice misses the messages cache for this one turn; Haiku 5.5 has
                # no mid-conversation system messages to say it another way.)
                kwargs["tool_choice"] = {"type": "none"}

            reflect_step: str | None = None
            pending_text = ""
            streaming_answer = False
            generation = telemetry.start(
                "llm turn",
                "generation",
                **{"gen_ai.request.model": model, "gen_ai.system": "anthropic"},
            )

            async def emit(text: str) -> None:
                # Answer text, held back while it's short enough to be a preamble.
                nonlocal pending_text, streaming_answer
                if streaming_answer:
                    await self.em.delta(text)
                    return
                pending_text += text
                if len(pending_text) > PREAMBLE_CHARS:
                    streaming_answer = True
                    await self.em.delta(pending_text)
                    pending_text = ""

            try:
                async with client.beta.messages.stream(**kwargs) as stream:
                    async for event in stream:
                        if event.type == "thinking" and event.thinking:
                            if reflect_step is None:
                                reflect_step = await self.em.step_start("reflect", "Thinking")
                            await self.em.reasoning(reflect_step, event.thinking)
                        elif event.type == "text":
                            await emit(event.text)
                        elif (
                            event.type == "content_block_stop"
                            and event.content_block.type == "text"
                            and getattr(event.content_block, "citations", None)
                        ):
                            # Claude cited this block: mark it with the sources' [n].
                            block = event.content_block
                            if markers := citation_markers(block.text, block.citations):
                                await emit(markers)
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
                usage = _claude_usage(final)
                telemetry.record_usage(generation, usage)
                telemetry.set_attrs(generation, **{"gen_ai.response.finish_reasons": [
                    final.stop_reason or ""]})  # fmt: skip
                self._add_usage(model, usage)
            except ValueError as exc:
                # Tool input JSON the SDK could not parse at all: re-issue the turn (bounded).
                telemetry.fail(generation, exc)
                json_retries += 1
                if json_retries > 2:
                    raise
                continue
            except BaseException as exc:
                telemetry.fail(generation, exc)
                raise
            finally:
                generation.end()
            json_retries = 0
            turn += 1

            tool_uses = [b for b in final.content if b.type == "tool_use"]
            await self._settle_turn_text(
                pending_text, reflect_step, streaming_answer, has_tools=bool(tool_uses)
            )

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

            results = []
            outcomes = await self._run_tools(
                [(b.name, b.input if isinstance(b.input, dict) else {}) for b in tool_uses]
            )
            for block, (content, is_error) in zip(tool_uses, outcomes, strict=True):
                result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
                if is_error:
                    result["is_error"] = True
                results.append(result)
                if not is_error and block.name in EVIDENCE_TOOLS:
                    evidence.append(content if isinstance(content, str) else json.dumps(content))
            searched = any(b.name in EVIDENCE_TOOLS for b in tool_uses)
            if searched and (hint := await self._coverage_note(evidence)):
                results.append({"type": "text", "text": hint})  # after the tool results
            messages.append({"role": "user", "content": results})

    async def _coverage_note(self, evidence: list[str]) -> str | None:
        """Run the sufficient-context check (SUFFICIENCY_CHECK: deep mode, always or off;
        at most MAX_COVERAGE_CHECKS per answer) and return the note for the model, if
        facts are missing."""
        mode = get_settings().sufficiency_check
        on = mode == "always" or (mode == "deep" and self.req.mode == "deep")
        if not on or self.coverage_checks >= MAX_COVERAGE_CHECKS:
            return None
        self.coverage_checks += 1
        step = await self.em.step_start("reflect", "Checking the sources cover the question")
        with telemetry.span("coverage check", "evaluator"):
            verdict = await sufficiency.check(self.req.question, "\n\n".join(evidence))
        if verdict is None or verdict["sufficient"] or not verdict["missing"]:
            label = "Sources cover the question" if verdict else "Coverage check skipped"
            await self.em.step_update(step, label=label, status="done")
            return None
        await self.em.step_update(
            step,
            label="Missing: " + "; ".join(verdict["missing"]),
            status="done",
            missing=verdict["missing"],
        )
        return sufficiency.note(verdict["missing"])

    async def _nvidia_turn(
        self, model: str, messages: list[dict[str, Any]], tools, tool_choice: str, deep: bool
    ) -> nvidia.ChatTurn:
        """Stream one model turn into the trace/answer; returns the completed turn."""
        result = nvidia.ChatTurn()
        provider = getattr(self, "provider", "nvidia")
        generation = telemetry.start(
            "llm turn", "generation", **{"gen_ai.request.model": model, "gen_ai.system": provider}
        )
        try:
            result = await self._stream_turn(provider, model, messages, tools, tool_choice, deep)
        except BaseException as exc:
            telemetry.fail(generation, exc)
            raise
        finally:
            telemetry.record_usage(generation, result.usage)
            telemetry.set_attrs(
                generation, **{"gen_ai.response.finish_reasons": [result.finish_reason or ""]}
            )
            generation.end()
        self._add_usage(model, result.usage)
        return result

    async def _stream_turn(
        self, provider: str, model: str, messages, tools, tool_choice: str, deep: bool
    ) -> nvidia.ChatTurn:
        reflect_step: str | None = None
        pending_text = ""
        streaming_answer = False
        result = nvidia.ChatTurn()
        async for kind, value in nvidia.stream_chat(
            provider=provider,
            model=model,
            messages=messages,
            # A turn that must answer gets no tools at all: offered tools with
            # tool_choice "none", Nemotron sometimes keeps planning tool calls and writes
            # nothing.
            tools=None if tool_choice == "none" else tools,
            tool_choice=None if tool_choice == "none" else tool_choice,
            max_tokens=NVIDIA_MAX_TOKENS["deep" if deep else "quick"],
        ):
            if kind == "reasoning":
                if reflect_step is None:
                    reflect_step = await self.em.step_start("reflect", "Thinking")
                await self.em.reasoning(reflect_step, value)
            elif kind == "text":
                if streaming_answer:
                    await self.em.delta(value)
                else:
                    pending_text += value
                    if len(pending_text) > PREAMBLE_CHARS:
                        streaming_answer = True
                        await self.em.delta(pending_text)
                        pending_text = ""
            elif kind == "tool_start" and pending_text and not streaming_answer:
                reflect_step = reflect_step or await self.em.step_start("reflect", "Thinking")
                await self.em.reasoning(reflect_step, pending_text)
                pending_text = ""
            elif kind == "done":
                result = value
        if tool_choice == "none":
            result.tool_calls = []
        await self._settle_turn_text(
            pending_text, reflect_step, streaming_answer, has_tools=bool(result.tool_calls)
        )
        return result

    async def _nvidia_loop(self, model, system, history, user_content, definitions, deep) -> None:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": user_content},
        ]
        tools = openai_tools(definitions)
        max_turns = MAX_TURNS["deep" if deep else "quick"]
        if self.graph_enabled and not deep:
            max_turns += 1  # list → explore → answer needs one more step than search alone
        researched = False
        retried_empty_start = False

        for turn in range(max_turns):
            # Research budget spent: the model must answer with what it has.
            tool_choice = "none" if turn == max_turns - 1 else "auto"
            result = await self._nvidia_turn(model, messages, tools, tool_choice, deep)
            calls = result.tool_calls
            if (
                not calls
                and not researched
                and not retried_empty_start
                and not result.content.strip()
                and not self.em.answer.strip()
            ):
                # Seen in the eval: Nemotron occasionally opens with an empty turn (no
                # text, no tool call). Ask again once, tools still on.
                retried_empty_start = True
                continue
            if not calls or result.finish_reason == "length":
                # No tool calls means the answer is complete; a truncated turn may hold
                # partial tool arguments, which must never run.
                break

            researched = True
            messages.append(result.assistant_message())
            parsed: dict[int, dict[str, Any]] = {}
            for i, call in enumerate(calls):
                with contextlib.suppress(ValueError):
                    parsed[i] = call.parsed()
            ran = iter(await self._run_tools([(calls[i].name, a) for i, a in parsed.items()]))
            for i, call in enumerate(calls):
                content, is_error = (
                    next(ran)
                    if i in parsed
                    else ("The tool arguments were not valid JSON. Send a JSON object.", True)
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": f"Error: {content}" if is_error else content,
                    }
                )

        if researched and not self.em.answer.strip():
            # Open models occasionally end a research loop with an empty turn. Ask once
            # for the answer from what was gathered, with tools off.
            messages.append({"role": "user", "content": ANSWER_NOW})
            await self._nvidia_turn(model, messages, tools, "none", deep)

    async def _settle_turn_text(
        self, pending_text: str, reflect_step: str | None, streaming_answer: bool, has_tools: bool
    ) -> None:
        """Short text held back at the end of a turn: narration if tools follow,
        otherwise the (short) answer."""
        if pending_text:
            if has_tools and not streaming_answer:
                reflect_step = reflect_step or await self.em.step_start("reflect", "Thinking")
                await self.em.reasoning(reflect_step, pending_text)
            else:
                await self.em.delta(pending_text)
        if reflect_step:
            await self.em.step_update(reflect_step, label="Reasoned", status="done")

    async def _run_tool(self, name: str, args: dict[str, Any]) -> tuple[ToolContent, bool]:
        """Run one tool call. Returns (content, is_error); input problems are reported
        back to the model so it can correct itself."""
        attrs: dict[str, Any] = {"gen_ai.tool.name": name}
        if telemetry.capture_content():
            attrs["gen_ai.tool.call.arguments"] = json.dumps(args, default=str)
        with telemetry.span(f"tool {name}", "tool", **attrs) as span:
            async with self._tool_session():
                content, is_error = await self._dispatch_tool(name, args)
            flat = content if isinstance(content, str) else json.dumps(content)
            telemetry.set_attrs(
                span,
                **{
                    "strata.tool.error": is_error,
                    "strata.tool.result_chars": len(flat),
                    "gen_ai.tool.call.result": flat[:8000] if telemetry.capture_content() else None,
                },
            )
            return content, is_error

    async def _dispatch_tool(self, name: str, args: dict[str, Any]) -> tuple[ToolContent, bool]:
        try:
            if name == "search_knowledge":
                step = await self.em.step_start(
                    "search", "Searching knowledge", {"queries": args.get("queries") or []}
                )
                try:
                    outcome = await self._search(step, args)
                except ToolInputError:
                    await self.em.step_update(step, label="Search failed", status="error")
                    raise
                if self.native_citations and outcome.results:
                    return outcome.results, False
                return outcome.text, False
            if name == "search_web" and self.web_enabled:
                step = await self.em.step_start(
                    "web", "Searching the web", {"queries": args.get("queries") or []}
                )
                started = time.perf_counter()
                try:
                    outcome = await self.tools.search_web(args)
                except ToolInputError:
                    await self.em.step_update(step, label="Web search failed", status="error")
                    raise
                self.retrieval_ms += int((time.perf_counter() - started) * 1000)
                await self.em.sources([s.to_source_ref() for s in outcome.new_sources])
                n = len(outcome.refs)
                await self.em.step_update(
                    step,
                    label=f"Found {n} web result{'s' if n != 1 else ''}" if n else "No web results",
                    status="done",
                    queries=outcome.queries,
                    refs=outcome.refs,
                )
                return outcome.text, False
            if name == "read_document":
                step = await self.em.step_start("read", "Reading document")
                try:
                    content, entry, result = await self.tools.read(args)
                except ToolInputError:
                    await self.em.step_update(step, label="Couldn't read document", status="error")
                    raise
                await self.em.step_update(
                    step,
                    label=f"Read “{entry.title}”",
                    status="done",
                    document_title=entry.title,
                    refs=[entry.ref],
                )
                return (result if self.native_citations else content), False
            if name in ("list_entities", "explore_entity") and self.graph_enabled:
                listing = name == "list_entities"
                step = await self.em.step_start(
                    "search",
                    "Listing entities" if listing else f"Exploring “{args.get('name', '')}”",
                )
                started = time.perf_counter()
                try:
                    outcome = await (
                        self.tools.list_entities(args)
                        if listing
                        else self.tools.explore_entity(args)
                    )
                except ToolInputError:
                    await self.em.step_update(step, label="Graph lookup failed", status="error")
                    raise
                self.retrieval_ms += int((time.perf_counter() - started) * 1000)
                await self.em.sources([s.to_source_ref() for s in outcome.new_sources])
                await self.em.step_update(
                    step,
                    label=(
                        f"Listed {outcome.hits} entities"
                        if listing
                        else f"Explored {args.get('name', '')}"
                    ),
                    status="done",
                    queries=outcome.queries,
                    refs=outcome.refs,
                )
                return outcome.text, False
            if name == "query_records" and self.record_schemas:
                step = await self.em.step_start(
                    "records", "Querying records", {"record_type": args.get("record_type")}
                )
                started = time.perf_counter()
                try:
                    outcome = await self.tools.query_records(args)
                except ToolInputError:
                    await self.em.step_update(step, label="Records query failed", status="error")
                    raise
                self.retrieval_ms += int((time.perf_counter() - started) * 1000)
                await self.em.sources([s.to_source_ref() for s in outcome.new_sources])
                await self.em.step_update(
                    step,
                    label=f"Found {outcome.hits} record{'s' if outcome.hits != 1 else ''}",
                    status="done",
                    queries=outcome.queries,
                    refs=outcome.refs,
                )
                return outcome.text, False
            if name == "list_collections":
                step = await self.em.step_start("search", "Listing collections")
                content = await self.tools.list_collections()
                await self.em.step_update(step, label="Listed collections", status="done")
                return content, False
            raise ToolInputError(f"Unknown tool {name}")
        except ToolInputError as exc:
            return str(exc), True

    # ── persistence ───────────────────────────────────────────────────────
    async def _persist(self, db, *, status: str, error: str | None = None) -> Message:
        message = await db.get(Message, self.req.assistant_message_id)
        content = collapse_repeated_markers(strip_images(self.em.answer))
        if error and not content.strip():
            content = error
        message.content = content
        message.sources = [r.to_source_ref() for r in self.registry.all()]
        message.trace = self.em.trace
        message.status = status
        message.usage = self.usage or None
        self.status = status

        cited = [
            r
            for r in cited_refs(content)
            if (e := self.registry.get(r)) is not None and e.origin == "knowledge"
        ]
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
        await audit.record_answer(db, self.req, message)  # each org whose documents it read
        conversation = await db.get(Conversation, self.req.conversation_id)
        if conversation:
            conversation.updated_at = datetime.now(UTC)
        await db.commit()
        await db.refresh(message)
        return message


def _claude_usage(final) -> dict[str, int] | None:
    """Token usage of a Claude turn; None when absent (observability never fails a run)."""
    u = getattr(final, "usage", None)
    if u is None:
        return None
    return {
        "input": getattr(u, "input_tokens", 0) or 0,
        "output": getattr(u, "output_tokens", 0) or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }


async def run_chat(req: ChatRequest, queue: EventQueue) -> None:
    await AgentRun(req, queue).execute()


async def direct_search(db, *, user_id, org, org_slugs, collection_ids, query, limit):
    """LLM-free search used by POST /search."""
    scope = await resolve_scope(
        db, user_id=user_id, primary_org=org, org_slugs=org_slugs, collection_ids=collection_ids
    )
    settings = OrgSettings(**(org.settings or {}))
    hits = await hybrid_search(
        db,
        [query],
        scope.collection_ids,
        limit=limit,
        per_doc=1,
        ranking=RankingOptions(rerank=settings.rerank, recency_bias=settings.recency_bias),
    )
    registry = CitationRegistry()
    for h in hits:  # one result per document, best chunk first
        registry.register(h, scope.orgs[h.org_id], scope.collections[h.collection_id])
    return registry.all()
