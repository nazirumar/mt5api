"""Shared WebSocket market stream: /ws/market?api_key=..."""

from __future__ import annotations

import asyncio
import hmac
import json
import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..core.helpers import timestamp_to_iso
from ..dependencies import AppContext

router = APIRouter(tags=["websocket"])


def _authorized(websocket: WebSocket, ctx: AppContext) -> bool:
    expected = ctx.settings.API_KEY
    supplied = (
        websocket.query_params.get("api_key")
        or websocket.query_params.get("api_key".lower())
        or websocket.headers.get(ctx.settings.API_KEY_HEADER, "")
    )
    return bool(supplied and expected and hmac.compare_digest(supplied, expected))


@router.websocket("/ws/market")
async def market_stream(websocket: WebSocket) -> None:
    ctx: AppContext = websocket.app.state.context
    if not _authorized(websocket, ctx):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    client_id = ctx.subscriptions.connect()
    await websocket.send_json(
        {
            "type": "welcome",
            "data": {
                "client_id": client_id,
                "message": "Send {'action': 'subscribe', 'symbols': ['XAUUSD']}",
                "timestamp": timestamp_to_iso(time.time()),
            },
        }
    )

    async def sender() -> None:
        while ctx.subscriptions.is_active(client_id):
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
        while True:
            raw = await websocket.receive_text()
            ctx.subscriptions.touch(client_id)
            try:
                payload: Any = json.loads(raw)
            except ValueError:
                await websocket.send_json(
                    {"type": "error", "data": {"message": "Invalid JSON"}}
                )
                continue
            if not isinstance(payload, dict):
                await websocket.send_json(
                    {"type": "error", "data": {"message": "Expected a JSON object"}}
                )
                continue
            action = str(payload.get("action", "")).lower()
            symbols = payload.get("symbols") or []
            if not isinstance(symbols, list):
                await websocket.send_json(
                    {"type": "error", "data": {"message": "symbols must be an array"}}
                )
                continue
            symbols = [str(symbol).strip() for symbol in symbols[:100] if str(symbol).strip()]

            if action == "subscribe":
                active = sorted(ctx.subscriptions.subscribe(client_id, symbols))
                cached = ctx.cache.get_many(active)
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
            elif action == "ping":
                await websocket.send_json(
                    {"type": "pong", "timestamp": timestamp_to_iso(time.time())}
                )
            else:
                await websocket.send_json(
                    {
                        "type": "error",
                        "data": {"message": "Unknown action; use subscribe, unsubscribe or ping"},
                    }
                )

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
