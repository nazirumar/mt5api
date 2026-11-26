"""Sanitized account response model."""

from __future__ import annotations

from pydantic import BaseModel


class AccountInfo(BaseModel):
    connected: bool
    login: int | None = None
    server: str | None = None
    company: str | None = None
    name: str | None = None
    currency: str | None = None
    balance: float | None = None
    credit: float | None = None
    profit: float | None = None
    equity: float | None = None
    margin: float | None = None
    margin_free: float | None = None
    margin_level: float | None = None
    leverage: int | None = None
    trade_mode: int | None = None
    broker: str | None = None
