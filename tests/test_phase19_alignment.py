"""Pure tests for deterministic multi-timeframe alignment."""

from __future__ import annotations

from app.snapshot.alignment import analyze_alignment, direction_of

DURATIONS = {
    "D1": 86_400,
    "H4": 14_400,
    "H1": 3_600,
    "M15": 900,
    "M5": 300,
    "M1": 60,
}


def item(timeframe: str, classification: str, *, available: bool = True):
    return {
        "timeframe": timeframe,
        "classification": classification,
        "duration_seconds": DURATIONS[timeframe],
        "available": available,
        "role": "test",
    }


def test_all_bullish_is_fully_aligned():
    result = analyze_alignment(
        [item("D1", "BULLISH"), item("H4", "STRONG_BULLISH"), item("H1", "BULLISH")]
    )

    assert result["classification"] == "FULLY_ALIGNED"
    assert result["bullish_count"] == 3
    assert result["bearish_count"] == 0
    assert result["alignment_ratio"] == 1.0
    assert result["conflict_count"] == 0
    assert result["incomplete"] is False


def test_all_bearish_is_fully_aligned():
    result = analyze_alignment(
        [item("D1", "BEARISH"), item("H4", "STRONG_BEARISH"), item("H1", "BEARISH")]
    )

    assert result["classification"] == "FULLY_ALIGNED"
    assert result["bearish_count"] == 3
    assert result["bullish_count"] == 0
    assert result["alignment_ratio"] == 1.0


def test_two_opposite_timeframes_are_conflicting():
    result = analyze_alignment([item("D1", "BULLISH"), item("H4", "BEARISH")])

    assert result["classification"] == "CONFLICTING"
    assert result["alignment_ratio"] == 0.5
    assert result["conflict_count"] == 1
    assert result["conflicts"][0]["higher_timeframe"] == "D1"
    assert result["conflicts"][0]["lower_timeframe"] == "H4"


def test_mostly_aligned_keeps_dominant_direction_without_hiding_conflicts():
    result = analyze_alignment(
        [
            item("D1", "BULLISH"),
            item("H4", "BEARISH"),
            item("H1", "BULLISH"),
            item("M15", "BULLISH"),
        ]
    )

    assert result["classification"] == "MOSTLY_ALIGNED"
    assert result["alignment_ratio"] == 0.75
    assert result["conflict_count"] == 2


def test_mixed_classification_when_no_clear_dominance():
    result = analyze_alignment(
        [item("D1", "BULLISH"), item("H4", "NEUTRAL"), item("H1", "NEUTRAL"), item("M15", "MIXED")]
    )

    assert result["classification"] == "MIXED"
    assert result["directional_count"] == 1
    assert result["dominant_classification"] == "NEUTRAL"


def test_insufficient_data_and_unknown_are_separate():
    insufficient = analyze_alignment([item("D1", "BULLISH")])
    unknown = analyze_alignment([item("D1", "NEUTRAL"), item("H4", "NEUTRAL")])

    assert insufficient["classification"] == "INSUFFICIENT_DATA"
    assert unknown["classification"] == "UNKNOWN"
    assert unknown["directional_count"] == 0


def test_missing_timeframes_are_counted_and_never_guessed():
    result = analyze_alignment(
        [
            item("D1", "BULLISH"),
            item("H4", "UNKNOWN", available=False),
            item("H1", "NEUTRAL"),
        ],
        requested=["D1", "H4", "H1"],
    )

    assert result["unknown_count"] == 1
    assert result["analyzed_count"] == 2
    assert result["incomplete"] is True
    assert result["details"][1]["classification"] == "UNKNOWN"


def test_conflicts_compare_adjacent_available_timeframes_in_duration_order():
    result = analyze_alignment(
        [
            item("M15", "BULLISH"),
            item("H1", "BEARISH"),
            item("H4", "BEARISH"),
            item("D1", "BULLISH"),
        ]
    )

    assert result["classification"] == "CONFLICTING"
    assert result["conflict_count"] == 2
    pairs = [(entry["higher_timeframe"], entry["lower_timeframe"]) for entry in result["conflicts"]]
    assert pairs == [("D1", "H4"), ("H1", "M15")]


def test_direction_normalization_and_no_signal_disclaimer():
    assert direction_of("STRONG_BULLISH") == "BULLISH"
    assert direction_of("SHARP_BEARISH") == "BEARISH"
    assert direction_of("unknown") == "UNKNOWN"

    result = analyze_alignment([item("D1", "BULLISH"), item("H4", "BULLISH")])

    assert "not a BUY/SELL signal" in result["disclaimer"]
    assert "win probability" in result["disclaimer"]
    assert not any(key in {"signal", "probability", "forecast"} for key in result)
