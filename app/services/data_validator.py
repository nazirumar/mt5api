"""Validation and normalization helpers for market history."""

from __future__ import annotations

from typing import Any

from ..core.exceptions import DataValidationError
from ..core.helpers import as_float, as_int, attr, timestamp_to_iso


def validate_ohlc(open_: float, high: float, low: float, close_: float) -> bool:
    if min(open_, high, low, close_) <= 0:
        return False
    return high >= max(open_, close_, low) and low <= min(open_, close_, high)


def normalize_candles(records: Any, timeframe_seconds: int) -> list[dict[str, Any]]:
    """Convert MT5 rate records to API-shaped, deduplicated UTC candles."""
    if records is None:
        return []
    result: list[dict[str, Any]] = []
    seen: set[int] = set()
    invalid = 0
    for record in records:
        raw_time = attr(record, "time", None)
        if raw_time is None:
            continue
        opened = as_int(raw_time)
        if opened in seen:
            continue
        seen.add(opened)
        open_ = as_float(attr(record, "open", 0))
        high = as_float(attr(record, "high", 0))
        low = as_float(attr(record, "low", 0))
        close_ = as_float(attr(record, "close", 0))
        if not validate_ohlc(open_, high, low, close_):
            invalid += 1
            continue
        import time as _time

        completed = opened + timeframe_seconds <= int(_time.time())
        result.append(
            {
                "time": timestamp_to_iso(opened),
                "epoch": opened,
                "open": open_,
                "high": high,
                "low": low,
                "close": close_,
                "tick_volume": as_int(attr(record, "tick_volume", 0)),
                "real_volume": as_int(attr(record, "real_volume", 0)) or None,
                "spread": as_int(attr(record, "spread", 0)),
                "completed": completed,
            }
        )
    if invalid:
        from ..core.logging import get_logger

        get_logger("nazbeen_mt5.validator").warning("Discarded %d invalid OHLC records", invalid)
    result.sort(key=lambda item: item["epoch"])
    return result


def normalize_ticks(records: Any) -> list[dict[str, Any]]:
    if records is None:
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[int, float, float]] = set()
    for record in records:
        raw = attr(record, "time_msc", None) or attr(record, "time", None)
        if raw is None:
            continue
        msc = as_int(raw)
        bid = as_float(attr(record, "bid", 0))
        ask = as_float(attr(record, "ask", 0))
        key = (msc, bid, ask)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "timestamp": timestamp_to_iso(msc),
                "epoch_ms": msc if msc > 1e11 else msc * 1000,
                "bid": bid or None,
                "ask": ask or None,
                "last": as_float(attr(record, "last", 0)) or None,
                "volume": as_int(attr(record, "volume", 0)) or None,
                "flags": as_int(attr(record, "flags", 0)),
            }
        )
    result.sort(key=lambda item: item["epoch_ms"])
    return result
