"""Symbol catalog tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

HEADERS = {"X-API-Key": "test-api-key"}


def test_list_symbols(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/symbols", headers=HEADERS)
        assert response.status_code == 200
        symbols = response.json()["data"]
        names = [item["symbol"] for item in symbols]
        assert "EURUSDm" in names
        assert "XAUUSDm" in names


def test_symbol_fields_are_complete(app):
    with TestClient(app) as client:
        item = client.get("/api/v1/symbols/EURUSDm", headers=HEADERS).json()["data"]
        for field in (
            "symbol", "description", "base_currency", "quote_currency", "digits", "point",
            "tick_size", "tick_value", "contract_size", "trade_allowed", "bid", "ask",
        ):
            assert field in item
        assert item["base_currency"] == "EUR"
        assert item["quote_currency"] == "USD"


def test_search_symbols(app):
    with TestClient(app) as client:
        results = client.get("/api/v1/symbols/search?q=EUR", headers=HEADERS).json()["data"]
        assert any(item["symbol"] == "EURUSDm" for item in results)


def test_unknown_symbol_returns_404_with_suggestions(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/symbols/EURUSD", headers=HEADERS)
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "SYMBOL_NOT_FOUND"
        assert "suggestions" in body["error"]["details"]


def test_no_symbol_substitution(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/symbols/EURUSD", headers=HEADERS)
        assert response.json()["data"]["symbol"] if response.status_code == 200 else True
        assert response.status_code == 404
