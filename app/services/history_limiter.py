"""Small in-process guard for expensive MT5 history calls."""

from __future__ import annotations

import asyncio
import threading
import time
from collections import defaultdict, deque
from typing import Any

from ..core.exceptions import RateLimitError
from ..core.security import client_identifier


class HistoryLimiter:
    """Per-client request rate limit plus a global concurrency cap."""

    def __init__(self, settings: Any):
        self.settings = settings
        self.max_calls = int(getattr(settings, "HISTORY_RATE_LIMIT_PER_MINUTE", 30))
        self.window = 60.0
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._semaphore = asyncio.Semaphore(
            max(1, int(getattr(settings, "HISTORY_MAX_CONCURRENT", 2)))
        )

    def _allow(self, client_id: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            calls = self._calls[client_id]
            while calls and now - calls[0] >= self.window:
                calls.popleft()
            if len(calls) >= self.max_calls:
                retry = max(1, int(self.window - (now - calls[0])) + 1)
                return False, retry
            calls.append(now)
            if len(self._calls) > 1000:
                stale = [key for key, values in self._calls.items() if not values]
                for key in stale:
                    self._calls.pop(key, None)
            return True, 0

    async def acquire(self, request: Any) -> asyncio.Semaphore:
        client_id = client_identifier(request, self.settings)
        allowed, retry_after = self._allow(client_id)
        if not allowed:
            raise RateLimitError(
                f"History rate limit exceeded ({self.max_calls}/minute)",
                details={"retry_after_seconds": retry_after, "client": client_id},
            )
        return self._semaphore

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"tracked_clients": len(self._calls), "max_calls_per_minute": self.max_calls}
