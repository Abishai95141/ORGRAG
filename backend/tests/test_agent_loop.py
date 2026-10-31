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


async def _run(
    app, monkeypatch, mode: str, turns, plan=None, fast_path=False, question="Friday deploys?"
):
    """Run the engine on one scripted conversation. The fast path is off unless asked
    for: these tests script the tool loop turn by turn."""
    from app.agent import engine
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "fast_path", fast_path)

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
        question=question,
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


def _cited_answer_turn():
    """Claude's answer as text blocks, the middle one cited (as the API returns them)."""
    cited = NS(
        type="text",
        text="Friday deploys need manager approval",
        citations=[NS(type="search_result_location", source="strata://source/1")],
    )
    blocks = [NS(type="text", text="Yes: ", citations=None), cited, NS(type="text", text=".")]
    events = []
    for b in blocks:
        events.append(NS(type="text", text=b.text))
        events.append(NS(type="content_block_stop", content_block=b))
    return events, NS(stop_reason="end_turn", content=blocks)


async def test_claude_citations_become_markers(app, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "claude_citations", True)
    fake, events = await _run(
        app, monkeypatch, "quick", [_tool_turn("friday deploy policy"), _cited_answer_turn()]
    )
    assert events[-1][0] == "done", events[-1]
    message = events[-1][1]["message"]
    assert message["content"] == "Yes: Friday deploys need manager approval [1]."
    assert message["sources"][0]["ref"] == 1
    # The passages went back as citable search_result blocks, one per sentence.
    (found,) = fake.calls[1]["messages"][-1]["content"][0]["content"]
    assert found["type"] == "search_result" and found["source"] == "strata://source/1"
    assert found["title"].startswith("[1] Deploy policy") and found["citations"]["enabled"]
    assert any("Friday deploys" in b["text"] for b in found["content"])
    assert "don't type their numbers" in fake.calls[0]["system"][0]["text"]


async def test_markers_the_model_also_wrote_are_not_doubled(app, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "claude_citations", True)
    cited = NS(
        type="text",
        text="Friday deploys need manager approval",
        citations=[NS(type="search_result_location", source="strata://source/1")],
    )
    tail = NS(type="text", text=" [1].", citations=None)
    events = [
        NS(type="text", text=cited.text),
        NS(type="content_block_stop", content_block=cited),
        NS(type="text", text=tail.text),
    ]
    turn = (events, NS(stop_reason="end_turn", content=[cited, tail]))
    _, events = await _run(app, monkeypatch, "quick", [_tool_turn("friday deploy"), turn])
    assert events[-1][1]["message"]["content"] == "Friday deploys need manager approval [1]."


async def test_without_native_citations_results_are_tagged_text(app, monkeypatch):
    answer = "Friday deploys need manager approval [1]."
    fake, _ = await _run(app, monkeypatch, "quick", [_tool_turn("friday"), _answer_turn(answer)])
    result = fake.calls[1]["messages"][-1]["content"][0]
    assert isinstance(result["content"], str) and '<source ref="1"' in result["content"]
    assert "list item and table row" in fake.calls[0]["system"][0]["text"]  # default rule


async def _with_coverage(app, monkeypatch, verdict):
    from app.agent import sufficiency
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "sufficiency_check", "always")
    calls = []

    async def fake_check(**kwargs):
        calls.append(kwargs)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict

    monkeypatch.setattr(sufficiency, "structured_call", fake_check)
    answer = "Friday deploys need manager approval [1]."
    fake, events = await _run(
        app, monkeypatch, "quick", [_tool_turn("friday deploy"), _answer_turn(answer)]
    )
    return fake, events, calls


async def test_coverage_check_tells_the_model_what_is_missing(app, monkeypatch):
    verdict = {"sufficient": False, "missing": ["who approves Friday deploys"]}
    fake, events, calls = await _with_coverage(app, monkeypatch, verdict)
    assert events[-1][0] == "done"
    # The check saw the question and the search results.
    assert "<question>\nFriday deploys?" in calls[0]["prompt"]
    assert "Friday deploys need manager approval" in calls[0]["prompt"]
    tool_result, note = fake.calls[1]["messages"][-1]["content"]
    assert tool_result["type"] == "tool_result" and note["type"] == "text"
    assert "<coverage_check>" in note["text"] and "who approves Friday deploys" in note["text"]
    labels = [d["label"] for e, d in events if e == "step" and d["status"] == "done"]
    assert "Missing: who approves Friday deploys" in labels


async def test_coverage_check_adds_nothing_when_sources_suffice(app, monkeypatch):
    fake, events, _ = await _with_coverage(app, monkeypatch, {"sufficient": True, "missing": []})
    assert [b["type"] for b in fake.calls[1]["messages"][-1]["content"]] == ["tool_result"]
    labels = [d["label"] for e, d in events if e == "step" and d["status"] == "done"]
    assert "Sources cover the question" in labels


async def test_a_failing_coverage_check_never_blocks_the_answer(app, monkeypatch):
    fake, events, _ = await _with_coverage(app, monkeypatch, RuntimeError("model down"))
    assert events[-1][0] == "done"
    assert [b["type"] for b in fake.calls[1]["messages"][-1]["content"]] == ["tool_result"]


