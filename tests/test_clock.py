"""Broker-time to UTC conversion tests."""

from __future__ import annotations

from datetime import datetime, timezone

from app.mt5 import clock as clock_module
from app.mt5.clock import BrokerClock


def test_fixed_offset_converts_broker_epoch_to_utc(settings):
    configured = settings.model_copy(update={"MT5_SERVER_UTC_OFFSET_MINUTES": 120})
    clock = BrokerClock(configured)

    assert clock.to_utc_epoch(1_700_007_200) == 1_700_000_000
    assert clock.to_broker_datetime(
        datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    assert clock.describe()["utc_offset_minutes"] == 120


def test_iana_timezone_converts_broker_epoch_to_utc(settings):
    configured = settings.model_copy(
        update={"MT5_SERVER_TIMEZONE": "Etc/GMT-2", "MT5_SERVER_UTC_OFFSET_MINUTES": None}
    )
    clock = BrokerClock(configured)

    assert clock.to_utc_epoch(0) == -7200
    assert clock.describe()["timezone"] == "Etc/GMT-2"


def test_auto_detection_rounds_to_nearest_quarter_hour(settings, monkeypatch):
    monkeypatch.setattr(clock_module.time, "time", lambda: 1000.0)
    clock = BrokerClock(settings)
    clock.observe(3700)

    assert clock.offset_seconds == 2700
    assert clock.to_utc_epoch(3700) == 1000
    assert clock.describe()["auto_detected"] is True
