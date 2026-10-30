"""Drives the real agent engine on the NVIDIA path with a scripted stand-in for the
streaming API: tool calls, reasoning, attachments and web search."""

import asyncio
import uuid

import pytest

from app.llm.nvidia import ChatTurn, ToolCall
from tests.test_api_integration import _ingest, _org, _signup

pytestmark = pytest.mark.integration


class FakeNvidia:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls: list[dict] = []

    def stream_chat(self, **kwargs):
        if not kwargs.get("tools"):
            # Helper calls (conversation title, research plan) run alongside the agent.
            events, turn = [], ChatTurn(content='{"title": "Leave", "sub_questions": []}')
        else:
            self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
            events, turn = self.turns.pop(0)

        async def gen():
            for event in events:
                yield event
            yield "done", turn

        return gen()


def _tool_turn(name: str, arguments: str, call_id: str = "call-1"):
    events = [("reasoning", "I should search first."), ("tool_start", name)]
    return events, ChatTurn(
        reasoning="I should search first.",
        tool_calls=[ToolCall(call_id, name, arguments)],
        finish_reason="tool_calls",
    )


def _answer_turn(text: str):
    events = [("text", text[:30]), ("text", text[30:])]
    return events, ChatTurn(content=text, finish_reason="stop")


async def _run(app, monkeypatch, turns, *, attachment: tuple[str, str] | None = None, web=False):
    from app.agent import engine
    from app.core.config import get_settings

    admin = await _signup(app, "nv")
    org = await _org(admin, "NV Co")
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    await _ingest(
        admin, slug, cols[0]["id"], {"leave.md": "# Leave policy\n\nStaff get 16 weeks of leave."}
    )

    fake = FakeNvidia(turns)
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", fake.stream_chat)
    if web:
        monkeypatch.setattr(get_settings(), "tavily_api_key", "tvly-test")

    conv = (await admin.post(f"/api/orgs/{slug}/conversations", json={})).json()
    attachment_ids = []
    if attachment:
        r = await admin.post(
            f"/api/orgs/{slug}/attachments",
            files={"file": (attachment[0], attachment[1].encode(), "text/markdown")},
        )
        assert r.status_code == 201, r.text
        attachment_ids = [r.json()["id"]]
    r = await admin.post(
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
        json={"content": "How much leave?", "attachment_ids": attachment_ids, "web_search": web},
    )
    return fake, r, admin, slug, conv


