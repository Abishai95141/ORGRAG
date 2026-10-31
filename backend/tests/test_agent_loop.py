"""Drives the real agent engine with a scripted stand-in for the Anthropic client to
check event mapping, tool execution, the plan step and persistence."""

import asyncio
import uuid
from types import SimpleNamespace as NS

import pytest

from tests.test_api_integration import _ingest, _org, _signup

pytestmark = pytest.mark.integration


class FakeStream:
    def __init__(self, events, final):
        self._events, self._final = events, final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e

        return gen()

    async def get_final_message(self):
        return self._final


class FakeClient:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls: list[dict] = []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        events, final = self.turns.pop(0)
        return FakeStream(events, final)


def _tool_turn(query: str):
    tool = NS(type="tool_use", id="toolu_1", name="search_knowledge", input={"queries": [query]})
    events = [
        NS(type="thinking", thinking="I should look up the deploy policy."),
        NS(type="text", text="Let me check."),
        NS(type="content_block_start", content_block=NS(type="tool_use")),
    ]
    final = NS(stop_reason="tool_use", content=[NS(type="text", text="Let me check."), tool])
    return events, final


def _answer_turn(text: str):
    events = [NS(type="text", text=text[:20]), NS(type="text", text=text[20:])]
    return events, NS(stop_reason="end_turn", content=[NS(type="text", text=text)])


async def _run(app, monkeypatch, mode: str, turns, plan=None):
    from app.agent import engine
    from app.core.config import get_settings

    admin = await _signup(app, "agent")
    org = await _org(admin, f"Agent Co {mode}")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    await _ingest(
        admin,
        slug,
        cols[0]["id"],
        {"deploys.md": "# Deploy policy\n\nFriday deploys need manager approval."},
    )

    fake = FakeClient(turns)
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "test-key")
    monkeypatch.setattr(engine, "get_client", lambda: fake)
    if plan is not None:

        async def fake_plan(**kwargs):
            return {"sub_questions": plan}

        monkeypatch.setattr(engine, "structured_call", fake_plan)

    conv = (await admin.post(f"/api/orgs/{slug}/conversations", json={"mode": mode})).json()
    me = (await admin.get("/api/auth/me")).json()["user"]
    from app.core.db import SessionLocal
    from app.models import Message

    async with SessionLocal() as db:
        db.add(Message(conversation_id=uuid.UUID(conv["id"]), role="user", content="Friday?"))
        reserved = Message(
            conversation_id=uuid.UUID(conv["id"]), role="assistant", status="streaming"
        )
        db.add(reserved)
        await db.commit()
    queue: asyncio.Queue = asyncio.Queue()
    req = engine.ChatRequest(
        conversation_id=uuid.UUID(conv["id"]),
        assistant_message_id=reserved.id,
        user_id=uuid.UUID(me["id"]),
        org_id=uuid.UUID(org["id"]),
        question="Friday deploys?",
        mode=mode,
        scope_org_ids=[uuid.UUID(org["id"])],
        collection_ids=None,
    )
    await engine.run_chat(req, queue)
    events = []
    while (item := queue.get_nowait()) is not None:
        events.append(item)
    return fake, events


async def test_quick_mode_tool_loop(app, monkeypatch):
    answer = "Friday deploys need approval from the on-call manager [1]."
    fake, events = await _run(
        app, monkeypatch, "quick", [_tool_turn("friday deploy policy"), _answer_turn(answer)]
    )
    names = [e for e, _ in events]
    assert names[-1] == "done", events[-1]

    # Short narration before the tool call is reasoning, not answer text.
    reasoning = "".join(d["text"] for e, d in events if e == "reasoning")
    assert "deploy policy" in reasoning and "Let me check." in reasoning
    assert "".join(d["text"] for e, d in events if e == "delta") == answer

    kinds = [d["kind"] for e, d in events if e == "step" and d["status"] == "done"]
    assert kinds == ["reflect", "search", "answer"]
    message = events[-1][1]["message"]
    assert message["content"] == answer and message["sources"][0]["title"] == "Deploy policy"

    # Second request carries the assistant turn unchanged plus the tool result.
    second = fake.calls[1]["messages"]
    assert second[-2]["role"] == "assistant"
    result = second[-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert '<source ref="1"' in result["content"]
    call = fake.calls[0]
    assert call["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert call["output_config"] == {"effort": "low"} and "tool_choice" not in call
    assert "temperature" not in call
    # Prompt caching: tools + system, then the growing loop.
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["cache_control"] == {"type": "ephemeral"}


async def test_deep_mode_plans_then_researches(app, monkeypatch):
    plan = [
        {"question": "What is the deploy window?", "rationale": "Core rule"},
        {"question": "Who approves Friday deploys?", "rationale": "Exception path"},
    ]
    answer = "Deploys run Monday–Thursday; Friday needs manager approval [1]."
    fake, events = await _run(
        app, monkeypatch, "deep", [_tool_turn("deploy window"), _answer_turn(answer)], plan=plan
    )
    steps = [d for e, d in events if e == "step" and d["status"] == "done"]
    assert steps[0]["kind"] == "plan" and steps[0]["detail"]["plan"] == plan
    assert "<research_plan>" in fake.calls[0]["messages"][-1]["content"]
    assert fake.calls[0]["output_config"] == {"effort": "high"}


async def test_unknown_tool_input_is_reported_to_model(app, monkeypatch):
    bad = NS(type="tool_use", id="toolu_x", name="read_document", input={"ref": 99})
    turn1 = ([], NS(stop_reason="tool_use", content=[bad]))
    fake, events = await _run(app, monkeypatch, "quick", [turn1, _answer_turn("No luck here.")])
    result = fake.calls[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "no source [99]" in result["content"].lower()
    assert events[-1][0] == "done"


async def test_tool_calls_in_one_turn_run_concurrently(app, monkeypatch):
    from app.agent.tools import ToolBox

    calls = [
        NS(type="tool_use", id=f"toolu_{i}", name="search_knowledge", input={"queries": [q]})
        for i, q in enumerate(["friday deploy policy", "deploy approval"])
    ]
    turn1 = ([], NS(stop_reason="tool_use", content=calls))
    started, sessions = [], []
    both_started = asyncio.Event()
    original = ToolBox.search

    async def search(self, args):
        # Each call waits until the other has started: run one at a time, this times out.
        started.append(args["queries"][0])
        sessions.append(self.db)
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=5)
        return await original(self, args)

    monkeypatch.setattr(ToolBox, "search", search)
    fake, events = await _run(app, monkeypatch, "quick", [turn1, _answer_turn("Done [1].")])
    assert events[-1][0] == "done", events[-1]
    assert len(sessions) == 2 and sessions[0] is not sessions[1]  # one session per call
    results = fake.calls[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["toolu_0", "toolu_1"]  # call order kept
    assert all('<source ref="' in r["content"] for r in results)
