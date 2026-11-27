"""Live quote endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ..dependencies import AppContext, get_context, require_api_key
from . import ok

router = APIRouter(tags=["quotes"], dependencies=[Depends(require_api_key)])


@router.get(
    "/quotes",
    summary="Quotes for one or more symbols",
    description="Use broker-native symbols, e.g. symbols=EURUSD,XAUUSD. When omitted, cached watchlist quotes are returned.",
)
async def get_quotes(
    request: Request,
    symbols: Annotated[str | None, Query(description="Comma-separated broker symbol names")] = None,
) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    if symbols:
        requested = [item.strip() for item in symbols.split(",") if item.strip()]
        quotes = await asyncio.to_thread(ctx.quotes.get_quotes, requested)
    else:
        window = ctx.settings.MARKET_POLL_INTERVAL * ctx.settings.QUOTE_STALE_MULTIPLIER
        cached = ctx.cache.all_fresh(window)
        quotes = list(cached.values()) if cached else []
    return ok(quotes, count=len(quotes), poll_interval=ctx.settings.MARKET_POLL_INTERVAL)


@router.get("/quote/{symbol}", summary="Quote for one exact broker symbol")
async def get_quote(request: Request, symbol: str) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    quote = await asyncio.to_thread(ctx.quotes.get_quote, symbol)
    return ok(quote)
