"""Phase 17 hardening regressions: public status, WS guards and lifecycle."""

from __future__ import annotations

import asyncio
import importlib
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.services.subscription_manager import SubscriptionManager

HEADERS = {"X-API-Key": "test-api-key"}


def test_websocket_security_settings_have_safe_defaults(settings):
    assert settings.WEBSOCKET_CHECK_ORIGIN is True
    assert settings.WEBSOCKET_AUTH_TIMEOUT_SECONDS == 30
    assert settings.WEBSOCKET_MAX_CLIENTS == 100
    assert settings.WEBSOCKET_MAX_FRAME_BYTES == 65536


def test_public_status_sanitizes_mt5_and_websocket_details(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/status")
        assert response.status_code == 200
        data = response.json()["data"]
        assert {
            "api", "mt5", "broker_clock", "collector", "monitor", "cache",
            "websocket", "data_freshness", "configuration",
        } <= set(data)

        assert set(data["mt5"]) == {
            "initialized", "terminal_connected", "last_success", "reconnect_attempts",
        }
        for key in (
            "terminal_path", "broker", "server", "account_login", "last_error",
            "initialized_at",
        ):
            assert key not in data["mt5"]
        assert "clients" in data["websocket"]
        assert data["websocket"]["subscriptions"] == []

        # The fake terminal's identifiers must never reach an anonymous client.
        lowered = response.text.lower()
        for marker in ("fake broker", "fake-demo", "123456"):
            assert marker not in lowered


def test_health_endpoints_do_not_run_manager_health_on_event_loop(app, monkeypatch):
    with TestClient(app) as client:
        manager = app.state.context.manager
        original = manager.health
        on_loop: list[bool] = []

        def recording_health():
            try:
                asyncio.get_running_loop()
                on_loop.append(True)
            except RuntimeError:
                on_loop.append(False)
            return original()

        monkeypatch.setattr(manager, "health", recording_health)
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/ready", headers=HEADERS).status_code == 200
        assert client.get("/api/v1/status").status_code == 200
        assert len(on_loop) == 3
        assert not any(on_loop)


def test_websocket_rejects_unlisted_origin(app):
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/ws/market?api_key=test-api-key",
                headers={"Origin": "https://evil.example"},
            ) as ws:
                ws.receive_json()
        assert exc_info.value.code == 1008


def test_websocket_allows_configured_and_absent_origin(app):
    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws/market?api_key=test-api-key",
            headers={"Origin": "http://mt5.nazbeen.test"},
        ) as ws:
            assert ws.receive_json()["type"] == "welcome"
        # Native clients send no Origin header.
        with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
            assert ws.receive_json()["type"] == "welcome"


def test_websocket_origin_check_can_be_disabled(settings, fake_mt5):
    relaxed = settings.model_copy(update={"WEBSOCKET_CHECK_ORIGIN": False})
    app = create_app(relaxed)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws/market?api_key=test-api-key",
            headers={"Origin": "https://evil.example"},
        ) as ws:
            assert ws.receive_json()["type"] == "welcome"


def test_subscription_manager_caps_clients_and_exposes_auth_state():
    manager = SubscriptionManager(max_clients=1)
    assert manager.max_clients == 1
    assert manager.has_capacity() is True
    client_id = manager.connect()
    assert manager.has_capacity() is False
    with pytest.raises(RuntimeError):
        manager.connect()
    assert manager.is_authenticated(client_id) is False
    manager.authenticate(client_id)
    assert manager.is_authenticated(client_id) is True


def test_app_passes_client_limit_setting(settings, fake_mt5):
    capped = settings.model_copy(update={"WEBSOCKET_MAX_CLIENTS": 7})
    app = create_app(capped)
    with TestClient(app):
        assert app.state.context.subscriptions.max_clients == 7


def test_websocket_rejects_client_when_registry_is_full(settings, fake_mt5):
    capped = settings.model_copy(update={"WEBSOCKET_MAX_CLIENTS": 1})
    app = create_app(capped)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
            assert ws.receive_json()["type"] == "welcome"
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(
                    "/ws/market?api_key=test-api-key"
                ) as rejected:
                    rejected.receive_json()
            assert exc_info.value.code == 1013
            assert app.state.context.subscriptions.active_count() == 1


def test_websocket_closes_unauthenticated_client_after_deadline(settings, fake_mt5):
    strict = settings.model_copy(
        update={"WEBSOCKET_AUTH_TIMEOUT_SECONDS": 0.1, "WEBSOCKET_STALE_TIMEOUT": 10}
    )
    with TestClient(create_app(strict)) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/market") as ws:
                assert ws.receive_json()["type"] == "auth_required"
                ws.receive_json()
        assert exc_info.value.code == 1008


