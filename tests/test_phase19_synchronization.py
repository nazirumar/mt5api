"""Pure tests for snapshot retrieval synchronization."""

from __future__ import annotations

from app.snapshot.synchronization import classify_synchronization

BASE = 1_700_000_000.0


def classify(**overrides):
    values = {
        "retrieval_times": {"M1": BASE, "H1": BASE + 1},
        "quote_retrieved_at": BASE + 0.5,
        "quote_freshness": "FRESH",
        "duration_ms": 42.0,
        "missing_timeframes": [],
        "latest_candle_epochs": {"M1": BASE - 60, "H1": BASE - 3600},
    }
    values.update(overrides)
    return classify_synchronization(**values)


def test_tight_retrieval_is_synchronized():
    result = classify()

    assert result["classification"] == "SYNCHRONIZED"
    assert result["retrieval_span_seconds"] == 1.0
    assert result["retrieved_at"]["M1"].endswith("Z")


def test_acceptable_retrieval_span_is_acceptable():
    result = classify(retrieval_times={"M1": BASE, "H1": BASE + 5}, quote_retrieved_at=BASE + 2)

    assert result["classification"] == "ACCEPTABLE"
    assert result["retrieval_span_seconds"] == 5.0
    assert any("tight" in warning for warning in result["warnings"])


def test_span_beyond_acceptable_tolerance_is_unsynchronized():
    result = classify(retrieval_times={"M1": BASE, "H1": BASE + 15})

    assert result["classification"] == "UNSYNCHRONIZED"
    assert result["retrieval_span_seconds"] == 15.0


def test_missing_timeframe_is_partially_synchronized_without_claiming_full_coverage():
    result = classify(missing_timeframes=["D1"])

    assert result["classification"] == "PARTIALLY_SYNCHRONIZED"
    assert result["missing_timeframes"] == ["D1"]
    assert any("D1" in warning for warning in result["warnings"])


def test_unusable_quote_forces_unsynchronized_even_when_times_are_tight():
    result = classify(quote_freshness="VERY_STALE")

    assert result["classification"] == "UNSYNCHRONIZED"
    assert any("VERY_STALE" in warning for warning in result["warnings"])


def test_candle_close_offsets_are_reported_separately_from_retrieval_times():
    result = classify(
        latest_candle_epochs={"M1": BASE - 60, "H1": BASE - 3600, "D1": BASE - 86_400}
    )

    assert result["candle_time_alignment"]["latest_candle_epochs"]["D1"] == BASE - 86_400
    assert "different UTC times" in result["candle_time_alignment"]["note"]
    assert result["retrieval_span_seconds"] == 1.0


def test_no_retrieval_timestamps_is_unknown():
    result = classify(retrieval_times={}, quote_retrieved_at=None)

    assert result["classification"] == "UNKNOWN"
    assert result["retrieval_span_seconds"] is None
    assert result["quote_retrieved_at"] is None
