"""Phase 17 regressions for broker-clock precision and tick identity."""

from __future__ import annotations

from types import SimpleNamespace

from app.mt5.quotes import QuoteService
from app.services.data_validator import normalize_ticks


def test_normalize_ticks_keeps_distinct_ticks_with_same_timestamp():
    records = [
        SimpleNamespace(time_msc=1_700_000_000_100, bid=1.10, ask=1.11, last=0.0, volume=1, flags=0),
        SimpleNamespace(time_msc=1_700_000_000_100, bid=1.10, ask=1.11, last=0.0, volume=2, flags=0),
        SimpleNamespace(time_msc=1_700_000_000_100, bid=1.10, ask=1.11, last=0.0, volume=2, flags=0),
    ]

    ticks = normalize_ticks(records)

    assert len(ticks) == 2
    assert [tick["volume"] for tick in ticks] == [1, 2]
    assert all(tick["epoch_ms"] == 1_700_000_000_100 for tick in ticks)


def test_quote_timestamp_preserves_broker_milliseconds(settings):
    configured = settings.model_copy(update={"MT5_SERVER_UTC_OFFSET_MINUTES": 120})
    service = QuoteService(manager=None, settings=configured)  # type: ignore[arg-type]
    tick = SimpleNamespace(
        time_msc=1_700_007_200_123,
        bid=1.09351,
        ask=1.09354,
        last=0.0,
        volume=1,
        real_volume=0,
    )

    quote = service.quote_from_tick("EURUSD", tick)

    assert quote["timestamp"] == "2023-11-14T22:13:20.123000Z"
    assert quote["bid"] == 1.09351
    assert quote["ask"] == 1.09354
