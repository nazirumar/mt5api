"""Live quote retrieval. Missing ticks are reported, never fabricated."""

from __future__ import annotations

import time
from typing import Any

from ..core.helpers import as_float, as_int, attr, timestamp_to_iso
from ..core.logging import get_logger
from .clock import BrokerClock
from .connection import MT5ConnectionManager

logger = get_logger("nazbeen_mt5.mt5.quotes")


def pip_size(symbol: str, digits: int, point: float) -> float:
    """Best-effort pip size; spread is also always reported in price/points."""
    upper = symbol.upper()
    if digits <= 2 or "JPY" in upper:
        return point if point > 0 else 0.01
    if point > 0:
        return point * 10
    return 0.0001


class QuoteService:
    def __init__(self, manager: MT5ConnectionManager, settings: Any, clock: BrokerClock | None = None):
        self.manager = manager
        self.settings = settings
        self.clock = clock or BrokerClock(settings)

    def quote_from_tick(self, symbol: str, tick: Any, info: Any | None = None) -> dict[str, Any]:
        bid = as_float(attr(tick, "bid", 0), 0.0)
        ask = as_float(attr(tick, "ask", 0), 0.0)
        last = as_float(attr(tick, "last", 0), 0.0)
        spread = round(max(ask - bid, 0.0), 10) if bid > 0 and ask > 0 else None
        digits = as_int(attr(info, "digits", 0)) if info else 0
        point = as_float(attr(info, "point", 0), 0.0) if info else 0.0
        psize = pip_size(symbol, digits, point)
        tick_time = attr(tick, "time_msc", None) or attr(tick, "time", None)
        utc_ms = self.clock.to_utc_ms(tick_time, sample=tick_time) if tick_time else None
        timestamp = timestamp_to_iso(utc_ms / 1000.0) if utc_ms is not None else None
        age: float | None = None
        if utc_ms is not None:
            age = max(round(time.time() - (utc_ms / 1000.0), 3), 0.0)

        stale_after = self.settings.MARKET_POLL_INTERVAL * self.settings.QUOTE_STALE_MULTIPLIER
        has_price = bid > 0 or ask > 0
        return {
            "symbol": symbol,
            "bid": bid if bid > 0 else None,
            "ask": ask if ask > 0 else None,
            "last": last if last > 0 else None,
            "spread": spread,
            "spread_points": round(spread / point, 2) if spread is not None and point > 0 else None,
            "spread_pips": round(spread / psize, 2) if spread is not None and psize > 0 else None,
            "volume": as_int(attr(tick, "volume", 0)) or None,
            "real_volume": as_int(attr(tick, "real_volume", 0)) or None,
            "timestamp": timestamp,
            "received_at": timestamp_to_iso(time.time()),
            "age_seconds": age,
            "stale_after_seconds": round(stale_after, 3),
            "is_fresh": bool(has_price and age is not None and age <= stale_after),
            "market_available": bool(has_price),
            "status": "ok" if has_price else "no_tick",
        }

    def get_quote(self, symbol: str, info: Any | None = None) -> dict[str, Any]:
        """Return one live quote; callers may reuse already-fetched symbol info."""
        if info is None:
            info = self.manager.call("symbol_info", symbol, allow_none=True)
        if info is None:
            from ..core.exceptions import SymbolNotFoundError

            raise SymbolNotFoundError(f"Symbol '{symbol}' was not found on this broker")
        tick = self.manager.call("symbol_info_tick", symbol, allow_none=True)
        return self.quote_from_tick(symbol, tick, info)

    def get_quotes(self, symbols: list[str]) -> list[dict[str, Any]]:
        quotes: list[dict[str, Any]] = []
        for symbol in symbols:
            try:
                quotes.append(self.get_quote(symbol))
            except Exception as exc:
                logger.debug("Quote lookup failed for %s: %s", symbol, exc)
                quotes.append(
                    {
                        "symbol": symbol,
                        "bid": None,
                        "ask": None,
                        "last": None,
                        "spread": None,
                        "spread_points": None,
                        "spread_pips": None,
                        "volume": None,
                        "real_volume": None,
                        "timestamp": None,
                        "received_at": timestamp_to_iso(time.time()),
                        "age_seconds": None,
                        "is_fresh": False,
                        "market_available": False,
                        "status": "unavailable",
                    }
                )
        return quotes
