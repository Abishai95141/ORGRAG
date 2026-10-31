"""A Claude provider for Google's LangExtract, which ships Gemini, OpenAI and Ollama
providers only.

LangExtract does the parts that make extraction trustworthy: chunking long documents,
few-shot prompting, and aligning every extracted value back to its character offsets
in the source text. This class only answers its prompts with Claude. Passed to
`langextract.extract(model=...)` directly, so it needs no plugin registration.

No sampling parameters are sent (API version 2026-10-21 rejects them) and thinking is
off: extraction is reading, not reasoning. Usage is totalled for cost reporting."""

import threading
from collections.abc import Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import anthropic
from langextract.core.base_model import BaseLanguageModel
from langextract.core.types import ScoredOutput

from app.llm import pricing

MAX_TOKENS = 8000


class ClaudeLanguageModel(BaseLanguageModel):
    def __init__(self, model_id: str, api_key: str, max_workers: int = 4, **kwargs: Any):
        super().__init__(**{k: v for k, v in kwargs.items() if k == "constraint"})
        self.model_id = model_id
        self.max_workers = max_workers
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self._lock = threading.Lock()
        self.usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        self.calls = 0
        self.cost_usd = 0.0

    def _complete(self, prompt: str) -> str:
        message = self._client.messages.create(
            model=self.model_id,
            max_tokens=MAX_TOKENS,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": prompt}],
        )
        u = message.usage
        call = {
            "input": u.input_tokens or 0,
            "output": u.output_tokens or 0,
            "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        with self._lock:
            self.calls += 1
            for k, v in call.items():
                self.usage[k] += v
            self.cost_usd += pricing.cost(self.model_id, call) or 0.0
        return next((b.text for b in message.content if b.type == "text"), "")

    def infer(
        self, batch_prompts: Sequence[str], **kwargs: Any
    ) -> Iterator[Sequence[ScoredOutput]]:
        if len(batch_prompts) == 1:
            outputs = [self._complete(batch_prompts[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(self.max_workers, len(batch_prompts))) as ex:
                outputs = list(ex.map(self._complete, batch_prompts))
        for text in outputs:
            yield [ScoredOutput(score=1.0, output=text)]