async def test_nvidia_tool_loop_searches_then_answers(app, monkeypatch):
    answer = "Staff get sixteen weeks of leave under the policy [1]."
    fake, r, admin, slug, conv = await _run(
        app,
        monkeypatch,
        [_tool_turn("search_knowledge", '{"queries": ["leave policy"]}'), _answer_turn(answer)],
    )
    assert r.status_code == 200, r.text
    message = r.json()["message"]
    assert message["status"] == "complete" and message["content"] == answer, message
    assert message["sources"], (message["trace"], fake.calls[1]["messages"][-1])
    assert message["sources"][0]["title"] == "Leave policy"
    kinds = [s["kind"] for s in message["trace"]]
    assert kinds == ["reflect", "search", "answer"]
    assert "search first" in message["trace"][0]["detail"]["reasoning"]

    first, second = fake.calls
    assert first["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert first["messages"][0]["role"] == "system" and first["tool_choice"] == "auto"
    assert {t["function"]["name"] for t in first["tools"]} == {
        "search_knowledge",
        "read_document",
        "list_collections",
    }
    tool_msg = second["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call-1"
    assert '<source ref="1"' in tool_msg["content"]
    assert second["messages"][-2]["tool_calls"][0]["function"]["name"] == "search_knowledge"

    # The org's stored Claude model resolves to NVIDIA when only NVIDIA has a key.
    org = (await admin.get(f"/api/orgs/{slug}")).json()
    assert org["effective_model"] == "nvidia/nemotron-3-ultra-550b-a55b"


async def test_malformed_tool_arguments_are_reported_back(app, monkeypatch):
    fake, r, *_ = await _run(
        app,
        monkeypatch,
        [_tool_turn("search_knowledge", '{"queries": ['), _answer_turn("I could not search.")],
    )
    assert r.status_code == 200
    tool_msg = fake.calls[1]["messages"][-1]
    assert tool_msg["content"].startswith("Error:") and "valid JSON" in tool_msg["content"]


async def test_attachment_is_inlined_and_citable(app, monkeypatch):
    answer = "The brief asks for a launch on 3 March [2]."
    fake, r, admin, slug, conv = await _run(
        app,
        monkeypatch,
        [_answer_turn(answer)],
        attachment=("brief.md", "# Launch brief\n\nLaunch on 3 March."),
    )
    assert r.status_code == 200, r.text
    message = r.json()["message"]
    user_content = fake.calls[0]["messages"][-1]["content"]
    assert '<attachment ref="1" filename="brief.md">' in user_content
    assert "Launch on 3 March" in user_content
    assert message["sources"][0]["origin"] == "attachment"

    detail = (await admin.get(f"/api/orgs/{slug}/conversations/{conv['id']}")).json()
    assert detail["messages"][0]["attachments"][0]["filename"] == "brief.md"
    listed = (await admin.get(f"/api/orgs/{slug}/conversations/{conv['id']}/attachments")).json()
    assert [a["filename"] for a in listed] == ["brief.md"]


async def test_web_search_tool_registers_web_sources(app, monkeypatch):
    from app.agent import tools
    from app.retrieval.web import WebResult

    async def fake_search(query, max_results=5):
        return [WebResult("NVIDIA news", "https://example.com/nv", "Nemotron 3 shipped.")]

    monkeypatch.setattr(tools, "search_web", fake_search)
    answer = "Nemotron 3 has shipped according to public reports [2]."
    fake, r, *_ = await _run(
        app,
        monkeypatch,
        [_tool_turn("search_web", '{"queries": ["nemotron 3 release"]}'), _answer_turn(answer)],
        web=True,
    )
    assert r.status_code == 200, r.text
    message = r.json()["message"]
    web = [s for s in message["sources"] if s["origin"] == "web"]
    assert web and web[0]["url"] == "https://example.com/nv" and web[0]["org_id"] is None
    assert any(s["kind"] == "web" and s["status"] == "done" for s in message["trace"])
    assert "search_web" in {t["function"]["name"] for t in fake.calls[0]["tools"]}


async def test_provider_error_maps_to_json_error(app, monkeypatch):
    from app.llm.nvidia import NvidiaError

    class Failing(FakeNvidia):
        def stream_chat(self, **kwargs):
            async def gen():
                raise NvidiaError(404, "Function not found")
                yield  # pragma: no cover

            return gen()

    from app.agent import engine
    from app.core.config import get_settings

    admin = await _signup(app, "nv")
    org = await _org(admin, "NV Err")
    monkeypatch.setattr(get_settings(), "nvidia_api_key", "nv-test")
    monkeypatch.setattr(engine.nvidia, "stream_chat", Failing([]).stream_chat)
    slug = org["slug"]
    cols = (await admin.get(f"/api/orgs/{slug}/collections")).json()
    await _ingest(admin, slug, cols[0]["id"], {"a.md": "# A\n\nText."})
    conv = (await admin.post(f"/api/orgs/{slug}/conversations", json={})).json()
    r = await admin.post(
        f"/api/orgs/{slug}/conversations/{conv['id']}/messages?stream=false",
        json={"content": "Hi"},
    )
    assert r.status_code == 502 and r.json()["code"] == "llm_model"
    await asyncio.sleep(0)
    detail = (await admin.get(f"/api/orgs/{slug}/conversations/{conv['id']}")).json()
    assert detail["messages"][-1]["status"] == "error"
    assert uuid.UUID(detail["messages"][-1]["id"])
