"""Response models for the deterministic market context endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QuoteContext(BaseModel):
    symbol: str
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    spread: float | None = None
    spread_points: float | None = None
    point: float | None = None
    digits: int | None = None
    tick_size: float | None = None
    tick_value: float | None = None
    timestamp: str | None = None
    age_seconds: float | None = None
    stale_after_seconds: float | None = None
    freshness: str
    market_available: bool = False
    status: str = "unavailable"
    source: str = "unknown"
    symbol_category: str = "other"


class DataQuality(BaseModel):
    score: int
    grade: str
    usable: bool
    method: str = ""
    disclaimer: str = ""
    checks: dict[str, Any] = Field(default_factory=dict)
    weights: dict[str, Any] = Field(default_factory=dict)
    missing_data: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    candles_available: int = 0
    duplicate_candles: int = 0
    invalid_ohlc_count: int = 0
    timestamp_monotonic: bool = False
    quote_freshness: str = "UNKNOWN"


class TimeframeContext(BaseModel):
    trend: dict[str, Any]
    regime: dict[str, Any]
    momentum: dict[str, Any]
    volatility: dict[str, Any]
    price_position: dict[str, Any]
    spread: dict[str, Any]
    candle_structure: dict[str, Any]
    indicators: dict[str, Any] = Field(default_factory=dict)
    data_sufficiency: bool = False
    method: str = "deterministic_rule_set_v1"
    candles: list[dict[str, Any]] = Field(default_factory=list)


class MarketContextResponse(BaseModel):
    symbol: str
    generated_at: str
    market_status: str
    quote: QuoteContext
    context: dict[str, Any]
    data_quality: DataQuality
    timeframes: dict[str, TimeframeContext]
    session: dict[str, Any]
    meta: dict[str, Any] = Field(default_factory=dict)
