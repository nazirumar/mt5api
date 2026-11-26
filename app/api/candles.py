"""Historical OHLC endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ..dependencies import AppContext, get_context, require_api_key
from ..schemas.candles import CandleCollection
from ..schemas.common import APIResponse
from . import ok

router = APIRouter(prefix="/candles", tags=["history"], dependencies=[Depends(require_api_key)])


@router.get(
    "/{symbol}",
    summary="Historical candles",
    description="Supports range (start/end) or most-recent (limit) retrieval. Timestamps are UTC; the forming candle is marked completed=false.",
    response_model=APIResponse[CandleCollection],
)
async def get_candles(
    request: Request,
    symbol: str,
    timeframe: Annotated[str, Query(description="M1...MN1")] = "M1",
    limit: Annotated[int | None, Query(ge=1)] = None,
    start: Annotated[str | None, Query(description="ISO-8601 UTC")] = None,
    end: Annotated[str | None, Query(description="ISO-8601 UTC")] = None,
) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    result = await asyncio.to_thread(ctx.candles.get_candles, symbol, timeframe, limit, start, end)
    return ok(result)
