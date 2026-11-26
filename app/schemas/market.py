"""Symbol and quote response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SymbolInfo(BaseModel):
    symbol: str
    description: str = ""
    category: str = "other"
    base_currency: str = ""
    quote_currency: str = ""
    digits: int = 0
    point: float = 0.0
    tick_size: float = 0.0
    tick_value: float = 0.0
    contract_size: float = 0.0
    trade_mode: int = 0
    trade_allowed: bool = False
    visible: bool = True
    bid: float | None = None
    ask: float | None = None


class Quote(BaseModel):
    symbol: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    spread: float | None = None
    spread_points: float | None = None
    spread_pips: float | None = None
    volume: int | None = None
    real_volume: int | None = None
    timestamp: str | None = None
    received_at: str | None = None
    age_seconds: float | None = None
    stale_after_seconds: float | None = None
    is_fresh: bool = False
    market_available: bool = False
    status: str = "unavailable"


class Tick(BaseModel):
    timestamp: str
    epoch_ms: int
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: int | None = None
    flags: int = 0


class MarketSession(BaseModel):
    name: str
    open: bool
    local_time: str
    note: str = ""
