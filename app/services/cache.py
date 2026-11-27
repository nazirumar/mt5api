"""Thread-safe in-memory quote cache with read-time freshness checks."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any


def _parse_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.timestamp() if value.tzinfo else value.replace(tzinfo=timezone.utc).timestamp()
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return None


def refresh_freshness(quote: dict[str, Any], stale_after_seconds: float, now: float | None = None) -> dict[str, Any]:
    """Return a copy whose freshness is computed now, not when it was cached."""
    fresh = dict(quote)
    current = time.time() if now is None else now
    stamp = _parse_timestamp(fresh.get("timestamp") or fresh.get("received_at"))
    if stamp is None:
        fresh["age_seconds"] = None
        fresh["is_fresh"] = False
        return fresh
    age = max(round(current - stamp, 3), 0.0)
    fresh["age_seconds"] = age
    fresh["is_fresh"] = bool(fresh.get("market_available", True) and age <= stale_after_seconds)
    return fresh


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

    def get_many_fresh(self, symbols: list[str], stale_after_seconds: float) -> dict[str, dict[str, Any]]:
        now = time.time()
        with self._lock:
            return {
                symbol: refresh_freshness(self._quotes[symbol], stale_after_seconds, now)
                for symbol in symbols
                if symbol in self._quotes
            }

    def all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._quotes.items()}

    def all_fresh(self, stale_after_seconds: float) -> dict[str, dict[str, Any]]:
        now = time.time()
        with self._lock:
            return {
                key: refresh_freshness(value, stale_after_seconds, now)
                for key, value in self._quotes.items()
            }

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
