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


def test_auto_detection_supports_negative_broker_offset(settings, monkeypatch):
    monkeypatch.setattr(clock_module.time, "time", lambda: 1000.0)
    clock = BrokerClock(settings)
    broker_time = 1000.0 - 4 * 3600

    clock.observe(broker_time)

    assert clock.offset_seconds == -14400
    assert clock.to_utc_epoch(broker_time) == 1000
    assert clock.describe()["auto_detected"] is True


def test_auto_detection_refines_downward_for_dst_fall_back(settings, monkeypatch):
    now = {"value": 1000.0}
    monkeypatch.setattr(clock_module.time, "time", lambda: now["value"])
    clock = BrokerClock(settings)

    clock.observe(1000.0 + 7200)
    assert clock.offset_seconds == 7200

    now["value"] = 1200.0  # still inside the same 300-second window
    clock.observe(1200.0)
    assert clock.offset_seconds == 0


def test_utc_ms_preserves_broker_milliseconds(settings):
    configured = settings.model_copy(update={"MT5_SERVER_UTC_OFFSET_MINUTES": 120})
    clock = BrokerClock(configured)

    assert clock.to_utc_ms(1_700_007_200_123) == 1_700_000_000_123


def test_monthly_candle_completion_uses_calendar_month(settings, monkeypatch):
    configured = settings.model_copy(update={"MT5_SERVER_UTC_OFFSET_MINUTES": 120})
    clock = BrokerClock(configured)
    # Broker epoch is server wall time: UTC+2 midnight is 02:00 as an epoch.
    candle = {"epoch": int(datetime(2026, 1, 1, 2, tzinfo=timezone.utc).timestamp())}
    monkeypatch.setattr(
        clock_module.time, "time",
        lambda: datetime(2026, 1, 31, 12, tzinfo=timezone.utc).timestamp(),
    )

    result = clock.convert_candles([dict(candle)], 2_592_000, timeframe="MN1")
    assert result[0]["completed"] is False

    monkeypatch.setattr(
        clock_module.time, "time",
        lambda: datetime(2026, 2, 1, 0, tzinfo=timezone.utc).timestamp(),
    )
    result = clock.convert_candles([dict(candle)], 2_592_000, timeframe="MN1")
    assert result[0]["completed"] is True


def test_clock_reports_missing_configuration_diagnostic(settings):
    clock = BrokerClock(settings)
    description = clock.describe()

    assert description["source"] == "unsampled"
    assert description["warning"]
    assert "not configured" in description["warning"]
