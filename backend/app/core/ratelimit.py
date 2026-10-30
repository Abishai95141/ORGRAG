"""In-process sliding-window rate limiter (per API process).

Used for login attempts: it slows down password guessing without any infrastructure.
Behind several API replicas, put a shared limiter (e.g. at the proxy) in front."""

import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def retry_after(self, key: str) -> int:
        """Seconds until `key` may try again; 0 when allowed (and the hit is recorded)."""
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(1, int(self.window - (now - hits[0])) + 1)
        hits.append(now)
        return 0

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
