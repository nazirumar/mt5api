"""Connection and health response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    api_status: str = "ok"
    mt5_initialized: bool = False
    terminal_connected: bool = False
    broker: str | None = None
    account_server: str | None = None
    last_successful_tick: str | None = None
    data_freshness: str = "unknown"
    active_websocket_connections: int = 0
    uptime_seconds: float = 0.0
    api_version: str = "1.0.0"
    last_error: str | None = None


class ConnectionResponse(BaseModel):
    initialized: bool
    initialized_at: str | None = None
    terminal_connected: bool = False
    terminal_path: str | None = None
    broker: str | None = None
    server: str | None = None
    account_login: int | None = None
    last_success: str | None = None
    last_error: str | None = None
    reconnect_attempts: int = 0
    monitor: dict = Field(default_factory=dict)
