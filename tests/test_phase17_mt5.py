"""Phase 17 MT5/API hardening regressions (mocked MT5 only, no live broker)."""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import market as market_module
from app.api.market import _in_window
from app.core.exceptions import MT5NotInitializedError
from app.database.models import Base, ServiceEvent
from app.database.repositories import recent_events
from app.mt5 import connection as connection_module
from app.mt5 import terminal as terminal_module
from app.mt5.connection import MT5ConnectionManager
from app.mt5.symbols import SymbolService, classify_symbol, normalize_symbol_name
from app.services import market_collector as collector_module
from app.services.cache import QuoteCache
from app.services.candle_cache import CandleCache
from app.services.history_limiter import HistoryLimiter
from app.services.market_collector import MarketCollector
from app.services.subscription_manager import SubscriptionManager

HEADERS = {"X-API-Key": "test-api-key"}


# --------------------------------------------------------------------------- #
# connection.py
# --------------------------------------------------------------------------- #

def test_module_import_failure_is_wrapped(settings, monkeypatch):
    def missing(name, *args, **kwargs):
        raise ModuleNotFoundError("No module named 'MetaTrader5'")

    monkeypatch.setattr(connection_module.importlib, "import_module", missing)
    manager = MT5ConnectionManager(settings)

    with pytest.raises(MT5NotInitializedError, match="not available"):
        manager.module()


