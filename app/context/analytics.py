"""Deterministic market analytics.

This module is pure: it performs no I/O, reads no clock, and never contacts MT5.
Every classification can therefore be unit tested with fixed candle snapshots.
All thresholds are supplied by ``Settings`` (or overridden by tests) through
``params``; no trading signal or future-price claim is made here.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

UNKNOWN = "UNKNOWN"

DEFAULT_PARAMS: dict[str, Any] = {
    "CONTEXT_SYMBOL_INFO_CACHE_SECONDS": 300.0,
    "CONTEXT_CANDLE_CACHE_SECONDS": 30.0,
    "CONTEXT_QUOTE_VERY_STALE_MULTIPLIER": 6.0,
    "CONTEXT_MAX_RETURN_CANDLES": 25,
    "CONTEXT_SESSION_TIMEZONE": "UTC",
    "CONTEXT_SESSION_WINDOWS": "",
    "CONTEXT_MIN_CANDLES": 10,
    "CONTEXT_TREND_LOOKBACK": 50,
    "CONTEXT_TREND_FAST_SMA": 10,
    "CONTEXT_TREND_SLOW_SMA": 30,
    "CONTEXT_TREND_SLOPE_THRESHOLD": 0.0005,
    "CONTEXT_TREND_CONSISTENCY_THRESHOLD": 0.6,
    "CONTEXT_ATR_PERIOD": 14,
    "CONTEXT_REGIME_VOLATILE_ATR_PERCENT": 1.5,
    "CONTEXT_REGIME_QUIET_ATR_PERCENT": 0.15,
    "CONTEXT_REGIME_EXPANSION_RATIO": 1.6,
    "CONTEXT_REGIME_CONTRACTION_RATIO": 0.75,
    "CONTEXT_REGIME_TREND_ATR_MOVE": 2.0,
    "CONTEXT_REGIME_RANGING_BODY_RATIO": 0.5,
    "CONTEXT_MOMENTUM_LOOKBACK": 5,
    "CONTEXT_MOMENTUM_WEAK_ATR_MULTIPLIER": 0.3,
    "CONTEXT_MOMENTUM_STRONG_ATR_MULTIPLIER": 2.0,
    "CONTEXT_VOLATILITY_VERY_LOW_ATR_PERCENT": 0.15,
    "CONTEXT_VOLATILITY_LOW_ATR_PERCENT": 0.35,
    "CONTEXT_VOLATILITY_HIGH_ATR_PERCENT": 1.5,
    "CONTEXT_VOLATILITY_VERY_HIGH_ATR_PERCENT": 3.0,
    "CONTEXT_VOLATILITY_PERCENTILE_WINDOW": 50,
    "CONTEXT_CANDLE_STRUCTURE_LIMIT": 3,
    "CONTEXT_CANDLE_STRONG_BODY_RATIO": 0.7,
    "CONTEXT_CANDLE_SMALL_BODY_RATIO": 0.15,
    "CONTEXT_CANDLE_DOJI_RATIO": 0.05,
    "CONTEXT_CANDLE_WICK_RATIO": 0.5,
    "CONTEXT_PRICE_POSITION_LOOKBACK": 50,
    "CONTEXT_PRICE_POSITION_LOWER_ZONE_PERCENT": 25.0,
    "CONTEXT_PRICE_POSITION_UPPER_ZONE_PERCENT": 75.0,
    "CONTEXT_SPREAD_LOOKBACK": 20,
    "CONTEXT_SPREAD_ELEVATED_RATIO": 1.5,
    "CONTEXT_SPREAD_ABNORMAL_RATIO": 3.0,
    "CONTEXT_DATA_QUALITY_MIN_SCORE": 50,
    "CONTEXT_DATA_QUALITY_GOOD_SCORE": 75,
    "CONTEXT_DATA_QUALITY_EXCELLENT_SCORE": 90,
}

CONTEXT_SETTING_NAMES = tuple(DEFAULT_PARAMS)


def settings_to_params(settings: Any) -> dict[str, Any]:
    """Copy relevant settings into a plain params dict with safe defaults."""
    return {name: getattr(settings, name, default) for name, default in DEFAULT_PARAMS.items()}


def _param(params: Mapping[str, Any] | None, name: str) -> Any:
    if params and name in params and params[name] is not None:
        return params[name]
    return DEFAULT_PARAMS[name]


def _as_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(value, digits)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    a, b = _as_float(numerator), _as_float(denominator)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _ohlc(candle: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    values = tuple(_as_float(candle.get(key)) for key in ("open", "high", "low", "close"))
    if any(value is None or value <= 0 for value in values):
        return None
    open_, high, low, close_ = values  # type: ignore[misc]
    if high < max(open_, close_, low) or low > min(open_, close_, high):
        return None
    return open_, high, low, close_


def completed_candles(candles: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Return valid, completed candles only; callers pass an already-sorted list."""
    result: list[dict[str, Any]] = []
    for candle in candles or []:
        if not isinstance(candle, Mapping) or candle.get("completed", True) is not True:
            continue
        if _ohlc(candle) is None:
            continue
        result.append(dict(candle))
    return result


