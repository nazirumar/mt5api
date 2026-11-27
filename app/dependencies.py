"""Request dependencies and the single application context object."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from .config import Settings
from .core.lifecycle import RuntimeState
from .core.security import verify_api_key


@dataclass
class AppContext:
    settings: Settings
    runtime: RuntimeState
    manager: Any
    clock: Any
    symbols: Any
    quotes: Any
    candles: Any
    ticks: Any
    account: Any
    cache: Any
    candle_cache: Any
    subscriptions: Any
    collector: Any
    monitor: Any
    history_limiter: Any
    database: dict[str, Any]


def get_context(request: Request) -> AppContext:
    return request.app.state.context


async def require_api_key(request: Request) -> str:
    settings: Settings = get_context(request).settings
    if not verify_api_key(request, settings):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Valid API key required"},
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return request.headers.get(settings.API_KEY_HEADER, "")
