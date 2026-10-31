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
import copy
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from sqlalchemy import select

from app.agent import runs
from app.agent.citations import CitationRegistry, cited_refs, strip_citations
from app.agent.prompts import PLAN_SYSTEM, build_system
from app.agent.sanitize import strip_images
from app.agent.tools import (
    READ_PAGE_CHARS,
    ToolBox,
    ToolInputError,
    openai_tools,
    tool_definitions,
)
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.llm import nvidia
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
from app.retrieval.access import RetrievalScope, resolve_scope
from app.retrieval.hybrid import RankingOptions, hybrid_search
from app.schemas.common import MessageOut, OrgSettings

log = logging.getLogger("strata.agent")

MAX_TURNS = {"quick": 4, "deep": 10}
HISTORY_MESSAGES = 12
PREAMBLE_CHARS = 160  # text shorter than this before a tool call is treated as reasoning
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
        self.tools = ToolBox(
            db,
            scope,
            self.registry,
            top_k=self.settings.retrieval_top_k,
            ranking=RankingOptions(
                rerank=self.settings.rerank, recency_bias=self.settings.recency_bias
            ),
            attachments=self.attachments,
            graph=self.graph_enabled,
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
        async with SessionLocal() as db:
            try:
                await self._load(db)
                if not self.scope.collections and not self.attachments and not self.web_enabled:
                    await self.em.delta(
                        "There's no knowledge I can search in this scope yet. Add a source "
                        "under **Sources**, or ask an admin for access to a collection."
                    )
                elif get_settings().llm_configured:
                    await self._run_llm(db)
                elif self.scope.collections:
                    await self._run_retrieval_only(db)
                else:
                    await self.em.delta(
                        "**Answer generation is off** — set `NVIDIA_API_KEY` or "
                        "`ANTHROPIC_API_KEY` so Strata can read attached files and the web."
                    )
                await self.em.finish_answer()
                message = await self._persist(db, status="complete")
                await self.em.queue.put(("done", {"message": message_payload(message)}))
            except Exception as exc:
                log.exception("Agent run failed")
                await self._fail(exc)
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

    async def _run_llm(self, db) -> None:
        deep = self.req.mode == "deep"
        provider, model = resolve_model(self.settings.model)
        system = build_system(
            org_names=[o.name for o in self.scope.orgs.values()],
            collections_note=self._collections_note(),
            agent_instructions=self.agent.instructions if self.agent else None,
            deep=deep,
            web=self.web_enabled,
            graph=self.graph_enabled,
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
        history = await self._history(db)
        definitions = tool_definitions(web=self.web_enabled, graph=self.graph_enabled)
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

        while turn < max_turns:
            last_turn = turn == max_turns - 1
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": 32000,
                "system": system,
                "messages": messages,
                "tools": definitions,
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
            for block in tool_uses:
                args = block.input if isinstance(block.input, dict) else {}
                content, is_error = await self._run_tool(block.name, args)
                result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
                if is_error:
                    result["is_error"] = True
                results.append(result)
            messages.append({"role": "user", "content": results})

    async def _nvidia_turn(
        self, model: str, messages: list[dict[str, Any]], tools, tool_choice: str, deep: bool
    ) -> nvidia.ChatTurn:
        """Stream one model turn into the trace/answer; returns the completed turn."""
        reflect_step: str | None = None
        pending_text = ""
        streaming_answer = False
        result = nvidia.ChatTurn()
        async for kind, value in nvidia.stream_chat(
            provider=getattr(self, "provider", "nvidia"),
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

        for turn in range(max_turns):
            # Research budget spent: the model must answer with what it has.
            tool_choice = "none" if turn == max_turns - 1 else "auto"
            result = await self._nvidia_turn(model, messages, tools, tool_choice, deep)
            calls = result.tool_calls
            if not calls or result.finish_reason == "length":
                # No tool calls means the answer is complete; a truncated turn may hold
                # partial tool arguments, which must never run.
                break

            researched = True
            messages.append(result.assistant_message())
            for call in calls:
                try:
                    args = call.parsed()
                except ValueError:
                    content, is_error = (
                        "The tool arguments were not valid JSON. Send a JSON object.",
                        True,
                    )
                else:
                    content, is_error = await self._run_tool(call.name, args)
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

    async def _run_tool(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Run one tool call. Returns (content, is_error); input problems are reported
        back to the model so it can correct itself."""
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
                    content, entry = await self.tools.read(args)
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
                return content, False
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
        content = strip_images(self.em.answer)
        if error and not content.strip():
            content = error
        message.content = content
        message.sources = [r.to_source_ref() for r in self.registry.all()]
        message.trace = self.em.trace
        message.status = status

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
