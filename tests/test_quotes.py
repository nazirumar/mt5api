"""Live quote and freshness tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

HEADERS = {"X-API-Key": "test-api-key"}


def test_single_quote_structure(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/quote/EURUSDm", headers=HEADERS)
        assert response.status_code == 200
        quote = response.json()["data"]
        assert quote["symbol"] == "EURUSDm"
        assert quote["bid"] > 0
        assert quote["ask"] >= quote["bid"]
        assert quote["spread"] is not None
        assert quote["spread_points"] is not None
        assert quote["is_fresh"] is True
        assert quote["timestamp"].endswith("Z")


def test_multiple_quotes(app):
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/quotes?symbols=EURUSDm,XAUUSDm,GBPUSDm", headers=HEADERS
        )
        assert response.status_code == 200
        quotes = response.json()["data"]
        assert [item["symbol"] for item in quotes] == ["EURUSDm", "XAUUSDm", "GBPUSDm"]


def test_stale_tick_is_not_reported_as_live(app, fake_mt5):
    fake_mt5.stale = True
    with TestClient(app) as client:
        quote = client.get("/api/v1/quote/XAUUSDm", headers=HEADERS).json()["data"]
        assert quote["is_fresh"] is False
        assert quote["market_available"] is True


def test_missing_tick_never_invents_price(app, fake_mt5):
    fake_mt5.symbols.append(
        __import__("types").SimpleNamespace(
            name="NOSPREADm", description="No tick", digits=5, point=0.00001,
            currency_base="XXX", currency_profit="USD", bid=0, ask=0,
        )
    )
    with TestClient(app) as client:
        quote = client.get("/api/v1/quote/NOSPREADm", headers=HEADERS).json()["data"]
        assert quote["bid"] is None
        assert quote["ask"] is None
        assert quote["status"] == "no_tick"
        assert quote["market_available"] is False
