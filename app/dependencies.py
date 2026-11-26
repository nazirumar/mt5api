"""Request dependencies and the single application context object."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import Any

from fastapi import Header, HTTPException, Request, status

from .config import Settings
from .core.lifecycle import RuntimeState


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
    subscriptions: Any
    collector: Any
    monitor: Any
    database: dict[str, Any]


def get_context(request: Request) -> AppContext:
    return request.app.state.context


async def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> str:
    settings: Settings = get_context(request).settings
    supplied = x_api_key or request.headers.get(settings.API_KEY_HEADER, "")
    if not supplied or not hmac.compare_digest(supplied, settings.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Valid API key required"},
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return supplied
