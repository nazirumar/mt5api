"""WebSocket subscription and multi-client tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.subscription_manager import SubscriptionManager


def test_subscription_manager_fanout():
    manager = SubscriptionManager()
    a = manager.connect()
    b = manager.connect()
    manager.subscribe(a, ["EURUSDm"])
    manager.subscribe(b, ["XAUUSDm"])
    manager.publish("EURUSDm", {"type": "quote", "symbol": "EURUSDm"})
    assert manager.queue_for(a).qsize() == 1
    assert manager.queue_for(b).qsize() == 0
    assert manager.target_symbols() == ["EURUSDm", "XAUUSDm"]
    assert manager.active_count() == 2
    manager.unsubscribe(a, ["EURUSDm"])
    assert manager.target_symbols() == ["XAUUSDm"]


def test_websocket_subscribe_and_quote_push(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
            welcome = ws.receive_json()
            assert welcome["type"] == "welcome"
            ws.send_json({"action": "subscribe", "symbols": ["EURUSDm"]})
            subscribed = ws.receive_json()
            assert subscribed["type"] == "subscribed"
            assert "EURUSDm" in subscribed["data"]["symbols"]
            app.state.context.subscriptions.publish(
                "EURUSDm", {"type": "quote", "data": {"symbol": "EURUSDm", "bid": 1.1}}
            )
            pushed = ws.receive_json()
            assert pushed["type"] == "quote"
            assert pushed["data"]["bid"] == 1.1
            ws.send_json({"action": "unsubscribe", "symbols": ["EURUSDm"]})
            assert ws.receive_json()["type"] == "unsubscribed"


def test_websocket_rejects_bad_api_key(app):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/market?api_key=wrong") as ws:
                ws.receive_json()
        assert exc_info.value.code == 1008


def test_multiple_websocket_clients(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market?api_key=test-api-key") as first, \
             client.websocket_connect("/ws/market?api_key=test-api-key") as second:
            for ws in (first, second):
                ws.receive_json()
                ws.send_json({"action": "subscribe", "symbols": ["XAUUSDm"]})
                assert ws.receive_json()["type"] == "subscribed"
            assert app.state.context.subscriptions.active_count() == 2
            app.state.context.subscriptions.publish_all(
                {"type": "connection", "data": {"connected": True}}
            )
            assert first.receive_json()["type"] == "connection"
            assert second.receive_json()["type"] == "connection"