def _tail(candles: Sequence[Mapping[str, Any]], count: int) -> list[dict[str, Any]]:
    size = max(1, int(count))
    return [dict(item) for item in list(candles)[-size:]]


def sma(values: Sequence[float], period: int) -> float | None:
    period = int(period)
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def linear_slope(values: Sequence[float]) -> float | None:
    """Least-squares slope per observation; positive means rising."""
    if len(values) < 2:
        return None
    mean_x = (len(values) - 1) / 2.0
    mean_y = sum(values) / len(values)
    numerator = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values))
    denominator = sum((index - mean_x) ** 2 for index in range(len(values)))
    return numerator / denominator if denominator else None


def true_ranges(candles: Sequence[Mapping[str, Any]]) -> list[float]:
    result: list[float] = []
    previous_close: float | None = None
    for candle in candles:
        values = _ohlc(candle)
        if values is None:
            continue
        _, high, low, close_ = values
        if previous_close is None:
            result.append(high - low)
        else:
            result.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = close_
    return result


def atr(candles: Sequence[Mapping[str, Any]], period: int) -> float | None:
    """Simple ATR over the last ``period`` true ranges."""
    ranges = true_ranges(candles)
    period = int(period)
    if period <= 0 or len(ranges) < max(2, period):
        return None
    return sum(ranges[-period:]) / period


def _directional_consistency(candles: Sequence[Mapping[str, Any]]) -> tuple[float | None, str | None]:
    closes = [_as_float(item.get("close")) for item in candles]
    closes = [value for value in closes if value is not None]
    up = down = 0
    for previous, current in zip(closes, closes[1:]):
        if current > previous:
            up += 1
        elif current < previous:
            down += 1
    total = up + down
    if not total:
        return None, None
    if up > down:
        return up / total, "BULLISH"
    if down > up:
        return down / total, "BEARISH"
    return 0.5, "NEUTRAL"


