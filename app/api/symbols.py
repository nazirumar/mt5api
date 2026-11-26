"""Broker symbol catalog endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from ..dependencies import AppContext, get_context, require_api_key
from . import ok

router = APIRouter(prefix="/symbols", tags=["symbols"], dependencies=[Depends(require_api_key)])


@router.get("", summary="List all broker symbols")
async def list_symbols(request: Request) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    symbols = await asyncio.to_thread(ctx.symbols.list_symbols)
    return ok(symbols, count=len(symbols), note="Names are broker-native; no substitution is performed")


@router.get("/search", summary="Search broker symbols")
async def search_symbols(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    results = await asyncio.to_thread(ctx.symbols.search_symbols, q, limit)
    return ok(results, count=len(results), query=q)


@router.get("/{symbol}", summary="Get one symbol by exact broker name")
async def get_symbol(request: Request, symbol: str) -> dict[str, Any]:
    ctx: AppContext = get_context(request)
    info = await asyncio.to_thread(ctx.symbols.get_symbol, symbol)
    return ok(info)
