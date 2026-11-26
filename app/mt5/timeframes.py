"""Timeframe validation and mapping to MetaTrader5 constants."""

from __future__ import annotations

from ..core.exceptions import InvalidTimeframeError

# seconds are used to distinguish completed vs forming candles
TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M2": 120,
    "M3": 180,
    "M4": 240,
    "M5": 300,
    "M6": 360,
    "M10": 600,
    "M12": 720,
    "M15": 900,
    "M20": 1200,
    "M30": 1800,
    "H1": 3600,
    "H2": 7200,
    "H3": 10800,
    "H4": 14400,
    "H6": 21600,
    "H8": 28800,
    "H12": 43200,
    "D1": 86400,
    "W1": 604800,
    "MN1": 2592000,
}

MT5_TIMEFRAME_NAMES: dict[str, str] = {
    "M1": "TIMEFRAME_M1",
    "M2": "TIMEFRAME_M2",
    "M3": "TIMEFRAME_M3",
    "M4": "TIMEFRAME_M4",
    "M5": "TIMEFRAME_M5",
    "M6": "TIMEFRAME_M6",
    "M10": "TIMEFRAME_M10",
    "M12": "TIMEFRAME_M12",
    "M15": "TIMEFRAME_M15",
    "M20": "TIMEFRAME_M20",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H2": "TIMEFRAME_H2",
    "H3": "TIMEFRAME_H3",
    "H4": "TIMEFRAME_H4",
    "H6": "TIMEFRAME_H6",
    "H8": "TIMEFRAME_H8",
    "H12": "TIMEFRAME_H12",
    "D1": "TIMEFRAME_D1",
    "W1": "TIMEFRAME_W1",
    "MN1": "TIMEFRAME_MN1",
}


def normalize_timeframe(value: str) -> str:
    tf = str(value or "").strip().upper()
    if tf not in TIMEFRAME_SECONDS:
        raise InvalidTimeframeError(
            f"Unsupported timeframe '{value}'. Supported: {', '.join(TIMEFRAME_SECONDS)}"
        )
    return tf


def mt5_timeframe(mt5_module: object, value: str) -> int:
    tf = normalize_timeframe(value)
    constant = getattr(mt5_module, MT5_TIMEFRAME_NAMES[tf], None)
    if constant is None:
        raise InvalidTimeframeError(f"Installed MetaTrader5 package lacks {MT5_TIMEFRAME_NAMES[tf]}")
    return int(constant)