def _structure_counts(candles: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = {"higher_highs": 0, "higher_lows": 0, "lower_highs": 0, "lower_lows": 0}
    for previous, current in zip(candles, list(candles)[1:]):
        old = _ohlc(previous)
        new = _ohlc(current)
        if old is None or new is None:
            continue
        if new[1] > old[1]:
            counts["higher_highs"] += 1
        elif new[1] < old[1]:
            counts["lower_highs"] += 1
        if new[2] > old[2]:
            counts["higher_lows"] += 1
        elif new[2] < old[2]:
            counts["lower_lows"] += 1
    return counts


def analyze_trend(
    candles: Sequence[Mapping[str, Any]] | None, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Classify trend from completed candles using four independent votes."""
    clean = completed_candles(candles)
    minimum = int(_param(params, "CONTEXT_MIN_CANDLES"))
    result: dict[str, Any] = {
        "classification": UNKNOWN,
        "method": "SMA relationship + regression slope + close consistency + swing structure",
        "strength": None,
        "direction": UNKNOWN,
        "votes": {},
        "higher_highs": None,
        "higher_lows": None,
        "lower_highs": None,
        "lower_lows": None,
        "net_directional_movement_percent": None,
        "trend_slope": None,
        "trend_consistency": None,
        "data_sufficiency": False,
    }
    if len(clean) < minimum:
        result["reason"] = f"At least {minimum} completed candles are required"
        return result

    segment = _tail(clean, int(_param(params, "CONTEXT_TREND_LOOKBACK")))
    closes = [_as_float(item.get("close")) for item in segment]
    closes = [value for value in closes if value is not None]
    if len(closes) < 2:
        result["reason"] = "Close values are unavailable"
        return result

    fast = sma(closes, int(_param(params, "CONTEXT_TREND_FAST_SMA")))
    slow = sma(closes, int(_param(params, "CONTEXT_TREND_SLOW_SMA")))
    slope = linear_slope(closes)
    mean_close = sum(closes) / len(closes)
    normalized_slope = _ratio(slope, mean_close)
    consistency, dominant = _directional_consistency(segment)
    structure = _structure_counts(segment)
    threshold = float(_param(params, "CONTEXT_TREND_SLOPE_THRESHOLD"))

    votes: dict[str, int] = {}
    if fast is not None and slow is not None:
        votes["sma"] = 1 if fast > slow else (-1 if fast < slow else 0)
    if normalized_slope is not None:
        votes["slope"] = 1 if normalized_slope > threshold else (-1 if normalized_slope < -threshold else 0)
    if consistency is not None and dominant in {"BULLISH", "BEARISH"} and consistency >= float(
        _param(params, "CONTEXT_TREND_CONSISTENCY_THRESHOLD")
    ):
        votes["consistency"] = 1 if dominant == "BULLISH" else -1
    structure_vote = 0
    if structure["higher_highs"] > structure["lower_highs"] and structure["higher_lows"] > structure["lower_lows"]:
        structure_vote = 1
    elif structure["lower_highs"] > structure["higher_highs"] and structure["lower_lows"] > structure["higher_lows"]:
        structure_vote = -1
    votes["structure"] = structure_vote

    score = sum(votes.values())
    if score >= 2:
        classification = "BULLISH"
    elif score <= -2:
        classification = "BEARISH"
    elif any(value > 0 for value in votes.values()) and any(value < 0 for value in votes.values()):
        classification = "MIXED"
    else:
        classification = "NEUTRAL"

    result.update(
        {
            "classification": classification,
            "direction": classification if classification in {"BULLISH", "BEARISH"} else "NEUTRAL",
            "strength": _round(abs(score) / max(1, len(votes)), 2),
            "score": score,
            "votes": votes,
            "higher_highs": structure["higher_highs"],
            "higher_lows": structure["higher_lows"],
            "lower_highs": structure["lower_highs"],
            "lower_lows": structure["lower_lows"],
            "net_directional_movement_percent": _round((closes[-1] - closes[0]) / closes[0] * 100.0),
            "trend_slope": _round(normalized_slope, 8),
            "trend_consistency": _round(consistency),
            "data_sufficiency": True,
            "candles_analyzed": len(segment),
        }
    )
    return result


def analyze_regime(
    candles: Sequence[Mapping[str, Any]] | None, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Classify the current regime from ATR, movement, and candle-body structure."""
    clean = completed_candles(candles)
    minimum = max(int(_param(params, "CONTEXT_MIN_CANDLES")), int(_param(params, "CONTEXT_ATR_PERIOD")) + 1)
    result: dict[str, Any] = {
        "classification": UNKNOWN,
        "method": "ATR level/expansion + net movement in ATR units + body/range ratio + consistency",
        "explanation": "Insufficient completed candle history",
        "data_sufficiency": False,
        "measurements": {},
    }
    if len(clean) < minimum:
        return result

    period = int(_param(params, "CONTEXT_ATR_PERIOD"))
    current_atr = atr(clean, period)
    half = len(clean) // 2
    prior_atr = atr(clean[:half], period) if half >= period else None
    if current_atr is None:
        return result

    closes = [_as_float(item.get("close")) for item in clean]
    closes = [value for value in closes if value is not None]
    last_close = closes[-1] if closes else None
    atr_percent = _ratio(current_atr, last_close)
    if atr_percent is not None:
        atr_percent *= 100.0
    expansion = _ratio(current_atr, prior_atr)
    consistency, _ = _directional_consistency(clean)
    net_move = abs(closes[-1] - closes[0]) if len(closes) >= 2 else None
    net_atr = _ratio(net_move, current_atr)

    body_ratios: list[float] = []
    for candle in _tail(clean, period):
        values = _ohlc(candle)
        if values is None:
            continue
        _, high, low, _ = values
        candle_range = high - low
        ratio = _ratio(abs(values[3] - values[0]), candle_range)
        if ratio is not None:
            body_ratios.append(ratio)
    average_body_ratio = sum(body_ratios) / len(body_ratios) if body_ratios else None

    volatile_atr_percent = float(_param(params, "CONTEXT_REGIME_VOLATILE_ATR_PERCENT"))
    quiet_atr_percent = float(_param(params, "CONTEXT_REGIME_QUIET_ATR_PERCENT"))
    expansion_ratio = float(_param(params, "CONTEXT_REGIME_EXPANSION_RATIO"))
    contraction_ratio = float(_param(params, "CONTEXT_REGIME_CONTRACTION_RATIO"))
    trend_atr_move = float(_param(params, "CONTEXT_REGIME_TREND_ATR_MOVE"))
    ranging_body_ratio = float(_param(params, "CONTEXT_REGIME_RANGING_BODY_RATIO"))

    if (atr_percent is not None and atr_percent >= volatile_atr_percent) or (
        expansion is not None and expansion >= expansion_ratio
    ):
        classification = "VOLATILE"
        explanation = "ATR level or ATR expansion exceeds the configured volatile threshold"
    elif atr_percent is not None and atr_percent <= quiet_atr_percent and (
        expansion is None or expansion <= contraction_ratio
    ):
        classification = "QUIET"
        explanation = "ATR level is low and range is not expanding"
    elif consistency is not None and consistency >= float(
        _param(params, "CONTEXT_TREND_CONSISTENCY_THRESHOLD")
    ) and net_atr is not None and net_atr >= trend_atr_move:
        classification = "TRENDING"
        explanation = "Directional consistency and net movement in ATR units exceed trend thresholds"
    elif average_body_ratio is not None and average_body_ratio <= ranging_body_ratio and (
        net_atr is None or net_atr < trend_atr_move
    ):
        classification = "RANGING"
        explanation = "Small average candle bodies and limited net movement indicate range-like conditions"
    else:
        classification = "TRANSITIONAL"
        explanation = "Measurements do not satisfy a single regime rule"

    measurements = {
        "atr": _round(current_atr),
        "atr_percent": _round(atr_percent),
        "prior_atr": _round(prior_atr),
        "atr_expansion_ratio": _round(expansion),
        "net_movement_atr_multiple": _round(net_atr),
        "average_body_to_range_ratio": _round(average_body_ratio),
        "directional_consistency": _round(consistency),
        "thresholds": {
            "volatile_atr_percent": volatile_atr_percent,
            "quiet_atr_percent": quiet_atr_percent,
            "expansion_ratio": expansion_ratio,
            "contraction_ratio": contraction_ratio,
            "trend_atr_move": trend_atr_move,
            "ranging_body_ratio": ranging_body_ratio,
        },
    }
    result.update(
        {
            "classification": classification,
            "explanation": explanation,
            "data_sufficiency": True,
            "measurements": measurements,
            "candles_analyzed": len(clean),
        }
    )
    return result


def analyze_momentum(
    candles: Sequence[Mapping[str, Any]] | None, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Classify recent candle momentum; this is not a future-direction forecast."""
    clean = completed_candles(candles)
    lookback = int(_param(params, "CONTEXT_MOMENTUM_LOOKBACK"))
    minimum = max(int(_param(params, "CONTEXT_MIN_CANDLES")), lookback + 1)
    result: dict[str, Any] = {
        "classification": UNKNOWN,
        "direction": UNKNOWN,
        "method": "Close-to-close move over lookback normalized by ATR, plus equal-window acceleration",
        "explanation": "Insufficient completed candle history",
        "data_sufficiency": False,
        "sequence": [],
    }
    if len(clean) < minimum:
        return result

    current_atr = atr(clean, int(_param(params, "CONTEXT_ATR_PERIOD")))
    closes = [_as_float(item.get("close")) for item in clean]
    closes = [value for value in closes if value is not None]
    if current_atr is None or current_atr <= 0 or len(closes) < lookback + 1:
        return result

    move_price = closes[-1] - closes[-lookback - 1]
    move_atr = move_price / current_atr
    acceleration: float | None = None
    if len(closes) >= 2 * lookback + 1:
        previous_move = closes[-lookback - 1] - closes[-2 * lookback - 1]
        acceleration = (move_price - previous_move) / current_atr

    weak = float(_param(params, "CONTEXT_MOMENTUM_WEAK_ATR_MULTIPLIER"))
    strong = float(_param(params, "CONTEXT_MOMENTUM_STRONG_ATR_MULTIPLIER"))
    if move_atr >= strong:
        classification = "STRONG_BULLISH"
    elif move_atr >= weak:
        classification = "BULLISH"
    elif move_atr <= -strong:
        classification = "STRONG_BEARISH"
    elif move_atr <= -weak:
        classification = "BEARISH"
    else:
        classification = "NEUTRAL"

    sequence: list[str] = []
    for candle in _tail(clean, min(lookback, 5)):
        values = _ohlc(candle)
        if values is None:
            continue
        sequence.append("BULLISH" if values[3] > values[0] else ("BEARISH" if values[3] < values[0] else "FLAT"))

    result.update(
        {
            "classification": classification,
            "direction": (
                "BULLISH" if classification.endswith("BULLISH") else
                "BEARISH" if classification.endswith("BEARISH") else "NEUTRAL"
            ),
            "explanation": "Descriptive momentum from completed candles, not a prediction",
            "data_sufficiency": True,
            "recent_directional_movement_price": _round(move_price),
            "momentum_atr_multiple": _round(move_atr),
            "momentum_acceleration_atr_multiple": _round(acceleration),
            "acceleration_state": (
                UNKNOWN if acceleration is None else
                "ACCELERATING" if acceleration > weak else
                "DECELERATING" if acceleration < -weak else "STABLE"
            ),
            "lookback": lookback,
            "sequence": sequence,
            "thresholds": {"weak_atr_multiple": weak, "strong_atr_multiple": strong},
        }
    )
    return result


def range_percentile(
    candles: Sequence[Mapping[str, Any]], window: int
) -> float | None:
    """Percentile rank of the latest candle range versus prior completed ranges."""
    ranges: list[float] = []
    for candle in candles:
        values = _ohlc(candle)
        if values is None:
            continue
        ranges.append(values[1] - values[2])
    window = max(1, int(window))
    if len(ranges) < window + 1:
        return None
    current = ranges[-1]
    prior = ranges[-window - 1:-1]
    return 100.0 * sum(1 for value in prior if value <= current) / len(prior)


def analyze_volatility(
    candles: Sequence[Mapping[str, Any]] | None, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Calculate ATR-based volatility with timestamp-aligned trailing windows."""
    clean = completed_candles(candles)
    period = int(_param(params, "CONTEXT_ATR_PERIOD"))
    minimum = max(int(_param(params, "CONTEXT_MIN_CANDLES")), period + 1)
    result: dict[str, Any] = {
        "classification": UNKNOWN,
        "method": "ATR percentage bands with trailing ATR expansion and range percentile",
        "explanation": "Insufficient completed candle history",
        "data_sufficiency": False,
        "atr": None,
        "atr_percent": None,
    }
    if len(clean) < minimum:
        return result

    current_atr = atr(clean, period)
    closes = [_as_float(item.get("close")) for item in clean]
    closes = [value for value in closes if value is not None]
    if current_atr is None or not closes or closes[-1] <= 0:
        return result

    atr_percent = current_atr / closes[-1] * 100.0
    ranges = []
    for candle in clean:
        values = _ohlc(candle)
        if values is None:
            continue
        ranges.append(values[1] - values[2])
    current_range = ranges[-1] if ranges else None
    average_range = sum(ranges[-period:]) / len(ranges[-period:]) if ranges else None
    relative = _ratio(current_range, average_range)
    prior_atr = atr(clean[:-period], period) if len(clean) >= 3 * period else None
    expansion = _ratio(current_atr, prior_atr)
    percentile = range_percentile(clean, int(_param(params, "CONTEXT_VOLATILITY_PERCENTILE_WINDOW")))

    very_low = float(_param(params, "CONTEXT_VOLATILITY_VERY_LOW_ATR_PERCENT"))
    low = float(_param(params, "CONTEXT_VOLATILITY_LOW_ATR_PERCENT"))
    high = float(_param(params, "CONTEXT_VOLATILITY_HIGH_ATR_PERCENT"))
    very_high = float(_param(params, "CONTEXT_VOLATILITY_VERY_HIGH_ATR_PERCENT"))
    if atr_percent >= very_high:
        classification = "VERY_HIGH"
    elif atr_percent >= high:
        classification = "HIGH"
    elif atr_percent <= very_low:
        classification = "VERY_LOW"
    elif atr_percent <= low:
        classification = "LOW"
    else:
        classification = "NORMAL"

    result.update(
        {
            "classification": classification,
            "explanation": "ATR percentage relative to the last close; descriptive only",
            "data_sufficiency": True,
            "atr": _round(current_atr),
            "atr_percent": _round(atr_percent),
            "current_candle_range": _round(current_range),
            "average_candle_range": _round(average_range),
            "relative_volatility": _round(relative),
            "volatility_expansion_ratio": _round(expansion),
            "volatility_state": (
                UNKNOWN if expansion is None else
                "EXPANDING" if expansion > float(_param(params, "CONTEXT_REGIME_EXPANSION_RATIO")) else
                "CONTRACTING" if expansion < float(_param(params, "CONTEXT_REGIME_CONTRACTION_RATIO")) else
                "STABLE"
            ),
            "historical_volatility_percentile": _round(percentile, 2),
            "atr_period": period,
            "thresholds": {
                "very_low_atr_percent": very_low,
                "low_atr_percent": low,
                "high_atr_percent": high,
                "very_high_atr_percent": very_high,
            },
        }
    )
    return result


def analyze_candle_structure(
    candles: Sequence[Mapping[str, Any]] | None, params: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Describe completed candle geometry using explicit ratio rules."""
    clean = completed_candles(candles)
    items: list[dict[str, Any]] = []
    strong = float(_param(params, "CONTEXT_CANDLE_STRONG_BODY_RATIO"))
    small = float(_param(params, "CONTEXT_CANDLE_SMALL_BODY_RATIO"))
    doji = float(_param(params, "CONTEXT_CANDLE_DOJI_RATIO"))
    wick = float(_param(params, "CONTEXT_CANDLE_WICK_RATIO"))

    for candle in _tail(clean, int(_param(params, "CONTEXT_CANDLE_STRUCTURE_LIMIT"))):
        values = _ohlc(candle)
        if values is None:
            continue
        open_, high, low, close_ = values
        candle_range = high - low
        body = abs(close_ - open_)
        upper = high - max(open_, close_)
        lower = min(open_, close_) - low
        body_ratio = _ratio(body, candle_range)
        upper_ratio = _ratio(upper, candle_range)
        lower_ratio = _ratio(lower, candle_range)
        direction = "BULLISH" if close_ > open_ else ("BEARISH" if close_ < open_ else "FLAT")
        if candle_range <= 0:
            pattern = "UNKNOWN"
        elif body_ratio is not None and body_ratio <= doji:
            pattern = "DOJI_LIKE"
        elif body_ratio is not None and body_ratio <= small:
            pattern = "SMALL_BODY"
        elif upper_ratio is not None and upper_ratio >= wick:
            pattern = "LONG_UPPER_WICK"
        elif lower_ratio is not None and lower_ratio >= wick:
            pattern = "LONG_LOWER_WICK"
        elif body_ratio is not None and body_ratio >= strong and direction == "BULLISH":
            pattern = "STRONG_BULLISH_CANDLE"
        elif body_ratio is not None and body_ratio >= strong and direction == "BEARISH":
            pattern = "STRONG_BEARISH_CANDLE"
        else:
            pattern = "NORMAL"
        items.append(
            {
                "time": candle.get("time"),
                "epoch": candle.get("epoch"),
                "open": open_,
                "high": high,
                "low": low,
                "close": close_,
                "direction": direction,
                "body_size": _round(body),
                "upper_wick": _round(upper),
                "lower_wick": _round(lower),
                "total_range": _round(candle_range),
                "body_to_range_ratio": _round(body_ratio),
                "upper_wick_to_range_ratio": _round(upper_ratio),
                "lower_wick_to_range_ratio": _round(lower_ratio),
                "classification": pattern,
            }
        )

    body_ratios = [item["body_to_range_ratio"] for item in items if item["body_to_range_ratio"] is not None]
    summary = {
        "count": len(items),
        "classifications": dict(Counter(item["classification"] for item in items)),
        "average_body_to_range_ratio": _round(sum(body_ratios) / len(body_ratios)) if body_ratios else None,
        "last_direction": items[-1]["direction"] if items else UNKNOWN,
    }
    return {
        "latest": items[-1] if items else None,
        "recent": items,
        "summary": summary,
        "method": "Explicit body/wick ratio rules; descriptive, not predictive",
        "thresholds": {
            "strong_body_ratio": strong,
            "small_body_ratio": small,
            "doji_ratio": doji,
            "wick_ratio": wick,
        },
    }


def analyze_price_position(
    candles: Sequence[Mapping[str, Any]] | None,
    current_price: float | None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Place the current price within a trailing completed-candle range."""
    clean = completed_candles(candles)
    lookback = int(_param(params, "CONTEXT_PRICE_POSITION_LOOKBACK"))
    segment = _tail(clean, lookback)
    result: dict[str, Any] = {
        "classification": UNKNOWN,
        "method": "Position of current price inside recent completed high/low range",
        "description": "Price position is unavailable",
        "data_sufficiency": False,
        "recent_high": None,
        "recent_low": None,
        "range_size": None,
        "midpoint": None,
        "range_percent": None,
    }
    if len(segment) < 2:
        result["reason"] = "At least two completed candles are required"
        return result

    highs = [_as_float(item.get("high")) for item in segment]
    lows = [_as_float(item.get("low")) for item in segment]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    if not highs or not lows:
        return result
    recent_high, recent_low = max(highs), min(lows)
    candle_range = recent_high - recent_low
    midpoint = (recent_high + recent_low) / 2.0
    price = _as_float(current_price)
    if price is None:
        closes = [_as_float(item.get("close")) for item in segment]
        closes = [value for value in closes if value is not None]
        price = closes[-1] if closes else None
    result.update(
        {
            "recent_high": _round(recent_high),
            "recent_low": _round(recent_low),
            "range_size": _round(candle_range),
            "midpoint": _round(midpoint),
            "current_price": _round(price),
            "data_sufficiency": True,
        }
    )
    if price is None or candle_range <= 0:
        result["reason"] = "Current price or a non-zero range is unavailable"
        return result

    raw_percent = (price - recent_low) / candle_range * 100.0
    position = max(0.0, min(100.0, raw_percent))
    lower = float(_param(params, "CONTEXT_PRICE_POSITION_LOWER_ZONE_PERCENT"))
    upper = float(_param(params, "CONTEXT_PRICE_POSITION_UPPER_ZONE_PERCENT"))
    if position >= upper:
        classification = "UPPER"
        description = f"Price is currently located in the upper {round(100 - upper)}% of the selected lookback range."
    elif position <= lower:
        classification = "LOWER"
        description = f"Price is currently located in the lower {round(lower)}% of the selected lookback range."
    else:
        classification = "MIDDLE"
        description = (
            f"Price is currently located in the middle {round(upper - lower)}% "
            "of the selected lookback range."
        )
    result.update(
        {
            "classification": classification,
            "description": description,
            "range_percent": _round(position, 2),
            "raw_range_percent": _round(raw_percent, 2),
            "zones": {"lower_percent": lower, "upper_percent": upper},
        }
    )
    return result


def analyze_spread(
    quote_context: Mapping[str, Any] | None,
    candles: Sequence[Mapping[str, Any]] | None,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify spread against symbol-specific recent observations only."""
    quote = quote_context or {}
    current_points = _as_float(quote.get("spread_points"))
    if current_points is None:
        current_points = _ratio(quote.get("spread"), quote.get("point"))
    lookback = int(_param(params, "CONTEXT_SPREAD_LOOKBACK"))
    history = [
        value for value in (
            _as_float(candle.get("spread")) for candle in _tail(completed_candles(candles), lookback)
        ) if value is not None and value >= 0
    ]
    result: dict[str, Any] = {
        "classification": UNKNOWN,
        "method": "Current spread points versus recent symbol-specific point observations",
        "data_availability": bool(current_points is not None and history),
        "current": _as_float(quote.get("spread")),
        "current_points": _round(current_points, 2),
        "recent_average_points": None,
        "relative_to_average": None,
        "observations": len(history),
        "explanation": "Current spread or recent spread observations are unavailable",
    }
    if current_points is None or not history:
        return result

    average = sum(history) / len(history)
    ratio = _ratio(current_points, average)
    elevated = float(_param(params, "CONTEXT_SPREAD_ELEVATED_RATIO"))
    abnormal = float(_param(params, "CONTEXT_SPREAD_ABNORMAL_RATIO"))
    if average <= 0:
        classification = "NORMAL" if current_points <= 0 else UNKNOWN
        explanation = "Recent spread observations are zero; relative classification is undefined"
    elif ratio is not None and ratio >= abnormal:
        classification = "ABNORMAL"
        explanation = "Current spread points exceed the abnormal multiple of the recent average"
    elif ratio is not None and ratio >= elevated:
        classification = "ELEVATED"
        explanation = "Current spread points exceed the elevated multiple of the recent average"
    else:
        classification = "NORMAL"
        explanation = "Current spread points are within the normal range of recent observations"
    result.update(
        {
            "classification": classification,
            "recent_average_points": _round(average, 2),
            "relative_to_average": _round(ratio, 2),
            "explanation": explanation,
            "thresholds": {"elevated_ratio": elevated, "abnormal_ratio": abnormal},
        }
    )
    return result


def assess_data_quality(
    symbol_info: Mapping[str, Any] | None,
    quote_context: Mapping[str, Any] | None,
    candles: Sequence[Mapping[str, Any]] | None,
    connected: bool,
    params: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score only the reliability/completeness of data, never trade confidence."""
    quote = quote_context or {}
    clean_input = [item for item in (candles or []) if isinstance(item, Mapping)]
    minimum = int(_param(params, "CONTEXT_MIN_CANDLES"))
    quote_freshness = quote.get("freshness", UNKNOWN)

    valid_ohlc = sum(1 for candle in clean_input if _ohlc(candle) is not None)
    epochs = [candle.get("epoch") for candle in clean_input if candle.get("epoch") is not None]
    duplicates = len(epochs) - len(set(epochs))
    monotonic = all(a < b for a, b in zip(epochs, epochs[1:])) if epochs else False
    symbol_available = bool(symbol_info and symbol_info.get("symbol"))
    quote_available = bool(quote.get("market_available") and quote.get("bid") is not None and quote.get("ask") is not None)
    quote_fresh = bool(quote_available and quote_freshness == "FRESH")
    candle_count_ok = len(clean_input) >= minimum
    ohlc_valid = bool(clean_input) and valid_ohlc == len(clean_input)
    timestamp_ok = bool(epochs) and duplicates == 0 and monotonic
    spread_available = quote.get("spread_points") is not None or quote.get("spread") is not None

    checks = {
        "symbol_available": symbol_available,
        "quote_available": quote_available,
        "quote_fresh": quote_fresh,
        "candle_count": candle_count_ok,
        "ohlc_valid": ohlc_valid,
        "timestamp_consistency": timestamp_ok,
        "spread_available": spread_available,
        "mt5_connected": bool(connected),
    }
    weights = {
        "symbol_available": 10,
        "quote_available": 15,
        "quote_fresh": 15,
        "candle_count": 20,
        "ohlc_valid": 15,
        "timestamp_consistency": 10,
        "spread_available": 5,
        "mt5_connected": 10,
    }
    score = sum(weights[key] for key, passed in checks.items() if passed)

    missing: list[str] = []
    warnings: list[str] = []
    if not symbol_available:
        missing.append("broker symbol metadata")
    if not quote_available:
        missing.append("live quote")
    if not candle_count_ok:
        missing.append(f"at least {minimum} completed candles")
    if not checks["ohlc_valid"]:
        missing.append("valid OHLC values for all returned candles")
    if not checks["timestamp_consistency"]:
        warnings.append("Candle timestamps contain duplicates or are not strictly increasing")
    if not spread_available:
        missing.append("spread observation")
    if not connected:
        missing.append("MT5 terminal connection")
    if quote_freshness == "STALE":
        warnings.append("Quote is stale; classifications are descriptive of cached data only")
    elif quote_freshness == "VERY_STALE":
        warnings.append("Quote is very stale; context is marked unusable")
    elif quote_freshness == "UNAVAILABLE":
        warnings.append("Quote freshness is unavailable")

    unusable = (
        not symbol_available
        or not quote_available
        or not candle_count_ok
        or not checks["ohlc_valid"]
        or quote_freshness in {"VERY_STALE", "UNAVAILABLE"}
        or not connected
    )
    excellent = int(_param(params, "CONTEXT_DATA_QUALITY_EXCELLENT_SCORE"))
    good = int(_param(params, "CONTEXT_DATA_QUALITY_GOOD_SCORE"))
    minimum_score = int(_param(params, "CONTEXT_DATA_QUALITY_MIN_SCORE"))
    if unusable:
        grade = "UNUSABLE"
    elif score >= excellent:
        grade = "EXCELLENT"
    elif score >= good:
        grade = "GOOD"
    elif score >= minimum_score:
        grade = "FAIR"
    else:
        grade = "POOR"

    return {
        "score": int(score),
        "grade": grade,
        "usable": not unusable,
        "method": "Weighted data completeness/reliability score (not prediction confidence)",
        "disclaimer": "This score is not a win probability, forecast, or buy/sell confidence.",
        "checks": checks,
        "weights": weights,
        "missing_data": missing,
        "warnings": warnings,
        "candles_available": len(clean_input),
        "duplicate_candles": duplicates,
        "invalid_ohlc_count": len(clean_input) - valid_ohlc,
        "timestamp_monotonic": monotonic,
        "quote_freshness": quote_freshness,
    }


def unknown_classifications(context: dict[str, Any]) -> dict[str, Any]:
    """Force descriptive classifications to UNKNOWN when the snapshot is unusable."""
    result = dict(context)
    for key, value in result.items():
        if isinstance(value, dict) and "classification" in value:
            updated = dict(value)
            updated["classification"] = UNKNOWN
            updated["suppressed_reason"] = "Data quality is unusable"
            result[key] = updated
    return result