def test_initialize_is_idempotent_and_clears_stale_error(settings, fake_mt5, monkeypatch):
    manager = MT5ConnectionManager(settings, fake_mt5)
    monkeypatch.setattr(manager, "ensure_terminal_running", lambda: None)
    calls: list[dict] = []
    original = fake_mt5.initialize

    def counted_initialize(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(fake_mt5, "initialize", counted_initialize)
    manager._last_error = "stale failure"

    assert manager.initialize() is True
    assert manager.inspect()["last_error"] is None
    assert manager.initialize() is True
    assert len(calls) == 1


def test_last_mt5_error_ignores_success_values(settings):
    mod = SimpleNamespace(last_error=lambda: (0, "OK"))
    manager = MT5ConnectionManager(settings, mod)

    assert manager._last_mt5_error() is None
    mod.last_error = lambda: (None, None)
    assert manager._last_mt5_error() is None
    mod.last_error = lambda: "OK"
    assert manager._last_mt5_error() is None

    mod.last_error = lambda: (5001, "no connection")
    assert manager._last_mt5_error() == "5001: no connection"


def test_call_clears_stale_error_before_attempt(settings, fake_mt5, monkeypatch):
    manager = MT5ConnectionManager(settings, fake_mt5)
    monkeypatch.setattr(manager, "ensure_terminal_running", lambda: None)
    assert manager.initialize() is True

    manager._last_error = "previous failure"
    manager.call("symbols_get")

    assert manager.inspect()["last_error"] is None


@pytest.mark.asyncio
async def test_reconnect_skips_sleep_after_final_attempt(settings, fake_mt5, monkeypatch):
    object.__setattr__(settings, "MT5_RECONNECT_INTERVAL", 0.01)
    object.__setattr__(settings, "MT5_MAX_RECONNECT_ATTEMPTS", 3)
    fake_mt5.initialize_failures = 3
    manager = MT5ConnectionManager(settings, fake_mt5)
    monkeypatch.setattr(manager, "ensure_terminal_running", lambda: None)
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(connection_module.asyncio, "sleep", record_sleep)

    assert await manager.reconnect_async() is False
    assert sleeps == [0.01, 0.02]


# --------------------------------------------------------------------------- #
# terminal.py
# --------------------------------------------------------------------------- #

def test_process_fallback_ignores_mt4_terminal_exe(monkeypatch):
    responses = {
        "wmi": SimpleNamespace(returncode=1, stdout="", stderr=""),
        "tasklist": SimpleNamespace(returncode=0, stdout="terminal.exe\r\n", stderr=""),
    }

    def fake_run(cmd, **kwargs):
        return responses["wmi"] if cmd[0] == "powershell" else responses["tasklist"]

    monkeypatch.setattr(terminal_module.subprocess, "run", fake_run)
    assert terminal_module.is_terminal_process_running() is False

    responses["tasklist"] = SimpleNamespace(
        returncode=0, stdout="terminal.exe\r\nterminal64.exe\r\n", stderr=""
    )
    assert terminal_module.is_terminal_process_running() is True


# --------------------------------------------------------------------------- #
# market_collector.py
# --------------------------------------------------------------------------- #

@pytest.fixture
def collector(settings):
    manager = SimpleNamespace(mark_success=lambda: None)
    quote_service = SimpleNamespace(get_quotes=lambda symbols: [])
    candle_service = SimpleNamespace(
        get_candles=lambda symbol, timeframe, count: {"candles": []}
    )
    symbol_service = SimpleNamespace(list_symbols=lambda: [])
    return MarketCollector(
        settings,
        manager,
        quote_service,
        candle_service,
        symbol_service,
        QuoteCache(),
        CandleCache(),
        SubscriptionManager(),
        lambda: None,
    )


@pytest.mark.asyncio
async def test_candle_events_dedupe_by_epoch_and_clear_on_unsubscribe(collector, monkeypatch):
    published: list[dict] = []
    pairs = [("EURUSDm", "M1")]
    clock = {"now": 1000.0}
    monkeypatch.setattr(collector_module.time, "time", lambda: clock["now"])
    monkeypatch.setattr(collector.subscriptions, "candle_targets", lambda: pairs)
    monkeypatch.setattr(
        collector.subscriptions,
        "publish_candle",
        lambda symbol, timeframe, message: published.append(message),
    )
    candles = {
        "candles": [
            {"epoch": 1000, "completed": True},
            {"epoch": 1060, "completed": True},
        ]
    }
    monkeypatch.setattr(
        collector.candle_service, "get_candles", lambda symbol, timeframe, count: candles
    )

    await collector._poll_candles()
    assert len(published) == 1
    assert collector.stats()["candle_events"] == 1
    assert collector.stats()["candle_pairs"] == 1

    # Same wall-clock bucket with the same broker epoch is a stale duplicate.
    await collector._poll_candles()
    assert len(published) == 1

    # A new bucket with the same epoch must still be suppressed by epoch.
    clock["now"] = 1060.0
    await collector._poll_candles()
    assert len(published) == 1

    candles["candles"] = [{"epoch": 1120, "completed": True}]
    clock["now"] = 1120.0
    await collector._poll_candles()
    assert len(published) == 2

    # Broker time going backwards must not emit an older candle again.
    candles["candles"] = [{"epoch": 1060, "completed": True}]
    clock["now"] = 1180.0
    await collector._poll_candles()
    assert len(published) == 2

    monkeypatch.setattr(collector.subscriptions, "candle_targets", lambda: [])
    await collector._poll_candles()
    assert collector.stats()["candle_pairs"] == 0
    assert collector.candle_cache.get("EURUSDm", "M1") is None

    monkeypatch.setattr(collector.subscriptions, "candle_targets", lambda: pairs)
    await collector._poll_candles()
    assert len(published) == 3


@pytest.mark.asyncio
async def test_quote_status_errors_set_last_error(collector, monkeypatch):
    monkeypatch.setattr(collector.subscriptions, "target_symbols", lambda: ["EURUSDm"])
    monkeypatch.setattr(
        collector.quote_service,
        "get_quotes",
        lambda symbols: [{"symbol": "EURUSDm", "status": "error"}],
    )

    await collector._poll_quotes()

    last_error = collector.stats()["last_error"]
    assert last_error and "EURUSDm" in last_error and "error" in last_error


@pytest.mark.asyncio
async def test_poll_cycle_does_not_clear_quote_error(collector, monkeypatch):
    monkeypatch.setattr(collector.subscriptions, "target_symbols", lambda: ["EURUSDm"])
    monkeypatch.setattr(
        collector.quote_service,
        "get_quotes",
        lambda symbols: [{"symbol": "EURUSDm", "status": "error"}],
    )

    async def stop_after_one_cycle(_delay: float):
        raise asyncio.CancelledError

    monkeypatch.setattr(collector_module.asyncio, "sleep", stop_after_one_cycle)

    with pytest.raises(asyncio.CancelledError):
        await collector._run()

    assert collector.stats()["last_error"]


@pytest.mark.asyncio
async def test_sqlite_writes_run_in_worker_threads(collector, monkeypatch):
    monkeypatch.setattr(collector.subscriptions, "target_symbols", lambda: ["EURUSDm"])
    monkeypatch.setattr(
        collector.quote_service,
        "get_quotes",
        lambda symbols: [{"symbol": "EURUSDm", "status": "ok"}],
    )
    collector._poll_count = 1
    calls: list[tuple[str, bool]] = []

    def fake_stat(*args):
        calls.append(("stat", threading.current_thread() is threading.main_thread()))

    def fake_event(*args, **kwargs):
        calls.append(("event", threading.current_thread() is threading.main_thread()))

    monkeypatch.setattr(collector_module, "record_collector_stat", fake_stat)
    monkeypatch.setattr(collector_module, "record_event", fake_event)

    await collector._poll_quotes()
    await collector.on_connection_event(True, "reconnected")

    assert calls == [("stat", False), ("event", False)]


# --------------------------------------------------------------------------- #
# history_limiter.py
# --------------------------------------------------------------------------- #

def test_history_limiter_removes_expired_other_clients(settings):
    limiter = HistoryLimiter(settings)
    now = time.monotonic()
    for index in range(1001):
        limiter._calls[f"stale-{index}"].append(now - 120)
    limiter._calls["recent"].append(now - 5)

    assert limiter.stats()["tracked_clients"] == 1002
    assert limiter._allow("current")[0] is True

    tracked = limiter.stats()["tracked_clients"]
    assert tracked == 2
    assert "current" in limiter._calls
    assert "recent" in limiter._calls
    assert "stale-0" not in limiter._calls


# --------------------------------------------------------------------------- #
# repositories.py
# --------------------------------------------------------------------------- #

def test_recent_events_filters_request_id_before_limit():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)

    def session_factory() -> Session:
        return Session(engine)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with session_factory() as session:
        session.add(
            ServiceEvent(
                timestamp=base,
                kind="audit",
                level="INFO",
                message="target",
                details={"request_id": "req-old"},
            )
        )
        session.add_all(
            [
                ServiceEvent(
                    timestamp=base + timedelta(seconds=index + 1),
                    kind="noise",
                    level="INFO",
                    message=f"noise-{index}",
                    details={},
                )
                for index in range(600)
            ]
        )
        session.add(
            ServiceEvent(
                timestamp=base + timedelta(seconds=700),
                kind="audit",
                level="INFO",
                message="newer",
                details={"request_id": "req-new"},
            )
        )
        session.commit()

    found = recent_events(session_factory, limit=20, request_id="req-old")

    assert [item["message"] for item in found] == ["target"]


