"""Bounded historical tick retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..core.exceptions import InvalidRequestError, SymbolNotFoundError
from ..services.data_validator import normalize_ticks
from .candles import parse_datetime
from .clock import BrokerClock
from .connection import MT5ConnectionManager


class TickService:
    def __init__(self, manager: MT5ConnectionManager, settings: Any, clock: BrokerClock | None = None):
        self.manager = manager
        self.settings = settings
        self.clock = clock or BrokerClock(settings)
        self._selected_symbols: set[str] = set()

    def _ensure_selected(self, symbol: str) -> None:
        """Make a broker symbol available to history calls without renaming it."""
        if symbol in self._selected_symbols:
            return
        try:
            selected = self.manager.call(
                "symbol_select", symbol, True, allow_none=True, check_result=False
            )
            if selected:
                self._selected_symbols.add(symbol)
        except Exception:
            pass

    def get_ticks(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        info = self.manager.call("symbol_info", symbol, allow_none=True)
        if info is None:
            raise SymbolNotFoundError(f"Symbol '{symbol}' was not found on this broker")
        self._ensure_selected(symbol)
        start_dt = parse_datetime(start, "start")
        end_dt = parse_datetime(end, "end")
        if not start_dt:
            raise InvalidRequestError("start is required for historical tick retrieval")
        if end_dt and end_dt <= start_dt:
            raise InvalidRequestError("end must be later than start")
        end_dt = end_dt or datetime.now(timezone.utc)
        max_days = int(getattr(self.settings, "MAX_TICK_RANGE_DAYS", 30))
        if (end_dt - start_dt).days > max_days:
            raise InvalidRequestError(
                f"Tick range exceeds MAX_TICK_RANGE_DAYS ({max_days})",
                details={"max_days": max_days},
            )
        count = min(limit or self.settings.MAX_TICK_RECORDS, self.settings.MAX_TICK_RECORDS)
        if count <= 0:
            raise InvalidRequestError("limit must be greater than zero")

        tick = self.manager.call("symbol_info_tick", symbol, allow_none=True)
        self.clock.observe_from_tick(tick)
        broker_start = self.clock.to_broker_datetime(start_dt)
        broker_end = self.clock.to_broker_datetime(end_dt)
        flags = getattr(self.manager.module(), "COPY_TICKS_ALL", 0)
        records = self.manager.call(
            "copy_ticks_range", symbol, broker_start, broker_end, flags, allow_none=True
        )
        if records is None:
            records = []
        ticks = normalize_ticks(records)[-count:]
        # Historical samples must never seed the clock; only live ticks do.
        ticks = self.clock.convert_ticks(ticks)
        return {
            "symbol": symbol,
            "count": len(ticks),
            "limit": count,
            "start": start_dt.isoformat().replace("+00:00", "Z"),
            "end": end_dt.isoformat().replace("+00:00", "Z"),
            "ticks": ticks,
            "source": "mt5_history",
        }
