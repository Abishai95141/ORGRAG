"""NVIDIA API (build.nvidia.com) client: OpenAI-compatible chat completions with
streaming tool calls, plus the hosted reranker.

`stream_chat` normalizes the wire format into a small event vocabulary the agent
engine consumes: ("reasoning", text) · ("text", text) · ("tool_start", name) and a
final ("done", ChatTurn). Reasoning arrives as `reasoning_content` on Nemotron
models; models that inline `<think>…</think>` in content are split the same way."""

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger("strata.llm.nvidia")

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
MAX_BACKOFF_S = 30


def backoff_seconds(attempt: int, retry_after: str | None) -> float:
    """Honour Retry-After when the API sends one; else exponential (2, 4, 8, 16 s)."""
    try:
        if retry_after:
            return min(float(retry_after), MAX_BACKOFF_S)
    except ValueError:
        pass
    return min(2.0**attempt, MAX_BACKOFF_S)


class NvidiaError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"NVIDIA API error {status}: {message}")
        self.status = status
        self.message = message


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str = ""

    def parsed(self) -> dict[str, Any]:
        """Arguments as a dict. Raises ValueError on malformed JSON."""
        if not self.arguments.strip():
            return {}
        value = json.loads(self.arguments)
        if not isinstance(value, dict):
            raise ValueError("tool arguments must be a JSON object")
        return value


