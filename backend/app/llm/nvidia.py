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
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger("strata.llm.nvidia")

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


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


@lru_cache
def get_http() -> httpx.AsyncClient:
    settings = get_settings()
    return httpx.AsyncClient(
        headers={"Authorization": f"Bearer {settings.nvidia_api_key}"},
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


class _ThinkSplitter:
    """Routes `<think>…</think>` spans in streamed content to reasoning."""

    def __init__(self) -> None:
        self.buffer = ""
        self.thinking = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        self.buffer += text
        out: list[tuple[str, str]] = []
        while self.buffer:
            tag = "</think>" if self.thinking else "<think>"
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
                if emit:
                    out.append(("reasoning" if self.thinking else "text", emit))
                break
            if idx:
                out.append(("reasoning" if self.thinking else "text", self.buffer[:idx]))
            self.buffer = self.buffer[idx + len(tag) :]
            self.thinking = not self.thinking
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest, self.buffer = self.buffer, ""
        return [("reasoning" if self.thinking else "text", rest)] if rest else []


async def stream_chat(
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | None = None,
    max_tokens: int = 8192,
    temperature: float = 0.3,
) -> AsyncIterator[tuple[str, Any]]:
    settings = get_settings()
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if tools:
        body["tools"] = tools
        if tool_choice:
            body["tool_choice"] = tool_choice
    url = f"{settings.nvidia_base_url}/chat/completions"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        async with get_http().stream(
            "POST", url, json=body, headers={"Accept": "text/event-stream"}
        ) as response:
            if response.status_code != 200:
                message = _error_message(await response.aread())
                if response.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                    delay = float(response.headers.get("retry-after") or 2 * attempt)
                    log.info("NVIDIA %s, retrying in %.0fs", response.status_code, delay)
                    await asyncio.sleep(min(delay, 20))
                    continue
                raise NvidiaError(response.status_code, message)

            turn = ChatTurn()
            calls: dict[int, ToolCall] = {}
            splitter = _ThinkSplitter()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        turn.reasoning += reasoning
                        yield "reasoning", reasoning
                    if delta.get("content"):
                        for kind, text in splitter.feed(delta["content"]):
                            if kind == "text":
                                turn.content += text
                            else:
                                turn.reasoning += text
                            yield kind, text
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", len(calls))
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
            for kind, text in splitter.flush():
                if kind == "text":
                    turn.content += text
                else:
                    turn.reasoning += text
                yield kind, text
            turn.tool_calls = [calls[i] for i in sorted(calls) if calls[i].name]
            yield "done", turn
            return


async def complete(
    *, model: str, system: str, prompt: str, max_tokens: int = 2048, temperature: float = 0.2
) -> str:
    """Non-streaming convenience wrapper: the final text of one turn."""
    turn = ChatTurn()
    async for kind, value in stream_chat(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        temperature=temperature,
    ):
        if kind == "done":
            turn = value
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
        response = await get_http().post(
            url, json=body, headers={"Accept": "application/json"}, timeout=30
        )
        if response.status_code == 200:
            break
        if response.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
            await asyncio.sleep(attempt)
            continue
        raise NvidiaError(response.status_code, _error_message(response.content))
    scores = [float("-inf")] * len(passages)
    for item in response.json().get("rankings", []):
        scores[item["index"]] = float(item["logit"])
    return scores
