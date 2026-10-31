"""The NVIDIA streaming client against recorded-shape SSE responses (no network)."""

import json

import httpx
import pytest

from app.llm import nvidia
from app.llm.nvidia import parse_text_tool_call

# Verbatim from a Nemotron 3 Ultra eval run: the tool call arrived as content text.
LEAKED = (
    "<tool_call>\n<function=search_knowledge>\n<parameter=queries>\n"
    '["Bayline", "reference lab", "laboratory BAA"]\n</parameter>\n'
    '<parameter=collections>\n["Vendors"]\n</parameter>\n</function>\n</tool_call>\n'
)


def _sse(deltas: list[dict], finish: str = "stop") -> bytes:
    chunks = [{"choices": [{"index": 0, "delta": d}]} for d in deltas]
    chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return body.encode()


def _split(text: str, size: int = 7) -> list[dict]:
    """Content deltas in small pieces, so tags straddle chunk boundaries."""
    return [{"content": text[i : i + size]} for i in range(0, len(text), size)]


async def _run(monkeypatch, body: bytes):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    )
    monkeypatch.setattr(nvidia, "get_http", lambda provider="nvidia": client)
    events = [e async for e in nvidia.stream_chat(model="m", messages=[], tools=[{"x": 1}])]
    return events, events[-1][1]


def test_parse_text_tool_call_forms():
    name, args = parse_text_tool_call(LEAKED.split("<tool_call>")[1].split("</tool_call>")[0])
    assert name == "search_knowledge"
    assert json.loads(args) == {
        "queries": ["Bayline", "reference lab", "laboratory BAA"],
        "collections": ["Vendors"],
    }
    assert parse_text_tool_call('{"name": "read_document", "arguments": {"ref": 2}}') == (
        "read_document",
        '{"ref": 2}',
    )
    assert parse_text_tool_call("<function=read_document><parameter=ref>3</parameter>") == (
        "read_document",
        '{"ref": 3}',
    )
    assert parse_text_tool_call("just some words") is None


async def test_text_tool_call_becomes_a_real_call(monkeypatch):
    events, turn = await _run(monkeypatch, _sse(_split(LEAKED), finish="stop"))
    assert turn.content.strip() == ""
    assert [c.name for c in turn.tool_calls] == ["search_knowledge"]
    assert turn.tool_calls[0].parsed()["queries"][0] == "Bayline"
    assert ("tool_start", "search_knowledge") in events
    assert not any(k == "text" and "<tool_call>" in v for k, v in events)


async def test_text_around_a_text_tool_call_is_kept(monkeypatch):
    text = "Let me check. " + LEAKED
    _, turn = await _run(monkeypatch, _sse(_split(text)))
    assert turn.content.strip() == "Let me check." and len(turn.tool_calls) == 1


async def test_truncated_text_tool_call_is_dropped(monkeypatch):
    _, turn = await _run(monkeypatch, _sse(_split(LEAKED[:60]), finish="length"))
    assert turn.tool_calls == [] and "<tool_call>" not in turn.content


async def test_api_tool_calls_and_reasoning_still_work(monkeypatch):
    deltas = [
        {"reasoning_content": "I should search."},
        {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "search_knowledge"}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": '{"queries": '}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": '["leave"]}'}}]},
    ]
    _, turn = await _run(monkeypatch, _sse(deltas, finish="tool_calls"))
    assert turn.reasoning == "I should search."
    assert [(c.id, c.name, c.parsed()) for c in turn.tool_calls] == [
        ("c1", "search_knowledge", {"queries": ["leave"]})
    ]


@pytest.mark.parametrize("answer", ["Plain answer [1].", "Use <b>bold</b> tags [2]."])
async def test_ordinary_answers_pass_through(monkeypatch, answer):
    _, turn = await _run(monkeypatch, _sse(_split(answer, 3)))
    assert turn.content == answer and turn.tool_calls == []


def test_backoff_honours_retry_after_and_caps():
    from app.llm.nvidia import backoff_seconds

    assert backoff_seconds(1, "7") == 7
    assert backoff_seconds(1, None) == 2 and backoff_seconds(3, None) == 8
    assert backoff_seconds(9, None) == 30 and backoff_seconds(1, "600") == 30
    assert backoff_seconds(2, "Wed, 21 Oct 2026 07:28:00 GMT") == 4


async def test_concurrent_requests_are_capped(monkeypatch):
    import asyncio

    in_flight = peak = 0

    async def slow(request):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return httpx.Response(200, content=_sse(_split("ok")))

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    monkeypatch.setattr(nvidia, "get_http", lambda provider="nvidia": client)
    monkeypatch.setattr(nvidia, "_slots", {"nvidia": asyncio.Semaphore(2)})

    async def one():
        return [e async for e in nvidia.stream_chat(model="m", messages=[])]

    results = await asyncio.gather(*(one() for _ in range(6)))
    assert peak == 2 and all(r[-1][1].content == "ok" for r in results)