@dataclass
class ChatTurn:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] | None = None  # input / output tokens, when the API reports them

    def assistant_message(self) -> dict[str, Any]:
        """The turn as an OpenAI-format assistant message for the next request."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": c.arguments or "{}"},
                }
                for c in self.tool_calls
            ]
        return message


# This client speaks the OpenAI-compatible chat API, so it also serves other
# OpenAI-compatible providers ("explabs": the Experiential Labs gateway).
OPENAI_COMPATIBLE = ("nvidia", "explabs")

_slots: dict[str, asyncio.Semaphore] = {}


def _endpoint(provider: str) -> tuple[str, str | None]:
    s = get_settings()
    if provider == "explabs":
        return s.explabs_base_url, s.explabs_api_key
    return s.nvidia_base_url, s.nvidia_api_key


def request_slot(provider: str = "nvidia") -> asyncio.Semaphore:
    """Caps requests in flight per provider in this process (NVIDIA_MAX_CONCURRENCY).
    API keys are rate limited per minute, so a burst — several chats plus graph
    extraction — queues here briefly instead of failing with 429s."""
    if provider not in _slots:
        _slots[provider] = asyncio.Semaphore(get_settings().nvidia_max_concurrency)
    return _slots[provider]


@lru_cache
def get_http(provider: str = "nvidia") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {_endpoint(provider)[1]}"},
        timeout=httpx.Timeout(180.0, connect=15.0),
    )


def _error_message(body: bytes) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body.decode(errors="replace")[:300]
    if isinstance(data, dict):
        for key in ("detail", "message", "error"):
            value = data.get(key)
            if isinstance(value, dict):
                value = value.get("message")
            if value:
                return str(value)[:300]
    return str(data)[:300]


class _TagSplitter:
    """Splits streamed content into ("text", …) and (`inner`, …) pieces, where `inner`
    is whatever sits between `open_tag` and `close_tag`. Inside spans are passed on as
    they stream when `stream_inner`; otherwise each whole span is emitted once closed."""

    def __init__(self, open_tag: str, close_tag: str, inner: str, stream_inner: bool = True):
        self.tags = (open_tag, close_tag)
        self.inner = inner
        self.stream_inner = stream_inner
        self.buffer = ""
        self.span = ""
        self.inside = False

    def _emit(self, text: str, out: list[tuple[str, str]]) -> None:
        if not text:
            return
        if not self.inside:
            out.append(("text", text))
        elif self.stream_inner:
            out.append((self.inner, text))
        else:
            self.span += text

    def feed(self, text: str) -> list[tuple[str, str]]:
        self.buffer += text
        out: list[tuple[str, str]] = []
        while self.buffer:
            tag = self.tags[1] if self.inside else self.tags[0]
            idx = self.buffer.find(tag)
            if idx == -1:
                # Hold back a possible partial tag at the end.
                keep = next(
                    (n for n in range(len(tag) - 1, 0, -1) if self.buffer.endswith(tag[:n])), 0
                )
                emit, self.buffer = (
                    self.buffer[: len(self.buffer) - keep],
                    self.buffer[len(self.buffer) - keep :],
                )
                self._emit(emit, out)
                break
            self._emit(self.buffer[:idx], out)
            self.buffer = self.buffer[idx + len(tag) :]
            if self.inside and not self.stream_inner:
                out.append((self.inner, self.span))
                self.span = ""
            self.inside = not self.inside
        return out

    def flush(self) -> list[tuple[str, str]]:
        """Remaining text. An unclosed non-streamed span (a truncated tool call) is
        dropped: running half a tool call is worse than running none."""
        out: list[tuple[str, str]] = []
        rest, self.buffer = self.buffer, ""
        self._emit(rest, out)
        self.span = ""
        return out


class _ThinkSplitter(_TagSplitter):
    """Routes `<think>…</think>` spans in streamed content to reasoning."""

    def __init__(self) -> None:
        super().__init__("<think>", "</think>", "reasoning")


_XML_FUNCTION = re.compile(r"<function=([\w.-]+)>(.*?)(?:</function>|$)", re.S)
_XML_PARAM = re.compile(r"<parameter=([\w.-]+)>\s*(.*?)\s*</parameter>", re.S)


def parse_text_tool_call(body: str) -> tuple[str, str] | None:
    """(name, JSON arguments) for a tool call a model wrote as text inside
    `<tool_call>…</tool_call>` instead of using the tool-calling API. Handles the XML
    parameter form (`<function=name><parameter=x>value</parameter>`) and the JSON form
    (`{"name": …, "arguments": {…}}`). Returns None when it isn't recognisable."""
    body = body.strip()
    if m := _XML_FUNCTION.search(body):
        args: dict[str, Any] = {}
        for key, raw in _XML_PARAM.findall(m.group(2)):
            try:
                args[key] = json.loads(raw)
            except ValueError:
                args[key] = raw
        return m.group(1), json.dumps(args)
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("name"), str):
        arguments = data.get("arguments", data.get("parameters", {}))
        if isinstance(arguments, str):
            return data["name"], arguments
        return data["name"], json.dumps(arguments if isinstance(arguments, dict) else {})
    return None


def _route(
    pieces: list[tuple[str, str]],
    turn: "ChatTurn",
    calls: dict[int, ToolCall],
    tool_text: _TagSplitter,
    model: str,
) -> Iterator[tuple[str, str]]:
    """Accumulate split content into the turn; text-written tool calls become calls."""
    for kind, text in pieces:
        if kind == "reasoning":
            turn.reasoning += text
            yield kind, text
            continue
        for sub_kind, sub_text in tool_text.feed(text):
            if sub_kind == "text":
                turn.content += sub_text
                yield "text", sub_text
            elif parsed := parse_text_tool_call(sub_text):
                name, arguments = parsed
                idx = len([k for k in calls if k < 1000])
                calls[idx] = ToolCall(f"call_text_{idx}", name, arguments)
                yield "tool_start", name
            else:
                log.warning("Unparseable text tool call from %s: %.200s", model, sub_text)


