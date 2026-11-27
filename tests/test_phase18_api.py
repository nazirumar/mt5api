"""Phase 18 REST endpoint tests with the existing deterministic fake MT5."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.mt5.candles import CandleService
from app.mt5.connection import MT5ConnectionManager

HEADERS = {"X-API-Key": "test-api-key"}
PATH = "/api/v1/market/context/EURUSDm"


def test_context_requires_api_key(app):
    with TestClient(app) as client:
        response = client.get(PATH)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_context_success_envelope_and_primary_classifications(app):
    with TestClient(app) as client:
        response = client.get(PATH, headers={**HEADERS, "X-Request-ID": "phase18-test"}, params={"lookback": 30})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["meta"]["request_id"] == "phase18-test"
    assert response.headers["X-Request-ID"] == "phase18-test"

    data = body["data"]
    assert data["symbol"] == "EURUSDm"
    assert data["generated_at"].endswith("Z")
    assert data["market_status"] == "AVAILABLE"
    assert data["quote"]["freshness"] == "FRESH"
    assert data["quote"]["digits"] == 5
    assert data["quote"]["point"] == 0.00001
    assert data["quote"]["mid"] is not None
    assert data["data_quality"]["grade"] == "EXCELLENT"
    assert data["data_quality"]["usable"] is True
    assert data["context"]["trend"]["classification"] == "BULLISH"
    assert data["context"]["regime"]["classification"] == "TRENDING"
    assert data["context"]["session"]["classification"] == "UNKNOWN"
    assert data["meta"]["request_id"] == "phase18-test"
    assert "test-api-key" not in response.text


def test_context_uses_broker_native_symbol_metadata(app):
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/market/context/XAUUSDm",
            headers=HEADERS,
            params={"timeframe": "M5", "lookback": 30},
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["symbol"] == "XAUUSDm"
    assert data["quote"]["digits"] == 2
    assert data["quote"]["point"] == 0.01
    assert data["quote"]["spread_points"] == 25.0


def test_context_invalid_symbol_timeframe_and_lookback(app):
    with TestClient(app) as client:
        missing = client.get("/api/v1/market/context/DOESNOTEXIST", headers=HEADERS)
        bad_tf = client.get(PATH, headers=HEADERS, params={"timeframe": "NOT_A_TF"})
        bad_lookback = client.get(PATH, headers=HEADERS, params={"lookback": 1000})
        query_validation = client.get(PATH, headers=HEADERS, params={"lookback": 0})

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "SYMBOL_NOT_FOUND"
    assert bad_tf.status_code == 400
    assert bad_tf.json()["error"]["code"] == "INVALID_TIMEFRAME"
    assert bad_lookback.status_code == 400
    assert bad_lookback.json()["error"]["code"] == "INVALID_REQUEST"
    assert query_validation.status_code == 422
    assert query_validation.json()["error"]["code"] == "VALIDATION_ERROR"


def test_context_multi_timeframe_and_include_flags(app):
    with TestClient(app) as client:
        response = client.get(
            PATH,
            headers=HEADERS,
            params={
                "timeframes": "M1,M5,H1",
                "lookback": 30,
                "include_candles": "true",
                "include_indicators": "false",
                "include_structure": "false",
            },
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert list(data["timeframes"]) == ["M1", "M5", "H1"]
    assert data["meta"]["timeframes_requested"] == ["M1", "M5", "H1"]
    assert len(data["timeframes"]["M5"]["candles"]) <= 25
    assert data["timeframes"]["M5"]["indicators"]["included"] is False
    assert data["timeframes"]["M5"]["candle_structure"]["summary"]["included"] is False


def test_context_stale_quote_is_reported_not_fabricated(app, fake_mt5):
    fake_mt5.stale = True
    with TestClient(app) as client:
        response = client.get(PATH, headers=HEADERS, params={"lookback": 30})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["quote"]["freshness"] == "VERY_STALE"
    assert data["data_quality"]["grade"] == "UNUSABLE"
    assert data["data_quality"]["usable"] is False
    assert data["context"]["trend"]["classification"] == "UNKNOWN"
    assert data["market_status"] == "UNAVAILABLE"


def test_context_insufficient_history_is_unusable(app, monkeypatch):
    def short_history(self, symbol, timeframe, limit=None, start=None, end=None, info=None):
        base = 1_700_000_000
        candles = [
            {
                "time": "2026-01-01T00:00:00Z",
                "epoch": base + index * 300,
                "open": 1.0 + index * 0.001,
                "high": 1.002 + index * 0.001,
                "low": 0.998 + index * 0.001,
                "close": 1.001 + index * 0.001,
                "spread": 10,
                "completed": True,
            }
            for index in range(3)
        ]
        return {"symbol": symbol, "timeframe": timeframe, "count": 3, "limit": limit, "candles": candles}

    monkeypatch.setattr(CandleService, "get_candles", short_history)
    with TestClient(app) as client:
        response = client.get(PATH, headers=HEADERS, params={"lookback": 30})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["data_quality"]["usable"] is False
    assert "at least 10 completed candles" in data["data_quality"]["missing_data"]
    assert data["context"]["trend"]["classification"] == "UNKNOWN"


def test_context_disconnected_mt5_is_unusable(app, monkeypatch):
    monkeypatch.setattr(MT5ConnectionManager, "is_connected", lambda self: False)
    with TestClient(app) as client:
        response = client.get(PATH, headers=HEADERS, params={"lookback": 30})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["data_quality"]["checks"]["mt5_connected"] is False
    assert data["data_quality"]["usable"] is False
    assert data["context"]["regime"]["classification"] == "UNKNOWN"


def test_context_respects_history_rate_limit(settings, fake_mt5):
    limited_settings = settings.model_copy(update={"HISTORY_RATE_LIMIT_PER_MINUTE": 1})
    limited_app = create_app(limited_settings)
    with TestClient(limited_app) as client:
        first = client.get(PATH, headers=HEADERS, params={"lookback": 30})
        second = client.get(PATH, headers=HEADERS, params={"lookback": 30})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RATE_LIMITED"


def test_context_settings_have_safe_defaults(settings):
    assert settings.CONTEXT_DEFAULT_TIMEFRAME == "M5"
    assert settings.CONTEXT_DEFAULT_LOOKBACK <= settings.CONTEXT_MAX_LOOKBACK
    assert settings.CONTEXT_MAX_TIMEFRAMES >= 1
    assert settings.CONTEXT_MIN_CANDLES >= 5
