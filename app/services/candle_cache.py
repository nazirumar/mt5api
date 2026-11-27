"""Latest completed candle per (symbol, timeframe) for WebSocket snapshots."""

from __future__ import annotations

import threading
from typing import Any

CandleKey = tuple[str, str]


class CandleCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._candles: dict[CandleKey, dict[str, Any]] = {}

    def set(self, symbol: str, timeframe: str, candle: dict[str, Any]) -> None:
        with self._lock:
            self._candles[(symbol, timeframe)] = dict(candle)

    def get(self, symbol: str, timeframe: str) -> dict[str, Any] | None:
        with self._lock:
            candle = self._candles.get((symbol, timeframe))
            return dict(candle) if candle else None

    def get_many(self, pairs: list[CandleKey]) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"symbol": symbol, "timeframe": timeframe, "candle": dict(self._candles[(symbol, timeframe)])}
                for symbol, timeframe in pairs
                if (symbol, timeframe) in self._candles
            ]

    def remove(self, symbol: str, timeframe: str) -> None:
        with self._lock:
            self._candles.pop((symbol, timeframe), None)

    def prune(self, active: set[CandleKey]) -> None:
        with self._lock:
            for key in list(self._candles):
                if key not in active:
                    self._candles.pop(key, None)

    def all(self) -> dict[CandleKey, dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._candles.items()}
