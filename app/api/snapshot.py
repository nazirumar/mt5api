"""Multi-timeframe market snapshot endpoint."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ..core.context import get_request_id
from ..dependencies import AppContext, get_context, require_api_key
from ..schemas.common import APIResponse
from ..schemas.market_snapshot import SnapshotResponse
from . import ok

router = APIRouter(
    prefix="/market/snapshot",
    tags=["market"],
    dependencies=[Depends(require_api_key)],
)


@router.get(
    "/{symbol}",
    summary="Multi-timeframe market snapshot",
    description=(
        "Read-only snapshot of one broker-native symbol across several MT5 timeframes. "
        "Candles, quote, contexts, alignment, and synchronization are derived from real "
        "MT5 data only. Descriptive classifications are not trading signals."
    ),
    response_model=APIResponse[SnapshotResponse],
)
async def market_snapshot(
    request: Request,
    symbol: str,
    timeframes: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int | None, Query(ge=1, le=5000)] = None,
    include_context: Annotated[bool, Query()] = True,
    include_candles: Annotated[bool, Query()] = True,
    include_indicators: Annotated[bool, Query()] = True,
    include_structure: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    # One history slot covers the whole snapshot, including every timeframe read.
    slot = await ctx.history_limiter.acquire(request)
    async with slot:
        payload = await asyncio.to_thread(
            ctx.snapshot_engine.build_snapshot,
            symbol,
            timeframes,
            limit,
            include_context,
            include_candles,
            include_indicators,
            include_structure,
            get_request_id(),
        )
    return ok(payload)
