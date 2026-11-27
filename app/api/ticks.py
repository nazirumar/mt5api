"""Historical tick endpoint."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ..dependencies import AppContext, get_context, require_api_key
from . import ok

router = APIRouter(prefix="/ticks", tags=["history"], dependencies=[Depends(require_api_key)])


@router.get(
    "/{symbol}",
    summary="Historical ticks (when the broker provides them)",
    description="A start timestamp is required. Records are bounded by MAX_TICK_RECORDS and never downloaded indefinitely.",
)
async def get_ticks(
    request: Request,
    symbol: str,
    start: Annotated[str, Query(description="ISO-8601 UTC")],
    end: Annotated[str | None, Query(description="ISO-8601 UTC")] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    slot = await ctx.history_limiter.acquire(request)
    async with slot:
        result = await asyncio.to_thread(ctx.ticks.get_ticks, symbol, start, end, limit)
    return ok(result)
