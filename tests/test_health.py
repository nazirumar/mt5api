"""Health and status endpoint tests."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_is_public_and_returns_envelope(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["data"]["api_status"] == "ok"
        assert "uptime_seconds" in body["data"]
        assert "api_version" in body["data"]
        assert "broker_clock" in body["data"]


def test_health_reports_degraded_when_mt5_not_initialized(app):
    with TestClient(app) as client:
        app.state.context.manager.shutdown()
        body = client.get("/api/v1/health").json()["data"]
        assert body["mt5_initialized"] is False
        assert body["status"] == "degraded"


def test_status_contains_components(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/status")
        assert response.status_code == 200
        data = response.json()["data"]
        assert {"api", "mt5", "broker_clock", "collector", "monitor", "cache"} <= set(data)
