"""Shared pytest fixtures: deterministic fake MT5 terminal, no live trading account."""

from __future__ import annotations

import importlib
import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("DISABLE_BACKGROUND", "true")

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def make_record(**kwargs):
    return SimpleNamespace(**kwargs)


class FakeMT5:
    """Minimal synchronous MT5 surface used by the unit tests."""

    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 12
    TIMEFRAME_M30 = 13
    TIMEFRAME_H1 = 8
    TIMEFRAME_H4 = 16
    TIMEFRAME_D1 = 24
    TIMEFRAME_W1 = 52
    TIMEFRAME_MN1 = 72
    COPY_TICKS_ALL = 0

    def __init__(self):
        self.initialized = False
        self.shutdown_count = 0
        self.initialize_failures = 0
        self.symbols = [
            make_record(
                name="EURUSDm", description="Euro / US Dollar", currency_base="EUR",
                currency_profit="USD", digits=5, point=0.00001, trade_tick_size=0.00001,
                trade_tick_value=1.0, trade_contract_size=100000, trade_mode=3,
                trade_allowed=True, visible=True, bid=1.085, ask=1.0852,
            ),
            make_record(
                name="XAUUSDm", description="Gold / US Dollar", currency_base="XAU",
                currency_profit="USD", digits=2, point=0.01, trade_tick_size=0.01,
                trade_tick_value=1.0, trade_contract_size=100, trade_mode=3,
                trade_allowed=True, visible=True, bid=2350.10, ask=2350.35,
            ),
            make_record(
                name="GBPUSDm", description="British Pound / US Dollar", currency_base="GBP",
                currency_profit="USD", digits=5, point=0.00001, trade_tick_size=0.00001,
                trade_tick_value=1.0, trade_contract_size=100000, trade_mode=3,
                trade_allowed=True, visible=True, bid=1.265, ask=1.2653,
            ),
        ]
        self.stale = False

    def initialize(self, **kwargs):
        if self.initialize_failures > 0:
            self.initialize_failures -= 1
            return False
        self.initialized = True
        return True

    def shutdown(self):
        self.shutdown_count += 1
        self.initialized = False

    def last_error(self):
        return (0, "OK")

    def terminal_info(self):
        return make_record(connected=True, company="Fake Broker", path="C:\\Fake\\terminal.exe")

    def account_info(self):
        return make_record(
            login=123456, server="Fake-Demo", company="Fake Broker", name="Test User",
            currency="USD", balance=10000.0, credit=0.0, profit=0.0, equity=10000.0,
            margin=0.0, margin_free=10000.0, margin_level=0.0, leverage=100, trade_mode=0,
        )

    def symbols_get(self):
        return list(self.symbols)

    def symbol_info(self, symbol=None):
        if symbol is None:
            return None
        for item in self.symbols:
            if item.name == symbol:
                return item
        return None

    def symbol_select(self, symbol, enable):
        return self.symbol_info(symbol) is not None

    def symbol_info_tick(self, symbol):
        if self.symbol_info(symbol) is None or symbol == "NOSPREADm":
            return None
        stamp = int(time.time() * 1000)
        if self.stale:
            stamp -= 10 * 60 * 1000
        return make_record(
            bid=1.085 if symbol == "EURUSDm" else (2350.10 if symbol == "XAUUSDm" else 1.265),
            ask=1.0852 if symbol == "EURUSDm" else (2350.35 if symbol == "XAUUSDm" else 1.2653),
            last=0.0, volume=7, real_volume=0, flags=0,
            time=int(stamp / 1000), time_msc=stamp,
        )

    def copy_rates_from_pos(self, symbol, timeframe, position, count):
        now = int(time.time())
        return [
            make_record(
                time=now - (count - index) * 60, open=1.0 + index * 0.001,
                high=1.002 + index * 0.001, low=0.998 + index * 0.001,
                close=1.001 + index * 0.001, tick_volume=100 + index,
                real_volume=0, spread=12,
            )
            for index in range(min(count, 20))
        ]

    def copy_rates_range(self, symbol, timeframe, start, end):
        return self.copy_rates_from_pos(symbol, timeframe, 0, 10)

    def copy_ticks_range(self, symbol, start, end, flags=0):
        base = int(time.time() * 1000)
        return [
            make_record(
                time_msc=base - (10 - index) * 1000, bid=1.08 + index * 0.0001,
                ask=1.0802 + index * 0.0001, last=0.0, volume=1, flags=0,
            )
            for index in range(10)
        ]


@pytest.fixture
def fake_mt5(monkeypatch):
    fake = FakeMT5()
    real_import = importlib.import_module

    def patched_import(name, *args, **kwargs):
        if name == "MetaTrader5":
            return fake
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", patched_import)
    return fake


@pytest.fixture
def settings(tmp_path):
    return Settings(
        APP_ENV="test",
        API_KEY="test-api-key",
        DISABLE_BACKGROUND=True,
        DATABASE_URL=f"sqlite:///{tmp_path / 'test.db'}",
        LOG_DIR=str(tmp_path / "logs"),
        MARKET_POLL_INTERVAL=0.5,
    )


@pytest.fixture
def app(settings, fake_mt5):
    return create_app(settings)
