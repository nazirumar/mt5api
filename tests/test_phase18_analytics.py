"""Phase 18 pure analytics tests using deterministic synthetic candles."""

from __future__ import annotations

from datetime import datetime, timezone

from app.context import analytics
from app.context.sessions import session_context

BASE_EPOCH = 1_700_000_000


def make_candles(
    count: int,
    *,
    start: float = 100.0,
    step: float = 0.0,
    candle_range: float = 0.4,
    body_fraction: float = 0.8,
    pattern: str = "linear",
) -> list[dict]:
    candles = []
    for index in range(count):
        if pattern == "zigzag":
            close = start + (1.0 if index % 2 == 0 else -1.0)
        elif pattern == "v_shape":
            close = start - index if index < count * 0.75 else start - count * 0.75 + (index - count * 0.75) * 3
        else:
            close = start + index * step
        body = candle_range * body_fraction
        open_ = close - body if step >= 0 or pattern in {"zigzag", "v_shape"} else close + body
        high = max(open_, close) + candle_range * (1 - body_fraction) / 2
        low = min(open_, close) - candle_range * (1 - body_fraction) / 2
        candles.append(
            {
                "time": datetime.fromtimestamp(BASE_EPOCH + index * 300, tz=timezone.utc).isoformat(),
                "epoch": BASE_EPOCH + index * 300,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "tick_volume": 100,
                "spread": 10,
                "completed": True,
            }
        )
    return candles


def test_atr_uses_true_range_and_ignores_insufficient_history():
    candles = [
        {"open": 100, "high": 101, "low": 99, "close": 100},
        {"open": 100, "high": 103, "low": 99, "close": 102},
        {"open": 102, "high": 104, "low": 101, "close": 103},
    ]
    assert analytics.atr(candles, 2) == 3.5
    assert analytics.atr(candles[:1], 2) is None


def test_trend_bullish_bearish_neutral_and_unknown():
    up = make_candles(40, step=0.2)
    down = make_candles(40, step=-0.2)
    flat = make_candles(40, step=0.0, candle_range=0.2)
    assert analytics.analyze_trend(up)["classification"] == "BULLISH"
    assert analytics.analyze_trend(down)["classification"] == "BEARISH"
    assert analytics.analyze_trend(flat)["classification"] == "NEUTRAL"
    assert analytics.analyze_trend(up[:3])["classification"] == "UNKNOWN"
    trend = analytics.analyze_trend(up)
    assert trend["higher_highs"] > 0
    assert trend["higher_lows"] > 0
    assert trend["strength"] is not None


def test_trend_mixed_when_votes_conflict():
    candles = make_candles(40, start=200.0, pattern="v_shape")
    params = {"CONTEXT_TREND_SLOPE_THRESHOLD": 1.0}
    result = analytics.analyze_trend(candles, params)
    assert result["classification"] == "MIXED"


def test_regime_classifications():
    up = make_candles(60, step=0.2)
    assert analytics.analyze_regime(up)["classification"] == "TRENDING"

    ranging = make_candles(60, start=100.0, step=0.0, candle_range=0.4, body_fraction=0.02, pattern="zigzag")
    ranging_params = {"CONTEXT_REGIME_VOLATILE_ATR_PERCENT": 1000, "CONTEXT_REGIME_EXPANSION_RATIO": 1000}
    result = analytics.analyze_regime(ranging, ranging_params)
    assert result["classification"] in {"RANGING", "TRANSITIONAL"}
    assert result["measurements"]["average_body_to_range_ratio"] is not None

    volatile_params = {"CONTEXT_REGIME_VOLATILE_ATR_PERCENT": 0.001}
    assert analytics.analyze_regime(up, volatile_params)["classification"] == "VOLATILE"

    quiet = make_candles(60, step=0.0, candle_range=0.01, body_fraction=0.1)
    quiet_params = {
        "CONTEXT_REGIME_QUIET_ATR_PERCENT": 1.0,
        "CONTEXT_REGIME_CONTRACTION_RATIO": 1000,
    }
    assert analytics.analyze_regime(quiet, quiet_params)["classification"] == "QUIET"

    transitional_params = {
        "CONTEXT_REGIME_VOLATILE_ATR_PERCENT": 1_000_000,
        "CONTEXT_REGIME_TREND_ATR_MOVE": 1_000_000,
        "CONTEXT_TREND_CONSISTENCY_THRESHOLD": 1.0,
        "CONTEXT_REGIME_RANGING_BODY_RATIO": 0.0,
    }
    assert analytics.analyze_regime(ranging, transitional_params)["classification"] == "TRANSITIONAL"
    assert analytics.analyze_regime(up[:3])["classification"] == "UNKNOWN"


