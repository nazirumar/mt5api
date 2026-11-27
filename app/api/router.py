"""Aggregated API router."""

from fastapi import APIRouter

from . import account, candles, connection, health, market, quotes, snapshot, symbols, ticks, websocket

api_router = APIRouter(prefix="/api/v1", tags=["Nazbeen MT5 Market Data API"])

api_router.include_router(health.router)
api_router.include_router(connection.router)
api_router.include_router(symbols.router)
api_router.include_router(quotes.router)
api_router.include_router(market.router)
api_router.include_router(snapshot.router)
api_router.include_router(candles.router)
api_router.include_router(ticks.router)
api_router.include_router(account.router)

# WebSocket routes are mounted by main.py (they are not under /api/v1).
websocket_router = websocket.router