# --------------------------------------------------------------------------- #
# api/market.py
# --------------------------------------------------------------------------- #

def test_in_window_is_end_exclusive_and_supports_overnight():
    assert _in_window(7, 7, 16) is True
    assert _in_window(10, 7, 16) is True
    assert _in_window(16, 7, 16) is False
    assert _in_window(22, 21, 6) is True
    assert _in_window(5, 21, 6) is True
    assert _in_window(10, 21, 6) is False


def test_market_sessions_at_ten_utc(app, monkeypatch):
    class FixedDatetime:
        @staticmethod
        def now(tz):
            return datetime(2026, 11, 27, 10, 0, tzinfo=tz)

    monkeypatch.setattr(market_module, "datetime", FixedDatetime)

    with TestClient(app) as client:
        response = client.get("/api/v1/market/sessions", headers=HEADERS)

    assert response.status_code == 200
    sessions = {item["name"]: item["open"] for item in response.json()["data"]}
    assert sessions["London"] is True
    assert sessions["Tokyo"] is False
    assert sessions["New York"] is False
    assert sessions["Sydney"] is False


# --------------------------------------------------------------------------- #
# mt5/symbols.py
# --------------------------------------------------------------------------- #

def test_broker_suffixes_are_classification_only(settings, fake_mt5):
    for name in ("EURUSDm", "EURUSD.pro", "EURUSD#", "EURUSDmini", "EURUSD_m"):
        assert normalize_symbol_name(name) == "EURUSD"
        assert classify_symbol(name) == "forex_major"

    manager = MT5ConnectionManager(settings, fake_mt5)
    manager._initialized = True  # mocked module; no live broker call
    service = SymbolService(manager, settings)

    assert service.get_symbol("EURUSDm")["symbol"] == "EURUSDm"
    assert service.get_symbol("EURUSDm")["category"] == "forex_major"