def test_momentum_all_classes_and_acceleration():
    up = make_candles(40, step=1.0, candle_range=0.2)
    down = make_candles(40, step=-1.0, candle_range=0.2)
    flat = make_candles(40, step=0.0, candle_range=0.2)
    assert analytics.analyze_momentum(up)["classification"] == "STRONG_BULLISH"
    assert analytics.analyze_momentum(down)["classification"] == "STRONG_BEARISH"
    assert analytics.analyze_momentum(flat)["classification"] == "NEUTRAL"
    assert analytics.analyze_momentum(up, {"CONTEXT_MOMENTUM_STRONG_ATR_MULTIPLIER": 100})["classification"] == "BULLISH"
    assert analytics.analyze_momentum(down, {"CONTEXT_MOMENTUM_STRONG_ATR_MULTIPLIER": 100})["classification"] == "BEARISH"
    assert analytics.analyze_momentum(up[:3])["classification"] == "UNKNOWN"
    result = analytics.analyze_momentum(up)
    assert result["acceleration_state"] in {"ACCELERATING", "DECELERATING", "STABLE", "UNKNOWN"}
    assert result["sequence"]


def test_volatility_calculations_and_classes():
    candles = make_candles(80, step=0.1, candle_range=0.4)
    result = analytics.analyze_volatility(candles)
    assert result["classification"] in {"VERY_LOW", "LOW", "NORMAL", "HIGH", "VERY_HIGH"}
    assert result["atr"] is not None
    assert result["atr_percent"] is not None
    assert result["current_candle_range"] is not None
    assert result["average_candle_range"] is not None
    assert 0 <= (result["historical_volatility_percentile"] or 0) <= 100
    very_high_params = {
        "CONTEXT_VOLATILITY_HIGH_ATR_PERCENT": 0.0001,
        "CONTEXT_VOLATILITY_VERY_HIGH_ATR_PERCENT": 0.0001,
    }
    assert analytics.analyze_volatility(candles, very_high_params)["classification"] == "VERY_HIGH"
    assert analytics.analyze_volatility(candles, {"CONTEXT_VOLATILITY_VERY_LOW_ATR_PERCENT": 1000})["classification"] == "VERY_LOW"
    assert analytics.analyze_volatility(candles[:3])["classification"] == "UNKNOWN"


def test_price_position_zones_and_neutral_description():
    candles = make_candles(30, step=0.0, candle_range=2.0, body_fraction=0.0)
    for candle in candles:
        candle["high"] = 110.0
        candle["low"] = 90.0
        candle["open"] = 100.0
        candle["close"] = 100.0
    assert analytics.analyze_price_position(candles, 108.0)["classification"] == "UPPER"
    assert analytics.analyze_price_position(candles, 92.0)["classification"] == "LOWER"
    middle = analytics.analyze_price_position(candles, 100.0)
    assert middle["classification"] == "MIDDLE"
    assert "upper 25%" in analytics.analyze_price_position(candles, 108.0)["description"]
    assert analytics.analyze_price_position(candles[:1], 100.0)["classification"] == "UNKNOWN"


