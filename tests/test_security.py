"""Authentication and private-by-default checks."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_protected_endpoint_requires_api_key(app):
    with TestClient(app) as client:
        assert client.get("/api/v1/quotes").status_code == 401
        assert client.get("/api/v1/account").status_code == 401


def test_wrong_api_key_is_rejected(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/quotes", headers={"X-API-Key": "wrong"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_correct_api_key_is_accepted(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/quotes", headers={"X-API-Key": "test-api-key"})
        assert response.status_code == 200
        assert response.json()["success"] is True


def test_docs_are_available(app):
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_account_never_exposes_password_fields(app):
    with TestClient(app) as client:
        app.state.context.manager.initialize()
        response = client.get("/api/v1/account", headers={"X-API-Key": "test-api-key"})
        text = response.text.lower()
        assert response.status_code == 200
        assert "password" not in text
        assert "secret" not in text
