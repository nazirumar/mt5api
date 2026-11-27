"""Response models for the multi-timeframe snapshot endpoint."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .market_context import QuoteContext


class SnapshotResponse(BaseModel):
    snapshot_id: str
    symbol: str
    generated_at: str
    snapshot_status: str
    synchronization: dict[str, Any]
    quote: QuoteContext
    timeframes: dict[str, dict[str, Any]]
    alignment: dict[str, Any]
    session: dict[str, Any]
    data_quality: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