async def test_coverage_check_is_off_in_quick_mode_by_default(app, monkeypatch):
    from app.agent import sufficiency

    async def fail(**kwargs):
        raise AssertionError("the check shouldn't run in quick mode by default")

    monkeypatch.setattr(sufficiency, "structured_call", fail)
    answer = "Friday deploys need manager approval [1]."
    fake, events = await _run(
        app, monkeypatch, "quick", [_tool_turn("friday"), _answer_turn(answer)]
    )
    assert events[-1][0] == "done"
    assert [b["type"] for b in fake.calls[1]["messages"][-1]["content"]] == ["tool_result"]


# ── quick-mode fast path ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("question", "eligible"),
    [
        ("Friday deploys?", True),
        ("How long is parental leave at Halcyon?", True),
        ("What does the PTO policy say about carryover?", True),  # acronyms aren't names
        ("How many vendors process PHI?", False),
        ("Compare Halcyon's leave policy with Northwind's.", False),
        ("Who approves changes to Driftwood and what does Ledger need?", False),
        ("Is the current on-call rota still the October one?", False),
        (" ".join(["word"] * 26), False),
    ],
)
def test_which_questions_take_the_fast_path(question, eligible):
    from app.agent import fastpath

    assert fastpath.eligible(question) is eligible


def test_a_confident_first_search(monkeypatch):
    from app.agent import fastpath
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "fast_path_min_top", 0.7)
    monkeypatch.setattr(get_settings(), "fast_path_min_gap", 0.2)
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    assert fastpath.confident([0.92, 0.6, 0.5, 0.4, 0.3], [a, b, c, a, b])
    assert not fastpath.confident([0.92, 0.9, 0.9, 0.85, 0.8], [a, b, c, a, b])  # no clear top
    assert not fastpath.confident([0.65, 0.2, 0.1, 0.1, 0.1], [a, b, c, a, b])  # too weak
    assert fastpath.confident([0.3, 0.2, 0.1], [a, a, a])  # three passages of one document
    assert not fastpath.confident([], [])
    # Without a reranker there's no probability: only the one-document rule applies.
    assert not fastpath.confident([None, None, None], [a, b, c])
    assert fastpath.confident([None, None, None], [a, a, a])


async def test_fast_path_answers_from_a_confident_first_search(app, monkeypatch):
    from app.agent import fastpath

    monkeypatch.setattr(fastpath, "confident", lambda scores, documents: True)
    answer = "Friday deploys need manager approval [1]."
    fake, events = await _run(app, monkeypatch, "quick", [_answer_turn(answer)], fast_path=True)
    assert events[-1][0] == "done" and events[-1][1]["message"]["content"] == answer
    (call,) = fake.calls  # one model call
    assert call["tool_choice"] == {"type": "none"}  # answer from the passages
    first = call["messages"][-1]["content"]
    assert "<first_search>" in first[0]["text"] and '<source ref="1"' in first[1]["text"]
    assert first[-1]["text"] == "Friday deploys?"
    labels = [d["label"] for e, d in events if e == "step" and d["status"] == "done"]
    assert labels[:2] == ["Found 1 source", "First search answers it"]


async def test_fast_path_seeds_the_loop_when_unsure(app, monkeypatch):
    from app.agent import fastpath

    monkeypatch.setattr(fastpath, "confident", lambda scores, documents: False)
    answer = "Friday deploys need manager approval [1]."
    fake, events = await _run(app, monkeypatch, "quick", [_answer_turn(answer)], fast_path=True)
    (call,) = fake.calls  # the model answered on its first turn, tools still offered
    assert "tool_choice" not in call and call["tools"]
    assert "<first_search>" in call["messages"][-1]["content"][0]["text"]
    assert "First search answers it" not in str(events)


async def test_multi_hop_questions_skip_the_fast_path(app, monkeypatch):
    answer = "Ask the manager [1]."
    fake, _ = await _run(
        app,
        monkeypatch,
        "quick",
        [_tool_turn("friday deploy policy"), _answer_turn(answer)],
        fast_path=True,
        question="Who approves Friday deploys and what does each team need?",
    )
    assert isinstance(fake.calls[0]["messages"][-1]["content"], str)  # no seeded search


async def test_an_empty_answer_from_the_fast_path_falls_back_to_the_loop(app, monkeypatch):
    """With tools off the model may want to search again and say nothing; the engine then
    researches normally, starting from the same passages."""
    from app.agent import fastpath

    monkeypatch.setattr(fastpath, "confident", lambda scores, documents: True)
    empty = ([], NS(stop_reason="end_turn", content=[]))
    answer = "Friday deploys need manager approval [1]."
    fake, events = await _run(
        app, monkeypatch, "quick", [empty, _answer_turn(answer)], fast_path=True
    )
    assert events[-1][1]["message"]["content"] == answer
    first, second = fake.calls
    assert first["tool_choice"] == {"type": "none"}
    assert "isn't possible" in first["messages"][-1]["content"][0]["text"]
    assert (
        "tool_choice" not in second
        and "keep researching" in (second["messages"][-1]["content"][0]["text"])
    )