async def stream_chat(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
    max_tokens: int = 8192,
    temperature: float = 0.3,
    provider: str = "nvidia",
) -> AsyncIterator[tuple[str, Any]]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
        # The last chunk then carries token usage (for tracing and per-org spend).
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
    url = f"{_endpoint(provider)[0]}/chat/completions"

    delay = 0.0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if delay:  # back off outside the slot so other requests can use it
            await asyncio.sleep(delay)
        async with (
            request_slot(provider),
            get_http(provider).stream(
                "POST", url, json=body, headers={"Accept": "text/event-stream"}
            ) as response,
        ):
            if response.status_code != 200:
                message = _error_message(await response.aread())
                if response.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                    delay = backoff_seconds(attempt, response.headers.get("retry-after"))
                    log.info("NVIDIA %s, retrying in %.0fs", response.status_code, delay)
                    continue
                raise NvidiaError(response.status_code, message)

            turn = ChatTurn()
            calls: dict[int, ToolCall] = {}
            splitter = _ThinkSplitter()
            # Some models (seen with Nemotron) occasionally write a tool call into the
            # content as text instead of using tool_calls; route those to real calls
            # rather than showing them to the reader as the answer.
            tool_text = _TagSplitter("<tool_call>", "</tool_call>", "tool", stream_inner=False)

            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                if usage := chunk.get("usage"):
                    turn.usage = {
                        "input": usage.get("prompt_tokens") or 0,
                        "output": usage.get("completion_tokens") or 0,
                    }
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        turn.reasoning += reasoning
                        yield "reasoning", reasoning
                    if delta.get("content"):
                        for event in _route(
                            splitter.feed(delta["content"]), turn, calls, tool_text, model
                        ):
                            yield event
                    for tc in delta.get("tool_calls") or []:
                        idx = 1000 + tc.get("index", 0)
                        fn = tc.get("function") or {}
                        if idx not in calls:
                            calls[idx] = ToolCall(id=tc.get("id") or f"call_{idx}", name="")
                        call = calls[idx]
                        if fn.get("name") and not call.name:
                            call.name = fn["name"]
                            yield "tool_start", call.name
                        call.arguments += fn.get("arguments") or ""
                    if choice.get("finish_reason"):
                        turn.finish_reason = choice["finish_reason"]
            for event in _route(splitter.flush(), turn, calls, tool_text, model):
                yield event
            for kind, text in tool_text.flush():
                turn.content += text
                yield kind, text
            turn.tool_calls = [calls[i] for i in sorted(calls) if calls[i].name]
            yield "done", turn
            return


async def complete(
    *,
    model: str,
    system: str,
    prompt: str,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    provider: str = "nvidia",
    on_usage: Callable[[str, dict[str, int]], None] | None = None,
) -> str:
    """Non-streaming convenience wrapper: the final text of one turn. `on_usage(model,
    usage)` gets its token usage when the API reports it."""
    turn = ChatTurn()
    async for kind, value in stream_chat(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
        provider=provider,
    ):
        if kind == "done":
            turn = value
    if on_usage is not None and turn.usage:
        on_usage(model, turn.usage)
    return turn.content


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """The first JSON object in a model reply (tolerates code fences and prose)."""
    match = _JSON_OBJECT.search(text)
    if not match:
        raise ValueError("no JSON object in reply")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("reply is not a JSON object")
    return value


async def rerank(query: str, passages: list[str]) -> list[float]:
    """Relevance logits for each passage, in input order."""
    settings = get_settings()
    model = settings.nvidia_rerank_model
    url = f"{settings.nvidia_rerank_url}/{model}/reranking"
    body = {
        "model": model,
        "query": {"text": query},
        "passages": [{"text": p} for p in passages],
        "truncate": "END",
    }
    for attempt in range(1, MAX_ATTEMPTS + 1):
        async with request_slot():
            response = await get_http().post(
                url, json=body, headers={"Accept": "application/json"}, timeout=30
            )
        if response.status_code == 200:
            break
        if response.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
            await asyncio.sleep(backoff_seconds(attempt, response.headers.get("retry-after")))
            continue
        raise NvidiaError(response.status_code, _error_message(response.content))
    scores = [float("-inf")] * len(passages)
    for item in response.json().get("rankings", []):
        scores[item["index"]] = float(item["logit"])
    return scores
