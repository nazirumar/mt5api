"""Historical candle and tick tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi.testclient import TestClient

from app.core.helpers import attr
from app.services.data_validator import normalize_candles

HEADERS = {"X-API-Key": "test-api-key"}


def test_candles_are_normalized_and_bounded(app):
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/candles/EURUSDm?timeframe=M1&limit=5", headers=HEADERS
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["timeframe"] == "M1"
        assert len(data["candles"]) <= 5
        candle = data["candles"][0]
        assert candle["time"].endswith("Z")
        assert candle["high"] >= candle["low"]
        assert isinstance(candle["completed"], bool)


def test_range_candles_accept_utc_timestamps(app):
    with TestClient(app) as client:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=2)
        response = client.get(
            "/api/v1/candles/XAUUSDm",
            params={
                "timeframe": "M5",
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            headers=HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["data"]["candles"]


def test_numpy_structured_records_are_supported():
    dtype = [
        ("time", "<i8"),
        ("open", "<f8"),
        ("high", "<f8"),
        ("low", "<f8"),
        ("close", "<f8"),
        ("tick_volume", "<u8"),
        ("spread", "<i4"),
        ("real_volume", "<u8"),
    ]
    records = np.array([(1_700_000_000, 1.0, 1.2, 0.9, 1.1, 10, 2, 0)], dtype=dtype)

    assert attr(records[0], "open") == 1.0
    candles = normalize_candles(records, 300)
    assert len(candles) == 1
    assert candles[0]["open"] == 1.0
    assert candles[0]["completed"] is True


def test_invalid_timeframe(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/candles/EURUSDm?timeframe=Z1", headers=HEADERS)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_TIMEFRAME"


def test_historical_ticks(app):
    with TestClient(app) as client:
        start = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        response = client.get(
            "/api/v1/ticks/EURUSDm", params={"start": start}, headers=HEADERS
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["count"] == 10
        assert data["ticks"][0]["timestamp"].endswith("Z")


def test_ticks_require_start(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/ticks/EURUSDm", headers=HEADERS)
        assert response.status_code == 422