def test_auth_deadline_does_not_apply_after_first_message_auth(settings, fake_mt5):
    strict = settings.model_copy(
        update={"WEBSOCKET_AUTH_TIMEOUT_SECONDS": 0.1, "WEBSOCKET_STALE_TIMEOUT": 10}
    )
    with TestClient(create_app(strict)) as client:
        with client.websocket_connect("/ws/market") as ws:
            assert ws.receive_json()["type"] == "auth_required"
            ws.send_json({"action": "auth", "api_key": "test-api-key"})
            seen = set()
            while "welcome" not in seen:
                seen.add(ws.receive_json()["type"])
            # Well past the auth deadline; the authenticated socket stays open.
            time.sleep(0.25)
            ws.send_json({"action": "ping"})
            assert ws.receive_json()["type"] == "pong"


def test_websocket_closes_oversized_frame(settings, fake_mt5):
    limited = settings.model_copy(update={"WEBSOCKET_MAX_FRAME_BYTES": 64})
    with TestClient(create_app(limited)) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
                assert ws.receive_json()["type"] == "welcome"
                ws.send_text("x" * 65)
                ws.receive_json()
        assert exc_info.value.code == 1009


def test_websocket_touches_last_seen_only_for_known_actions(app, monkeypatch):
    with TestClient(app) as client:
        subscriptions = app.state.context.subscriptions
        touched: list[int] = []
        original = subscriptions.touch

        def recording_touch(client_id: int) -> None:
            touched.append(client_id)
            original(client_id)

        monkeypatch.setattr(subscriptions, "touch", recording_touch)
        with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
            client_id = ws.receive_json()["data"]["client_id"]
            ws.send_text("not json")
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"action": "not_an_action"})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({})
            assert ws.receive_json()["type"] == "error"
            assert touched == []
            ws.send_json({"action": "ping"})
            assert ws.receive_json()["type"] == "pong"
            assert touched == [client_id]


def test_preauth_client_does_not_receive_broadcasts(app):
    with TestClient(app) as client:
        subscriptions = app.state.context.subscriptions
        with client.websocket_connect("/ws/market") as ws:
            assert ws.receive_json()["type"] == "auth_required"
            subscriptions.publish_all({"type": "connection", "data": {"connected": False}})
            ws.send_json({"action": "auth", "api_key": "test-api-key"})

            messages: list[dict] = []
            while not any(item["type"] == "welcome" for item in messages):
                messages.append(ws.receive_json())
            welcome = next(item for item in messages if item["type"] == "welcome")
            assert all(item["type"] != "connection" for item in messages)
            client_id = welcome["data"]["client_id"]
            assert subscriptions.is_authenticated(client_id) is True

            subscriptions.publish_all({"type": "connection", "data": {"connected": True}})
            assert ws.receive_json()["type"] == "connection"


def test_query_auth_marks_client_authenticated(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
            client_id = ws.receive_json()["data"]["client_id"]
            assert app.state.context.subscriptions.is_authenticated(client_id) is True


def test_stale_registry_removal_closes_websocket(settings, fake_mt5):
    short = settings.model_copy(
        update={"WEBSOCKET_STALE_TIMEOUT": 0.2, "WEBSOCKET_HEARTBEAT_INTERVAL": 1.0}
    )
    app = create_app(short)
    with TestClient(app) as client:
        subscriptions = app.state.context.subscriptions
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
                client_id = ws.receive_json()["data"]["client_id"]
                subscriptions._clients[client_id].last_seen -= 10
                assert subscriptions.cleanup_stale() == [client_id]
                assert not subscriptions.is_active(client_id)
                ws.receive_json()
        assert exc_info.value.code == 1008


def test_websocket_errors_include_additive_top_level_fields(app):
    with TestClient(app) as client:
        with client.websocket_connect("/ws/market?api_key=test-api-key") as ws:
            assert ws.receive_json()["type"] == "welcome"
            ws.send_json({"action": "unknown_action"})
            payload = ws.receive_json()
            assert payload["type"] == "error"
            assert payload["data"]["code"] == "UNKNOWN_ACTION"
            assert payload["error"] == payload["data"]
            assert payload["message"] == payload["data"]["message"]


def test_startup_survives_missing_mt5_package(settings, monkeypatch):
    real_import = importlib.import_module

    def missing_mt5(name: str, *args, **kwargs):
        if name == "MetaTrader5":
            raise ModuleNotFoundError("No module named 'MetaTrader5'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", missing_mt5)
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/status").status_code == 200
        startup_errors = app.state.context.runtime.startup_errors
        assert startup_errors
        assert all("password" not in item.lower() for item in startup_errors)
