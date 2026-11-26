"""Bounded historical tick retrieval."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..core.exceptions import InvalidRequestError, SymbolNotFoundError
from ..core.helpers import attr
from ..services.data_validator import normalize_ticks
from .candles import parse_datetime
from .clock import BrokerClock
from .connection import MT5ConnectionManager


class TickService:
    def __init__(self, manager: MT5ConnectionManager, settings: Any, clock: BrokerClock | None = None):
        self.manager = manager
        self.settings = settings
        self.clock = clock or BrokerClock(settings)

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
        start_dt = parse_datetime(start, "start")
        end_dt = parse_datetime(end, "end")
        if not start_dt:
            raise InvalidRequestError("start is required for historical tick retrieval")
        if end_dt and end_dt <= start_dt:
            raise InvalidRequestError("end must be later than start")
        end_dt = end_dt or datetime.now(timezone.utc)
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
        broker_times = [
            attr(record, "time_msc", None) or attr(record, "time", None) for record in records
        ]
        broker_times = [int(value) for value in broker_times if value is not None]
        sample_epoch_ms = max(broker_times) if broker_times else None
        ticks = self.clock.convert_ticks(ticks, sample_epoch_ms)
        return {
            "symbol": symbol,
            "count": len(ticks),
            "limit": count,
            "start": start_dt.isoformat().replace("+00:00", "Z"),
            "end": end_dt.isoformat().replace("+00:00", "Z"),
            "ticks": ticks,
            "source": "mt5_history",
        }