def test_candle_structure_ratios_and_patterns():
    base = {
        "time": "2026-01-01T00:00:00Z",
        "epoch": BASE_EPOCH,
        "open": 100.0,
        "high": 100.9,
        "low": 99.9,
        "close": 100.8,
        "completed": True,
    }
    strong_bull = analytics.analyze_candle_structure([base])["latest"]
    assert strong_bull["classification"] == "STRONG_BULLISH_CANDLE"
    assert strong_bull["body_to_range_ratio"] == 0.8
    doji = dict(base, close=100.0)
    assert analytics.analyze_candle_structure([doji])["latest"]["classification"] == "DOJI_LIKE"
    upper_wick = dict(base, open=100.0, close=101.0, high=104.0, low=99.8)
    assert analytics.analyze_candle_structure([upper_wick])["latest"]["classification"] == "LONG_UPPER_WICK"
    lower_wick = dict(base, open=100.0, close=101.0, high=101.2, low=97.0)
    assert analytics.analyze_candle_structure([lower_wick])["latest"]["classification"] == "LONG_LOWER_WICK"
    result = analytics.analyze_candle_structure([base, doji])
    assert result["summary"]["count"] == 2
    assert result["latest"]["epoch"] == BASE_EPOCH


def test_spread_classification_uses_symbol_specific_points():
    candles = make_candles(30, step=0.0)
    for candle in candles:
        candle["spread"] = 10
    normal = analytics.analyze_spread({"spread": 0.0001, "spread_points": 10, "point": 0.00001}, candles)
    elevated = analytics.analyze_spread({"spread": 0.0002, "spread_points": 20, "point": 0.00001}, candles)
    abnormal = analytics.analyze_spread({"spread": 0.0004, "spread_points": 40, "point": 0.00001}, candles)
    assert normal["classification"] == "NORMAL"
    assert elevated["classification"] == "ELEVATED"
    assert abnormal["classification"] == "ABNORMAL"
    assert analytics.analyze_spread({"spread_points": None}, candles)["classification"] == "UNKNOWN"


def test_data_quality_score_grade_and_unusable_states():
    info = {"symbol": "EURUSDm"}
    quote = {
        "market_available": True,
        "bid": 1.1,
        "ask": 1.1002,
        "spread_points": 2,
        "freshness": "FRESH",
    }
    candles = make_candles(30, step=0.1)
    result = analytics.assess_data_quality(info, quote, candles, True)
    assert result["score"] == 100
    assert result["grade"] == "EXCELLENT"
    assert result["usable"] is True
    assert "not a win probability" in result["disclaimer"]

    stale = analytics.assess_data_quality(info, dict(quote, freshness="VERY_STALE"), candles, True)
    assert stale["grade"] == "UNUSABLE"
    assert stale["usable"] is False
    offline = analytics.assess_data_quality(info, quote, candles, False)
    assert offline["grade"] == "UNUSABLE"
    assert "MT5 terminal connection" in offline["missing_data"]
    no_quote = analytics.assess_data_quality(info, {}, candles, True)
    assert no_quote["usable"] is False


def test_unknown_classifications_suppresses_all_blocks():
    context = {"trend": {"classification": "BULLISH"}, "regime": {"classification": "TRENDING"}}
    result = analytics.unknown_classifications(context)
    assert result["trend"]["classification"] == "UNKNOWN"
    assert result["regime"]["classification"] == "UNKNOWN"


def test_session_context_uses_utc_windows_overlap_and_timezone():
    at_ten = session_context(datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc))
    assert at_ten["classification"] == "UNKNOWN"
    assert "London" in at_ten["active_sessions"]
    assert "New York" not in at_ten["active_sessions"]
    assert at_ten["session_states"]["london"] == "OPEN"
    assert at_ten["upcoming_transition"] is not None

    at_thirteen = session_context(datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc))
    assert at_thirteen["overlap"]["london_new_york"] is True

    tokyo = session_context(
        datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        params={"CONTEXT_SESSION_TIMEZONE": "Asia/Tokyo"},
    )
    assert tokyo["timezone"] == "Asia/Tokyo"
    assert "19:00" in tokyo["local_session_time"]

    custom = session_context(
        datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
        params={"CONTEXT_SESSION_WINDOWS": "Custom=9-11"},
    )
    assert custom["active_sessions"] == ["Custom"]
