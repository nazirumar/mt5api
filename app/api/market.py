"""Market session information and deterministic market context."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ..context.sessions import WINDOWS, in_window as _in_window
from ..core.context import get_request_id
from ..dependencies import AppContext, get_context, require_api_key
from ..schemas.common import APIResponse
from ..schemas.market_context import MarketContextResponse
from . import ok

router = APIRouter(prefix="/market", tags=["market"], dependencies=[Depends(require_api_key)])


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


@router.get(
    "/context/{symbol}",
    summary="Deterministic market context snapshot",
    description=(
        "Read-only, rule-based context derived from live MT5 quotes and completed candles. "
        "Classifications are descriptive measurements, not trading signals or predictions."
    ),
    response_model=APIResponse[MarketContextResponse],
)
async def market_context(
    request: Request,
    symbol: str,
    timeframe: Annotated[str | None, Query(max_length=16)] = None,
    timeframes: Annotated[str | None, Query(max_length=256)] = None,
    lookback: Annotated[int | None, Query(ge=1, le=5000)] = None,
    include_candles: Annotated[bool, Query()] = False,
    include_indicators: Annotated[bool, Query()] = True,
    include_structure: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    # One history slot covers the whole request, including multi-timeframe reads.
    slot = await ctx.history_limiter.acquire(request)
    async with slot:
        payload = await asyncio.to_thread(
            ctx.context_engine.build_context,
            symbol,
            timeframe or ctx.settings.CONTEXT_DEFAULT_TIMEFRAME,
            timeframes,
            lookback,
            include_candles,
            include_indicators,
            include_structure,
            get_request_id(),
        )
    return ok(payload)
