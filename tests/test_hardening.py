"""Regression tests for the hardening and observability roadmap."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.context import request_id_var
from app.core.logging import StructuredFormatter, configure_logging
from app.database.repositories import record_event
from app.main import create_app
from app.mt5.clock import BrokerClock

HEADERS = {"X-API-Key": "test-api-key"}
ROOT = Path(__file__).resolve().parents[1]


def test_public_endpoints_setting_is_removed():
    config = (ROOT / "app" / "config.py").read_text(encoding="utf-8")
    assert "PUBLIC_ENDPOINTS" not in config


def test_events_is_authenticated_and_filterable(app):
    with TestClient(app) as client:
        context = app.state.context
        token = request_id_var.set("req-filter-check")
        try:
            record_event(
                context.database["session_factory"],
                "audit",
                "correlation check",
                details={"tag": "roadmap"},
            )
        finally:
            request_id_var.reset(token)

        assert client.get("/api/v1/events").status_code == 401
        response = client.get(
            "/api/v1/events",
            params={"kind": "audit", "level": "INFO", "request_id": "req-filter-check"},
            headers=HEADERS,
        )
        assert response.status_code == 200
        records = response.json()["data"]
        assert records
        assert all(item["kind"] == "audit" for item in records)
        assert records[0]["details"]["request_id"] == "req-filter-check"


def test_health_freshness_is_computed_at_read_time(app):
    with TestClient(app) as client:
        context = app.state.context
        context.cache.set(
            {
                "symbol": "EURUSDm",
                "timestamp": datetime.fromtimestamp(time.time() - 60, timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "status": "ok",
                "market_available": True,
            }
        )
        assert client.get("/api/v1/health").json()["data"]["data_freshness"] == "stale"

        context.cache.set(
            {
                "symbol": "EURUSDm",
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "status": "ok",
                "market_available": True,
            }
        )
        assert client.get("/api/v1/health").json()["data"]["data_freshness"] == "live"


def test_clock_auto_detection_ignores_historical_samples(settings):
    neutral = settings.model_copy(
        update={"MT5_SERVER_TIMEZONE": "", "MT5_SERVER_UTC_OFFSET_MINUTES": None}
    )
    clock = BrokerClock(neutral)

    # Samples older than the plausible broker-offset window are historical.
    # A one-hour-old *fresh* tick can legitimately mean a UTC-1 broker, so the
    # boundary is now wider while callers avoid passing history records here.
    clock.observe(time.time() - 2 * 86400)
    assert clock.describe()["auto_detected"] is False

    clock.observe(time.time() + 7200)
    assert clock.offset_seconds == 7200
    assert clock.describe()["auto_detected"] is True


def test_history_range_caps_are_enforced(settings, fake_mt5):
    strict = settings.model_copy(
        update={"MAX_CANDLE_RANGE_DAYS": 1, "MAX_TICK_RANGE_DAYS": 1}
    )
    with TestClient(create_app(strict)) as client:
        start = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat().replace("+00:00", "Z")
        candles = client.get(
            "/api/v1/candles/EURUSDm",
            params={"timeframe": "M1", "start": start},
            headers=HEADERS,
        )
        assert candles.status_code == 400
        assert "MAX_CANDLE_RANGE_DAYS" in candles.json()["error"]["message"]

        ticks = client.get(
            "/api/v1/ticks/EURUSDm", params={"start": start}, headers=HEADERS
        )
        assert ticks.status_code == 400
        assert "MAX_TICK_RANGE_DAYS" in ticks.json()["error"]["message"]


def test_history_rate_limit_returns_429(settings, fake_mt5):
    limited = settings.model_copy(update={"HISTORY_RATE_LIMIT_PER_MINUTE": 1})
    with TestClient(create_app(limited)) as client:
        assert client.get("/api/v1/candles/EURUSDm", headers=HEADERS).status_code == 200
        blocked = client.get("/api/v1/candles/EURUSDm", headers=HEADERS)
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "RATE_LIMITED"


def test_ready_requires_key_and_reports_components(app):
    with TestClient(app) as client:
        assert client.get("/api/v1/ready").status_code == 401
        response = client.get("/api/v1/ready", headers=HEADERS)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["ready"] is True
        assert {"mt5", "broker_clock", "collector", "websocket"} <= set(data)


def test_request_ids_are_echoed_and_generated(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/health", headers={"X-Request-ID": "cid-123"})
        assert response.headers["X-Request-ID"] == "cid-123"
        assert response.json()["meta"]["request_id"] == "cid-123"

        generated = client.get("/api/v1/health")
        assert len(generated.headers["X-Request-ID"]) == 32


def test_structured_log_formatter_includes_request_id():
    record = logging.LogRecord(
        "nazbeen_mt5.test", logging.INFO, __file__, 1, "hello", (), None
    )
    record.request_id = "cid-456"
    record.method = "GET"
    record.path = "/api/v1/health"
    record.status_code = 200
    payload = json.loads(StructuredFormatter().format(record))
    assert payload["request_id"] == "cid-456"
    assert payload["method"] == "GET"
    assert payload["status_code"] == 200
    assert payload["message"] == "hello"


def test_previous_api_key_supports_rotation(settings):
    rotating = settings.model_copy(
        update={
            "API_KEY": "new-random-api-key-123",
            "API_KEY_PREVIOUS": "old-random-api-key-456",
        }
    )
    with TestClient(create_app(rotating)) as client:
        assert client.get("/api/v1/ready", headers={"X-API-Key": "new-random-api-key-123"}).status_code == 200
        assert client.get("/api/v1/ready", headers={"X-API-Key": "old-random-api-key-456"}).status_code == 200
        assert client.get("/api/v1/ready", headers={"X-API-Key": "wrong"}).status_code == 401


def test_websocket_first_message_auth(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market") as ws:
            assert ws.receive_json()["type"] == "auth_required"
            ws.send_json({"action": "subscribe", "symbols": ["EURUSDm"]})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"action": "auth", "api_key": "wrong"})
            assert ws.receive_json()["type"] == "error"
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()

        with client.websocket_connect("/ws/market") as ws:
            seen: set[str] = set()
            ws.send_json({"action": "auth", "api_key": "test-api-key"})
            while "welcome" not in seen:
                seen.add(ws.receive_json()["type"])
            assert "authenticated" in seen
            ws.send_json({"action": "subscribe", "symbols": ["EURUSDm"]})
            assert ws.receive_json()["type"] == "subscribed"


def test_logging_follows_configured_directory(settings):
    configure_logging(settings)
    file_handlers = [
        handler
        for handler in logging.getLogger("nazbeen_mt5").handlers
        if isinstance(handler, logging.FileHandler)
    ]
    assert file_handlers
    assert Path(file_handlers[-1].baseFilename).parent == settings.log_directory


def test_candle_stream_can_be_disabled(settings, fake_mt5):
    disabled = settings.model_copy(update={"CANDLE_STREAM_ENABLED": False})
    with TestClient(create_app(disabled)) as client:
        with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
            assert ws.receive_json()["type"] == "welcome"
            ws.send_json(
                {
                    "action": "subscribe_candles",
                    "candles": [{"symbol": "EURUSDm", "timeframe": "M1"}],
                }
            )
            message = ws.receive_json()
            assert message["type"] == "error"
            assert message["data"]["code"] == "STREAM_DISABLED"
