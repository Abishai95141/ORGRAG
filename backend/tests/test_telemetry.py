"""Tracing: what a chat answer exports, and what it keeps out of traces."""

import base64
import dataclasses
import uuid

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import telemetry
from app.core.config import get_settings
from tests.test_agent_nvidia import _answer_turn, _run, _tool_turn

EXPORTER = InMemorySpanExporter()


@pytest.fixture
def spans():
    # The global tracer provider can be set once per process; every test shares it.
    telemetry.setup("strata-test", exporter=EXPORTER)
    EXPORTER.clear()
    yield EXPORTER
    EXPORTER.clear()


def _with_usage(turn, input_tokens, output_tokens):
    events, chat_turn = turn
    return events, dataclasses.replace(
        chat_turn, usage={"input": input_tokens, "output": output_tokens}
    )


def test_exporter_config_prefers_langfuse(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "langfuse_public_key", None)
    monkeypatch.setattr(s, "langfuse_secret_key", None)
    monkeypatch.setattr(s, "otel_exporter_otlp_endpoint", None)
    assert telemetry.exporter_config() is None

    monkeypatch.setattr(s, "otel_exporter_otlp_endpoint", "http://collector:4318/")
    monkeypatch.setattr(s, "otel_exporter_otlp_headers", "x-team=search, api-key=abc")
    assert telemetry.exporter_config() == (
        "http://collector:4318/v1/traces",
        {"x-team": "search", "api-key": "abc"},
    )

    monkeypatch.setattr(s, "langfuse_public_key", "pk-lf-1")
    monkeypatch.setattr(s, "langfuse_secret_key", "sk-lf-2")
    monkeypatch.setattr(s, "langfuse_host", "https://langfuse.internal/")
    endpoint, headers = telemetry.exporter_config()
    assert endpoint == "https://langfuse.internal/api/public/otel/v1/traces"
    assert base64.b64decode(headers["Authorization"].removeprefix("Basic ")) == b"pk-lf-1:sk-lf-2"
    assert headers["x-langfuse-ingestion-version"] == "4"


async def test_a_chat_answer_is_one_trace_with_turns_tools_and_usage(app, monkeypatch, spans):
    answer = "Staff get sixteen weeks of leave under the policy [1]."
    turns = [
        _with_usage(_tool_turn("search_knowledge", '{"queries": ["leave policy"]}'), 900, 40),
        _with_usage(_answer_turn(answer), 1500, 25),
    ]
    _, r, admin, slug, conv = await _run(app, monkeypatch, turns)
    message = r.json()["message"]
    assert message["status"] == "complete"

    by_name = {}
    for s in spans.get_finished_spans():
        by_name.setdefault(s.name, []).append(s)
    (chat,) = by_name["chat"]
    turns_ = by_name["llm turn"]
    (tool,) = by_name["tool search_knowledge"]
    (retrieve,) = by_name["retrieve"]

    # One trace: the answer is its root (not the HTTP request), everything else nests.
    assert chat.parent is None
    trace_id = chat.context.trace_id
    assert all(s.context.trace_id == trace_id for s in [*turns_, tool, retrieve])
    assert tool.parent.span_id == chat.context.span_id
    assert retrieve.parent.span_id == tool.context.span_id

    a = chat.attributes
    assert a["langfuse.observation.type"] == "agent"
    assert a["langfuse.session.id"] == conv["id"]
    assert a["strata.org_id"] == a["langfuse.trace.metadata.org_id"]
    assert a["langfuse.trace.metadata.org"] == slug
    assert a["strata.status"] == "complete" and a["strata.sources"] == 1
    assert (a["gen_ai.usage.input_tokens"], a["gen_ai.usage.output_tokens"]) == (2400, 65)
    assert [t.attributes["gen_ai.usage.input_tokens"] for t in turns_] == [900, 1500]
    assert all(t.attributes["langfuse.observation.type"] == "generation" for t in turns_)
    assert tool.attributes["strata.tool.error"] is False
    assert retrieve.attributes["strata.hits"] >= 1

    # Text stays out of traces unless TRACE_CONTENT is on.
    exported = [v for s in spans.get_finished_spans() for v in s.attributes.values()]
    assert not any(isinstance(v, str) and "sixteen weeks" in v for v in exported)
    assert "langfuse.observation.input" not in a

    # Usage is saved on the message, for spend per org without a tracing backend.
    from app.core.db import SessionLocal
    from app.models import Message

    async with SessionLocal() as db:
        saved = await db.get(Message, uuid.UUID(message["id"]))
    (model_usage,) = saved.usage.values()
    assert model_usage == {"input": 2400, "output": 65, "calls": 2}
    (row,) = (await admin.get(f"/api/orgs/{slug}/insights")).json()["usage"]
    assert row["answers"] == 1 and row["calls"] == 2
    assert (row["input_tokens"], row["output_tokens"]) == (2400, 65)


async def test_content_is_traced_only_when_enabled(app, monkeypatch, spans):
    monkeypatch.setattr(get_settings(), "trace_content", True)
    answer = "Staff get sixteen weeks of leave [1]."
    await _run(
        app,
        monkeypatch,
        [_tool_turn("search_knowledge", '{"queries": ["leave policy"]}'), _answer_turn(answer)],
    )
    by_name = {s.name: s for s in spans.get_finished_spans()}
    assert by_name["chat"].attributes["langfuse.observation.input"] == "How much leave?"
    assert by_name["chat"].attributes["langfuse.observation.output"] == answer
    assert (
        "leave policy" in by_name["tool search_knowledge"].attributes["gen_ai.tool.call.arguments"]
    )
    assert by_name["retrieve"].attributes["langfuse.observation.input"] == "leave policy"


async def test_a_failed_answer_marks_its_trace(app, monkeypatch, spans):
    from opentelemetry.trace import StatusCode

    # No scripted turns: the fake model raises on its first call.
    _, r, *_ = await _run(app, monkeypatch, [])
    assert r.status_code >= 500, r.text
    chat = next(s for s in spans.get_finished_spans() if s.name == "chat")
    assert chat.attributes["strata.status"] == "error"
    turn = next(s for s in spans.get_finished_spans() if s.name == "llm turn")
    assert turn.status.status_code == StatusCode.ERROR
