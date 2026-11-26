"""Candle response models."""

from __future__ import annotations

from pydantic import BaseModel


class Candle(BaseModel):
    time: str
    epoch: int
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 0
    real_volume: int | None = None
    spread: int = 0
    completed: bool = True


class CandleCollection(BaseModel):
    symbol: str
    timeframe: str
    count: int
    limit: int
    candles: list[Candle]
    source: str = "mt5_history"


class TickCollection(BaseModel):
    symbol: str
    count: int
    limit: int
    start: str
    end: str
    ticks: list[dict]
    source: str = "mt5_history"
