"""Completed-candle WebSocket stream tests."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

HEADERS = {"X-API-Key": "test-api-key"}


def test_candle_subscriptions_are_registered(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
            assert ws.receive_json()["type"] == "welcome"
            ws.send_json(
                {
                    "action": "subscribe_candles",
                    "candles": [{"symbol": "XAUUSDm", "timeframe": "M1"}],
                }
            )
            acknowledgement = ws.receive_json()
            assert acknowledgement["type"] == "subscribed_candles"
            assert acknowledgement["data"]["candles"] == [
                {"symbol": "XAUUSDm", "timeframe": "M1"}
            ]
            assert app.state.context.subscriptions.candle_targets() == [
                ("XAUUSDm", "M1")
            ]


def test_invalid_candle_payload_returns_ws_error(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
            assert ws.receive_json()["type"] == "welcome"
            ws.send_json({"action": "subscribe_candles", "candles": "M1"})
            message = ws.receive_json()
            assert message["type"] == "error"
            assert message["data"]["code"] == "INVALID_REQUEST"


def test_collector_emits_completed_candle(app):
    with TestClient(app) as client:
        context = app.state.context
        client_id = context.subscriptions.connect()
        context.subscriptions.subscribe_candles(client_id, [("XAUUSDm", "M1")])

        asyncio.run(context.collector._poll_candles())

        candle = context.candle_cache.get("XAUUSDm", "M1")
        assert candle is not None
        assert "epoch" in candle
        assert candle["completed"] is True
        queue = context.subscriptions.queue_for(client_id)
        assert queue is not None
        message = queue.get_nowait()
        assert message["type"] == "candle"
        assert message["data"]["symbol"] == "XAUUSDm"
        assert message["data"]["timeframe"] == "M1"
        assert message["data"]["candle"]["completed"] is True
