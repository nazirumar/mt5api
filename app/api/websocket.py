"""Shared WebSocket market stream: /ws/market (query or first-message auth)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..core.exceptions import ServiceError
from ..core.helpers import timestamp_to_iso
from ..core.security import is_valid_api_key
from ..dependencies import AppContext
from ..mt5.timeframes import normalize_timeframe

router = APIRouter(tags=["websocket"])


def _supplied_key(websocket: WebSocket, ctx: AppContext) -> str:
    return (
        websocket.query_params.get("api_key")
        or websocket.headers.get(ctx.settings.API_KEY_HEADER, "")
        or ""
    )


def _stale_window(ctx: AppContext) -> float:
    return ctx.settings.MARKET_POLL_INTERVAL * ctx.settings.QUOTE_STALE_MULTIPLIER


def _parse_candle_pairs(payload: dict[str, Any], ctx: AppContext) -> list[tuple[str, str]]:
    if not getattr(ctx.settings, "CANDLE_STREAM_ENABLED", True):
        raise ServiceError("Candle stream is disabled", code="STREAM_DISABLED")
    raw = payload.get("candles")
    if raw is None and payload.get("symbols") is not None and payload.get("timeframe"):
        timeframe = str(payload["timeframe"])
        raw = [
            {"symbol": symbol, "timeframe": timeframe}
            for symbol in payload.get("symbols") or []
        ]
    if not isinstance(raw, list):
        raise ServiceError("candles must be an array", code="INVALID_REQUEST")

    max_pairs = int(getattr(ctx.settings, "CANDLE_STREAM_MAX_SUBSCRIPTIONS", 50))
    if len(raw) > max_pairs:
        raise ServiceError(
            f"Too many candle subscriptions (max {max_pairs})",
            code="INVALID_REQUEST",
        )

    pairs: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, dict):
            symbol = str(item.get("symbol", "")).strip()
            timeframe = str(item.get("timeframe", "")).strip()
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            symbol, timeframe = str(item[0]).strip(), str(item[1]).strip()
        else:
            raise ServiceError(
                "Each candle subscription needs symbol and timeframe",
                code="INVALID_REQUEST",
            )
        if not symbol:
            raise ServiceError("A candle symbol is required", code="INVALID_REQUEST")
        pairs.append((symbol, normalize_timeframe(timeframe)))
    return pairs


def _pairs_payload(pairs: list[tuple[str, str]]) -> list[dict[str, str]]:
    return [{"symbol": symbol, "timeframe": timeframe} for symbol, timeframe in pairs]


@router.websocket("/ws/market")
async def market_stream(websocket: WebSocket) -> None:
    ctx: AppContext = websocket.app.state.context
    supplied = _supplied_key(websocket, ctx)
    if supplied and not is_valid_api_key(supplied, ctx.settings):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    authenticated = bool(supplied)
    await websocket.accept()
    client_id = ctx.subscriptions.connect()

    async def send_error(message: str, code: str = "WS_ERROR") -> None:
        await websocket.send_json(
            {
                "type": "error",
                "data": {"code": code, "message": message},
                "timestamp": timestamp_to_iso(time.time()),
            }
        )

    async def send_welcome(auth_mode: str) -> None:
        await websocket.send_json(
            {
                "type": "welcome",
                "data": {
                    "client_id": client_id,
                    "auth_mode": auth_mode,
                    "message": "Send {'action': 'subscribe', 'symbols': ['XAUUSDm']}",
                    "timestamp": timestamp_to_iso(time.time()),
                },
            }
        )

    if authenticated:
        await send_welcome("query_or_header")
    else:
        await websocket.send_json(
            {
                "type": "auth_required",
                "data": {
                    "message": "Send {'action': 'auth', 'api_key': '...'} as the first message.",
                    "timestamp": timestamp_to_iso(time.time()),
                },
            }
        )

    async def sender() -> None:
        while ctx.subscriptions.is_active(client_id):
            if not authenticated:
                await asyncio.sleep(0.1)
                continue
            queue = ctx.subscriptions.queue_for(client_id)
            if queue is None:
                break
            try:
                message = await asyncio.wait_for(
                    queue.get(), timeout=ctx.settings.WEBSOCKET_HEARTBEAT_INTERVAL
                )
            except asyncio.TimeoutError:
                message = {"type": "heartbeat", "timestamp": timestamp_to_iso(time.time())}
            if not ctx.subscriptions.is_active(client_id) or ctx.subscriptions.is_stale(client_id):
                break
            try:
                await websocket.send_json(message)
            except (WebSocketDisconnect, RuntimeError, OSError):
                break

    async def receiver() -> None:
        nonlocal authenticated
        while True:
            raw = await websocket.receive_text()
            ctx.subscriptions.touch(client_id)
            try:
                payload: Any = json.loads(raw)
            except ValueError:
                await send_error("Invalid JSON", "INVALID_JSON")
                continue
            if not isinstance(payload, dict):
                await send_error("Expected a JSON object", "INVALID_REQUEST")
                continue

            action = str(payload.get("action", "")).lower()

            if not authenticated:
                if action == "auth":
                    candidate = str(payload.get("api_key", ""))
                    if not is_valid_api_key(candidate, ctx.settings):
                        await send_error("Valid API key required", "UNAUTHORIZED")
                        await websocket.close(code=1008, reason="Unauthorized")
                        return
                    authenticated = True
                    await websocket.send_json(
                        {
                            "type": "authenticated",
                            "data": {
                                "message": "Authenticated; you may now subscribe.",
                                "timestamp": timestamp_to_iso(time.time()),
                            },
                        }
                    )
                    await send_welcome("first_message")
                    continue
                await send_error("Authenticate first with action 'auth'", "UNAUTHORIZED")
                continue

            if action == "auth":
                await send_error("Already authenticated", "ALREADY_AUTHENTICATED")
                continue

            symbols = payload.get("symbols") or []
            if not isinstance(symbols, list):
                await send_error("symbols must be an array", "INVALID_REQUEST")
                continue
            symbols = [str(symbol).strip() for symbol in symbols[:100] if str(symbol).strip()]

            try:
                if action == "subscribe":
                    active = sorted(ctx.subscriptions.subscribe(client_id, symbols))
                    cached = ctx.cache.get_many_fresh(active, _stale_window(ctx))
                    await websocket.send_json(
                        {
                            "type": "subscribed",
                            "data": {"symbols": active, "quotes": list(cached.values())},
                        }
                    )
                elif action == "unsubscribe":
                    active = sorted(ctx.subscriptions.unsubscribe(client_id, symbols))
                    await websocket.send_json(
                        {"type": "unsubscribed", "data": {"symbols": active}}
                    )
                elif action in {"subscribe_candles", "unsubscribe_candles"}:
                    pairs = _parse_candle_pairs(payload, ctx)
                    if action == "subscribe_candles":
                        active = ctx.subscriptions.subscribe_candles(client_id, pairs)
                        cached = ctx.candle_cache.get_many(active)
                        await websocket.send_json(
                            {
                                "type": "subscribed_candles",
                                "data": {"candles": _pairs_payload(active), "cached": cached},
                            }
                        )
                    else:
                        active = ctx.subscriptions.unsubscribe_candles(client_id, pairs)
                        await websocket.send_json(
                            {
                                "type": "unsubscribed_candles",
                                "data": {"candles": _pairs_payload(active)},
                            }
                        )
                elif action == "ping":
                    await websocket.send_json(
                        {"type": "pong", "timestamp": timestamp_to_iso(time.time())}
                    )
                else:
                    await send_error(
                        "Unknown action; use subscribe, unsubscribe, subscribe_candles, "
                        "unsubscribe_candles or ping",
                        "UNKNOWN_ACTION",
                    )
            except ServiceError as exc:
                await send_error(exc.message, exc.code)

    sender_task = asyncio.create_task(sender(), name=f"ws-sender-{client_id}")
    try:
        await receiver()
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        ctx.subscriptions.disconnect(client_id)
        sender_task.cancel()
        try:
            await sender_task
        except asyncio.CancelledError:
            pass
