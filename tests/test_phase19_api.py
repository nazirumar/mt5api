"""API tests for the multi-timeframe snapshot engine using mocked MT5."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import ServiceError
from app.main import create_app
from app.mt5.candles import CandleService
from app.mt5.connection import MT5ConnectionManager
from app.context.engine import MarketContextEngine

HEADERS = {"X-API-Key": "test-api-key"}
PATH = "/api/v1/market/snapshot/EURUSDm"


def make_candles(count: int, *, start: int | None = None, step: int = 60) -> list[dict]:
    base = int(start if start is not None else time.time()) - count * step
    candles = []
    for index in range(count):
        epoch = base + index * step
        price = 1.0 + index * 0.001
        candles.append(
            {
                "time": datetime.fromtimestamp(epoch, tz=timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "epoch": epoch,
                "open": price,
                "high": price + 0.002,
                "low": price - 0.002,
                "close": price + 0.001,
                "tick_volume": 100 + index,
                "real_volume": 0,
                "spread": 12,
                "completed": True,
            }
        )
    return candles


def forced_classifications(values: list[str]):
    """Force Phase 18 trend classifications while keeping real MT5 candle retrieval."""
    original = MarketContextEngine.context_for_series
    remaining = list(values)

    def wrapper(self, series, quote, *args, **kwargs):
        result = original(self, series, quote, *args, **kwargs)
        classification = remaining.pop(0)
        result["trend"]["classification"] = classification
        result["trend"]["direction"] = (
            classification if classification in {"BULLISH", "BEARISH"} else "NEUTRAL"
        )
        return result

    return wrapper


def test_snapshot_requires_api_key(app):
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1", "limit": 20})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_snapshot_success_envelope_request_id_and_bounded_real_candles(app):
    with TestClient(app) as client:
        response = client.get(
            PATH,
            params={"timeframes": "M1,M5,H1", "limit": 30},
            headers={**HEADERS, "X-Request-ID": "phase19-request"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert response.headers["X-Request-ID"] == "phase19-request"
    assert body["meta"]["request_id"] == "phase19-request"
    data = body["data"]
    assert data["meta"]["request_id"] == "phase19-request"
    assert isinstance(data["snapshot_id"], str) and data["snapshot_id"]
    assert data["generated_at"].endswith("Z")
    assert data["symbol"] == "EURUSDm"
    assert data["snapshot_status"] == "COMPLETE"
    assert data["quote"]["freshness"] == "FRESH"
    assert list(data["timeframes"].keys()) == ["M1", "M5", "H1"]
    assert data["alignment"]["classification"] == "FULLY_ALIGNED"
    assert data["alignment"]["disclaimer"].startswith("Alignment describes")
    assert data["data_quality"]["usable"] is True
    assert data["warnings"] == [] or isinstance(data["warnings"], list)

    for timeframe in ("M1", "M5", "H1"):
        entry = data["timeframes"][timeframe]
        assert entry["status"] == "OK"
        assert 0 < entry["metadata"]["returned_candles"] <= 100
        assert entry["metadata"]["available_candles"] == 20
        assert entry["metadata"]["retrieved_at"].endswith("Z")
        assert entry["context"]["trend"]["classification"] in {
            "BULLISH", "BEARISH", "NEUTRAL", "MIXED", "UNKNOWN"
        }
        for candle in entry["candles"]:
            assert candle["time"].endswith("Z")
            assert isinstance(candle["epoch"], int)
            assert isinstance(candle["completed"], bool)


def test_all_requested_timeframes_including_intraday_and_daily(app):
    timeframes = "M1,M5,M15,M30,H1,H4,D1"
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": timeframes, "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()["data"]
    assert list(data["timeframes"].keys()) == timeframes.split(",")
    assert [item["metadata"]["duration_seconds"] for item in data["timeframes"].values()] == [
        60, 300, 900, 1800, 3600, 14400, 86400
    ]
    assert all(item["status"] == "OK" for item in data["timeframes"].values())


def test_additional_supported_mt5_timeframes_are_accepted(settings, fake_mt5):
    custom = settings.model_copy(update={"SNAPSHOT_MAX_TIMEFRAMES": 9})
    app = create_app(custom)
    with TestClient(app) as client:
        response = client.get(
            PATH, params={"timeframes": "W1,MN1", "limit": 20}, headers=HEADERS
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert set(data["timeframes"]) == {"W1", "MN1"}
    assert data["timeframes"]["W1"]["metadata"]["duration_seconds"] == 604800
    assert data["timeframes"]["MN1"]["metadata"]["duration_seconds"] == 2592000
    assert all(item["status"] == "OK" for item in data["timeframes"].values())


def test_include_flags_disable_candles_indicators_and_structure(app):
    with TestClient(app) as client:
        response = client.get(
            PATH,
            params={
                "timeframes": "M1,H1",
                "limit": 20,
                "include_candles": "false",
                "include_indicators": "false",
                "include_structure": "false",
            },
            headers=HEADERS,
        )

    assert response.status_code == 200
    data = response.json()["data"]
    for entry in data["timeframes"].values():
        assert entry["candles"] == []
        assert entry["metadata"]["returned_candles"] == 0
        assert entry["context"]["indicators"]["included"] is False
        assert entry["context"]["candle_structure"]["summary"]["included"] is False
    assert data["alignment"]["classification"] == "INSUFFICIENT_DATA"


def test_context_can_be_omitted_without_failing(app):
    with TestClient(app) as client:
        response = client.get(
            PATH,
            params={"timeframes": "M1,H1", "limit": 20, "include_context": "false"},
            headers=HEADERS,
        )

    assert response.status_code == 200
    data = response.json()["data"]
    assert all(entry["context"]["available"] is False for entry in data["timeframes"].values())
    assert data["alignment"]["classification"] == "INSUFFICIENT_DATA"
    assert data["timeframes"]["M1"]["data_quality"]["usable"] is True


@pytest.mark.parametrize(
    ("params", "expected_status", "expected_code"),
    [
        ({"timeframes": ""}, 400, "INVALID_REQUEST"),
        ({"timeframes": "M1,,H1"}, 400, "INVALID_REQUEST"),
        ({"timeframes": "M1,M1"}, 400, "INVALID_REQUEST"),
        ({"timeframes": "m1,m1"}, 400, "INVALID_REQUEST"),
        ({"timeframes": "NOT_A_TF"}, 400, "INVALID_TIMEFRAME"),
        ({"timeframes": "M1,M5,M15,M30,H1,H4,D1,W1,MN1"}, 400, "INVALID_REQUEST"),
        ({"timeframes": "M1", "limit": 501}, 400, "INVALID_REQUEST"),
        ({"timeframes": "M1", "limit": 0}, 422, "VALIDATION_ERROR"),
        ({"timeframes": "M1", "limit": 5001}, 422, "VALIDATION_ERROR"),
    ],
)
def test_invalid_empty_duplicate_excessive_and_over_limit_requests(app, params, expected_status, expected_code):
    with TestClient(app) as client:
        response = client.get(PATH, params=params, headers=HEADERS)

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == expected_code


def test_unknown_symbol_returns_broker_native_404(app):
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/market/snapshot/DOES_NOT_EXIST",
            params={"timeframes": "M1", "limit": 20},
            headers=HEADERS,
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SYMBOL_NOT_FOUND"


def test_stale_quote_marks_snapshot_partial_and_suppresses_classifications(app, fake_mt5):
    fake_mt5.stale = True
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1,H1", "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["quote"]["freshness"] == "VERY_STALE"
    assert data["snapshot_status"] == "PARTIAL"
    assert data["data_quality"]["usable"] is False
    assert data["data_quality"]["grade"] == "UNUSABLE"
    assert data["synchronization"]["classification"] == "UNSYNCHRONIZED"
    assert any("Context classifications suppressed" in warning for warning in data["warnings"])
    assert data["timeframes"]["M1"]["context"]["trend"]["classification"] == "UNKNOWN"
    assert data["timeframes"]["M1"]["context"]["trend"]["suppressed_reason"] == "Data quality is unusable"


def test_disconnected_mt5_is_reported_without_fabricating_data(app, monkeypatch):
    monkeypatch.setattr(MT5ConnectionManager, "is_connected", lambda self: False)
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1,H1", "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["data_quality"]["checks"]["mt5_connected"] is False
    assert data["data_quality"]["usable"] is False
    assert data["snapshot_status"] == "PARTIAL"
    assert any("MT5 terminal connection" in warning for warning in data["warnings"])


def test_one_failed_timeframe_is_recorded_without_failing_snapshot(app, monkeypatch):
    original = CandleService.get_candles

    def failing(self, symbol, timeframe, *args, **kwargs):
        if timeframe == "H1":
            raise ServiceError("history unavailable", code="MT5_OPERATION_FAILED")
        return original(self, symbol, timeframe, *args, **kwargs)

    monkeypatch.setattr(CandleService, "get_candles", failing)
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1,H1,M5", "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["timeframes"]["H1"]["status"] == "ERROR"
    assert data["timeframes"]["H1"]["error"]["code"] == "MT5_OPERATION_FAILED"
    assert data["timeframes"]["M1"]["status"] == "OK"
    assert data["timeframes"]["M5"]["status"] == "OK"
    assert data["snapshot_status"] == "PARTIAL"
    assert "H1" in data["synchronization"]["missing_timeframes"]
    assert "H1" in data["data_quality"]["missing_timeframes"]
    assert data["alignment"]["incomplete"] is True
    assert any("H1" in warning for warning in data["warnings"])


def test_insufficient_history_suppresses_context(app, monkeypatch):
    few = make_candles(3)

    def short_series(self, symbol, timeframe, *args, **kwargs):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": len(few),
            "limit": kwargs.get("limit", args[0] if args else 20),
            "candles": [dict(item) for item in few],
            "source": "mt5_history",
        }

    monkeypatch.setattr(CandleService, "get_candles", short_series)
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1", "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()["data"]
    entry = data["timeframes"]["M1"]
    assert entry["status"] == "OK"
    assert entry["metadata"]["available_candles"] == 3
    assert entry["data_quality"]["grade"] == "UNUSABLE"
    assert any("at least 10 completed candles" in item for item in entry["data_quality"]["missing_data"])
    assert data["data_quality"]["usable"] is False
    assert entry["context"]["trend"]["classification"] == "UNKNOWN"
    assert data["snapshot_status"] == "PARTIAL"


def test_empty_candles_are_reported_as_partial_not_fabricated(app, monkeypatch):
    def empty_series(self, symbol, timeframe, *args, **kwargs):
        return {"symbol": symbol, "timeframe": timeframe, "count": 0, "candles": [], "source": "mt5_history"}

    monkeypatch.setattr(CandleService, "get_candles", empty_series)
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1,H1", "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["snapshot_status"] == "PARTIAL"
    assert all(entry["metadata"]["available_candles"] == 0 for entry in data["timeframes"].values())
    assert all(entry["candles"] == [] for entry in data["timeframes"].values())
    assert any("No candles returned" in warning for warning in data["warnings"])
    assert all(entry["context"]["trend"]["classification"] == "UNKNOWN" for entry in data["timeframes"].values())


def test_forming_candle_is_marked_and_not_treated_as_completed(app):
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M5", "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    entry = response.json()["data"]["timeframes"]["M5"]
    assert entry["metadata"]["latest_candle_completion"] == "FORMING"
    assert entry["candles"][-1]["completed"] is False
    assert entry["data_quality"]["timestamp_monotonic"] is True


def test_out_of_order_and_duplicate_candles_are_normalized_with_warnings(app, monkeypatch):
    ordered = make_candles(12)
    messy = list(reversed(ordered)) + [dict(ordered[0])]

    def messy_series(self, symbol, timeframe, *args, **kwargs):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": len(messy),
            "candles": [dict(item) for item in messy],
            "source": "mt5_history",
        }

    monkeypatch.setattr(CandleService, "get_candles", messy_series)
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1", "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    data = response.json()["data"]
    entry = data["timeframes"]["M1"]
    assert entry["metadata"]["available_candles"] == 12
    assert entry["data_quality"]["duplicate_candles"] == 0
    assert entry["data_quality"]["timestamp_monotonic"] is True
    assert [item["epoch"] for item in entry["candles"]] == sorted(item["epoch"] for item in entry["candles"])
    assert any("duplicate" in warning for warning in data["warnings"])
    assert any("sorted chronologically" in warning for warning in data["warnings"])


def test_all_bullish_timeframes_align(app):
    # FakeMT5 emits one-minute-spaced candles, so M1 and M5 provide enough
    # completed candles for Phase 18 trend analysis; higher bars do not.
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1,M5", "limit": 30}, headers=HEADERS)

    assert response.status_code == 200
    alignment = response.json()["data"]["alignment"]
    assert alignment["classification"] == "FULLY_ALIGNED"
    assert alignment["bullish_count"] == 2
    assert alignment["conflict_count"] == 0


def test_all_bearish_timeframes_align(app, monkeypatch):
    monkeypatch.setattr(
        MarketContextEngine, "context_for_series", forced_classifications(["BEARISH", "BEARISH", "BEARISH"])
    )
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1,H1,D1", "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    alignment = response.json()["data"]["alignment"]
    assert alignment["classification"] == "FULLY_ALIGNED"
    assert alignment["bearish_count"] == 3


def test_conflicting_timeframes_are_reported(app, monkeypatch):
    monkeypatch.setattr(
        MarketContextEngine, "context_for_series", forced_classifications(["BULLISH", "BEARISH"])
    )
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1,H1", "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    alignment = response.json()["data"]["alignment"]
    assert alignment["classification"] == "CONFLICTING"
    assert alignment["conflict_count"] == 1
    assert alignment["conflicts"][0]["higher_timeframe"] == "H1"
    assert alignment["conflicts"][0]["lower_timeframe"] == "M1"


def test_mixed_timeframes_are_reported_without_signal_language(app, monkeypatch):
    monkeypatch.setattr(
        MarketContextEngine,
        "context_for_series",
        forced_classifications(["BULLISH", "NEUTRAL", "MIXED"]),
    )
    with TestClient(app) as client:
        response = client.get(PATH, params={"timeframes": "M1,H1,D1", "limit": 20}, headers=HEADERS)

    assert response.status_code == 200
    alignment = response.json()["data"]["alignment"]
    assert alignment["classification"] == "MIXED"
    assert "BUY/SELL signal" in alignment["disclaimer"]
    assert "probability" in response.json()["data"]["data_quality"]["disclaimer"]


def test_snapshot_history_slot_is_rate_limited(settings, fake_mt5):
    limited = settings.model_copy(update={"HISTORY_RATE_LIMIT_PER_MINUTE": 1})
    app = create_app(limited)
    with TestClient(app) as client:
        first = client.get(PATH, params={"timeframes": "M1", "limit": 20}, headers=HEADERS)
        second = client.get(PATH, params={"timeframes": "M1", "limit": 20}, headers=HEADERS)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "RATE_LIMITED"


def test_snapshot_default_settings_are_bounded(settings):
    assert settings.SNAPSHOT_DEFAULT_LIMIT <= settings.SNAPSHOT_MAX_LIMIT
    assert settings.SNAPSHOT_MAX_LIMIT <= settings.MAX_CANDLE_LIMIT
    assert settings.SNAPSHOT_MAX_CANDLES_PER_TIMEFRAME <= settings.SNAPSHOT_MAX_LIMIT
    assert settings.SNAPSHOT_MIN_ALIGNED_TIMEFRAMES <= settings.SNAPSHOT_MAX_TIMEFRAMES
    assert settings.SNAPSHOT_SYNC_TIGHT_SECONDS <= settings.SNAPSHOT_ACCEPTABLE_SYNC_SECONDS


def test_docs_env_and_examples_are_aligned_without_browser_api_keys():
    root = Path(__file__).resolve().parents[1]
    env_text = (root / ".env.example").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    python_example = (root / "examples" / "python_client.py").read_text(encoding="utf-8")
    nextjs_example = (root / "examples" / "nextjs_client.ts").read_text(encoding="utf-8")
    snapshot_doc = (root / "docs" / "PHASE_19_MULTI_TIMEFRAME_SNAPSHOT.md").read_text(encoding="utf-8")

    for name in (
        "SNAPSHOT_DEFAULT_TIMEFRAMES",
        "SNAPSHOT_MAX_TIMEFRAMES",
        "SNAPSHOT_DEFAULT_LIMIT",
        "SNAPSHOT_MAX_LIMIT",
        "SNAPSHOT_MAX_CANDLES_PER_TIMEFRAME",
        "SNAPSHOT_ALIGNMENT_DOMINANT_RATIO",
    ):
        assert name in env_text

    assert "/api/v1/market/snapshot/" in python_example
    assert "/api/v1/market/snapshot/" in nextjs_example
    assert "fetchMarketSnapshot" in nextjs_example
    assert "docs/PHASE_19_MULTI_TIMEFRAME_SNAPSHOT.md" in readme
    assert "snapshot_status" in snapshot_doc

    # API keys must not be exposed through public browser environment variables.
    assert "NEXT_PUBLIC_API_KEY" not in nextjs_example
    assert "NEXT_PUBLIC_MT5_API_KEY" not in nextjs_example
    assert "NEXT_PUBLIC_API_KEY" not in readme
    assert "NEXT_PUBLIC_MT5_API_KEY" not in readme
