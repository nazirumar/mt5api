"""Thread-safe in-memory quote cache."""

from __future__ import annotations

import threading
import time
from typing import Any


class QuoteCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotes: dict[str, dict[str, Any]] = {}
        self._updated: dict[str, float] = {}

    def set(self, quote: dict[str, Any]) -> None:
        symbol = quote["symbol"]
        with self._lock:
            self._quotes[symbol] = quote
            self._updated[symbol] = time.monotonic()

    def get(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            quote = self._quotes.get(symbol)
            return dict(quote) if quote else None

    def get_many(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                symbol: dict(self._quotes[symbol])
                for symbol in symbols
                if symbol in self._quotes
            }

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._quotes.items()}

    def remove(self, symbol: str) -> None:
        with self._lock:
            self._quotes.pop(symbol, None)
            self._updated.pop(symbol, None)

    def clear(self) -> None:
        with self._lock:
            self._quotes.clear()
            self._updated.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {"symbols": len(self._quotes), "cached_at": dict(self._updated)}
