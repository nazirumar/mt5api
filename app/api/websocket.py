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

_KNOWN_ACTIONS = {
    "subscribe",
    "unsubscribe",
    "subscribe_candles",
    "unsubscribe_candles",
    "ping",
}


class _FrameTooLarge(Exception):
    """An inbound WebSocket frame exceeded WEBSOCKET_MAX_FRAME_BYTES."""


def _normalize_origin(value: str) -> str:
    return value.strip().rstrip("/").lower()


def _origin_allowed(websocket: WebSocket, ctx: AppContext) -> bool:
    """Allow native clients without Origin; check browsers against CORS origins."""
    if not getattr(ctx.settings, "WEBSOCKET_CHECK_ORIGIN", True):
        return True
    origin = websocket.headers.get("origin")
    if not origin or not origin.strip():
        return True
    allowed = {_normalize_origin(str(item)) for item in ctx.settings.CORS_ORIGINS}
    if "*" in allowed:
        return True
    return _normalize_origin(origin) in allowed


async def _close_websocket(websocket: WebSocket, code: int, reason: str) -> None:
    """Best-effort close; a peer may already have closed the socket."""
    try:
        await websocket.close(code=code, reason=reason)
    except (RuntimeError, OSError, WebSocketDisconnect):
        pass


async def _receive_text_capped(websocket: WebSocket, max_bytes: int) -> str:
    """Receive one frame without letting an oversized frame reach JSON parsing."""
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(message.get("code", 1000), message.get("reason"))
    if message["type"] != "websocket.receive":
        raise RuntimeError("Expected ASGI message 'websocket.receive'")
    text = message.get("text")
    if text is None:
        data = message.get("bytes")
        if data is None:
            raise RuntimeError("Expected WebSocket frame to contain text or bytes")
        if len(data) > max_bytes:
            raise _FrameTooLarge
        return data.decode("utf-8", errors="replace")
    if len(text.encode("utf-8")) > max_bytes:
        raise _FrameTooLarge
    return text


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

    if not _origin_allowed(websocket, ctx):
        await _close_websocket(websocket, 1008, "Forbidden origin")
        return

    supplied = _supplied_key(websocket, ctx)
    if supplied and not is_valid_api_key(supplied, ctx.settings):
        await _close_websocket(websocket, 1008, "Unauthorized")
        return

    if not ctx.subscriptions.has_capacity():
        await _close_websocket(websocket, 1013, "Server busy")
        return

    authenticated = bool(supplied)
    await websocket.accept()
    try:
        client_id = ctx.subscriptions.connect()
    except RuntimeError:
        await _close_websocket(websocket, 1013, "Server busy")
        return
    if authenticated:
        ctx.subscriptions.authenticate(client_id)

    # Only first-message auth has a deadline; query/header auth is immediate.
    auth_deadline = (
        None if authenticated
        else time.monotonic() + ctx.settings.WEBSOCKET_AUTH_TIMEOUT_SECONDS
    )
    max_frame_bytes = ctx.settings.WEBSOCKET_MAX_FRAME_BYTES

    async def send_error(message: str, code: str = "WS_ERROR") -> None:
        detail = {"code": code, "message": message}
        await websocket.send_json(
            {
                "type": "error",
                # Additive top-level fields for shipped clients that do not
                # look inside `data`; the original envelope is unchanged.
                "error": detail,
                "message": message,
                "data": detail,
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

    heartbeat_interval = ctx.settings.WEBSOCKET_HEARTBEAT_INTERVAL
    poll_interval = min(heartbeat_interval, 0.25)
    last_heartbeat = time.monotonic()

    async def sender() -> None:
        nonlocal last_heartbeat
        while True:
            if (
                not ctx.subscriptions.is_active(client_id)
                or ctx.subscriptions.is_stale(client_id)
            ):
                # A stale registry entry used to leave the socket half-open.
                await _close_websocket(websocket, 1008, "Stale connection")
                break
            if not authenticated:
                await asyncio.sleep(poll_interval)
                continue
            queue = ctx.subscriptions.queue_for(client_id)
            if queue is None:
                await _close_websocket(websocket, 1008, "Client disconnected")
                break
            try:
                message = await asyncio.wait_for(queue.get(), timeout=poll_interval)
            except asyncio.TimeoutError:
                if time.monotonic() - last_heartbeat < heartbeat_interval:
                    continue
                message = {"type": "heartbeat", "timestamp": timestamp_to_iso(time.time())}
                last_heartbeat = time.monotonic()
            if (
                not ctx.subscriptions.is_active(client_id)
                or ctx.subscriptions.is_stale(client_id)
            ):
                await _close_websocket(websocket, 1008, "Stale connection")
                break
            try:
                await websocket.send_json(message)
            except (WebSocketDisconnect, RuntimeError, OSError):
                break

    async def receive_frame() -> str:
        if auth_deadline is None:
            return await _receive_text_capped(websocket, max_frame_bytes)
        remaining = auth_deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(
            _receive_text_capped(websocket, max_frame_bytes), timeout=remaining
        )

    async def receiver() -> None:
        nonlocal authenticated, auth_deadline
        while True:
            try:
                raw = await receive_frame()
            except asyncio.TimeoutError:
                await _close_websocket(websocket, 1008, "Authentication timeout")
                return
            except _FrameTooLarge:
                await _close_websocket(websocket, 1009, "Frame too large")
                return
            try:
                payload: Any = json.loads(raw)
            except ValueError:
                await send_error("Invalid JSON", "INVALID_JSON")
                continue
            if not isinstance(payload, dict):
                await send_error("Expected a JSON object", "INVALID_REQUEST")
                continue

            action = str(payload.get("action", "")).strip().lower()
            if not action:
                await send_error("An action is required", "INVALID_REQUEST")
                continue

            if not authenticated:
                if action != "auth":
                    await send_error("Authenticate first with action 'auth'", "UNAUTHORIZED")
                    continue
                candidate = str(payload.get("api_key", ""))
                if not is_valid_api_key(candidate, ctx.settings):
                    await send_error("Valid API key required", "UNAUTHORIZED")
                    await _close_websocket(websocket, 1008, "Unauthorized")
                    return
                authenticated = True
                auth_deadline = None
                ctx.subscriptions.authenticate(client_id)
                ctx.subscriptions.touch(client_id)
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

            if action == "auth":
                await send_error("Already authenticated", "ALREADY_AUTHENTICATED")
                continue
            if action not in _KNOWN_ACTIONS:
                await send_error(
                    "Unknown action; use subscribe, unsubscribe, subscribe_candles, "
                    "unsubscribe_candles or ping",
                    "UNKNOWN_ACTION",
                )
                continue

            # Only a recognized, non-empty action counts as client activity.
            ctx.subscriptions.touch(client_id)

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
