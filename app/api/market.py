"""Approximate market-session information (UTC-based, broker-independent)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from ..dependencies import AppContext, get_context, require_api_key
from . import ok

router = APIRouter(prefix="/market", tags=["market"], dependencies=[Depends(require_api_key)])

WINDOWS = {
    "Sydney": ((21, 0), (6, 0)),
    "Tokyo": ((0, 0), (9, 0)),
    "London": ((7, 0), (16, 0)),
    "New York": ((12, 0), (21, 0)),
}


def _in_window(hour: int, start: int, end: int) -> bool:
    if start < end:
        return start <= hour < end
    # Overnight sessions (e.g. Sydney 21:00-06:00) wrap past midnight.
    return hour >= start or hour < end


@router.get("/sessions", summary="Approximate global FX sessions")
async def market_sessions(request: Request) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    now = datetime.now(timezone.utc)
    sessions = [
        {
            "name": name,
            "open": _in_window(now.hour, start[0], end[0]),
            "local_time": now.isoformat().replace("+00:00", "Z"),
            "note": "Approximate UTC window; confirm exact hours with your broker.",
        }
        for name, (start, end) in WINDOWS.items()
    ]
    return ok(sessions, timezone="UTC")
