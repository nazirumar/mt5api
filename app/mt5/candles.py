"""Historical OHLC candle retrieval with UTC normalization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.exceptions import InvalidRequestError, SymbolNotFoundError
from ..services.data_validator import normalize_candles
from .clock import BrokerClock
from .connection import MT5ConnectionManager
from .timeframes import TIMEFRAME_SECONDS, mt5_timeframe, normalize_timeframe


def parse_datetime(value: str | None, field: str) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidRequestError(f"Invalid {field} timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class CandleService:
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
            # Selection is best-effort; the subsequent copy call reports real errors.
            pass

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int | None = None,
        start: str | None = None,
        end: str | None = None,
        info: Any | None = None,
    ) -> dict[str, Any]:
        tf = normalize_timeframe(timeframe)
        module = self.manager.module()
        mt5_tf = mt5_timeframe(module, tf)
        count = min(limit or self.settings.DEFAULT_CANDLE_LIMIT, self.settings.MAX_CANDLE_LIMIT)
        if count <= 0:
            raise InvalidRequestError("limit must be greater than zero")

        if info is None:
            info = self.manager.call("symbol_info", symbol, allow_none=True)
        if info is None:
            raise SymbolNotFoundError(f"Symbol '{symbol}' was not found on this broker")
        self._ensure_selected(symbol)

        start_dt = parse_datetime(start, "start")
        end_dt = parse_datetime(end, "end")
        max_days = int(getattr(self.settings, "MAX_CANDLE_RANGE_DAYS", 365))
        if end_dt and not start_dt:
            start_dt = end_dt - timedelta(days=max_days)
        effective_end = end_dt or datetime.now(timezone.utc)
        if start_dt and effective_end <= start_dt:
            raise InvalidRequestError("end must be later than start")
        if start_dt and (effective_end - start_dt).days > max_days:
            raise InvalidRequestError(
                f"Candle range exceeds MAX_CANDLE_RANGE_DAYS ({max_days})",
                details={"max_days": max_days},
            )

        if start_dt or end_dt:
            # MT5 range arguments are broker server time, so convert the API's
            # UTC bounds before calling it.
            tick = self.manager.call("symbol_info_tick", symbol, allow_none=True)
            self.clock.observe_from_tick(tick)
            from_dt = self.clock.to_broker_datetime(
                start_dt or datetime.fromtimestamp(0, tz=timezone.utc)
            )
            to_dt = self.clock.to_broker_datetime(effective_end)
            records = self.manager.call("copy_rates_range", symbol, mt5_tf, from_dt, to_dt, allow_none=True)
            if records is None:
                records = []
            candles = normalize_candles(records, TIMEFRAME_SECONDS[tf])[-count:]
        else:
            records = self.manager.call("copy_rates_from_pos", symbol, mt5_tf, 0, count, allow_none=True)
            if records is None:
                records = []
            candles = normalize_candles(records, TIMEFRAME_SECONDS[tf])

        # Do not pass history timestamps to the clock: only live ticks are a
        # valid reference for broker offset auto-detection.
        candles = self.clock.convert_candles(
            candles, TIMEFRAME_SECONDS[tf], timeframe=tf
        )

        return {
            "symbol": symbol,
            "timeframe": tf,
            "count": len(candles),
            "limit": count,
            "candles": candles,
            "source": "mt5_history",
        }
